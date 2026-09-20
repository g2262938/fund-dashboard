#!/usr/bin/env python3
"""
美股财报监控系统 v2（基于 yfinance，无需 API Key）
功能：
  1. 抓取 watchlist 股票的未来财报日期
  2. 对已公布财报的股票，对比 EPS 实际 vs 预期
  3. 分类：✅超预期 / ❌不及预期 / 📊符合预期 / ⏳待公布
  4. 微信推送 + Web 页面展示
"""

import yfinance as yf
import json
import os
import time
import csv
import re
import ssl
import urllib.request
from datetime import date, datetime, timedelta
from typing import Optional

# ========== 配置 ==========
WATCHLIST = [
    # 科技巨头
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "INTC", "NFLX", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "QCOM", "TXN", "MU", "AMAT", "LRCX",
    # 金融
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "BLK", "SCHW", "AXP",
    # 消费
    "KO", "PEP", "MCD", "SBUX", "NKE", "COST", "WMT", "HD", "LOW", "TGT", "TJX",
    # 医疗
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "ABT", "TMO", "DHR",
    # 其他
    "DIS", "CMCSA", "VZ", "T", "BA", "CAT", "GE", "MMM", "RTX", "LMT",
    # 新能源/热门
    "PLTR", "RIVN", "LCID", "SOFI", "COIN", "HOOD",
    # 中概
    "BABA", "PDD", "JD", "NTES", "BIDU",
    # ETF（用作市场情绪参考）
    "SPY", "QQQ", "DIA", "IWM",
]

# 每次请求间隔（秒），避免被限速
REQUEST_DELAY = 0.5

# 重复推送冷却（秒）= 12小时
COOLDOWN_SECONDS = 12 * 3600

DATA_DIR = "/home/ubuntu/.openclaw/workspace/reports/data"
EARNINGS_FILE = f"{DATA_DIR}/us_earnings.json"
HISTORY_FILE = f"{DATA_DIR}/us_earnings_history.json"
STATE_FILE = f"{DATA_DIR}/us_earnings_state.json"
LOG_FILE = "/tmp/us_earnings_monitor.log"

# ========== 工具函数 ==========

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def load_json(path: str, default=dict):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default() if callable(default) else default

def save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def get_state() -> dict:
    return load_json(STATE_FILE, dict)

def save_state(state: dict):
    save_json(STATE_FILE, state)

def should_push(state: dict, key: str) -> bool:
    """同一只股票同一结果，12小时内不重复推"""
    last = state.get(f"pushed_{key}", 0)
    return (time.time() - last) > COOLDOWN_SECONDS

def mark_pushed(state: dict, key: str):
    state[f"pushed_{key}"] = time.time()

# ========== 核心抓取函数 ==========

def fetch_ticker_info(sym: str) -> dict:
    """获取单只股票财报相关信息（财报日期 + EPS预期 + 超预期数据）"""
    result = {
        "symbol": sym,
        "name": sym,
        "nextEarningsDate": None,
        "earnHigh": None,
        "earnLow": None,
        "earnAvg": None,
        "latestActual": None,
        "latestEstimate": None,
        "latestSurprise": None,
        "reportingToday": False,
        "recentQuarters": [],
    }
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info
        result["name"] = info.get("shortName", info.get("longName", sym))

        today = date.today()
        today_str = today.strftime("%Y-%m-%d")
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        # 获取财报日期列表（含实际/预期/超预期数据）
        earn_dates = ticker.get_earnings_dates(limit=8)
        if earn_dates is not None and not earn_dates.empty:
            for idx, row in earn_dates.iterrows():
                idx_str = str(idx)
                # 判断是过去（已公布）还是未来（待公布）
                date_only = idx_str[:10]

                est = row.get("EPS Estimate")
                act = row.get("Reported EPS")
                surprise = row.get("Surprise(%)")

                # 跳过未公布的值（nan）
                import math
                act_val = None
                if act is not None and not (isinstance(act, float) and math.isnan(act)):
                    act_val = float(act)
                est_val = None
                if est is not None and not (isinstance(est, float) and math.isnan(est)):
                    est_val = float(est)
                surp_val = None
                if surprise is not None and not (isinstance(surprise, float) and math.isnan(surprise)):
                    surp_val = float(surprise)

                entry = {
                    "date": date_only,
                    "estimate": est_val,
                    "actual": act_val,
                    "surprise": surp_val,
                }

                # 记录最近一个有实际数据的季度
                if result["latestActual"] is None and act_val is not None:
                    result["latestActual"] = act_val
                    result["latestEstimate"] = est_val
                    result["latestSurprise"] = surp_val

                if result["nextEarningsDate"] is None and date_only >= today_str:
                    result["nextEarningsDate"] = date_only
                    result["earnAvg"] = est
                    result["reportingToday"] = date_only in (today_str, tomorrow)

                result["recentQuarters"].append(entry)

    except Exception as e:
        log(f"  ⚠️ {sym} info error: {e}")
    return result


