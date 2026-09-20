#!/bin/bash
# 每日选股运算（盘前）
# 每个工作日 9:15 执行：跑选股模型，输出结果到指定文件
# 9:27 的推送任务读取此文件内容推送微信

WORKDIR="/home/ubuntu/.openclaw/workspace/scripts"
OUTFILE="/tmp/daily_pick_result.txt"
LOGFILE="/tmp/pick_log.txt"

cd $WORKDIR

# 运行选股（CAN SLIM版）- 使用系统python3
python3 四维选股CANSLIM.py 2>&1 | tee $OUTFILE

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 选股完成，结果已保存" >> $LOGFILE
# 注:generate_pick_page.py 不在包里,跳过可视化页面生成
