#!/usr/bin/env python3
"""
选股 → 复盘 → 因子分析 → 策略改进 闭环系统
=============================================
每天收盘后运行:
  1. 自动拉取推荐股的今日行情（开盘/收盘/最高/最低）
  2. 计算每个因子的预测效果（量比/形态/PE/动量/行业）
  3. 输出改进建议，写入策略调整日志
  4. 追踪昨日推荐 → 今日表现，自动更新复盘报告
"""
import json, re, datetime, sys
from pathlib import Path
from collections import defaultdict

REPORTS_DIR = Path("/home/ubuntu/.openclaw/workspace/reports")
PICK_DIR = Path("/home/ubuntu/.openclaw/workspace/每日选股")
SCRIPTS_DIR = Path("/home/ubuntu/.openclaw/workspace/scripts")
LOG_FILE = REPORTS_DIR / "因子效果日志.json"
STRATEGY_CHANGELOG = REPORTS_DIR / "策略改进日志.md"


def load_trade_history() -> list[dict]:
    """加载历史交易（含入场/出场/因子快照）"""
    f = REPORTS_DIR / "backtest_trades.json"
    if f.exists():
        with open(f) as fp:
            return json.load(fp)
    return []


def parse_recommended_stocks(date: str) -> list[dict]:
    """解析某日推荐文件，提取股票列表"""
    f = PICK_DIR / f"推荐-{date}.md"
    if not f.exists():
        return []
    stocks = []
    for line in f.read_text().split("\n"):
        if line.startswith("|") and "名称" not in line and "---" not in line:
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) >= 5:
                stocks.append({
                    "name": parts[0],
                    "code": parts[1],
                    "score": parts[2],
                    "mode": parts[3],
                    "industry": parts[4] if len(parts) > 4 else "",
                })
    return stocks


def fetch_today_closes(codes: list[str]) -> dict[str, dict]:
    """用腾讯接口拉今日行情（含昨收/今收/最高/最低）"""
    if not codes:
        return {}
    qt_codes = []
    for c in codes:
        c = c.strip().zfill(6)
        if c.startswith(("6", "9")):
            qt_codes.append(f"sh{c}")
        else:
            qt_codes.append(f"sz{c}")

    import urllib.request
    url = f"https://qt.gtimg.cn/q={','.join(qt_codes)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"  ⚠️ 行情拉取失败: {e}")
        return {}

    result = {}
    for c in codes:
        c = c.strip().zfill(6)
        prefix = f"sh{c}" if c.startswith(("6", "9")) else f"sz{c}"
        m = re.search(rf'v_{prefix}="([^"]+)"', raw)
        if m:
            flds = m.group(1).split("~")
            if len(flds) >= 35 and flds[3]:
                try:
                    result[c] = {
                        "name": flds[1] if len(flds) > 1 else c,
                        "prev_close": float(flds[4]) if flds[4] else 0,
                        "open": float(flds[5]) if flds[5] else 0,
                        "current": float(flds[3]),
                        "high": float(flds[33]) if flds[33] else 0,
                        "low": float(flds[34]) if flds[34] else 0,
                        "volume_ratio": float(flds[49]) if len(flds) > 49 and flds[49] else 0,
                        "turnover": float(flds[38]) if len(flds) > 38 and flds[38] else 0,
                    }
                except:
                    pass
    return result