def judge_surprise(surprise_pct: float | None) -> tuple:
    """判断超/不及预期"""
    import math
    # 过滤 nan 和 None
    if surprise_pct is None or (isinstance(surprise_pct, float) and math.isnan(surprise_pct)):
        return "unknown", "⚠️ 数据异常"
    if surprise_pct > 5:
        return "beat", f"✅ 超预期 (+{surprise_pct:.1f}%)"
    elif surprise_pct < -5:
        return "miss", f"❌ 不及预期 ({surprise_pct:.1f}%)"
    elif surprise_pct is not None:
        return "inline", f"📊 符合预期 ({surprise_pct:+.1f}%)"
    else:
        return "unknown", "⚠️ 数据异常"


# ========== 主逻辑 ==========

def run_full_scan(today: date) -> list[dict]:
    """并行扫描 watchlist，返回分析结果"""
    import concurrent.futures
    log(f"开始并行扫描 {len(WATCHLIST)} 只股票...")
    today_str = today.strftime("%Y-%m-%d")
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    def process_one(sym: str) -> dict:
        info = fetch_ticker_info(sym)
        surprise = info.get("latestSurprise")
        if surprise is not None:
            verdict_code, verdict_cn = judge_surprise(surprise)
        else:
            verdict_code, verdict_cn = "unknown", "暂无数据"
        return {
            "symbol": sym,
            "name": info["name"],
            "nextEarningsDate": info["nextEarningsDate"],
            "earnHigh": info["earnHigh"],
            "earnLow": info["earnLow"],
            "earnAvg": info["earnAvg"],
            "latestActual": info["latestActual"],
            "latestEstimate": info["latestEstimate"],
            "latestSurprise": info["latestSurprise"],
            "reportingToday": info["reportingToday"],
            "recentQuarters": info.get("recentQuarters", [])[:4],
            "verdictCode": verdict_code,
            "verdictCN": verdict_cn,
        }

    analyzed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(process_one, s): s for s in WATCHLIST}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            sym = futures[fut]
            try:
                rec = fut.result()
                analyzed.append(rec)
                log(f"  [{i}/{len(WATCHLIST)}] {sym} ✓")
            except Exception as e:
                log(f"  [{i}/{len(WATCHLIST)}] {sym} ⚠️ {e}")
    return analyzed


def build_summary(records: list[dict]) -> dict:
    """构建汇总数据"""
    beat = [r for r in records if r.get("verdictCode") == "beat"]
    miss = [r for r in records if r.get("verdictCode") == "miss"]
    inline = [r for r in records if r.get("verdictCode") == "inline"]
    reporting_today = [r for r in records if r.get("reportingToday")]
    unknown = [r for r in records if r.get("verdictCode") == "unknown"]
    next_week = [r for r in records if r.get("nextEarningsDate")]

    return {
        "beat_count": len(beat),
        "miss_count": len(miss),
        "inline_count": len(inline),
        "reporting_today_count": len(reporting_today),
        "unknown_count": len(unknown),
        "total_scanned": len(records),
        "recent_beat": beat[:5],
        "recent_miss": miss[:5],
        "reporting_today": reporting_today,
    }


def format_push(records: list[dict], push_date: str) -> str:
    """格式化微信推送"""
    summary = build_summary(records)
    beat = summary["recent_beat"]
    miss = summary["recent_miss"]
    reporting = summary["reporting_today"]

    lines = [f"📋 美股财报监控 {push_date}", f"扫描 {summary['total_scanned']} 只 | ✅超预期{summary['beat_count']} ❌不及{summary['miss_count']} 📊符合{summary['inline_count']}", ""]

    if reporting:
        lines.append(f"🔔 今日/明日公布财报 ({len(reporting)} 只)：")
        for r in reporting:
            lines.append(f"  {r['symbol']} {r['name']} | 下次公布: {r['nextEarningsDate']}")
        lines.append("")

    if beat:
        lines.append(f"✅ 最近超预期 ({len(beat)} 只)：")
        for r in beat:
            lines.append(f"  {r['symbol']} | 实际 EPS ${r['latestActual']} vs 预期 ${r['latestEstimate']} | {r['latestSurprise']:+.1f}%")
        lines.append("")

    if miss:
        lines.append(f"❌ 最近不及预期 ({len(miss)} 只)：")
        for r in miss:
            lines.append(f"  {r['symbol']} | 实际 EPS ${r['latestActual']} vs 预期 ${r['latestEstimate']} | {r['latestSurprise']:+.1f}%")
        lines.append("")

    if not beat and not miss and not reporting:
        lines.append("今日无重大财报事件，等今晚美股开盘后留意。")

    return "\n".join(lines).strip()


