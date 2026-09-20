#!/bin/bash
# 每日选股运算（盘前）
# 每个工作日 9:15 执行：跑选股模型，输出结果到指定文件
# 9:27 的推送任务读取此文件内容推送微信

set -uo pipefail

WORKDIR="${FUND_WORKDIR:-/home/ubuntu/.openclaw/workspace/scripts}"
OUTFILE="${FUND_OUT_DIR:-/tmp}/daily_pick_result.txt"
LOGFILE="${FUND_LOG_DIR:-/tmp}/pick_log.txt"

# flock 互斥：上一次未跑完则跳过本次
LOCKFILE="$(dirname "$OUTFILE")/选股运算.lock"
mkdir -p "$(dirname "$OUTFILE")" "$(dirname "$LOGFILE")"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⏳ 上一次选股任务仍在运行，跳过本次" >> "$LOGFILE"
    exit 0
fi

cd "$WORKDIR" || { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ cd $WORKDIR 失败" >> "$LOGFILE"; exit 1; }

# 运行选股（CAN SLIM版）
python3 四维选股CANSLIM.py 2>&1 | tee "$OUTFILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 选股完成，结果已保存" >> "$LOGFILE"
# 注:generate_pick_page.py 不在包里,跳过可视化页面生成
