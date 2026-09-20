"""
quick_backtest.py - 简化版回测 (用于沙箱验证逻辑)

只用预加载的 K线缓存里已有的 30 只热门股, 跑一次完整回测
"""

import sys, os, json, time, statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/scripts')

from simple_picker import (
    score_value, score_momentum, score_quality, score_volume_price,
    classify, hard_filter, pass_form_filter, calc_pe_change,
    fetch_tencent, get_index_today_return, _infer_industry
)
from position_manager import check_exit_signal

CACHE_DIR = Path("/home/ubuntu/.openclaw/workspace/data/kline_cache")

# 30 只热门大盘股 (覆盖银行/券商/消费/能源/科技)
TEST_CODES = [
    "601318", "601628", "600519", "600036", "000333",
    "600900", "601398", "601939", "601288", "601988",
    "600028", "600938", "600276", "600030", "601166",
    "601668", "601888", "601012", "600905", "601088",
    "000858", "000651", "002594", "002475", "300750",
    "601899", "601919", "600000", "601800", "601688",
]

def fetch_one(code):
    cache = CACHE_DIR / f"{code}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        return data.get("kline", [])
    if code.startswith(("6", "9")):
        prefix = "sh"
    else:
        prefix = "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,60,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        d = json.loads(raw)
        rows = d.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
        kline = [{"date": r[0], "open": float(r[1]), "close": float(r[2]),
                  "high": float(r[3]), "low": float(r[4]), "volume": float(r[5])}
                 for r in rows]
        cache.write_text(json.dumps({"date": "2026-07-06", "kline": kline}))
        return kline
    except:
        return []

import urllib.request

def fetch_index(days=60):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,{days},qfq"
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
            d = json.loads(raw)
            rows = d.get("data", {}).get("sh000001", {}).get("qfqday", [])
            return [{"date": r[0], "close": float(r[2]),
                     "change_pct": 0} for r in rows]
        except Exception as e:
            time.sleep(1)
    return []


def main():
    print("=" * 60)
    print("📊 简化回测 v1 — 30 只热门股")
    print("=" * 60)

    print("\n拉取 30 只股 60 日 K 线...")
    klines = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_one, c): c for c in TEST_CODES}
        for fut in as_completed(futures):
            code = futures[fut]
            k = fut.result()
            if k and len(k) >= 25:
                klines[code] = k
    print(f"获取: {len(klines)}/{len(TEST_CODES)} 只")

    print("\n拉取上证指数...")
    index_kline = fetch_index(60)
    if not index_kline:
        print("⚠️ 指数拉取失败,用样例数据代理")
        # 从任一股票 K 线取日期
        sample = next(iter(klines.values()))
        index_kline = [{"date": k["date"], "close": k["close"], "change_pct": 0}
                       for k in sample[-60:]]
    print(f"指数天数: {len(index_kline)}")

    # 用现有实时行情算 quote (用今天的数据)
    print("\n获取当前行情作为 quote 基线...")
    quotes = fetch_tencent(TEST_CODES)
    print(f"  行情: {len(quotes)} 只")

    # 回测循环: 每个交易日作为入场日
    # 由于实时行情只有今天, 我们用今天的 quote + K线历史
    # 简化: 只对最后一笔入场日(T = 倒数第N日)做模拟, 看收益
    total_days = len(index_kline)
    entry_indices = list(range(20, min(total_days - 5, 30)))  # 10 个入场日

    records = []
    code_name = {
        "601318": "中国平安", "601628": "中国人寿", "600519": "贵州茅台",
        "600036": "招商银行", "000333": "美的集团", "600900": "长江电力",
        "601398": "工商银行", "601939": "建设银行", "601288": "农业银行",
        "601988": "中国银行", "600028": "中国石化", "600938": "中国海油",
        "600276": "恒瑞医药", "600030": "中信证券", "601166": "兴业银行",
        "601668": "中国建筑", "601888": "中国中免", "601012": "隆基绿能",
        "600905": "三峡能源", "601088": "中国神华", "000858": "五粮液",
        "000651": "格力电器", "002594": "比亚迪", "002475": "立讯精密",
        "300750": "宁德时代", "601899": "紫金矿业", "601919": "中远海控",
        "600000": "浦发银行", "601800": "中国交建", "601688": "华泰证券",
    }

    for entry_idx in entry_indices:
        entry_date = index_kline[entry_idx]['date']
        if entry_idx > 0:
            idx_chg = (index_kline[entry_idx]['close'] - index_kline[entry_idx-1]['close']) / index_kline[entry_idx-1]['close'] * 100
        else:
            idx_chg = 0

        picks = []
        for code, kline in klines.items():
            if len(kline) <= entry_idx:
                continue
            history = kline[:entry_idx + 1]
            if len(history) < 20:
                continue

            # 构造 quote: 用当时日的数据
            last = history[-1]
            prev = history[-2] if len(history) >= 2 else history[-1]
            quote = {
                "code": code, "name": code_name.get(code, code),
                "current": last["close"],
                "prev_close": prev["close"],
                "open": last["open"],
                "change_pct": (last["close"] - prev["close"]) / prev["close"] * 100,
                "high": last["high"], "low": last["low"],
                "volume": last["volume"],
                "turnover": 1.5,  # 假定
                "pe": quotes.get(code, {}).get("pe", 20.0),
                "market_cap": quotes.get(code, {}).get("market_cap", 500),
                "industry": _infer_industry(code, code_name.get(code, code)),
            }
            ok, _ = hard_filter(quote)
            if not ok:
                continue
            if not pass_form_filter(quote):
                continue

            pe_chg = calc_pe_change(history)
            v = score_value(quote, pe_chg)
            m = score_momentum(quote, history, idx_chg)
            q = score_quality(quote)
            vp = score_volume_price(quote, history)
            total = v + m + q + vp
            mode_tag, _ = classify(total)
            if total < 75:
                continue

            # 入场价 = T+1 开盘
            if entry_idx + 1 >= len(kline):
                continue
            entry_price = kline[entry_idx + 1]["open"]
            mode_key = "🟢 强势" if total >= 88 else "🟡 稳健"
            picks.append({
                "code": code, "name": code_name.get(code, code),
                "mode": mode_key, "score": total,
                "entry_date": entry_date, "entry_price": entry_price,
                "kline_full": kline, "entry_idx_in_kline": entry_idx + 1,
            })

        picks.sort(key=lambda x: -x["score"])
        selected = picks[:5]  # 每天 top 5
        for pick in selected:
            records.append(simulate(pick, (5, 20)))

    print(f"\n回测样本: {len(records)} 笔")
    print_stats(records, (5, 20))