def save_for_web(records: list[dict], push_date: str):
    """保存 Web 页面展示用的 JSON"""
    summary = build_summary(records)
    # 所有有财报日期的股票（近期）
    upcoming = sorted(
        [r for r in records if r.get("nextEarningsDate") and r["nextEarningsDate"] > push_date],
        key=lambda x: x["nextEarningsDate"]
    )
    # 最近超/不及预期的
    significant = [r for r in records if r.get("verdictCode") in ("beat", "miss")]

    web_data = {
        "date": push_date,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_scanned": summary["total_scanned"],
            "beat_count": summary["beat_count"],
            "miss_count": summary["miss_count"],
            "inline_count": summary["inline_count"],
            "reporting_today_count": summary["reporting_today_count"],
        },
        "reporting_today": summary["reporting_today"],
        "recent_beat": summary["recent_beat"],
        "recent_miss": summary["recent_miss"],
        "upcoming": upcoming[:20],  # 未来2周
        "significant": significant[:20],  # 有超/不及预期的
    }
    save_json(EARNINGS_FILE, web_data)


def wechat_push(message: str):
    """发送微信推送"""
    # 尝试从环境变量或配置文件读取 webhook
    webhook_key = os.environ.get("WECHAT_WEBHOOK_KEY", "")
    if not webhook_key:
        # 尝试从已有配置文件读取
        webhook_key = _get_wechat_webhook()
    
    if not webhook_key:
        log("⚠️ 未配置微信 Webhook，无法推送")
        return

    import urllib.request
    payload = json.dumps({
        "msgtype": "text",
        "text": {"content": message}
    }).encode("utf-8")

    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}"
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get("errcode") == 0:
                log("✅ 微信推送成功")
            else:
                log(f"⚠️ 微信推送失败: {result}")
    except Exception as e:
        log(f"⚠️ 微信推送异常: {e}")


def _get_wechat_webhook() -> str:
    """从配置文件读取微信 webhook"""
    possible_paths = [
        "/home/ubuntu/.openclaw/workspace/scripts/wechat_webhook.txt",
        "/home/ubuntu/.openclaw/wechat_webhook.txt",
        "/tmp/wechat_webhook.txt",
    ]
    for p in possible_paths:
        if os.path.exists(p):
            return open(p).read().strip()
    return ""


def main():
    today = date.today()
    push_date = today.strftime("%Y-%m-%d")

    log(f"\n{'='*50}")
    log(f"美股财报监控 | {push_date}")
    log(f"{'='*50}")

    # 1. 全面扫描
    records = run_full_scan(today)
    log(f"扫描完成，共 {len(records)} 只股票")

    # 2. 保存 Web 数据
    save_for_web(records, push_date)
    log(f"Web 数据已保存: {EARNINGS_FILE}")

    # 3. 汇总
    summary = build_summary(records)
    log(f"结果：✅超预期 {summary['beat_count']} | ❌不及预期 {summary['miss_count']} | 📊符合 {summary['inline_count']}")
    log(f"  今日/明日公布财报: {summary['reporting_today_count']} 只")

    # 4. 微信推送（只推今天/明天公布财报 + 超/不及预期的）
    state = get_state()
    to_push = []

    for r in records:
        sym = r["symbol"]
        verdict = r.get("verdictCode", "unknown")
        
        # 今天/明天公布财报的 → 必须推
        if r.get("reportingToday"):
            key = f"reporting_{sym}"
            if should_push(state, key):
                mark_pushed(state, key)
                to_push.append((sym, f"🔔 {sym} {r['name']} 今日公布财报 | 预期EPS ${r.get('earnAvg', '?') or '?'}"))

        # 超/不及预期的 → 推
        if verdict in ("beat", "miss"):
            key = f"{sym}_{verdict}"
            if should_push(state, key):
                mark_pushed(state, key)
                surprise = r.get("latestSurprise")
                actual = r.get("latestActual")
                estimate = r.get("latestEstimate")
                to_push.append((sym, f"{'✅' if verdict=='beat' else '❌'} {sym} | 实际EPS ${actual} vs 预期${estimate} | {surprise:+.1f}%"))

    save_state(state)

    if to_push:
        push_lines = [f"📋 美股财报监控 {push_date}", f"扫描 {len(records)} 只 | ✅超预期{summary['beat_count']} ❌不及{summary['miss_count']}", ""]
        for _, line in to_push:
            push_lines.append(line)
        push_msg = "\n".join(push_lines)
        log(f"\n📱 推送内容：\n{push_msg}")
        wechat_push(push_msg)
    else:
        log("✅ 无需推送（无重大财报事件）")

    log("✅ 完成")


if __name__ == "__main__":
    main()
