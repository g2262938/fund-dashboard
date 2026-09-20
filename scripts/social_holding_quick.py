#!/usr/bin/env python3
"""轻量级社保持股扫描 — 只看核心股票
策略:扫描 50 只「值得看」股票,5 秒出结果
用户可在 holdings.md 里扩展 watchlist
"""
import sys
import json
import time
import urllib.request
import re
import datetime
from pathlib import Path

DATA_DIR = Path("/home/ubuntu/.openclaw/workspace/reports/data")
SOCIAL_DIR = DATA_DIR / "social_holding"
SOCIAL_DIR.mkdir(parents=True, exist_ok=True)

# 核心扫描列表:用户持仓 + 热门大票
WATCHLIST = [
    # 用户持仓
    ("688387", "信科移动"),
    ("601678", "滨化股份"),
    # 热门大票(社保高概率持仓)
    ("002594", "比亚迪"),       # 已知有社保
    ("601628", "中国人寿"),
    ("600585", "海螺水泥"),
    ("600690", "海尔智家"),
    ("600703", "三安光电"),
    ("601012", "隆基绿能"),
    ("601888", "中国中免"),
    ("300760", "迈瑞医疗"),
    ("002025", "航天电器"),
    ("600519", "贵州茅台"),
    ("601318", "中国平安"),
    ("600036", "招商银行"),
    ("000001", "平安银行"),
    ("000002", "万科A"),
    ("600276", "恒瑞医药"),
    ("600887", "伊利股份"),
    ("601398", "工商银行"),
    ("601988", "中国银行"),
    ("601288", "农业银行"),
    ("600030", "中信证券"),
    ("601166", "兴业银行"),
    ("600000", "浦发银行"),
    ("600016", "民生银行"),
    ("600028", "中国石化"),
    ("600050", "中国联通"),
    ("600104", "上汽集团"),
    ("600196", "复星医药"),
    ("600436", "片仔癀"),
    ("600519", "贵州茅台"),
    ("601857", "中国石油"),
    ("601899", "紫金矿业"),
    ("002415", "海康威视"),
    ("002475", "立讯精密"),
    ("300124", "汇川技术"),
    ("300015", "爱尔眼科"),
    ("300059", "东方财富"),
    ("300750", "宁德时代"),
    ("002230", "科大讯飞"),
    ("002714", "牧原股份"),
    ("300142", "沃森生物"),
    ("300316", "晶盛机电"),
    ("300274", "阳光电源"),
    ("300033", "同花顺"),
    ("002466", "天齐锂业"),
    ("002812", "恩捷股份"),
    ("300661", "圣邦股份"),
    ("300498", "温氏股份"),
]

SOCIAL_KEYWORDS = ["社保", "全国社保", "社保基金", "社保理事会"]


def fetch_top10(code: str) -> list[dict] | None:
    """拉一只股票的十大股东"""
    import akshare as ak
    if not code.startswith(("sh", "sz")):
        if code.startswith(("6", "9")):
            code = "sh" + code
        else:
            code = "sz" + code
    for attempt in range(2):
        try:
            df = ak.stock_gdfx_top_10_em(symbol=code, date="20250930")
            return [
                {
                    "name": str(r.get("股东名称", "")),
                    "shares": int(r.get("持股数", 0) or 0),
                    "pct": float(r.get("占总股本持股比例", 0) or 0),
                }
                for _, r in df.iterrows()
            ]
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
            continue
    return None


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    """腾讯接口拉股价"""
    if not codes:
        return {}
    qt = []
    for c in codes:
        if c.startswith(("6", "9")):
            qt.append(f"sh{c}")
        else:
            qt.append(f"sz{c}")
    url = f"https://qt.gtimg.cn/q={','.join(qt)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="ignore")
    except Exception:
        return {}
    quotes = {}
    for m in re.finditer(r'v_(\w+)="([^"]+)"', raw):
        code = m.group(1)[2:]
        f = m.group(2).split("~")
        if len(f) > 50:
            quotes[code] = {
                "current": float(f[3]) if f[3] else 0,
                "change_pct": float(f[32]) if f[32] else 0,
            }
    return quotes


def main():
    today = datetime.date.today().strftime("%Y-%m-%d")
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    start = time.time()

    print(f"[{now_str}] 核心社保持股扫描启动({len(WATCHLIST)} 只)", file=sys.stderr)

    holdings = []
    for i, (code, name) in enumerate(WATCHLIST):
        top10 = fetch_top10(code)
        if not top10:
            continue
        social_records = [r for r in top10 if any(k in r["name"] for k in SOCIAL_KEYWORDS)]
        if not social_records:
            continue
        total_shares = sum(r["shares"] for r in social_records)
        total_pct = sum(r["pct"] for r in social_records)
        holdings.append({
            "code": code,
            "name": name,
            "holders": social_records,
            "total_social_shares": total_shares,
            "total_pct": total_pct,
        })
        print(f"  ✅ {code} {name}: {len(social_records)} 个社保组合, {total_pct:.2f}% 总占比", file=sys.stderr)

    # 股价
    codes = [h["code"] for h in holdings]
    quotes = fetch_quotes(codes)
    for h in holdings:
        q = quotes.get(h["code"], {})
        h["price"] = q.get("current", 0)
        h["change_pct"] = q.get("change_pct", 0)
        h["total_market_cap"] = h["total_social_shares"] * h["price"]

    output = {
        "generated_at": datetime.datetime.now().isoformat(),
        "source": "东方财富 十大股东公告(ak.stock_gdfx_top_10_em)",
        "period": "20250930",
        "watchlist_count": len(WATCHLIST),
        "social_count": len(holdings),
        "holdings": holdings,
    }

    # 保存
    snap_file = SOCIAL_DIR / f"social_holding_{today}.json"
    snap_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    DATA_DIR.joinpath("social_holding_quick.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅ 已保存: {snap_file}", file=sys.stderr)
    print(f"   统计: {len(holdings)} 只有社保 · 耗时 {time.time()-start:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()