def simulate(pick, hold_periods):
    kline = pick["kline_full"]
    entry_idx = pick["entry_idx_in_kline"]
    entry_price = pick["entry_price"]
    mode = pick["mode"]

    hold_max = hold_periods[0] if mode == "🟢 强势" else hold_periods[1]
    hold_max = min(hold_max, 20)

    highest = entry_price
    days_held = 0
    sold = False
    sold_price = None
    sold_date = None
    sold_reason = ""

    for i in range(entry_idx + 1, min(entry_idx + 1 + hold_max, len(kline))):
        bar = kline[i]
        days_held += 1
        # 硬止损 (盘中)
        if bar["low"] <= entry_price * 0.93:
            sold = True
            sold_price = entry_price * 0.93
            sold_date = bar["date"]
            sold_reason = "硬止损-7%"
            break
        highest = max(highest, bar["high"])
        exit_info = check_exit_signal(entry_price, bar["close"], highest,
                                       days_held, pick["score"])
        if exit_info["action"] in ("sell_half", "sell_all"):
            sold = True
            sold_price = kline[i+1]["open"] if i+1 < len(kline) else bar["close"]
            sold_date = kline[i+1]["date"] if i+1 < len(kline) else bar["date"]
            sold_reason = exit_info["reason"][:30]
            break
        if days_held >= hold_max:
            sold_price = bar["close"]
            sold_date = bar["date"]
            sold_reason = f"持有{hold_max}日到期"
            sold = True
            break

    if not sold:
        sold_price = kline[-1]["close"]
        sold_date = kline[-1]["date"]
        sold_reason = "回测结束"

    pnl_pct = (sold_price - entry_price) / entry_price * 100
    return {
        "code": pick["code"], "name": pick["name"],
        "mode": mode, "score": pick["score"],
        "entry_date": pick["entry_date"],
        "entry_price": entry_price,
        "sold_date": sold_date, "sold_price": sold_price,
        "days_held": days_held, "sold_reason": sold_reason,
        "pnl_pct": pnl_pct,
    }


def print_stats(records, hold_periods):
    print("\n" + "=" * 70)
    print(f"📊 回测结果 (样本 {len(records)} 笔)")
    print("=" * 70)

    for mode in ["🟢 强势", "🟡 稳健"]:
        sub = [r for r in records if r["mode"] == mode]
        if not sub:
            continue
        wins = [r for r in sub if r["pnl_pct"] > 0]
        losses = [r for r in sub if r["pnl_pct"] <= 0]
        print(f"\n【{mode}】{len(sub)} 笔 | 胜率 {len(wins)/len(sub)*100:.1f}%")
        if wins:
            print(f"  平均盈利: {statistics.mean([r['pnl_pct'] for r in wins]):+.2f}%")
        if losses:
            print(f"  平均亏损: {statistics.mean([r['pnl_pct'] for r in losses]):+.2f}%")
        print(f"  整体平均: {statistics.mean([r['pnl_pct'] for r in sub]):+.2f}%")

    print(f"\n退出原因分布:")
    reasons = {}
    for r in records:
        re = r["sold_reason"][:15]
        reasons[re] = reasons.get(re, 0) + 1
    for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    print(f"\n🏆 Top 5:")
    for r in sorted(records, key=lambda x: -x["pnl_pct"])[:5]:
        print(f"  {r['name']}({r['code']}) {r['entry_date']}→{r['sold_date']} {r['pnl_pct']:+.2f}%")

    print(f"\n💔 Worst 5:")
    for r in sorted(records, key=lambda x: x["pnl_pct"])[:5]:
        print(f"  {r['name']}({r['code']}) {r['entry_date']}→{r['sold_date']} {r['pnl_pct']:+.2f}%")

    total_win_rate = len([r for r in records if r["pnl_pct"]>0]) / len(records) * 100
    avg = statistics.mean([r["pnl_pct"] for r in records])
    print(f"\n📈 总胜率: {total_win_rate:.1f}% | 总平均收益: {avg:+.2f}%")


if __name__ == "__main__":
    main()
