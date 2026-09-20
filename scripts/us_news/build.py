"""
build.py — 美股新闻信息中心 主流程
===================================

用法:
    python3 scripts/us_news/build.py              # 全量构建
    python3 scripts/us_news/build.py --use-mock   # 用 mock 数据（沙箱/无网络环境）

数据流:
    1. fetcher.get_universe()        → 标的池（市值 > 30B）
    2. fetcher.fetch_all_news()      → 全量新闻
    3. sentiment.analyze_sentiment() → 每条新闻打分
    4. 聚合 → sector 视图 → 大盘视图
    5. 原子写入 data/us_news_latest.json

输出文件:
    data/us_news_latest.json — 供 us_news.html 前端读取

注意:
    - 写入用 tmp + os.replace 原子替换（与本仓库其他脚本一致）
    - 抓数据失败时自动 fallback（Finnhub → yfinance → mock）
"""

from __future__ import annotations
import os
import sys
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 让 scripts/ 在 path 上
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.us_news.fetcher import (
    get_universe, fetch_all_news, MARKET_CAP_MIN
)
from scripts.us_news.sentiment import analyze_sentiment, aggregate_sentiment
from scripts.us_news.sector_map import SECTOR_LABELS_ZH


OUTPUT_FILE = _PROJECT_ROOT / "data" / "us_news_latest.json"


# ============================================================
# 聚合逻辑
# ============================================================

def build_per_ticker_view(universe: list[dict], news_items: list[dict]) -> list[dict]:
    """标的视角：每个 ticker 一行，含 sentiment 摘要 + 新闻列表"""
    news_by_ticker = defaultdict(list)
    for n in news_items:
        news_by_ticker[n["ticker"]].append(n)

    result = []
    for u in universe:
        ticker = u["ticker"]
        news = news_by_ticker.get(ticker, [])
        # 每条新闻都打 sentiment 分
        scored = []
        for n in news:
            s = analyze_sentiment(n["title"], n.get("summary", ""))
            scored.append({**n, "sentiment": s})

        agg = aggregate_sentiment([item["sentiment"] for item in scored])

        # 最新 5 条
        scored.sort(key=lambda x: x.get("published_at", 0), reverse=True)
        result.append({
            **u,
            "news_count": len(scored),
            "sentiment_summary": agg,
            "recent_news": scored[:5],
        })
    return result


def build_per_sector_view(per_ticker: list[dict]) -> list[dict]:
    """板块视角：11 大 sector 的聚合"""
    by_sector = defaultdict(list)
    for item in per_ticker:
        by_sector[item["sector"]].append(item)

    result = []
    for sector, items in by_sector.items():
        # 把每只票的"新闻 sentiment 列表"展开
        all_news_sentiments = []
        ticker_breakdown = []
        for t in items:
            t_news_sentiments = [n["sentiment"] for n in t.get("recent_news", [])]
            all_news_sentiments.extend(t_news_sentiments)
            ticker_breakdown.append({
                "ticker": t["ticker"],
                "name": t.get("name", t["ticker"]),
                "news_count": t.get("news_count", 0),
                "aggregate_score": t["sentiment_summary"]["aggregate_score"],
                "dominant_label": t["sentiment_summary"]["dominant_label"],
            })

        agg = aggregate_sentiment(all_news_sentiments)
        ticker_breakdown.sort(key=lambda x: x["aggregate_score"])

        result.append({
            "sector": sector,
            "sector_zh": SECTOR_LABELS_ZH.get(sector, sector),
            "ticker_count": len(items),
            "news_count": sum(t.get("news_count", 0) for t in items),
            "sentiment_summary": agg,
            "tickers": ticker_breakdown,
        })

    # 按 aggregate_score 升序（利空最严重的在前）
    result.sort(key=lambda x: x["sentiment_summary"]["aggregate_score"])
    return result


def build_market_view(per_ticker: list[dict], per_sector: list[dict]) -> dict:
    """大盘视角：整体 + Top 5 利空/利多板块"""
    all_sentiments = []
    for t in per_ticker:
        for n in t.get("recent_news", []):
            all_sentiments.append(n["sentiment"])

    overall = aggregate_sentiment(all_sentiments)

    # Top 5 利空板块（仅含 score < 0 的；空则不补）
    bearish = sorted(
        [s for s in per_sector if s["sentiment_summary"]["aggregate_score"] < 0],
        key=lambda x: x["sentiment_summary"]["aggregate_score"],
    )[:5]
    # Top 5 利多板块（仅含 score > 0 的）
    bullish = sorted(
        [s for s in per_sector if s["sentiment_summary"]["aggregate_score"] > 0],
        key=lambda x: x["sentiment_summary"]["aggregate_score"],
        reverse=True,
    )[:5]

    # 影响最严重的 ticker（按新闻数 × |score| 加权）
    impacted_tickers = sorted(
        per_ticker,
        key=lambda t: t.get("news_count", 0) * abs(t["sentiment_summary"]["aggregate_score"]),
        reverse=True,
    )[:10]

    return {
        "overall": overall,
        "top_bearish_sectors": bearish,
        "top_bullish_sectors": bullish,
        "most_impacted_tickers": impacted_tickers,
    }


# ============================================================
# 原子写入
# ============================================================

def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ============================================================
# 主流程
# ============================================================

def build(use_mock: bool = False) -> dict:
    started = time.time()

    if use_mock:
        print("[build] 使用 mock 数据（沙箱/无网络环境）")
        mock_path = _PROJECT_ROOT / "data" / "us_news_mock.json"
        if not mock_path.exists():
            print(f"[build] ✗ mock 文件不存在: {mock_path}")
            print("[build] 请先生成 mock: python3 scripts/us_news/build_mock.py")
            sys.exit(1)
        with open(mock_path, encoding="utf-8") as f:
            data = json.load(f)
        universe = data["universe"]
        news_items = data["news"]
    else:
        print("[build] 1/4 获取标的池...")
        universe = get_universe()
        if not universe:
            print("[build] ✗ 标的池为空")
            sys.exit(1)
        print(f"[build] ✓ {len(universe)} 只标的")

        print("[build] 2/4 拉取新闻...")
        news_items = fetch_all_news(universe)
        if not news_items:
            print("[build] ⚠️ 未拉到任何新闻，建议加 --use-mock 或检查 API key")

    print("[build] 3/4 计算 sentiment + 聚合...")
    per_ticker = build_per_ticker_view(universe, news_items)
    per_sector = build_per_sector_view(per_ticker)
    market_view = build_market_view(per_ticker, per_sector)

    print("[build] 4/4 写入数据文件...")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_local": datetime.now().isoformat(),
        "universe_count": len(universe),
        "news_count": len(news_items),
        "market_cap_min_usd": MARKET_CAP_MIN,
        "data_source": "mock" if use_mock else (
            "finnhub" if os.environ.get("FINNHUB_API_KEY") else "yfinance"
        ),
        "per_ticker": per_ticker,
        "per_sector": per_sector,
        "market_view": market_view,
    }
    atomic_write_json(OUTPUT_FILE, payload)

    elapsed = time.time() - started
    print(f"\n[build] ✓ 完成（耗时 {elapsed:.1f}s）")
    print(f"  - 标的: {len(per_ticker)}")
    print(f"  - 板块: {len(per_sector)}")
    print(f"  - 新闻: {len(news_items)}")
    print(f"  - 输出: {OUTPUT_FILE}")

    return payload


if __name__ == "__main__":
    use_mock = "--use-mock" in sys.argv
    build(use_mock=use_mock)