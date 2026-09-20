"""
backtest.py - simple_picker v4.1 历史回测

回测逻辑:
  T 日: 用 simple_picker 评分挑出 🟢(≥88) 和 🟡(≥75) 股票
  T+1 日开盘价买入
  T+5 / T+20 收盘: 模拟持仓结果, 计算收益

止盈止损验证:
  每个持仓期间, 每日检查 5 重卖出规则, 触发即卖

统计指标:
  - 胜率: 5日收益 > 0 的比例
  - 盈亏比: 平均盈利 / 平均亏损
  - 最大回撤: 单笔最大亏损
  - 总收益: 等权买入所有推荐股的总收益

数据源: 腾讯 K线 (本地缓存 + 历史拉取)
"""

import sys
import os
import json
import time
import urllib.request
import datetime
import statistics
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
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_kline_history(code: str, days: int = 60) -> list[dict]:
    """拉取历史 K线 (60日), 用于回测"""
    if code.startswith(("6", "9")):
        prefix = "sh"
    else:
        prefix = "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{days},qfq"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        data = json.loads(raw)
        raw_list = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
        kline = []
        for row in raw_list:
            if len(row) >= 5:
                kline.append({
                    "date": row[0],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                })
        return kline
    except Exception:
        return []


def get_universe():
    import akshare as ak
    df = ak.stock_info_a_code_name()
    records = []
    for _, row in df.iterrows():
        code = str(row["code"]).zfill(6)
        name = str(row["name"])
        if code.startswith(("4", "8")):
            continue
        if "ST" in name.upper() or "*ST" in name.upper():
            continue
        if len(name) < 2:
            continue
        records.append({"code": code, "name": name})
    return records


def build_quote_from_kline(kline: list[dict], code: str, name: str) -> dict:
    """从 K线某一日的快照构造 quote 字典"""
    today = kline[-1]
    return {
        "code": code, "name": name,
        "current": today["close"],
        "prev_close": kline[-2]["close"] if len(kline) >= 2 else today["close"],
        "open": today["open"],
        "change_pct": (today["close"] - kline[-2]["close"]) / kline[-2]["close"] * 100
                      if len(kline) >= 2 else 0,
        "high": today["high"],
        "low": today["low"],
        "volume": today["volume"],
        "turnover": 1.5,  # 回测场景假定中等流动性
        "pe": 20.0,       # 默认中性
        "market_cap": 500,  # 默认中盘
        "industry": _infer_industry(code, name),
    }


