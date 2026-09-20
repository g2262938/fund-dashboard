"""
position_manager_v8.py — 复利雪球 v8 仓位管理

核心思路 (从 《2020收官!账户破200万，复利雪球开始滚动!》提炼的公开原则):
  1. 复利: 盈利再投入 (锦上添花); 亏损减仓 (雪中送炭)
  2. 雪球: 越赚越大, 但每球都有止盈止损, 不失控
  3. 滚动: 强势留, 弱势踢, 每日重排

对比 v7 关键改动:
  v7                          →  v8 (复利雪球)
  硬止损 -7% (强势)           →  -5%  (更严, 本金保护)
  硬止损 -3% (稳健)           →  -3%  (不变, 已合理)
  移动止盈 5%/10% (强势)      →  7%/12% (让利润奔跑)
  移动止盈 7%/15% (稳健)      →  8%/16% (同上)
  [新] +15% 触发分批止盈       →  +15% 卖 1/3, +30% 卖 1/3, 跟踪止盈剩 1/3
  [新] 动态仓位 (复利)         →  浮盈 >10% 加仓 1.3x, 浮亏 <-3% 减仓 0.7x
  [新] 滚动调仓                →  跌出 top 1/3 即换股
  总仓位上限 100%              →  80% (20% 现金储备, 等机会)
  同时持仓 5 只                →  3 只 (聚焦复利)
"""

POSITION_CONFIG_V8 = {
    "🟢 强势": {
        "weight": 0.25,              # 略降, 留空间给加仓
        "weight_after_profit": 0.30, # 浮盈>10% 加仓到 30%
        "weight_after_loss": 0.18,   # 浮亏<-3% 减仓到 18%
        "hold_days_max": 5,          # 5 日波段
        "stop_loss_pct": 5.0,        # 🔺 从 -7% 收紧到 -5%
        "trailing_sell_half": 7.0,   # 🔺 从 5% 放宽到 7% (让利润奔跑)
        "trailing_sell_all": 12.0,   # 🔺 从 10% 放宽到 12%
        "partial_profit_1": 15.0,    # [新] +15% 卖 1/3
        "partial_profit_2": 30.0,    # [新] +30% 再卖 1/3, 剩 1/3 跟踪
    },
    "🟡 稳健": {
        "weight": 0.15,
        "weight_after_profit": 0.20,
        "weight_after_loss": 0.10,
        "hold_days_max": 20,         # 20 日中线
        "stop_loss_pct": 3.0,        # 不变
        "trailing_sell_half": 8.0,
        "trailing_sell_all": 16.0,   # 🔺 从 15% 放宽到 16%
        "partial_profit_1": 20.0,    # [新] +20% 卖 1/3
        "partial_profit_2": 40.0,    # [新] +40% 再卖 1/3
    },
    "⚪ 观察": {
        "weight": 0.0,
        "hold_days_max": 0,
    },
}

# 大盘风控阈值
MARKET_PANIC_DROP = 2.0
MARKET_DECLINE_DAYS = 3
MARKET_CONSEC_DROP_PCT = 3.0

# 时间止损
TIME_STOP_DAYS = 10

# 🔺 复利雪球全局规则
MAX_POSITIONS = 3                  # 🔻 从 5 只降到 3 只 (聚焦复利)
TOTAL_POSITION_LIMIT = 0.80        # [新] 总仓位上限 80% (留 20% 现金)
SINGLE_POS_LIMIT = 0.35
ROLLING_RANK_THRESHOLD = 0.33      # [新] 跌出 top 1/3 即触发滚动调仓


def calc_position_size_v8(
    mode: str,
    total_capital: float,
    unrealized_pnl_pct: float = 0.0,  # 当前浮盈/亏%
    days_held: int = 0,
) -> float:
    """
    v8 动态仓位计算 — 复利雪球核心
    浮盈 > 10%: 加仓 1.2x (锦上添花)
    浮亏 < -3%: 减仓 0.7x (雪中送炭)
    """
    cfg = POSITION_CONFIG_V8.get(mode)
    if not cfg:
        return 0

    # 基础仓位
    weight = cfg["weight"]

    # 复利动态调整
    if unrealized_pnl_pct >= 10.0 and days_held >= 2:
        weight = cfg.get("weight_after_profit", weight * 1.2)
    elif unrealized_pnl_pct <= -3.0:
        weight = cfg.get("weight_after_loss", weight * 0.7)

    # 单只上限
    weight = min(weight, SINGLE_POS_LIMIT)

    return total_capital * weight


