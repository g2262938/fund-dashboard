"""
fetcher.py — 美股新闻 + 标的数据获取
====================================

数据源（按优先级）：
1. Finnhub（推荐，免费 60 calls/min）
   - 注册：https://finnhub.io/register → FINNHUB_API_KEY 环境变量
   - 新闻 endpoint: /news?category=general 或 /company-news?symbol=AAPL
   - 市值: /stock/profile2 带 marketCapitalization（单位：百万美元）
2. Yahoo Finance via yfinance（fallback，无需 key）
   - Ticker.news（8-20 条/票，来源 Yahoo 聚合）
   - Ticker.info['marketCap']

标的池（market cap > 30B USD）：
- 优先 yfinance 实时校验（24h 缓存）
- 离线 fallback 用 sector_map.STATIC_MAP 的 keys

新闻条目格式（统一）：
{
    "ticker": "AAPL",
    "title": "...",
    "summary": "...",
    "url": "https://...",
    "publisher": "Reuters",
    "published_at": 1695000000,     # Unix epoch seconds
    "published_iso": "2026-09-20T...",
    "source": "finnhub" | "yfinance"
}
"""

from __future__ import annotations
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 配置
# ============================================================

FINNHUB_BASE = "https://finnhub.io/api/v1"
CACHE_DIR = Path(os.environ.get("FUND_US_NEWS_CACHE",
                                 str(Path.home() / ".cache" / "us_news")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_CACHE_FILE = CACHE_DIR / "universe.json"
UNIVERSE_CACHE_TTL = 24 * 3600  # 24 hours
MARKET_CAP_MIN = 30_000_000_000  # 30B USD


# ============================================================
# 工具函数
# ============================================================

def _http_get_json(url: str, headers: dict | None = None, timeout: int = 10) -> Any:
    """HTTP GET → JSON，失败抛 IOError"""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _epoch_to_iso(epoch: int | float) -> str:
    """epoch seconds → ISO 8601 UTC"""
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return ""


# ============================================================
# 标的池
# ============================================================

def get_universe() -> list[dict]:
    """获取市值 > 30B USD 的美股 ticker 列表。

    数据流：
    1. 先读 UNIVERSE_CACHE_FILE（24h 内有效则直接返回）
    2. 否则尝试 yfinance：扫描 STATIC_MAP 所有 ticker → 取 marketCap
    3. yfinance 失败时 fallback 到 STATIC_MAP（无市值数据，全部纳入）

    返回 list[dict]: [{ticker, market_cap, sector, name, ...}, ...]
    """
    # 1. 缓存命中
    if UNIVERSE_CACHE_FILE.exists():
        age = time.time() - UNIVERSE_CACHE_FILE.stat().st_mtime
        if age < UNIVERSE_CACHE_TTL:
            try:
                data = json.loads(UNIVERSE_CACHE_FILE.read_text())
                if data:
                    return data
            except Exception:
                pass

    # 2. yfinance 实时校验
    universe = _fetch_universe_via_yfinance()

    # 3. 缓存
    if universe:
        UNIVERSE_CACHE_FILE.write_text(json.dumps(universe, ensure_ascii=False, indent=2))

    return universe


def _fetch_universe_via_yfinance() -> list[dict]:
    """用 yfinance 拉所有 static map 的 ticker 市值"""
    # 延迟 import，yfinance 是可选依赖
    try:
        from sector_map import STATIC_MAP, SECTOR_LABELS_ZH
        import yfinance as yf
    except ImportError as e:
        print(f"  [universe] yfinance 不可用: {e}，使用静态映射 fallback")
        return _static_universe()

    universe: list[dict] = []
    tickers = list(STATIC_MAP.keys())
    print(f"  [universe] 扫描 {len(tickers)} 个 ticker...")

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            mc = info.get("marketCap")
            if mc is None:
                continue
            if mc < MARKET_CAP_MIN:
                continue
            sector = info.get("sector") or STATIC_MAP[ticker][0]
            sub = info.get("industry") or STATIC_MAP[ticker][1]
            universe.append({
                "ticker": ticker,
                "market_cap": mc,
                "sector": sector,
                "sector_zh": SECTOR_LABELS_ZH.get(sector, sector),
                "sub_industry": sub,
                "name": info.get("shortName") or info.get("longName") or ticker,
                "exchange": "na",
            })
        except Exception as e:
            # 单个 ticker 失败不影响整体
            continue

    universe.sort(key=lambda x: x.get("market_cap", 0), reverse=True)
    print(f"  [universe] 拿到 {len(universe)} 只 > 30B 标的")
    return universe


def _static_universe() -> list[dict]:
    """完全离线 fallback：返回 static map 中的所有 ticker，无市值字段"""
    from sector_map import STATIC_MAP, SECTOR_LABELS_ZH
    universe = []
    for ticker, (sector, sub, label_zh) in STATIC_MAP.items():
        universe.append({
            "ticker": ticker,
            "market_cap": None,        # 未知
            "sector": sector,
            "sector_zh": SECTOR_LABELS_ZH.get(sector, sector),
            "sub_industry": sub,
            "name": label_zh,
            "exchange": "na",
        })
    universe.sort(key=lambda x: x.get("ticker", ""))
    return universe


# ============================================================
# 新闻获取
# ============================================================

def fetch_news_for_ticker(ticker: str, days_back: int = 1) -> list[dict]:
    """获取单个 ticker 的新闻（按时间倒序）"""
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if api_key:
        news = _fetch_via_finnhub(ticker, days_back, api_key)
        if news:
            return news

    # fallback to yfinance
    return _fetch_via_yfinance(ticker)


def _fetch_via_finnhub(ticker: str, days_back: int, api_key: str) -> list[dict]:
    """Finnhub /company-news endpoint"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from_date = datetime.fromtimestamp(
        time.time() - days_back * 86400, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    url = (
        f"{FINNHUB_BASE}/company-news?symbol={ticker}"
        f"&from={from_date}&to={today}&token={api_key}"
    )
    try:
        raw = _http_get_json(url, timeout=10)
    except Exception as e:
        print(f"  [finnhub {ticker}] {e}")
        return []

    items = []
    for n in (raw if isinstance(raw, list) else []):
        url_link = n.get("url", "")
        if not url_link:
            continue
        ts = n.get("datetime", 0)
        items.append({
            "ticker": ticker,
            "title": n.get("headline", ""),
            "summary": n.get("summary", ""),
            "url": url_link,
            "publisher": n.get("source", ""),
            "published_at": ts,
            "published_iso": _epoch_to_iso(ts),
            "source": "finnhub",
        })
    return items


def _fetch_via_yfinance(ticker: str) -> list[dict]:
    """yfinance Ticker.news fallback"""
    try:
        import yfinance as yf
    except ImportError:
        return []

    try:
        t = yf.Ticker(ticker)
        raw = t.news or []
    except Exception as e:
        print(f"  [yfinance {ticker}] {e}")
        return []

    items = []
    for n in raw:
        title = n.get("title", "")
        link = n.get("link", "")
        if not title or not link:
            continue
        # yfinance 用 providerPublishTime（epoch seconds）
        ts = n.get("providerPublishTime") or n.get("pubDate") or 0
        publisher = n.get("publisher", "")
        # yfinance 有时给 thumbnail, type
        items.append({
            "ticker": ticker,
            "title": title,
            "summary": n.get("summary", ""),  # yfinance 通常无 summary
            "url": link,
            "publisher": publisher,
            "published_at": ts,
            "published_iso": _epoch_to_iso(ts),
            "source": "yfinance",
        })
    return items


# ============================================================
# 批量获取
# ============================================================

def fetch_all_news(universe: list[dict],
                   max_workers: int = 8,
                   max_tickers: int | None = None) -> list[dict]:
    """并发拉取所有标的的新闻"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tickers = [u["ticker"] for u in universe]
    if max_tickers:
        tickers = tickers[:max_tickers]

    all_news: list[dict] = []
    print(f"  [news] 并发拉取 {len(tickers)} 个 ticker 的新闻...")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_news_for_ticker, t): t for t in tickers}
        done = 0
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                news = fut.result() or []
                all_news.extend(news)
            except Exception:
                pass
            done += 1
            if done % 20 == 0:
                print(f"  [news] 进度 {done}/{len(tickers)}, 累计 {len(all_news)} 条")

    # 按时间倒序
    all_news.sort(key=lambda n: n.get("published_at", 0), reverse=True)
    print(f"  [news] 拉取完成，共 {len(all_news)} 条新闻")
    return all_news


# ============================================================
# main（命令行入口）
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "universe":
        u = get_universe()
        print(f"\n标的池（> 30B USD）：{len(u)} 只")
        for item in u[:10]:
            print(f"  {item['ticker']:6} | {item.get('sector_zh', ''):6} | "
                  f"{item.get('name', '')[:30]:30} | "
                  f"{item.get('market_cap', 0) / 1e9:5.0f}B USD")
    else:
        ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
        print(f"\n=== {ticker} ===")
        news = fetch_news_for_ticker(ticker)
        for n in news[:5]:
            print(f"\n  [{n['published_iso']}] {n['publisher']}")
            print(f"  {n['title']}")
            print(f"  → {n['url']}")