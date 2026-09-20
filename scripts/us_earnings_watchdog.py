#!/usr/bin/env python3
"""
美股财报实时监控 - 轻量定时版
每5分钟运行一次，增量检测 + 微信推送
配合 cron 或 systemd timer
"""
import yfinance as yf, json, ssl, urllib.request, os, sys, math
import concurrent.futures
from datetime import date, datetime

STATE_FILE = "/home/ubuntu/.openclaw/workspace/scripts/seen_earnings.json"
WATCHLIST = [
    'AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','AMD','INTC','NFLX',
    'JPM','BAC','GS','MS','V','MA','AXP','BLK','SCHW','AVGO','ORCL','CRM',
    'ADBE','QCOM','MU','TXN','LRCX','AMAT','CSCO','PEP','KO','MCD','SBUX',
    'WMT','COST','NKE','HD','TGT','LOW','TJX','JNJ','UNH','LLY','ABBV',
    'ABT','MRK','PFE','TMO','DHR','DIS','CMCSA','BA','T','VZ','CAT','GE',
    'MMM','LMT','RTX','PLTR','LCID','SOFI','RIVN','COIN','HOOD','BABA',
    'JD','NTES','PDD','BIDU','DIA','QQQ','SPY','IWM','SMH','XLE'
]

def log(m): print(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {m}", flush=True)

def load_state():
    if os.path.exists(STATE_FILE):
        try: return json.loads(open(STATE_FILE).read())
        except: pass
    return {"seen": {}, "last_scan": None}

def save_state(state):
    with open(STATE_FILE, "w") as f: json.dump(state, f)
    os.chmod(STATE_FILE, 0o600)

def push_wechat(text):
    """通过 Hermes cron deliver 机制推送：写入临时文件供外部处理"""
    # 方案：写入已知可被 cron/hermes 读取的位置
    flag_file = "/tmp/earn_push_flag.txt"
    with open(flag_file, "w") as f:
        f.write(text)
    log(f"推送内容已写入 {flag_file}")
    log(text)

def fetch_one(sym):
    try:
        t = yf.Ticker(sym)
        ed = t.get_earnings_dates(limit=3)
        if ed is None or ed.empty: return None
        for _, row in ed.iterrows():
            s = row.get("Surprise(%)")
            a = row.get("Reported EPS")
            if s is None or (isinstance(s, float) and math.isnan(s)): continue
            if a is None or (isinstance(a, float) and math.isnan(a)): continue
            edate = row.get("Date")
            ds = str(edate)[:10] if edate else ""
            return {
                "symbol": sym,
                "name": sym,
                "actual": float(a),
                "estimate": float(row["EPS Estimate"]) if row.get("EPS Estimate") and not (isinstance(row["EPS Estimate"], float) and math.isnan(row["EPS Estimate"])) else None,
                "surprise": float(s),
                "date": ds,
            }
    except: pass
    return None

def scan_all():
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_one, s): s for s in WATCHLIST}
        for fut in concurrent.futures.as_completed(futures, timeout=40):
            r = fut.result()
            if r: results.append(r)
    return results

def judge(surprise):
    if surprise > 5: return "beat"
    elif surprise < -5: return "miss"
    return "inline"

def main():
    log("开始扫描...")
    records = scan_all()
    state = load_state()

    new_items = []
    for rec in records:
        sym = rec["symbol"]
        seen = state["seen"].get(sym, {})
        prev_actual = seen.get("actual")
        curr_actual = rec["actual"]

        if prev_actual is None:
            # 首次见到，记录但不推送
            state["seen"][sym] = {"actual": curr_actual, "surprise": rec["surprise"], "date": rec["date"]}
            log(f"  首次记录 {sym}: EPS ${curr_actual:.2f} ({rec['surprise']:+.1f}%)")
        elif prev_actual != curr_actual:
            # 财报结果更新了（可能是新季度财报覆盖旧数据）
            verdict = judge(rec["surprise"])
            new_items.append((rec, verdict))
            log(f"  🔔 新财报 {sym}: EPS ${curr_actual:.2f} (vs ${prev_actual:.2f})")
            state["seen"][sym] = {"actual": curr_actual, "surprise": rec["surprise"], "date": rec["date"]}
        elif seen.get("date") != rec["date"]:
            # 同一实际值，但日期变了（新季度），也通知
            verdict = judge(rec["surprise"])
            new_items.append((rec, verdict))
            state["seen"][sym] = {"actual": curr_actual, "surprise": rec["surprise"], "date": rec["date"]}

    state["last_scan"] = datetime.now().isoformat()
    save_state(state)

    if new_items:
        lines = [f"📋 美股财报提醒 {date.today().isoformat()}\n"]
        for rec, verdict in new_items:
            if verdict in ("beat", "miss"):
                label = "✅超预期" if verdict == "beat" else "❌不及预期"
                est_str = f"${rec['estimate']:.2f}" if rec.get("estimate") else "?"
                lines.append(f"{label} {rec['symbol']} | 实际EPS ${rec['actual']:.2f} vs 预期{est_str} | {rec['surprise']:+.1f}%\n")
        msg = "".join(lines)
        log(f"\n发现 {len(new_items)} 条新财报，推送：")
        print(msg)
        push_wechat(msg)
    else:
        log("暂无新财报变化")

if __name__ == "__main__":
    main()
