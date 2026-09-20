"""
backtest_v6_test.py - 测试 🟡 稳健用更严格的 -5% 止损
"""
import sys, os, json, time, statistics, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))
import simple_picker
from backtest import (
    get_index_history, get_all_klines_cached,
    simulate_trade, generate_report, save_report
)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--strong", type=int, default=3)
    parser.add_argument("--steady", type=int, default=6)
    args = parser.parse_args()

    klines = get_all_klines_cached()
    index_kline = get_index_history(60)
    if not klines or not index_kline:
        print("❌ 数据不足"); return

    index_dates = sorted({b["date"] for b in index_kline})
    trading_days = [d for d in index_dates if d <= simple_picker.TODAY][-args.days:]

    strong_trades, steady_trades = [], []
    strong_mode, steady_mode = [], []

    print(f"回测区间: 近 {args.days} 个交易日")
    print(f"入场日: {len(trading_days)} 个")

    for i, entry_date in enumerate(trading_days[:-5]):
        day_klines = {code: [b for b in bars if b["date"] <= entry_date][-60:]
                      for code, bars in klines.items()}
        quotes = {code: simple_picker.build_quote(day_klines[code], code)
                  for code in day_klines if day_klines[code]}

        index_ret = 0.0
        idx_bar = [b for b in index_kline if b["date"] == entry_date]
        if idx_bar and len(index_kline) > 1:
            prev = index_kline[index_kline.index(idx_bar[0]) - 1]["close"]
            index_ret = (idx_bar[0]["close"] - prev) / prev * 100 if prev else 0

        scored = []
        for code, q in quotes.items():
            ok, _ = simple_picker.hard_filter(q)
            if not ok: continue
            if not simple_picker.pass_form_filter(q): continue
            kl = day_klines.get(code, [])
            v = simple_picker.score_value(q, simple_picker.calc_pe_change(kl))
            m = simple_picker.score_momentum(q, kl, index_ret)
            q_ = simple_picker.score_quality(q)
            vp = simple_picker.score_volume_price(q, kl)
            total = v + m + q_ + vp
            tag = simple_picker.classify(total)[0]
            scored.append((code, q, total, tag, m))

        strong = [s for s in scored if s[3].startswith("🟢")]
        steady = [s for s in scored if s[3].startswith("🟡")]
        strong.sort(key=lambda x: -x[2]); steady.sort(key=lambda x: -x[2])

        # 🟢 强势用 -7% 止损
        for code, q, total, tag, momentum in strong[:args.strong]:
            trade = simulate_trade(
                code, q, day_klines.get(code, []),
                holding_days=5, stop_loss=0.07,
                entry_date=entry_date, index_kline=index_kline,
                trade_type="strong"
            )
            if trade: strong_trades.append(trade)

        # 🟡 稳健用 -5% 止损（更严格）
        for code, q, total, tag, momentum in steady[:args.steady]:
            trade = simulate_trade(
                code, q, day_klines.get(code, []),
                holding_days=5, stop_loss=0.05,  # 🟡 更严格止损
                entry_date=entry_date, index_kline=index_kline,
                trade_type="steady"
            )
            if trade: steady_trades.append(trade)

    # 打印对比
    print("\n" + "="*70)
    print("🟡 止损对比测试: -7% (原始) vs -5% (更严格)")
    print("="*70)

    # 用 backtest.py 原始版本跑 -7% 结果
    print("\n【-7% 止损 - 原始】请参考上方 v4.1 回测数据")
    print(f"\n【-5% 止损 - 测试】稳健 {len(steady_trades)} 笔:")

    if steady_trades:
        wins = [t for t in steady_trades if t["pnl_pct"] > 0]
        losses = [t for t in steady_trades if t["pnl_pct"] <= 0]
        win_rate = len(wins) / len(steady_trades) * 100
        avg_win = statistics.mean([t["pnl_pct"] for t in wins]) if wins else 0
        avg_loss = statistics.mean([t["pnl_pct"] for t in losses]) if losses else 0
        avg_ret = statistics.mean([t["pnl_pct"] for t in steady_trades])
        print(f"  胜率: {win_rate:.1f}% ({len(wins)}/{len(steady_trades)})")
        print(f"  平均盈利: {avg_win:+.2f}%  平均亏损: {avg_loss:+.2f}%")
        print(f"  盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss else "  盈亏比: N/A")
        print(f"  单笔平均: {avg_ret:+.2f}%")

        from collections import Counter
        reasons = Counter(t["exit_reason"] for t in steady_trades)
        print(f"  退出原因: {dict(reasons)}")

if __name__ == "__main__":
    main()
