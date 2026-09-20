#!/bin/bash
# 美股盘中时段判断脚本
# 北京时间 21:30 - 04:00 (次日) = 美股交易时段 (9:30-16:00 ET)
# 周五21:30后美股已收盘，不监控周六

NOW=$(date +%w\ %H\ %M)
WEEKDAY=$(echo $NOW | awk '{print $1}')
HOUR=$(echo $NOW | awk '{print $2}')
MIN=$(echo $NOW | awk '{print $3}')

# 转为分钟
TOTAL=$((10#$HOUR * 60 + 10#$MIN))

# 判断：周一~周五 且 在21:30(1290)-24:00 或 00:00-04:00(0-240)
if [[ "$WEEKDAY" =~ ^[1-5]$ ]]; then
    # 21:30-23:59
    if [[ $TOTAL -ge 1290 ]] && [[ $TOTAL -le 1439 ]]; then
        exit 0  # 运行
    fi
    # 00:00-04:00
    if [[ $TOTAL -le 240 ]]; then
        exit 0  # 运行
    fi
fi

exit 1  # 不运行
