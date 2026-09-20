#!/usr/bin/env python3
"""
美股财报实时监控 Daemon
- 常驻后台，每5分钟检查一次 yfinance
- 首次运行：记录所有已知的财报结果（不打搅）
- 后续运行：只推送新公布的超预期/不及预期财报
- 微信推送 via Hermes gateway
- 状态持久化：seen_earnings.json（防止重复推送）
"""

import yfinance as yf
import json, time, ssl, urllib.request, os, sys
import concurrent.futures
from datetime import date, datetime, timedelta
from pathlib import Path

# ========== 配置 ==========
POLL_INTERVAL = 300      # 5分钟轮询一次
STATE_FILE = "/home/ubuntu/.openclaw/workspace/scripts/seen_earnings.json"
WEBHOOK_FILE = "/home/ubuntu/.openclaw/wechat_webhook.txt"
WATCHLIST = [
    'AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','AMD','INTC','NFLX',
    'JPM','BAC','GS','MS','V','MA','AXP','BLK','SCHW','AVGO','ORCL','CRM',
    'ADBE','QCOM','MU','TXN','LRCX','AMAT','CSCO','PEP','KO','MCD','SBUX',
    'WMT','COST','NKE','HD','TGT','LOW','TJX','JNJ','UNH','LLY','ABBV',
    'ABT','MRK','PFE','TMO','DHR','DIS','CMCSA','BA','T','VZ','CAT','GE',
    'MMM','LMT','RTX','PLTR','LCID','SOFI','RIVN','COIN','HOOD','BABA',
    'JD','NTES','PDD','BIDU','DIA','QQQ','SPY','IWM','SMH','XLE'
]

# ========== 日志 ==========
def log(msg):
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ========== 微信推送 ==========
def _read_webhook():
    for p in [WEBHOOK_FILE, "/tmp/wechat_webhook.txt"]:
        if os.path.exists(p):
            return open(p).read().strip()
    return ""

def push_wechat(text: str) -> bool:
    """通过企业微信 Webhook 推送，支持本地 Hermes gateway"""
    import socket

    webhook = _read_webhook()
    if not webhook:
        # 没有本地 webhook，尝试走 Hermes 本地 gateway
        # Hermes gateway 监听 localhost，可转发到 weixin
        gateway_url = "http://localhost:18999/api/send"
        try:
            import urllib.request
            payload = json.dumps({"chat_id": os.environ.get("HERMES_SESSION_CHAT_ID",""), "text": text}).encode()
            req = urllib.request.Request(gateway_url, data=payload, headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    log("✅ Hermes gateway 推送成功")
                    return True
        except Exception as e:
            log(f"   gateway 推送失败（正常，如未配置）: {e}")

        log("⚠️ 未配置微信 Webhook，无法推送")
        return False

    # 企业微信 webhook
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    payload = json.dumps({"msgtype":"text","text":{"content":text}}).encode("utf-8")
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook}"
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            result = json.loads(resp.read().decode())
            if result.get("errcode") == 0:
                log("✅ 微信推送成功")
                return True
            else:
                log(f"⚠️ 微信推送失败: {result}")
    except Exception as e:
        log(f"⚠️ 微信推送异常: {e}")
    return False

# ========== 状态管理 ==========
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            return json.loads(open(STATE_FILE).read())
        except:
            pass
    return {"seen": {}, "last_run": None, "initialized": False}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
    os.chmod(STATE_FILE, 0o600)

def mark_seen(state: dict, sym: str, item: dict):
    """标记为已推送（防止重复）"""
    state["seen"][sym] = {
        "lastActual": item.get("latestActual"),
        "lastEstimate": item.get("latestEstimate"),
        "lastSurprise": item.get("latestSurprise"),
        "pushed_at": datetime.now().isoformat(),
    }

def is_new(state: dict, sym: str, item: dict) -> bool:
    """判断是否是新结果（未推送过）"""
    seen = state["seen"].get(sym, {})
    if not seen:
        return True  # 从未见过
    # 实际值变化 = 新结果
    prev_actual = seen.get("lastActual")
    curr_actual = item.get("latestActual")
    if prev_actual != curr_actual and curr_actual is not None:
        return True
    return False