def check_exit_signal_v8(
    entry_price: float,
    current_price: float,
    highest_price: float,
    days_held: int,
    current_score: int,
    current_rank_pct: float = 0.0,  # [新] 当前在评分池中的排名分位 (0~1, 越小越强)
    sold_partial: int = 0,          # [新] 已分批卖出的次数 (0/1/2)
    market_drop_today: float = 0.0,
) -> dict:
    """
    v8 卖出信号检测 — 复利雪球版
    优先级:
      1. 大盘风控 (紧急)
      2. 硬止损 (-5% 强势 / -3% 稳健, 比 v7 更严)
      3. 分批止盈 (+15%/+30% 强势, +20%/+40% 稳健)
      4. 移动止盈 (回撤 7%/12% 强势, 8%/16% 稳健)
      5. 滚动调仓 (跌出 top 1/3)
      6. 时间止损 (10 日仍未启动)
      7. 信号止盈 (打分跌落 <65)
    """
    pnl_pct = (current_price - entry_price) / entry_price * 100
    trailing_pct = (highest_price - current_price) / highest_price * 100 if highest_price > 0 else 0

    # 确定模式 (硬止损阈值需要模式)
    # 注: 这里简化用 score 推断模式
    if current_score >= 88:
        mode = "🟢 强势"
        stop_loss = 5.0
        trailing_half = 7.0
        trailing_all = 12.0
        profit_1, profit_2 = 15.0, 30.0
        sell_pct_1, sell_pct_2 = 0.33, 0.33
    else:
        mode = "🟡 稳健"
        stop_loss = 3.0
        trailing_half = 8.0
        trailing_all = 16.0
        profit_1, profit_2 = 20.0, 40.0
        sell_pct_1, sell_pct_2 = 0.33, 0.33

    # 1. 大盘风控
    if market_drop_today >= MARKET_PANIC_DROP:
        return {
            "action": "sell_all",
            "reason": f"🆘大盘暴跌(-{market_drop_today:.1f}%) 风控清仓",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }

    # 2. 硬止损 (v8 收严: -5% / -3%)
    if pnl_pct <= -stop_loss:
        return {
            "action": "sell_all",
            "reason": f"硬止损({mode}):亏损{pnl_pct:.1f}%≤-{stop_loss}%",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }

    # 3. 分批止盈 (复利雪球核心)
    if pnl_pct >= profit_2 and sold_partial < 2:
        return {
            "action": "sell_partial",
            "pct": sell_pct_2,  # 卖 1/3
            "step": 2,
            "reason": f"分批止盈②:已赚{pnl_pct:.1f}%≥+{profit_2}%,卖1/3保留底仓跟踪",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }
    if pnl_pct >= profit_1 and sold_partial < 1:
        return {
            "action": "sell_partial",
            "pct": sell_pct_1,
            "step": 1,
            "reason": f"分批止盈①:已赚{pnl_pct:.1f}%≥+{profit_1}%,先卖1/3锁利",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }

    # 4. 移动止盈 (回撤触发)
    if trailing_pct >= trailing_all:
        return {
            "action": "sell_all",
            "reason": f"移动止盈全卖({mode}):从最高{highest_price:.2f}回撤{trailing_pct:.1f}%≥{trailing_all}%",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }
    if trailing_pct >= trailing_half and pnl_pct > 2.0 and sold_partial == 0:
        return {
            "action": "sell_partial",
            "pct": 0.5,
            "step": "half",
            "reason": f"移动止盈卖一半({mode}):回撤{trailing_pct:.1f}%≥{trailing_half}%,赚{pnl_pct:.1f}%",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }

    # 5. 滚动调仓 (跌出 top 1/3)
    if current_rank_pct > ROLLING_RANK_THRESHOLD and days_held >= 3:
        return {
            "action": "sell_all",
            "reason": f"滚动调仓({mode}):排名跌至后{current_rank_pct*100:.0f}%分位,换强势股",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }

    # 6. 时间止损
    if days_held >= TIME_STOP_DAYS and pnl_pct < 2.0:
        return {
            "action": "sell_all",
            "reason": f"时间止损({mode}):持{days_held}日仅赚{pnl_pct:.1f}%<2%",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }

    # 7. 信号止盈 (打分跌落)
    if current_score < 65 and days_held >= 3:
        return {
            "action": "sell_all",
            "reason": f"信号止盈({mode}):打分跌至{current_score}<65,基本面变差",
            "pnl_pct": pnl_pct,
            "mode": mode,
        }

    return {
        "action": "hold",
        "reason": "持仓中",
        "pnl_pct": pnl_pct,
        "trailing_pct": trailing_pct,
        "mode": mode,
    }


