#!/bin/bash
# 每日选股推送 watchdog
# 工作日 9:15 执行：跑选股模型 → 推送微信 → 更新Web看板
#
# 流程：
#   1. simple_picker.py（主引擎）→ 生成 每日选股/综合选股-{date}.md + 推荐-{date}.md
#   2. 推送选股结果.py → 读取综合选股报告 → 推送微信
#   3. gen_report.py → 刷新 Web 仪表板（index.html）
#
# Web Dashboard 数据源：gen_report.py 的 get_latest_picks() 读取 每日选股/推荐-{date}.md

set -uo pipefail   # 不加 -e：单步失败不应让后续步骤（如推送）全部停摆；显式检查退出码

WORKDIR="${FUND_WORKDIR:-/home/ubuntu/.openclaw/workspace/scripts}"
LOGFILE="${FUND_LOG_DIR:-/tmp}/pick_log.txt"
TODAY="$(date '+%Y%m%d')"

# flock 互斥：上一次未跑完则跳过本次（cron 触发防重叠）
LOCKFILE="${FUND_LOCK_DIR:-/tmp}/picker_watchdog.lock"
mkdir -p "$(dirname "$LOGFILE")"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⏳ 上一次任务仍在运行，跳过本次" >> "$LOGFILE"
    exit 0
fi

cd "$WORKDIR" || { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ cd $WORKDIR 失败" >> "$LOGFILE"; exit 1; }

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========== 选股任务开始 ==========" >> "$LOGFILE"

# ── 步骤1：跑选股引擎（simple_picker.py）────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [步骤1] 运行 simple_picker.py ..." >> "$LOGFILE"
python3 simple_picker.py 2>&1 | tee -a "$LOGFILE"
PICKER_EXIT=${PIPESTATUS[0]}

if [ "$PICKER_EXIT" -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ simple_picker.py 退出异常 rc=$PICKER_EXIT" >> "$LOGFILE"
fi

# 等待文件系统同步
sleep 2

# ── 步骤2：推送微信 ─────────────────────────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [步骤2] 推送微信 ..." >> "$LOGFILE"
python3 推送选股结果.py 2>&1 | tee -a "$LOGFILE"
PUSH_EXIT=${PIPESTATUS[0]}

if [ "$PUSH_EXIT" -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ 推送选股结果.py 退出异常 rc=$PUSH_EXIT" >> "$LOGFILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 微信推送完成" >> "$LOGFILE"
fi

# ── 步骤3：刷新 Web 仪表板 ─────────────────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [步骤3] 刷新 Web 仪表板 ..." >> "$LOGFILE"
python3 gen_report.py 2>&1 | tee -a "$LOGFILE"
WEB_EXIT=${PIPESTATUS[0]}

if [ "$WEB_EXIT" -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ gen_report.py 退出异常 rc=$WEB_EXIT" >> "$LOGFILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Web仪表板更新完成" >> "$LOGFILE"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========== 选股任务完成 ==========" >> "$LOGFILE"
echo ""