def compute_factor_analysis(rec_stocks: list[dict], today_data: dict[str, dict],
                            prev_data: dict[str, dict] | None = None) -> dict:
    """
    分析每个因子对今日收益的预测能力
    因子列表: 量比, 形态分, PE, 换手率, 评分, 行业, 模式
    """
    records = []
    for s in rec_stocks:
        code = s["code"].zfill(6)
        td = today_data.get(code, {})
        pd = prev_data.get(code, {}) if prev_data else {}
        if not td or not td.get("current"):
            continue

        # 涨跌
        if td.get("prev_close"):
            chg_pct = (td["current"] - td["prev_close"]) / td["prev_close"] * 100
        else:
            chg_pct = 0

        # 开盘到收盘
        if td.get("open") and td.get("prev_close"):
            open_chg = (td["open"] - td["prev_close"]) / td["prev_close"] * 100
        else:
            open_chg = 0

        # 日内振幅
        if td.get("high") and td.get("low"):
            amplitude = (td["high"] - td["low"]) / td["prev_close"] * 100 if td.get("prev_close") else 0
        else:
            amplitude = 0

        records.append({
            "code": code,
            "name": s["name"],
            "mode": s["mode"],
            "industry": s["industry"],
            "score": s.get("score", ""),
            "open_chg": round(open_chg, 2),
            "close_chg": round(chg_pct, 2),
            "high": td.get("high", 0),
            "low": td.get("low", 0),
            "amplitude": round(amplitude, 2),
            "volume_ratio": td.get("volume_ratio", 0),
            "turnover": td.get("turnover", 0),
        })

    if not records:
        return {}

    # ── 因子效果统计 ──────────────────────────────────
    def avg(lst): return sum(lst) / len(lst) if lst else 0
    def win_rate(lst): return len([x for x in lst if x > 0]) / len(lst) * 100 if lst else 0

    # 按模式
    by_mode = defaultdict(list)
    for r in records:
        by_mode[r["mode"]].append(r["close_chg"])

    # 按行业
    by_ind = defaultdict(list)
    for r in records:
        by_ind[r["industry"]].append(r["close_chg"])

    # 按量比分组 (≤1 / 1-2 / >2)
    by_vol = {"≤1": [], "1-2": [], ">2": []}
    for r in records:
        vr = r["volume_ratio"]
        if vr <= 1:
            by_vol["≤1"].append(r["close_chg"])
        elif vr <= 2:
            by_vol["1-2"].append(r["close_chg"])
        else:
            by_vol[">2"].append(r["close_chg"])

    # 按换手率分组 (<1% / 1-3% / >3%)
    by_turn = {"<1%": [], "1-3%": [], ">3%": []}
    for r in records:
        t = r["turnover"]
        if t < 1:
            by_turn["<1%"].append(r["close_chg"])
        elif t <= 3:
            by_turn["1-3%"].append(r["close_chg"])
        else:
            by_turn[">3%"].append(r["close_chg"])

    # 按振幅分组
    by_amp = {"低振幅(<3%)": [], "中振幅(3-6%)": [], "高振幅(>6%)": []}
    for r in records:
        a = r["amplitude"]
        if a < 3:
            by_amp["低振幅(<3%)"].append(r["close_chg"])
        elif a <= 6:
            by_amp["中振幅(3-6%)"].append(r["close_chg"])
        else:
            by_amp["高振幅(>6%)"].append(r["close_chg"])

    # 开盘强弱（竞价涨幅 vs 收盘涨幅）
    # 竞价涨幅 > 收盘涨幅 → 高开低走预警
    hk_warning = [r for r in records if r["open_chg"] > 2 and r["close_chg"] < r["open_chg"] - 1]
    good竞价 = [r for r in records if r["open_chg"] > 0 and r["close_chg"] > r["open_chg"]]
    bad竞价 = [r for r in records if r["open_chg"] > 0 and r["close_chg"] < 0]

    factor_report = {
        "当日统计": {
            "总股数": len(records),
            "上涨家数": len([r for r in records if r["close_chg"] > 0]),
            "下跌家数": len([r for r in records if r["close_chg"] < 0]),
            "平均收益": round(avg([r["close_chg"] for r in records]), 2),
            "平均竞价涨幅": round(avg([r["open_chg"] for r in records]), 2),
        },
        "按模式": {m: {"家数": len(vs), "均值": round(avg(vs), 2), "胜率": round(win_rate(vs), 1)}
                   for m, vs in by_mode.items()},
        "按行业": {ind: {"家数": len(vs), "均值": round(avg(vs), 2)}
                   for ind, vs in sorted(by_ind.items(), key=lambda x: avg(x[1]), reverse=True)},
        "按量比": {k: {"家数": len(vs), "均值": round(avg(vs), 2), "胜率": round(win_rate(vs), 1)}
                   for k, vs in by_vol.items()},
        "按换手": {k: {"家数": len(vs), "均值": round(avg(vs), 2), "胜率": round(win_rate(vs), 1)}
                   for k, vs in by_turn.items()},
        "按振幅": {k: {"家数": len(vs), "均值": round(avg(vs), 2), "胜率": round(win_rate(vs), 1)}
                   for k, vs in by_amp.items()},
        "高开低走预警": [f"{r['name']}(+{r['open_chg']}→{r['close_chg']})" for r in hk_warning],
        "竞价强收盘更强": [f"{r['name']}(+{r['open_chg']}→+{r['close_chg']})" for r in good竞价],
        "竞价强收盘弱": [f"{r['name']}(+{r['open_chg']}→{r['close_chg']})" for r in bad竞价],
        "详细记录": records,
    }

    return factor_report