# ── 复利雪球回测核心 ──────────────────────────────────────

def simulate_compound_snowball(
    picks_by_day: list[list[dict]],
    initial_capital: float = 1_000_000.0,
    daily_index: list[dict] = None,
    rebalance_freq: int = 5,
) -> dict:
    """
    复利雪球回测模拟器

    Args:
        picks_by_day: 每日选股列表 [[{code, name, mode, score, kline_full, entry_idx}, ...], ...]
        initial_capital: 起始资金
        daily_index: 每日大盘指数 [{date, close, change_pct}], 用于大盘风控
        rebalance_freq: 调仓频率 (每 5 个交易日重新评估持仓)

    Returns:
        {
          "trades": [...],
          "final_capital": float,
          "total_return_pct": float,
          "max_drawdown_pct": float,
          "win_rate": float,
          "compound_multiple": float,  # 复利倍数
        }
    """
    capital = initial_capital
    cash = initial_capital  # 现金储备
    positions = {}  # {code: {shares, entry_price, entry_date, mode, score, highest, days_held, sold_partial}}
    trades = []
    equity_curve = [initial_capital]

    for day_idx, daily_picks in enumerate(picks_by_day):
        if not daily_picks:
            continue

        # ── 1. 大盘风控 ──
        market_drop = daily_index[day_idx].get("change_pct", 0) if daily_index and day_idx < len(daily_index) else 0

        # ── 2. 评估现有持仓 (检查卖出信号) ──
        codes_to_remove = []
        for code, pos in positions.items():
            # 找当天的价格 (用 kline[entry_idx + days_held])
            kl = pos.get("kline_full")
            cur_idx = pos.get("entry_idx", 0) + pos["days_held"] + 1
            if cur_idx >= len(kl):
                codes_to_remove.append(code)
                continue

            cur_price = kl[cur_idx]["close"]
            cur_high = max(pos["highest"], kl[cur_idx]["high"])
            pos["highest"] = cur_high
            pos["days_held"] += 1

            # 排名分位 (这里简化: 用当前打分, 后面回测中重新打分)
            rank_pct = pos.get("rank_pct", 0.5)
            score = pos.get("current_score", 75)

            sig = check_exit_signal_v8(
                entry_price=pos["entry_price"],
                current_price=cur_price,
                highest_price=cur_high,
                days_held=pos["days_held"],
                current_score=score,
                current_rank_pct=rank_pct,
                sold_partial=pos.get("sold_partial", 0),
                market_drop_today=-market_drop if market_drop > 0 else 0,
            )

            if sig["action"] == "sell_all":
                proceeds = pos["shares"] * cur_price * 0.999  # 千一手续费
                pnl = proceeds - pos["shares"] * pos["entry_price"]
                cash += proceeds
                trades.append({
                    "code": code, "name": pos["name"], "mode": pos["mode"],
                    "entry_date": pos["entry_date"], "exit_date": kl[cur_idx]["date"],
                    "entry_price": pos["entry_price"], "exit_price": cur_price,
                    "shares": pos["shares"], "pnl": pnl, "pnl_pct": sig["pnl_pct"],
                    "reason": sig["reason"], "days_held": pos["days_held"],
                })
                codes_to_remove.append(code)

            elif sig["action"] == "sell_partial":
                sell_shares = int(pos["shares"] * sig["pct"])
                if sell_shares < 100:
                    sell_shares = 100  # A 股最小 100 股
                sell_shares = min(sell_shares, pos["shares"])
                proceeds = sell_shares * cur_price * 0.999
                pnl = proceeds - sell_shares * pos["entry_price"]
                cash += proceeds
                pos["shares"] -= sell_shares
                pos["sold_partial"] = pos.get("sold_partial", 0) + 1
                trades.append({
                    "code": code, "name": pos["name"], "mode": pos["mode"],
                    "entry_date": pos["entry_date"], "exit_date": kl[cur_idx]["date"],
                    "entry_price": pos["entry_price"], "exit_price": cur_price,
                    "shares": sell_shares, "pnl": pnl, "pnl_pct": sig["pnl_pct"],
                    "reason": sig["reason"], "days_held": pos["days_held"],
                    "is_partial": True,
                })

        for c in codes_to_remove:
            positions.pop(c, None)

        # ── 3. 滚动调仓: 每 rebalance_freq 天评估一次, 跌出 top 1/3 即卖 ──
        if day_idx > 0 and day_idx % rebalance_freq == 0:
            # 当前持仓按打分排名
            my_codes = list(positions.keys())
            if len(my_codes) > 1:
                # 跌出 top 1/3 的卖掉
                n = len(my_codes)
                cutoff = max(1, int(n * 0.34))  # 保留 top 66%
                # 按 score 排序
                sorted_codes = sorted(my_codes, key=lambda c: -positions[c].get("current_score", 0))
                for code in sorted_codes[cutoff:]:
                    pos = positions[code]
                    kl = pos["kline_full"]
                    cur_idx = pos["entry_idx"] + pos["days_held"] + 1
                    if cur_idx < len(kl):
                        cur_price = kl[cur_idx]["close"]
                        proceeds = pos["shares"] * cur_price * 0.999
                        pnl = proceeds - pos["shares"] * pos["entry_price"]
                        cash += proceeds
                        trades.append({
                            "code": code, "name": pos["name"], "mode": pos["mode"],
                            "entry_date": pos["entry_date"], "exit_date": kl[cur_idx]["date"],
                            "entry_price": pos["entry_price"], "exit_price": cur_price,
                            "shares": pos["shares"], "pnl": pnl, "pnl_pct": (cur_price - pos["entry_price"]) / pos["entry_price"] * 100,
                            "reason": f"📊 滚动调仓: 跌出 top 1/3, 换强势股",
                            "days_held": pos["days_held"],
                            "is_rolling": True,
                        })
                        positions.pop(code, None)

        # ── 4. 开新仓 (有 cash + 总仓位 < 80%) ──
        current_position_value = sum(p["shares"] * p.get("last_price", p["entry_price"]) for p in positions.values())
        total_value = cash + current_position_value
        if total_value > 0:
            position_ratio = current_position_value / total_value
        else:
            position_ratio = 0

        if position_ratio < TOTAL_POSITION_LIMIT and len(positions) < MAX_POSITIONS:
            available_cash = cash * 0.8  # 每次只用 80% 现金
            for pick in daily_picks:
                if pick["code"] in positions:
                    continue
                if cash < 10000:  # 现金不足
                    break

                # 计算仓位
                size = calc_position_size_v8(
                    pick["mode"], total_value,
                    unrealized_pnl_pct=0.0,  # 新开仓
                    days_held=0,
                )
                if size <= 0:
                    continue
                size = min(size, cash * 0.5)  # 单只不超过现金 50%

                entry_price = pick["entry_price"]
                shares = int(size / entry_price / 100) * 100  # 100 股一手
                if shares < 100:
                    continue
                cost = shares * entry_price * 1.001  # 千一手续费
                if cost > cash:
                    shares = int(cash / entry_price / 100) * 100
                    cost = shares * entry_price * 1.001
                if shares < 100 or cost > cash:
                    continue

                cash -= cost
                positions[pick["code"]] = {
                    "name": pick["name"],
                    "shares": shares,
                    "entry_price": entry_price,
                    "entry_date": pick.get("entry_date", ""),
                    "mode": pick["mode"],
                    "score": pick["score"],
                    "current_score": pick["score"],
                    "highest": entry_price,
                    "days_held": 0,
                    "sold_partial": 0,
                    "rank_pct": 0.0,  # 新开仓 rank 设为 0 (最强)
                    "kline_full": pick["kline_full"],
                    "entry_idx": pick["entry_idx_in_kline"],
                    "last_price": entry_price,
                }

        # ── 5. 更新持仓最新价 + equity curve ──
        total_value = cash
        for code, pos in positions.items():
            kl = pos["kline_full"]
            cur_idx = pos["entry_idx"] + pos["days_held"] + 1
            if cur_idx < len(kl):
                pos["last_price"] = kl[cur_idx]["close"]
            total_value += pos["shares"] * pos["last_price"]

        equity_curve.append(total_value)

    # ── 6. 平仓所有持仓 ──
    for code, pos in list(positions.items()):
        kl = pos["kline_full"]
        cur_idx = pos["entry_idx"] + pos["days_held"] + 1
        cur_price = kl[min(cur_idx, len(kl) - 1)]["close"]
        proceeds = pos["shares"] * cur_price * 0.999
        pnl = proceeds - pos["shares"] * pos["entry_price"]
        cash += proceeds
        trades.append({
            "code": code, "name": pos["name"], "mode": pos["mode"],
            "entry_date": pos["entry_date"], "exit_date": kl[-1]["date"],
            "entry_price": pos["entry_price"], "exit_price": cur_price,
            "shares": pos["shares"], "pnl": pnl,
            "pnl_pct": (cur_price - pos["entry_price"]) / pos["entry_price"] * 100,
            "reason": "回测结束强制平仓",
            "days_held": pos["days_held"],
        })

    # ── 7. 统计 ──
    final_capital = cash
    total_return_pct = (final_capital - initial_capital) / initial_capital * 100
    compound_multiple = final_capital / initial_capital

    # 最大回撤
    peak = equity_curve[0]
    max_dd = 0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # 胜率
    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        win_rate = len(wins) / len(trades) * 100
    else:
        win_rate = 0

    return {
        "trades": trades,
        "final_capital": final_capital,
        "total_return_pct": total_return_pct,
        "compound_multiple": compound_multiple,
        "max_drawdown_pct": max_dd,
        "win_rate": win_rate,
        "equity_curve": equity_curve,
        "n_trades": len(trades),
    }


