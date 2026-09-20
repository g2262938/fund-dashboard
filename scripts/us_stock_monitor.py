#!/usr/bin/env python3
"""
美股实时行情监控守护进程
- 每30分钟检查SPY/QQQ/DIA走势
- 跌幅超过阈值立即推送微信
- 正常状态每2小时轻推送一次（心跳）
"""
import re
import sys
import time
import json
import datetime
import urllib.request
from pathlib import Path

CHECK_INTERVAL = 10 * 60  # 10分钟检查一次
THRESHOLDS = {'warning': -0.5, 'alert': -1.5, 'critical': -3.0}
LAST_ALERT_FILE = '/tmp/us_crash_alert.json'
LOG_FILE = '/tmp/us_crash_monitor.log'
STATE_FILE = '/tmp/us_crash_state.json'
WECHAT_TARGET = 'o9cq80z4Nv9VbLwPfTbahT8986V0@im.wechat'

# 美股交易时段（北京时间）
# US market: Mon-Fri 9:30 AM - 4:00 PM ET
# 北京时间 = ET + 13小时（夏令时）/ + 14小时（冬令时）
# 简化为：周一至周五 21:30 - 次日 04:00 为交易时段
# 即北京时间当天 21:30-23:59 和次日 00:00-04:00

US_MARKET_START_H = 21  # 北京时间 21:30 开始（简化为21:00）
US_MARKET_START_M = 30
US_MARKET_END_H = 4     # 次日 04:00 结束

def is_us_market_hours(dt):
    """判断当前北京时间是否处于美股交易时段"""
    weekday = dt.weekday()  # 0=周一, 6=周日
    if weekday >= 5:  # 周六周日
        return False
    
    hour = dt.hour
    minute = dt.minute
    
    # 交易时段：北京时间 21:30 - 次日 04:00
    # 当天 21:30-23:59
    if hour > US_MARKET_START_H or (hour == US_MARKET_START_H and minute >= US_MARKET_START_M):
        return True
    # 次日 00:00-04:00
    if hour < US_MARKET_END_H:
        return True
    
    return False


def _get_next_market_time(dt):
    """返回下一个美股交易时段开始的友好描述"""
    weekday = dt.weekday()
    hour = dt.hour
    
    # 周一到周五白天 -> 今晚21:30
    if weekday < 5:
        if hour < US_MARKET_END_H:
            return f"今晚 21:30"
        else:
            return f"今晚 21:30"
    # 周六 -> 周一21:30
    elif weekday == 5:
        return "周一 21:30"
    # 周日 -> 周一21:30
    else:
        return "周一 21:30"

def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    Path(LOG_FILE).write_text(line + '\n', errors='replace')


def get_us_etf_data():
    url = "https://qt.gtimg.cn/q=usSPY,usQQQ,usDIA"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read().decode('gbk')
    return data


def parse_tencent_data(data):
    results = {}
    for match in re.finditer(r'v_us(\w+)="([^"]+)"', data):
        sym = match.group(1)
        fields = match.group(2).split('~')
        if len(fields) > 38:
            results[sym] = {
                'name': fields[1],
                'current': float(fields[3]) if fields[3] else 0,
                'prev_close': float(fields[4]) if fields[4] else 0,
                'high': float(fields[33]) if fields[33] else 0,
                'low': float(fields[34]) if fields[34] else 0,
                'day_chg_pct': float(fields[32]) if fields[32] else 0,
                'datetime': fields[30],
            }
    return results


def get_alert_level(pct):
    if pct <= THRESHOLDS['critical']:
        return '🔴 严重暴跌'
    elif pct <= THRESHOLDS['alert']:
        return '🟠 较大跌幅'
    elif pct <= THRESHOLDS['warning']:
        return '🟡 注意'
    return '✅ 正常'


def load_state():
    try:
        return json.loads(Path(STATE_FILE).read_text())
    except:
        return {}


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False))


def send_wechat(msg):
    """通过Hermes发送微信消息"""
    import subprocess
    cmd = ['hermes', 'send', '--to', f'weixin:{WECHAT_TARGET}', msg]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            log(f"微信推送成功")
        else:
            log(f"微信推送失败: {r.stderr[:100]}")
    except Exception as e:
        log(f"微信推送异常: {e}")