def generate_improvement_suggestions(analysis: dict) -> list[str]:
    """基于因子分析生成策略改进建议"""
    suggestions = []
    stats = analysis.get("当日统计", {})
    if not stats:
        return suggestions

    # 量比效果
    vol = analysis.get("按量比", {})
    if "≤1" in vol and ">2" in vol:
        low = vol["≤1"]["均值"]
        high = vol[">2"]["均值"]
        if high > low + 1:
            suggestions.append(f"✅ 量比>2有效: 均值+{high}% vs ≤1均值{low}%，建议维持量比>1.2门槛或适当提高")
        elif high < low - 1:
            suggestions.append(f"⚠️ 量比>2效果差: 均值{high}% vs ≤1均值{low}%，需检查量比过滤是否过严")

    # 换手率效果
    turn = analysis.get("按换手", {})
    if "<1%" in turn and ">3%" in turn:
        low = turn["<1%"]["均值"]
        high = turn[">3%"]["均值"]
        if high > low + 1:
            suggestions.append(f"✅ 高换手(>3%)有效: 均值+{high}% vs <1%均值{low}%")
        else:
            suggestions.append(f"ℹ️ 换手率区分度不强: >3%均值{high}% vs <1%均值{low}%")

    # 振幅效果
    amp = analysis.get("按振幅", {})
    if "低振幅(<3%)" in amp and "高振幅(>6%)" in amp:
        low = amp["低振幅(<3%)"]["均值"]
        high = amp["高振幅(>6%)"]["均值"]
        if high > low + 1.5:
            suggestions.append(f"ℹ️ 高振幅(>6%)股收益更高: +{high}% vs {low}%，可在风控允许范围内存留")
        elif low > high + 1:
            suggestions.append(f"✅ 低振幅(<3%)更稳定: 均值+{low}% vs 高振幅+{high}%，建议优先选低振幅")

    # 高开低走预警
    warns = analysis.get("高开低走预警", [])
    if warns:
        suggestions.append(f"⚠️ 高开低走({len(warns)}只): {', '.join(warns[:3])}，建议加强对竞价涨幅>2%股票的过滤")

    # 竞价强收盘弱
    bad = analysis.get("竞价强收盘弱", [])
    if bad:
        suggestions.append(f"⚠️ 竞价强收盘弱({len(bad)}只): {', '.join(bad[:3])}，这类股次日溢价难以持续")

    # 行业效果
    inds = analysis.get("按行业", {})
    if inds:
        best = max(inds.items(), key=lambda x: x[1]["均值"])
        worst = min(inds.items(), key=lambda x: x[1]["均值"])
        suggestions.append(f"📊 今日行业: 最强{best[0]}(+{best[1]['均值']}%) vs 最弱{worst[0]}({worst[1]['均值']}%)")

    # 平均收益
    avg_ret = stats.get("平均收益", 0)
    if avg_ret > 2:
        suggestions.append(f"🎯 今日推荐均值+{avg_ret}%，策略整体跑赢大盘")
    elif avg_ret > 0:
        suggestions.append(f"📊 今日推荐均值+{avg_ret}%，中性偏多")
    else:
        suggestions.append(f"📉 今日推荐均值{avg_ret}%，需关注大盘系统性风险")

    return suggestions


def build_auto_review(date: str, rec_stocks: list[dict],
                      today_data: dict[str, dict]) -> str:
    """自动生成复盘报告"""
    analysis = compute_factor_analysis(rec_stocks, today_data)
    suggestions = generate_improvement_suggestions(analysis)

    lines = [
        f"📊 自动复盘 {date}",
        f"🕐 收盘分析 · 推荐{len(rec_stocks)}只 · 实追踪{len(analysis.get('详细记录',[]))}只",
        "",
        "━━━━━━━━━━━━━━━",
        f"今日均值: {analysis.get('当日统计',{}).get('平均收益','?')}%"
        f"  |  上涨{analysis.get('当日统计',{}).get('上涨家数','?')}"
        f"  下跌{analysis.get('当日统计',{}).get('下跌家数','?')}",
        "━━━━━━━━━━━━━━━",
        "",
    ]

    # 按收益排序
    records = analysis.get("详细记录", [])
    records.sort(key=lambda x: x["close_chg"], reverse=True)

    # 结果分类
    strong = [r for r in records if r["close_chg"] >= 3]
    small_up = [r for r in records if 0 < r["close_chg"] < 3]
    flat = [r for r in records if r["close_chg"] == 0]
    down = [r for r in records if r["close_chg"] < 0]

    if strong:
        lines.append("**🚀 强势股（+3%以上）**")
        lines.append("| 名称 | 代码 | 竞价 | 收盘 | 结果 |")
        lines.append("|------|------|------|------|------|")
        for r in strong:
            lines.append(f"| {r['name']} | {r['code']} | {r['open_chg']:+.2f}% | {r['close_chg']:+.2f}% | 🚀强势 |")
        lines.append("")

    if small_up:
        lines.append("**📈 小涨股（0~+3%）**")
        lines.append("| 名称 | 代码 | 竞价 | 收盘 | 结果 |")
        lines.append("|------|------|------|------|------|")
        for r in small_up:
            lines.append(f"| {r['name']} | {r['code']} | {r['open_chg']:+.2f}% | {r['close_chg']:+.2f}% | ↑小涨 |")
        lines.append("")

    if down:
        lines.append("**📉 亏损股（<0%）**")
        lines.append("| 名称 | 代码 | 竞价 | 收盘 | 结果 |")
        lines.append("|------|------|------|------|------|")
        for r in sorted(down, key=lambda x: x["close_chg"]):
            lines.append(f"| {r['name']} | {r['code']} | {r['open_chg']:+.2f}% | {r['close_chg']:+.2f}% | ↓亏损 |")
        lines.append("")

    # 因子效果
    if analysis.get("按量比"):
        lines.append("**📊 因子效果**")
        vol = analysis["按量比"]
        lines.append(f"- 量比: ≤1={vol.get('≤1',{}).get('均值','?')}%({vol.get('≤1',{}).get('家数',0)}只) "
                     f"| 1-2={vol.get('1-2',{}).get('均值','?')}%({vol.get('1-2',{}).get('家数',0)}只) "
                     f"| >2={vol.get('>2',{}).get('均值','?')}%({vol.get('>2',{}).get('家数',0)}只)")
        lines.append("")

    # 改进建议
    if suggestions:
        lines.append("**💡 策略改进建议**")
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

    lines.append("🟢 仅供参考，不构成投资建议")
    return "\n".join(lines)


