#!/usr/bin/env python3
"""
A股宽基指数实时监控守护进程
- 每15分钟检查主要指数涨跌
- 重要节点（9:30开盘/11:30上午收盘/13:00下午开盘/14:30盘中/15:00收盘）推送
- 异常波动（>±2%）立即推送
"""
import time
import json
import datetime
import requests
from pathlib import Path

CHECK_INTERVAL = 15 * 60  # 15分钟
THRESHOLD_ALERT = 2.0  # ±2% 异常波动
PUSH_WINDOWS = [
    (datetime.time(9, 25), "📈 开盘在即"),
    (datetime.time(9, 31), "📈 开盘走势"),
    (datetime.time(11, 29), "📊 午盘收盘"),
    (datetime.time(13, 1),  "📈 下午开盘"),
    (datetime.time(14, 55), "📊 尾盘最后5分钟"),
    (datetime.time(15, 1),  "📊 收盘汇总"),
]

INDICES = {
    "上证指数": "sh000001",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
    "科创50":   "sh000688",
    "沪深300": "sh000300",
    "中证500": "sh000905",
    "中证1000": "sh000852",
    "上证50":   "sh000016",
}

LAST_ALERT_FILE = '/tmp/index_alert.json'
LOG_FILE = '/tmp/index_monitor.log'
STATE_FILE = '/tmp/index_state.json'
LAST_PUSH_FILE = '/tmp/index_last_push.json'


def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    Path(LOG_FILE).write_text(line + '\n', errors='replace')


def get_indices_data():
    codes = ",".join(INDICES.values())
    url = f"https://qt.gtimg.cn/q={codes}"
    headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)', 'Referer': 'https://gu.qq.com/'}
    r = requests.get(url, headers=headers, timeout=10)
    r.encoding = 'gbk'
    return r.text


def parse_line(line):
    try:
        inner = line.split('="')[1].rstrip('";')
        parts = inner.split('~')
        if len(parts) < 5:
            return None
        name = parts[1]
        current = float(parts[3]) if parts[3] else 0
        prev_close = float(parts[4]) if parts[4] else 0
        change = current - prev_close
        pct = (change / prev_close * 100) if prev_close else 0
        return {'name': name, 'current': current, 'change': change, 'pct': pct}
    except:
        return None


def load_state():
    try:
        return json.loads(Path(STATE_FILE).read_text())
    except:
        return {}


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False))


def load_last_push():
    try:
        return json.loads(Path(LAST_PUSH_FILE).read_text())
    except:
        return {}


def save_last_push(data):
    Path(LAST_PUSH_FILE).write_text(json.dumps(data, ensure_ascii=False))


def is_trading_hour():
    """判断当前是否为交易时间"""
    now = datetime.datetime.now()
    weekday = now.weekday()
    if weekday >= 5:  # 周末
        return False
    t = now.time()
    # 9:30-11:30 上午 / 13:00-15:00 下午
    in_morning = datetime.time(9, 25) <= t <= datetime.time(11, 30)
    in_afternoon = datetime.time(12, 55) <= t <= datetime.time(15, 5)
    return in_morning or in_afternoon


def is_market_close():
    return not is_trading_hour()

def get_push_type():
    """判断当前属于哪个重要节点"""
    now = datetime.datetime.now()
    t = now.time()
    for target_time, label in PUSH_WINDOWS:
        # 窗口前后5分钟
        before = (datetime.datetime.combine(now.date(), target_time) - datetime.timedelta(minutes=5)).time()
        after = (datetime.datetime.combine(now.date(), target_time) + datetime.timedelta(minutes=5)).time()
        if before <= t <= after:
            return label
    return None


def send_wechat(msg):
    import subprocess
    cmd = ['hermes', 'send', '--to', f'weixin:{WECHAT_TARGET}', msg]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        log(f"微信推送: {'成功' if r.returncode == 0 else '失败'}")
    except Exception as e:
        log(f"微信推送异常: {e}")


WECHAT_TARGET = 'o9cq80z4Nv9VbLwPfTbahT8986V0@im.wechat'


