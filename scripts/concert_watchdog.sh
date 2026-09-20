#!/bin/bash
# 演唱会抢票监控 - 依次抓取数据并推送微信
set -uo pipefail

WORKDIR="${FUND_WORKDIR:-/home/ubuntu/.openclaw/workspace/scripts}"
LOGDIR="${FUND_LOG_DIR:-/tmp}"

LOCKFILE="$LOGDIR/concert_watchdog.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo "[$(date)] ⏳ 上一次抢票监控仍在运行，跳过本次" >> "$LOGDIR/concert_monitor.log"
    exit 0
fi

cd "$WORKDIR" || exit 1

# Step 1: 抓取最新数据
echo "[$(date)] 开始抓取演唱会数据..."
timeout 120 python3 concert_monitor.py >> "$LOGDIR/concert_monitor.log" 2>&1
MONITOR_EXIT=$?

if [ $MONITOR_EXIT -ne 0 ]; then
    echo "[$(date)] 抓取失败，exit=$MONITOR_EXIT"
fi

# Step 2: 推送微信（无动态时不推送）
echo "[$(date)] 推送微信..."
timeout 30 python3 concert_push.py >> "$LOGDIR/concert_push.log" 2>&1
PUSH_EXIT=$?

echo "[$(date)] 完成 monitor=$MONITOR_EXIT push=$PUSH_EXIT"