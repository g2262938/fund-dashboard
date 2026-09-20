"""
backtest_v8_compound.py — 复利雪球 v8 vs v7 对比回测

回测逻辑:
  1. 用 simple_picker v4.1 评分, 每日选 🟢≥88 / 🟡≥75
  2. 两种策略并跑同一选股池:
     - v7: 现行规则 (硬止损 -7%, 移动止盈 5/10%, 5 只持仓)
     - v8: 复利雪球 (硬止损 -5%, 分批止盈 +15/+30, 动态仓位, 3 只滚动)
  3. 输出对比: 总收益, 复利倍数, 最大回撤, 胜率, 盈亏比

数据源: 腾讯 K线 (本地缓存) + akshare 股票池
"""
import sys, os, json, time, urllib.request, datetime, statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/scripts')
from simple_picker import (
    score_value, score_momentum, score_quality, score_volume_price,
    classify, hard_filter, pass_form_filter, calc_pe_change,
    _infer_industry,
)
from position_manager import check_exit_signal as v7_check
from position_manager_v8 import (
    check_exit_signal_v8,
    simulate_compound_snowball,
    POSITION_CONFIG_V8,
)

CACHE_DIR = Path("/home/ubuntu/.openclaw/workspace/data/kline_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── 数据准备 (复用 v7 backtest 逻辑) ──────────────────────

def fetch_kline_history(code: str, days: int = 60) -> list[dict]:
    """优先用本地缓存"""
    cache_file = Path(f"/home/ubuntu/.openclaw/workspace/data/kline_cache/{code}.json")
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                d = json.load(f)
                kl = d.get("kline", [])
                # 缓存格式: list of {date, open, close, high, low, volume}
                out = []
                for r in kl:
                    if all(k in r for k in ("date","open","close","high","low","volume")):
                        out.append(r)
                return out[-days:] if len(out) >= days else out
        except:
            pass
    # fallback: 网络 (沙箱可能拦)
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
    """从本地 K线缓存读取股票池 (不依赖 akshare)"""
    cache_dir = Path("/home/ubuntu/.openclaw/workspace/data/kline_cache")
    records = []
    for f in cache_dir.glob("*.json"):
        code = f.stem
        if code.startswith(("4", "8")):
            continue
        # 没有名字就用代码代替
        try:
            with open(f) as fh:
                d = json.load(fh)
                if d.get("kline") and len(d["kline"]) >= 30:
                    records.append({"code": code, "name": code})
        except:
            continue
    return records


def get_index_history(days: int = 80) -> list[dict]:
    """上证指数 K线, 优先本地缓存, 否则用任一股票作为代理"""
    for fname in ["000001.json", "sh000001.json"]:
        cache_file = Path(f"/home/ubuntu/.openclaw/workspace/data/kline_cache/{fname}")
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    d = json.load(f)
                    kl = d.get("kline", [])[-days:]
                    prev_close = None
                    out = []
                    for r in kl:
                        close = r["close"]
                        change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0
                        out.append({"date": r["date"], "close": close, "change_pct": change_pct})
                        prev_close = close
                    return out
            except:
                pass
    cache_dir = Path("/home/ubuntu/.openclaw/workspace/data/kline_cache")
    sample_files = list(cache_dir.glob("*.json"))
    if sample_files:
        with open(sample_files[0]) as f:
            d = json.load(f)
            kl = d.get("kline", [])[-days:]
            prev_close = None
            out = []
            for r in kl:
                close = r["close"]
                change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0
                out.append({"date": r["date"], "close": close, "change_pct": change_pct})
                prev_close = close
            print(f"  ⚠️ 用 {sample_files[0].stem} 作为时间轴代理")
            return out
    return []

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        data = json.loads(raw)
        raw_list = data.get("data", {}).get("sh000001", {}).get("qfqday", [])
        kline = []
        prev_close = None
        for row in raw_list:
            if len(row) >= 3:
                close = float(row[2])
                change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0
                kline.append({"date": row[0], "close": close, "change_pct": change_pct})
                prev_close = close
        return kline
    except Exception:
        return []


def build_quote_from_kline(kline: list[dict], code: str, name: str) -> dict:
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
        "turnover": 1.5,
        "pe": 20.0,
        "market_cap": 500,
        "industry": _infer_industry(code, name),
    }


# ── v7 简单回测 (用于对比) ────────────────────────────────