def build_msg(results, push_type, state):
    now = datetime.datetime.now()
    ts = now.strftime('%Y-%m-%d %H:%M:%S')
    avg_pct = sum(r['pct'] for r in results) / len(results)
    up = sum(1 for r in results if r['pct'] > 0)
    down = sum(1 for r in results if r['pct'] < 0)

    lines = [f"{push_type or '📊 指数监控'} [{ts}]\n"]
    for r in results:
        emoji = "🟢" if r['pct'] >= 0 else "🔴"
        direction = "▲" if r['pct'] >= 0 else "▼"
        sign = "+" if r['pct'] >= 0 else ""
        lines.append(f"{emoji} {r['name']:10s}: {r['current']:>8.2f}  {direction}{sign}{r['pct']:.2f}%")

    lines.append("")
    lines.append(f"📈 上涨: {up} | 📉 下跌: {down} | 平均: {'+' if avg_pct >= 0 else ''}{avg_pct:.2f}%")

    if avg_pct > 2.0:
        sentiment = "🟢 强势大涨"
    elif avg_pct > 1.0:
        sentiment = "🟢 明显上涨"
    elif avg_pct > 0.5:
        sentiment = "🟡 小幅上涨"
    elif avg_pct > -0.5:
        sentiment = "⚪ 震荡整理"
    elif avg_pct > -1.0:
        sentiment = "🟠 小幅下跌"
    elif avg_pct > -2.0:
        sentiment = "🔴 明显下跌"
    else:
        sentiment = "🔴 大幅杀跌"

    lines.append(f"🏭 市场: {sentiment}")

    # 对比上次推送
    prev = state.get('last_avg_pct')
    if prev is not None:
        delta = avg_pct - prev
        if abs(delta) > 0.5:
            lines.append(f"📐 距上次推送变化: {'+' if delta >= 0 else ''}{delta:.2f}%")

    return "\n".join(lines)


def should_push(results, state, push_type):
    """判断是否推送"""
    if is_market_close(): return False
    last_push = load_last_push()
    last_push_time = last_push.get('time', '')
    avg_pct = sum(r['pct'] for r in results) / len(results)
    max_pct = max(abs(r['pct']) for r in results)

    # 重要节点必推
    if push_type:
        return True

    # 异常波动必推（且10分钟内不重复）
    if max_pct >= THRESHOLD_ALERT:
        if last_push_time:
            last_t = datetime.datetime.fromisoformat(last_push_time)
            if (datetime.datetime.now() - last_t).total_seconds() < 10 * 60:
                return False  # 10分钟内已推
        return True

    # 每30分钟常规推送（交易时间内）
    if is_trading_hour() and last_push_time:
        last_t = datetime.datetime.fromisoformat(last_push_time)
        if (datetime.datetime.now() - last_t).total_seconds() < 30 * 60:
            return False
        return True

    return False


def main():
    log("宽基指数监控守护进程启动")
    state = load_state()

    while True:
        now_dt = datetime.datetime.now()
        push_type = get_push_type()
        check_count = state.get('check_count', 0) + 1
        state['check_count'] = check_count
        state['last_check'] = now_dt.isoformat()

        try:
            text = get_indices_data()
            results = []
            for line in text.split('\n'):
                d = parse_line(line)
                if d:
                    # 匹配INDICES
                    for name, code in INDICES.items():
                        if d['name'] == name or code[2:] in line:
                            results.append(d)
                            break

            if not results:
                log("数据获取失败，重试")
                time.sleep(60)
                continue

            avg_pct = sum(r['pct'] for r in results) / len(results)
            state['last_avg_pct'] = avg_pct

            if should_push(results, state, push_type):
                msg = build_msg(results, push_type, state)
                save_state(state)
                save_last_push({'time': now_dt.isoformat(), 'avg': avg_pct})
                send_wechat(msg)
                log(f"推送成功 | 均幅: {avg_pct:+.2f}%")
            elif is_market_close():
                log(f"检查{check_count} | 均幅: {avg_pct:+.2f}% | 非交易时间，跳过")
            else:
                log(f"检查{check_count} | 均幅: {avg_pct:+.2f}% | 无需推送")

        except Exception as e:
            log(f"异常: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()