def main():
    today = datetime.date.today()
    yesterday = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    today_str = today.strftime("%Y%m%d")

    print(f"[因子分析] 今日: {today_str} | 昨日推荐: {yesterday}")

    # 1. 加载昨日推荐
    rec_stocks = parse_recommended_stocks(yesterday)
    print(f"  推荐: {len(rec_stocks)} 只")
    if not rec_stocks:
        print("  ⚠️ 无推荐数据，跳过")
        return

    # 2. 拉今日行情
    codes = [s["code"].zfill(6) for s in rec_stocks]
    today_data = fetch_today_closes(codes)
    print(f"  行情: {len(today_data)}/{len(codes)} 只成功")

    # 3. 计算因子分析
    analysis = compute_factor_analysis(rec_stocks, today_data)
    suggestions = generate_improvement_suggestions(analysis)

    # 4. 生成自动复盘
    auto_review = build_auto_review(today_str, rec_stocks, today_data)

    # 5. 保存自动复盘
    auto_review_file = PICK_DIR / f"自动复盘-{today_str}.md"
    auto_review_file.write_text(auto_review, encoding="utf-8")
    print(f"  ✅ 自动复盘已写入: {auto_review_file.name}")

    # 6. 更新策略改进日志
    changelog_lines = [f"\n## {today_str}", f"昨日推荐: {len(rec_stocks)} 只", ""]
    changelog_lines.append("**因子效果:**")
    stats = analysis.get("当日统计", {})
    changelog_lines.append(f"- 上涨{stats.get('上涨家数','?')}/下跌{stats.get('下跌家数','?')}, 均值{stats.get('平均收益','?')}%")
    if analysis.get("按量比"):
        vol = analysis["按量比"]
        changelog_lines.append(f"- 量比: ≤1={vol.get('≤1',{}).get('均值','?')}% | 1-2={vol.get('1-2',{}).get('均值','?')}% | >2={vol.get('>2',{}).get('均值','?')}%")
    changelog_lines.append("**建议:**")
    for s in suggestions:
        changelog_lines.append(f"- {s}")
    changelog_lines.append("")

    if STRATEGY_CHANGELOG.exists():
        existing = STRATEGY_CHANGELOG.read_text(encoding="utf-8")
    else:
        existing = f"# 策略改进日志\n\n每日复盘因子分析汇总\n\n"

    # 追加到头部
    new_log = existing + "\n".join(changelog_lines)
    # 保持不超过100行
    lines = new_log.split("\n")
    if len(lines) > 200:
        lines = lines[:200]
    STRATEGY_CHANGELOG.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✅ 策略日志已更新")

    # 7. 保存因子分析JSON
    analysis_copy = {k: v for k, v in analysis.items() if k != "详细记录"}
    analysis_copy["date"] = today_str
    analysis_copy["prev_date"] = yesterday
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            all_logs = json.load(f)
    else:
        all_logs = []
    all_logs.append(analysis_copy)
    # 只保留最近30天
    all_logs = all_logs[-30:]
    LOG_FILE.write_text(json.dumps(all_logs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*50}")
    print("💡 改进建议:")
    for s in suggestions:
        print(f"  {s}")


if __name__ == "__main__":
    main()