def backtest_v7(picks_by_day, initial_capital=1_000_000):
    """v7 现有策略: 每日等权买, 单笔独立算盈亏 (不真正复利)"""
    capital = initial_capital
    cash = initial_capital
    positions = {}
    equity_curve = [initial_capital]
    trades = []

    for day_idx, daily_picks in enumerate(picks_by_day):
        # 评估持仓
        codes_to_remove = []
        for code, pos in positions.items():
            kl = pos["kline_full"]
            cur_idx = pos["entry_idx"] + pos["days_held"] + 1
            if cur_idx >= len(kl):
                codes_to_remove.append(code)
                continue

            cur_price = kl[cur_idx]["close"]
            cur_high = max(pos["highest"], kl[cur_idx]["high"])
            pos["highest"] = cur_high
            pos["days_held"] += 1

            # v7 规则
            market_drop = -2.0 if pos.get("market_panic") else 0
            sig = v7_check(
                entry_price=pos["entry_price"],
                current_price=cur_price,
                highest_price=cur_high,
                days_held=pos["days_held"],
                current_score=pos["score"],
                market_drop_today=market_drop,
            )

            if sig["action"] in ("sell_all", "sell_half"):
                sell_pct = 1.0 if sig["action"] == "sell_all" else 0.5
                sell_shares = int(pos["shares"] * sell_pct / 100) * 100
                if sell_shares < 100:
                    sell_shares = pos["shares"]
                sell_shares = min(sell_shares, pos["shares"])
                proceeds = sell_shares * cur_price * 0.999
                pnl = proceeds - sell_shares * pos["entry_price"]
                cash += proceeds
                pos["shares"] -= sell_shares
                trades.append({
                    "code": code, "mode": pos["mode"],
                    "pnl": pnl, "pnl_pct": sig["pnl_pct"],
                    "reason": sig["reason"],
                })
                if sig["action"] == "sell_all" or pos["shares"] < 100:
                    codes_to_remove.append(code)

        for c in codes_to_remove:
            positions.pop(c, None)

        # 开新仓 (v7: 5 只持仓, 等权)
        max_pos = 5
        if len(positions) < max_pos:
            for pick in daily_picks[:max_pos - len(positions)]:
                if pick["code"] in positions or cash < 10000:
                    continue
                entry_price = pick["entry_price"]
                size_per_pos = (initial_capital * 0.95) / max_pos  # 95% 资金等权
                shares = int(size_per_pos / entry_price / 100) * 100
                if shares < 100:
                    continue
                cost = shares * entry_price * 1.001
                if cost > cash:
                    continue
                cash -= cost
                positions[pick["code"]] = {
                    "name": pick["name"],
                    "shares": shares,
                    "entry_price": entry_price,
                    "mode": pick["mode"],
                    "score": pick["score"],
                    "highest": entry_price,
                    "days_held": 0,
                    "kline_full": pick["kline_full"],
                    "entry_idx": pick["entry_idx_in_kline"],
                }

        # equity
        total_value = cash + sum(p["shares"] * p["kline_full"][min(p["entry_idx"] + p["days_held"] + 1, len(p["kline_full"])-1)]["close"] for p in positions.values())
        equity_curve.append(total_value)

    # 平仓
    for code, pos in list(positions.items()):
        kl = pos["kline_full"]
        cur_idx = pos["entry_idx"] + pos["days_held"] + 1
        cur_price = kl[min(cur_idx, len(kl)-1)]["close"]
        proceeds = pos["shares"] * cur_price * 0.999
        cash += proceeds
        trades.append({"code": code, "pnl": proceeds - pos["shares"] * pos["entry_price"], "reason": "期末"})

    final = cash
    peak = equity_curve[0]
    max_dd = 0
    for v in equity_curve:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "final_capital": final,
        "total_return_pct": (final - initial_capital) / initial_capital * 100,
        "compound_multiple": final / initial_capital,
        "max_drawdown_pct": max_dd,
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "n_trades": len(trades),
        "equity_curve": equity_curve,
    }


# ── 主回测 ─────────────────────────────────────────────

