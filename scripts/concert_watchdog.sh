#!/bin/bash
# 演唱会抢票监控 - 依次抓取数据并推送微信
cd /home/ubuntu

# Step 1: 抓取最新数据
echo "[$(date)] 开始抓取演唱会数据..."
timeout 120 python3 /home/ubuntu/.openclaw/workspace/scripts/concert_monitor.py >> /tmp/concert_monitor.log 2>&1
MONITOR_EXIT=$?

if [ $MONITOR_EXIT -ne 0 ]; then
    echo "[$(date)] 抓取失败，exit=$MONITOR_EXIT"
fi

# Step 2: 推送微信（无动态时不推送）
echo "[$(date)] 推送微信..."
timeout 30 python3 /home/ubuntu/.openclaw/workspace/scripts/concert_push.py >> /tmp/concert_push.log 2>&1
PUSH_EXIT=$?

echo "[$(date)] 完成 monitor=$MONITOR_EXIT push=$PUSH_EXIT"