def build_alert_msg(data):
    now = datetime.datetime.now().strftime('%H:%M:%S')
    lines = [
        f"📛 美股监控 [{now}]",
        "",
    ]
    for sym, info in data.items():
        pct = info['day_chg_pct']
        level = get_alert_level(pct)
        direction = "▲" if pct >= 0 else "▼"
        sign = "+" if pct >= 0 else ""
        lines.append(f"{level} {sym}({info['name']})")
        lines.append(f"  现价: {info['current']:.2f}  {direction}{sign}{pct:.2f}%")
        lines.append(f"  昨收: {info['prev_close']:.2f}  今高: {info['high']:.2f}  今低: {info['low']:.2f}")
        lines.append("")

    worst = min(data.items(), key=lambda x: x[1]['day_chg_pct'])
    worst_pct = worst[1]['day_chg_pct']
    if worst_pct <= THRESHOLDS['critical']:
        lines.append("⚠️ 【严重暴跌预警】美股系统性风险，建议减仓观望！")
    elif worst_pct <= THRESHOLDS['alert']:
        lines.append("⚠️ 【较大跌幅预警】注意美股回调风险！")
    elif worst_pct <= THRESHOLDS['warning']:
        lines.append("⚠️ 【注意】美股小幅走弱，持续关注。")
    else:
        lines.append("✅ 美股三大指数运行正常。")

    return "\n".join(lines)


def build_heartbeat_msg(data):
    """每2小时正常心跳"""
    now = datetime.datetime.now().strftime('%H:%M')
    lines = [f"💓 美股监控心跳 [{now}]"]
    for sym, info in data.items():
        pct = info['day_chg_pct']
        direction = "+" if pct >= 0 else ""
        lines.append(f"  {sym}: {info['current']:.2f} ({direction}{pct:.2f}%)")
    return "\n".join(lines)


def should_push(state, worst_pct, now_dt):
    """判断是否需要推送（防重复）"""
    last_alert = state.get('last_alert_time', '')
    last_heartbeat = state.get('last_heartbeat_time', '')
    last_pct = state.get('last_worst_pct', 0)

    # 暴跌预警：一定推
    if worst_pct <= THRESHOLDS['alert']:
        return True
    # 心跳：每2小时一次
    if last_heartbeat:
        last_h = datetime.datetime.fromisoformat(last_heartbeat)
        if (now_dt - last_h).total_seconds() >= 2 * 3600:
            return True
    # 正常状态变化大：>1%
    if abs(worst_pct - last_pct) > 1.0 and worst_pct <= -2:
        return True
    return False


def main():
    log("美股监控守护进程启动")
    state = load_state()

    while True:
        now_dt = datetime.datetime.now()
        now_ts = now_dt.strftime('%Y-%m-%d %H:%M:%S')
        check_count = state.get('check_count', 0) + 1
        state['check_count'] = check_count

        # 非交易时段只打印状态，不推送，减少噪音
        if not is_us_market_hours(now_dt):
            next_check = _get_next_market_time(now_dt)
            log(f"非美股交易时段，下次检查: {next_check} | 进入休眠")
            time.sleep(CHECK_INTERVAL)
            continue

        try:
            raw = get_us_etf_data()
            data = parse_tencent_data(raw)

            if not data:
                log("数据获取失败（空），重试")
                time.sleep(60)
                continue

            worst = min(data.items(), key=lambda x: x[1]['day_chg_pct'])
            worst_pct = worst[1]['day_chg_pct']

            # 更新状态
            state['last_check'] = now_ts
            state['last_worst_pct'] = worst_pct

            # 判断推送
            if should_push(state, worst_pct, now_dt):
                if worst_pct <= THRESHOLDS['alert']:
                    # 暴跌/预警级别
                    msg = build_alert_msg(data)
                    state['last_alert_time'] = now_ts
                    state['last_alert_pct'] = worst_pct
                else:
                    # 心跳
                    msg = build_heartbeat_msg(data)
                    state['last_heartbeat_time'] = now_ts

                save_state(state)
                send_wechat(msg)
                log(f"推送已发送 | 最差: {worst_pct:+.2f}%")

            else:
                log(f"检查{check_count} | 最差: {worst_pct:+.2f}% | 无需推送")

        except Exception as e:
            log(f"异常: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()