# ========== 数据抓取 ==========
def fetch_ticker_earnings(sym: str) -> dict | None:
    """单股票获取最近财报 EPS 数据"""
    try:
        t = yf.Ticker(sym)
        ed = t.get_earnings_dates(limit=3)
        if ed is None or ed.empty:
            return None

        import math
        for _, row in ed.iterrows():
            surprise = row.get("Surprise(%)")
            # 跳过未公布（nan）
            if surprise is None or (isinstance(surprise, float) and math.isnan(surprise)):
                continue
            actual = row.get("Reported EPS")
            estimate = row.get("EPS Estimate")
            # 跳过无实际值
            if actual is None or (isinstance(actual, float) and math.isnan(actual)):
                continue

            # 日期处理
            edate = row.get("Date")
            date_str = ""
            if edate is not None:
                try:
                    if isinstance(edate, str):
                        date_str = edate[:10]
                    else:
                        date_str = str(edate)[:10]
                except:
                    date_str = str(edate)[:10] if edate else ""

            return {
                "symbol": sym,
                "name": t.info.get("shortName", sym) if hasattr(t, "info") else sym,
                "actual": float(actual),
                "estimate": float(estimate) if estimate and not (isinstance(estimate, float) and math.isnan(estimate)) else None,
                "surprise": float(surprise),
                "date": date_str,
            }
    except Exception as e:
        pass
    return None

def scan_watchlist() -> list[dict]:
    """并行扫描所有 watchlist，返回最近有财报的股票"""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_ticker_earnings, sym): sym for sym in WATCHLIST}
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            r = fut.result()
            if r:
                results.append(r)
    return results

# ========== 判断 ==========
def judge(surprise: float) -> tuple[str, str]:
    if surprise > 5:
        return "beat", "✅超预期"
    elif surprise < -5:
        return "miss", "❌不及预期"
    else:
        return "inline", "📊符合预期"

# ========== 主循环 ==========
def main():
    log("=" * 50)
    log("美股财报实时监控 Daemon 启动")
    log(f"监控 {len(WATCHLIST)} 只股票，轮询间隔 {POLL_INTERVAL}s")
    log("=" * 50)

    state = load_state()
    initialized = state.get("initialized", False)
    push_wechat("📺 美股财报实时监控已启动（后台运行）")

    while True:
        try:
            scan_start = datetime.now()
            log("开始扫描...")

            records = scan_watchlist()
            log(f"扫描完成，获取到 {len(records)} 条财报记录")

            if not initialized:
                # 首次运行：只记录，不推送
                for rec in records:
                    sym = rec["symbol"]
                    state["seen"][sym] = {
                        "lastActual": rec["actual"],
                        "lastEstimate": rec.get("estimate"),
                        "lastSurprise": rec["surprise"],
                        "pushed_at": None,
                    }
                state["initialized"] = True
                save_state(state)
                initialized = True
                log(f"初始化完成，已记录 {len(records)} 只股票的财报状态")

                # 打印当前财报概况
                beat = [r for r in records if judge(r["surprise"])[0] == "beat"]
                miss = [r for r in records if judge(r["surprise"])[0] == "miss"]
                log(f"当前：✅超预期 {len(beat)} | ❌不及预期 {len(miss)}")
            else:
                # 增量检查：只推送新的超预期/不及预期
                new_items = []
                for rec in records:
                    sym = rec["symbol"]
                    if is_new(state, sym, rec):
                        verdict, label = judge(rec["surprise"])
                        if verdict in ("beat", "miss"):
                            new_items.append((rec, verdict, label))

                if new_items:
                    log(f"发现 {len(new_items)} 条新财报，开始推送...")
                    lines = [f"📋 美股财报提醒 {date.today().isoformat()}\n"]
                    for rec, verdict, label in new_items:
                        est_str = f"${rec['estimate']:.2f}" if rec.get("estimate") else "?"
                        lines.append(
                            f"{label} {rec['symbol']} | 实际EPS ${rec['actual']:.2f}"
                            f" vs 预期{est_str} | {rec['surprise']:+.1f}%\n"
                        )
                        mark_seen(state, rec["symbol"], {
                            "latestActual": rec["actual"],
                            "latestEstimate": rec.get("estimate"),
                            "latestSurprise": rec["surprise"],
                        })

                    save_state(state)
                    msg = "".join(lines)
                    print(msg)
                    push_wechat(msg)
                else:
                    log("暂无新财报")

            # 计算到下次轮询的时间
            elapsed = (datetime.now() - scan_start).total_seconds()
            sleep_time = max(10, POLL_INTERVAL - elapsed)
            log(f"下次扫描约 {int(sleep_time)}s 后...")
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log("收到中断信号，退出")
            push_wechat("📺 美股财报监控已停止")
            break
        except Exception as e:
            log(f"轮询异常: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