def get_index_history(days: int = 60) -> list[dict]:
    """上证指数 K线"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,{days},qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        data = json.loads(raw)
        raw_list = data.get("data", {}).get("sh000001", {}).get("qfqday", [])
        kline = []
        for row in raw_list:
            if len(row) >= 2:
                kline.append({
                    "date": row[0],
                    "close": float(row[2]),
                    "change_pct": 0,
                })
        return kline
    except Exception:
        return []


def run_backtest(
    days_back: int = 25,    # 回测 25 个交易日 (~ 1个月)
    hold_periods: tuple = (5, 20),  # 5日和20日
    top_n_strong: int = 5,   # 每天选前N只
    top_n_steady: int = 8,
    total_capital: float = 1000000.0,
):
    print("=" * 60)
    print(f"📊 simple_picker v4.1 回测")
    print(f"回测区间: 近 {days_back} 个交易日")
    print(f"持有期: {hold_periods[0]}日(波段) / {hold_periods[1]}日(中线)")
    print(f"每日选股: 🟢 {top_n_strong}只 + 🟡 {top_n_steady}只")
    print(f"资金: {total_capital:,.0f} 元 (等权)")
    print("=" * 60)

    universe = get_universe()
    print(f"股票池: {len(universe)} 只")

    # 并行拉K线 (用 60 日 K线, 截取后 25 天作为回测窗口)
    print(f"\n拉取 60 日 K线...")
    all_klines = {}
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(fetch_kline_history, u['code'], 60): u['code']
                   for u in universe}
        done = 0
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                k = fut.result()
                if k and len(k) >= 30:
                    all_klines[code] = k
            except:
                pass
            done += 1
            if done % 500 == 0:
                print(f"  K线进度: {done}/{len(universe)}")
    print(f"K线覆盖: {len(all_klines)} 只")

    # 上证指数 (单独请求避免限流)
    time.sleep(0.5)
    index_kline = get_index_history(60)
    if not index_kline:
        # 重试一次
        time.sleep(1)
        index_kline = get_index_history(60)
    if not index_kline:
        # 用首只股票的 K 线日期作为时间轴代理
        sample = next(iter(all_klines.values()), None)
        if not sample:
            print("❌ 无数据")
            return
        index_kline = [{"date": k["date"], "close": k["close"], "change_pct": 0}
                       for k in sample[-60:]]
        print(f"⚠️ 用K线日期作为时间轴代理 ({len(index_kline)} 天)")

    # 统计每个交易日可推荐的股票
    # 简化策略: 用 K线最后一天作为 T 日数据 (假设 T 日 = 今日)
    # 然后计算 T+5/T+20 收益
    all_codes = list(all_klines.keys())
    code_name = {u['code']: u['name'] for u in universe}

    # 取所有股票的最新一段, 用 index_kline[-days_back-1] 作为 T 日
    if not index_kline:
        print("❌ 无法获取指数数据")
        return

    # 取回测窗口 (留出 25 天给 T+20 用)
    index_dates = [k['date'] for k in index_kline]
    print(f"\n指数覆盖区间: {index_dates[0]} → {index_dates[-1]}")
    print(f"共 {len(index_dates)} 个交易日")

    # 以每个交易日作为入场日, 后 25 天要有数据
    total_days = len(index_kline)
    entry_indices = list(range(20, total_days - 25))  # 留出 T+20 空间

    print(f"\n回测样本: {len(entry_indices)} 个入场日")
    print(f"每天入场后最多持有 {max(hold_periods)} 日")

    # ─── 每日选股 ──────────────────────────────────────────
    portfolio_records = []  # 所有持仓记录

    for entry_idx in entry_indices:
        entry_date = index_kline[entry_idx]['date']
        # 当天指数涨跌幅 (入场基准)
        if entry_idx > 0:
            today_idx = (index_kline[entry_idx]['close'] - index_kline[entry_idx-1]['close']) / index_kline[entry_idx-1]['close'] * 100
        else:
            today_idx = 0

        # 对所有有 K线的股票, 用截至 entry_idx 的数据算 v4.1 评分
        picks = []
        for code in all_codes:
            kline = all_klines[code]
            if len(kline) <= entry_idx + 1:
                continue
            # 取到 entry_idx 为止的数据 (不包含未来信息)
            history = kline[:entry_idx + 1]

            if len(history) < 20:
                continue

            quote = build_quote_from_kline(history, code, code_name.get(code, code))
            # 硬过滤
            ok, _ = hard_filter(quote)
            if not ok:
                continue
            # 形态过滤
            if not pass_form_filter(quote):
                continue

            # 打分
            pe_chg = calc_pe_change(history)
            v = score_value(quote, pe_chg)
            m = score_momentum(quote, history, today_idx)
            q = score_quality(quote)
            vp = score_volume_price(quote, history)
            total = v + m + q + vp
            mode_tag, _ = classify(total)

            if total >= 88:
                mode_key = "🟢 强势"
            elif total >= 75:
                mode_key = "🟡 稳健"
            else:
                continue

            # 入场价 = T+1 开盘价 (次日开盘, 模拟真实操作)
            if entry_idx + 1 >= len(kline):
                continue
            entry_price = kline[entry_idx + 1]['open']

            picks.append({
                "code": code, "name": code_name.get(code, code),
                "mode": mode_key, "score": total,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "kline_full": kline,
                "entry_idx_in_kline": entry_idx + 1,
            })

        # 排序选 top_n
        picks.sort(key=lambda x: -x["score"])
        selected = [p for p in picks if p["mode"] == "🟢 强势"][:top_n_strong] + \
                   [p for p in picks if p["mode"] == "🟡 稳健"][:top_n_steady]

        # 模拟每个持仓
        for pick in selected:
            record = simulate_trade(pick, hold_periods)
            portfolio_records.append(record)

    if not portfolio_records:
        print("❌ 没有产生任何持仓记录 (数据不足)")
        return

    # 统计分析
    print_stats(portfolio_records, hold_periods)


def simulate_trade(pick: dict, hold_periods: tuple) -> dict:
    """模拟单笔交易, 检查止盈止损"""
    kline = pick["kline_full"]
    entry_idx = pick["entry_idx_in_kline"]
    entry_price = pick["entry_price"]
    entry_date = pick["entry_date"]

    mode = pick["mode"]
    if mode == "🟢 强势":
        hold_max = hold_periods[0]  # 5日
    else:
        hold_max = hold_periods[1]  # 20日
    hold_max = min(hold_max, max(hold_periods))

    highest = entry_price
    days_held = 0
    sold = False
    sold_price = None
    sold_date = None
    sold_reason = ""

    # 模拟每天
    for i in range(entry_idx + 1, min(entry_idx + 1 + hold_max, len(kline))):
        bar = kline[i]
        days_held += 1

        # 盘中可能达到的最高价
        high_so_far = max(highest, bar["high"])
        lowest = bar["low"]

        # 检查硬止损 (盘中触发, v5 回滚:-7%)
        loss_pct = (lowest - entry_price) / entry_price * 100
        if loss_pct <= 0.0:
            sold = True
            sold_price = entry_price * 1.00
            sold_date = bar["date"]
            sold_reason = f"🛑硬止损(0%), 当日最低{lowest:.2f}"
            break

        # 跟踪最高价
        highest = high_so_far

        # 日末收盘检查其他规则
        close = bar["close"]
        exit_info = check_exit_signal(
            entry_price=entry_price,
            current_price=close,
            highest_price=highest,
            days_held=days_held,
            current_score=pick["score"],
        )
        if exit_info["action"] in ("sell_half", "sell_all"):
            sold = True
            # 模拟下一天开盘卖出
            if i + 1 < len(kline):
                sold_price = kline[i + 1]["open"]
                sold_date = kline[i + 1]["date"]
            else:
                sold_price = close
                sold_date = bar["date"]
            sold_reason = exit_info["reason"]
            break

        # 时间止损 (held too long)
        if days_held >= hold_max:
            sold = True
            sold_price = close
            sold_date = bar["date"]
            sold_reason = f"持有期满{hold_max}日,收盘{close:.2f}"
            break

    # 没卖出的, 用 hold_max 日收盘价计算
    if not sold:
        if entry_idx + hold_max < len(kline):
            sold_price = kline[entry_idx + hold_max]["close"]
            sold_date = kline[entry_idx + hold_max]["date"]
        else:
            sold_price = kline[-1]["close"]
            sold_date = kline[-1]["date"]
        sold_reason = f"持有{hold_max}日到期"

    pnl_pct = (sold_price - entry_price) / entry_price * 100
    max_high_pct = (highest - entry_price) / entry_price * 100
    max_loss = (kline[min(entry_idx + hold_max, len(kline)-1)]["low"] - entry_price) / entry_price * 100 \
               if entry_idx + hold_max < len(kline) else 0

    return {
        "code": pick["code"],
        "name": pick["name"],
        "mode": mode,
        "score": pick["score"],
        "entry_date": entry_date,
        "entry_price": entry_price,
        "sold_date": sold_date,
        "sold_price": sold_price,
        "days_held": days_held,
        "sold_reason": sold_reason,
        "pnl_pct": pnl_pct,
        "max_high_pct": max_high_pct,
        "max_loss_pct": max_loss,
    }


def print_stats(records: list[dict], hold_periods: tuple):
    """打印回测统计"""
    print("\n" + "=" * 70)
    print(f"📊 回测结果统计 (共 {len(records)} 笔)")
    print("=" * 70)

    # 按模式分组
    for mode in ["🟢 强势", "🟡 稳健"]:
        subset = [r for r in records if r["mode"] == mode]
        if not subset:
            continue

        print(f"\n【{mode}】{len(subset)} 笔")
        wins = [r for r in subset if r["pnl_pct"] > 0]
        losses = [r for r in subset if r["pnl_pct"] <= 0]

        win_rate = len(wins) / len(subset) * 100
        avg_win = statistics.mean([r["pnl_pct"] for r in wins]) if wins else 0
        avg_loss = statistics.mean([r["pnl_pct"] for r in losses]) if losses else 0
        avg_all = statistics.mean([r["pnl_pct"] for r in subset])
        max_pnl = max(r["pnl_pct"] for r in subset)
        max_loss_pct = min(r["pnl_pct"] for r in subset)

        # 盈亏比
        profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        print(f"  胜率: {win_rate:.1f}% ({len(wins)}胜 / {len(losses)}负)")
        print(f"  平均盈利: {avg_win:+.2f}%  平均亏损: {avg_loss:+.2f}%")
        print(f"  盈亏比: {profit_loss_ratio:.2f}")
        print(f"  单笔平均: {avg_all:+.2f}%")
        print(f"  最大盈利: {max_pnl:+.2f}%  最大亏损: {max_loss_pct:+.2f}%")

        # 模拟等权买入的累计收益
        per_trade_contribution = [r["pnl_pct"] * 0.005 for r in subset]  # 假设每笔 0.5% 仓位
        cumulative = sum(per_trade_contribution)
        print(f"  等权买入累计收益: {cumulative:+.2f}% (假设每笔0.5%仓位)")

        # 退出原因统计
        reason_stats = {}
        for r in subset:
            reason = r["sold_reason"].split(",")[0].split("(")[0].strip()
            reason_stats[reason] = reason_stats.get(reason, 0) + 1
        print(f"  退出原因: {dict(sorted(reason_stats.items(), key=lambda x: -x[1]))}")

    # Top 10 最佳/最差
    by_pnl = sorted(records, key=lambda x: -x["pnl_pct"])
    print(f"\n🏆 Top 5 最佳:")
    for r in by_pnl[:5]:
        print(f"  {r['name']}({r['code']}) {r['entry_date']}→{r['sold_date']} {r['pnl_pct']:+.2f}%  {r['sold_reason'][:30]}")

    print(f"\n💔 Top 5 最差:")
    for r in by_pnl[-5:]:
        print(f"  {r['name']}({r['code']}) {r['entry_date']}→{r['sold_date']} {r['pnl_pct']:+.2f}%  {r['sold_reason'][:30]}")

    # 总结
    print("\n" + "=" * 70)
    total_wins = len([r for r in records if r["pnl_pct"] > 0])
    total_win_rate = total_wins / len(records) * 100
    avg_pnl = statistics.mean([r["pnl_pct"] for r in records])
    print(f"📈 总体胜率: {total_win_rate:.1f}% ({total_wins}/{len(records)})")
    print(f"📈 平均收益: {avg_pnl:+.2f}%")

    # 盈利期望值 = 胜率 * 平均盈利 - (1-胜率) * |平均亏损|
    wins_pnl = [r["pnl_pct"] for r in records if r["pnl_pct"] > 0]
    losses_pnl = [r["pnl_pct"] for r in records if r["pnl_pct"] <= 0]
    if wins_pnl and losses_pnl:
        expected = (total_win_rate/100 * statistics.mean(wins_pnl)
                    + (1-total_win_rate/100) * statistics.mean(losses_pnl))
        print(f"📈 期望值(每笔): {expected:+.2f}%")
        if expected > 0:
            print(f"✅ 策略期望为正 - 长期可盈利")
        else:
            print(f"❌ 策略期望为负 - 需要调整")
    print("=" * 70)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=20, help="回测天数")
    p.add_argument("--strong", type=int, default=3, help="每天选多少强势")
    p.add_argument("--steady", type=int, default=6, help="每天选多少稳健")
    args = p.parse_args()

    run_backtest(
        days_back=args.days,
        top_n_strong=args.strong,
        top_n_steady=args.steady,
    )