def run_compare(days_back: int = 30, total_capital: float = 1_000_000.0):
    print("=" * 70)
    print(f"📊 复利雪球 v8 vs v7 对比回测 (近 {days_back} 个交易日)")
    print("=" * 70)

    universe = get_universe()
    print(f"股票池: {len(universe)} 只")

    # 拉 K线
    print("拉取 K线...")
    all_klines = {}
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(fetch_kline_history, u["code"], 80): u["code"] for u in universe}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                k = fut.result()
                if k and len(k) >= 30:
                    all_klines[code] = k
            except:
                pass
    print(f"K线覆盖: {len(all_klines)} 只")

    index_kline = get_index_history(80)
    if not index_kline:
        print("❌ 无指数数据")
        return

    code_name = {u["code"]: u["name"] for u in universe}

    # 每日选股
    total_days = len(index_kline)
    entry_indices = list(range(10, total_days - 15))  # 数据仅31天, 缩小窗口
    print(f"回测样本: {len(entry_indices)} 个入场日 (31天数据)")

    picks_by_day = []  # [[{code, name, mode, score, kline_full, entry_idx, entry_price, entry_date}, ...]]
    daily_index_returns = []  # [change_pct, ...]

    for entry_idx in entry_indices:
        entry_date = index_kline[entry_idx]["date"]
        if entry_idx > 0:
            today_idx = (index_kline[entry_idx]["close"] - index_kline[entry_idx-1]["close"]) / index_kline[entry_idx-1]["close"] * 100
        else:
            today_idx = 0
        daily_index_returns.append(today_idx)

        picks = []
        scores_by_code = []
        for code, kline in all_klines.items():
            if len(kline) <= entry_idx + 1:
                continue
            history = kline[:entry_idx + 1]
            if len(history) < 20:
                continue
            quote = build_quote_from_kline(history, code, code_name.get(code, code))
            ok, _ = hard_filter(quote)
            if not ok:
                continue
            if not pass_form_filter(quote, history, today_idx):
                continue
            pe_chg = calc_pe_change(history)
            v = score_value(quote, pe_chg)
            m = score_momentum(quote, history, today_idx)
            q = score_quality(quote)
            vp = score_volume_price(quote, history)
            total = v + m + q + vp

            if total < 75:
                continue
            mode_tag, _ = classify(total)

            if entry_idx + 1 >= len(kline):
                continue
            entry_price = kline[entry_idx + 1]["open"]

            scores_by_code.append((code, total))
            picks.append({
                "code": code, "name": code_name.get(code, code),
                "mode": mode_tag, "score": total,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "kline_full": kline,
                "entry_idx_in_kline": entry_idx + 1,
            })

        # 排序, 选 top 8 (给 v7 5 只 + v8 3 只都用)
        picks.sort(key=lambda x: -x["score"])

        # 计算 rank_pct (用于 v8 滚动调仓)
        total_in_day = len(picks)
        for i, p in enumerate(picks):
            p["rank_pct"] = i / total_in_day if total_in_day > 0 else 0.5

        # 每天最多 8 只入选 (够两个策略用)
        picks_by_day.append(picks[:8])

    print(f"\n每日平均选股: {sum(len(p) for p in picks_by_day) / len(picks_by_day):.1f} 只")

    # ── 跑 v7 ──
    print("\n" + "─" * 70)
    print("🅰️  v7 (现行) 回测...")
    v7_result = backtest_v7(picks_by_day, initial_capital=total_capital)

    # ── 跑 v8 (复利雪球) ──
    print("🅱️  v8 (复利雪球) 回测...")
    daily_index_list = [{"change_pct": r} for r in daily_index_returns]
    v8_result = simulate_compound_snowball(
        picks_by_day,
        initial_capital=total_capital,
        daily_index=daily_index_list,
        rebalance_freq=5,
    )

    # ── 输出对比 ──
    print("\n" + "=" * 70)
    print("📊 对比结果")
    print("=" * 70)

    rows = [
        ("总收益", f"{v7_result['total_return_pct']:.2f}%", f"{v8_result['total_return_pct']:.2f}%"),
        ("最终资金", f"¥{v7_result['final_capital']:,.0f}", f"¥{v8_result['final_capital']:,.0f}"),
        ("复利倍数", f"{v7_result['compound_multiple']:.3f}x", f"{v8_result['compound_multiple']:.3f}x"),
        ("最大回撤", f"{v7_result['max_drawdown_pct']:.2f}%", f"{v8_result['max_drawdown_pct']:.2f}%"),
        ("胜率", f"{v7_result['win_rate']:.1f}%", f"{v8_result['win_rate']:.1f}%"),
        ("交易次数", f"{v7_result['n_trades']}", f"{v8_result['n_trades']}"),
    ]

    print(f"{'指标':<14}{'v7 (现行)':<25}{'v8 (复利雪球)':<25}{'差异':<15}")
    print("─" * 79)
    for name, v7, v8 in rows:
        diff = ""
        if "%" in v7 and "%" in v8:
            try:
                v7n = float(v7.replace("%", ""))
                v8n = float(v8.replace("%", ""))
                d = v8n - v7n
                diff = f"{'+' if d > 0 else ''}{d:.2f}%"
            except:
                pass
        print(f"{name:<14}{v7:<25}{v8:<25}{diff:<15}")

    # 结论
    print("\n" + "─" * 70)
    if v8_result["compound_multiple"] > v7_result["compound_multiple"]:
        ratio = v8_result["compound_multiple"] / v7_result["compound_multiple"]
        print(f"✅ v8 复利雪球胜出: 复利倍数 {ratio:.2f}x v7")
    else:
        print(f"⚠️  v7 略胜: 需进一步调参")

    if v8_result["max_drawdown_pct"] < v7_result["max_drawdown_pct"]:
        print(f"✅ v8 回撤更小: -{v8_result['max_drawdown_pct']:.1f}% vs v7 -{v7_result['max_drawdown_pct']:.1f}%")
    else:
        print(f"⚠️  v8 回撤更大: -{v8_result['max_drawdown_pct']:.1f}% vs v7 -{v7_result['max_drawdown_pct']:.1f}%")

    # 保存
    out = {
        "run_date": datetime.date.today().isoformat(),
        "v7": v7_result, "v8": v8_result,
        "config_v8": {k: v for k, v in POSITION_CONFIG_V8.items()},
    }
    out_path = Path("/home/ubuntu/.openclaw/workspace/reports/backtest_v8_vs_v7.json")
    # 不能序列化 kline_full, 移除
    out["v7"].pop("equity_curve", None)
    out["v8"].pop("equity_curve", None)
    out["v8"].pop("trades", None)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n📁 结果保存到: {out_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--capital", type=float, default=1_000_000)
    args = parser.parse_args()
    run_compare(days_back=args.days, total_capital=args.capital)