if __name__ == "__main__":
    print("=" * 60)
    print("📊 复利雪球 v8 — 仓位管理规则")
    print("=" * 60)
    print(f"🟢 强势: 25% 仓位 → 浮盈>10% 加到 30% / 浮亏<-3% 减到 18%")
    print(f"       5日波段, -5%硬止损, +15%/+30%分批, 跟踪止盈 7%/12%")
    print(f"🟡 稳健: 15% 仓位 → 浮盈>10% 加到 20% / 浮亏<-3% 减到 10%")
    print(f"       20日中线, -3%硬止损, +20%/+40%分批, 跟踪止盈 8%/16%")
    print(f"⚪ 观察: 不买入")
    print()
    print(f"📌 复利雪球全局:")
    print(f"   - 同时持仓 ≤ {MAX_POSITIONS} 只 (聚焦)")
    print(f"   - 总仓位 ≤ {TOTAL_POSITION_LIMIT*100:.0f}% ({1-TOTAL_POSITION_LIMIT:.0%} 现金储备)")
    print(f"   - 单只 ≤ {SINGLE_POS_LIMIT*100:.0f}%")
    print(f"   - 每 {5} 个交易日滚动调仓 (跌出 top 1/3 即换股)")
    print()
    print("对比 v7:")
    print("  硬止损:  -7% → -5% (本金保护更严)")
    print("  移动止盈: 5%/10% → 7%/12% (让利润奔跑)")
    print("  [新] 分批止盈: +15%/+30% 卖 1/3 + 1/3 + 跟踪 1/3")
    print("  [新] 动态仓位: 浮盈加仓 / 浮亏减仓 (复利)")
    print("  [新] 滚动调仓: 跌出 top 1/3 即换")
    print("  持仓数:  5 只 → 3 只 (聚焦)")