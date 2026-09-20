#!/usr/bin/env python3
"""
美股交易时段判断（替代原 us_market_hours.sh）

原 shell 脚本有两个严重 bug：
1. 美股周五下半场（北京时间周六 00:00-04:00，对应美东周五 12:00-16:00）
   被 WEEKDAY=6 排除，导致每周固定漏掉最后一小时；
2. 写死 21:30-04:00 等价于假定美东永远是 EDT（夏令时），
   冬令时（EST，UTC-5）下美股对应北京 22:30-05:00，会漏最后一小时。

本脚本用 zoneinfo + 系统时区库自动处理夏令时，避开节假日可由调用方
用 pandas_market_calendars 进一步过滤。

用法：
    python3 us_market_hours.py    # 退出码 0 = 交易时段内，1 = 非交易时段
"""

import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo


def is_us_market_open() -> bool:
    """判断当前（服务器时间）是否处于美股常规交易时段"""
    et_now = datetime.now(ZoneInfo("America/New_York"))
    # 周末直接否
    if et_now.weekday() >= 5:
        return False
    # 美股常规时段 9:30-16:00 ET（节假日未过滤，调用方可叠加判断）
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= et_now.time() < market_close


if __name__ == "__main__":
    sys.exit(0 if is_us_market_open() else 1)
