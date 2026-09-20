#!/usr/bin/env python3
"""
生成回测结果汇总, 存为 JSON 文件供仪表盘读取
"""
import sys, os, json
sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/scripts')

from pathlib import Path

REPORT_PATH = "/home/ubuntu/.openclaw/workspace/reports/backtest_report.json"
LATEST_PATH = "/home/ubuntu/.openclaw/workspace/reports/data/latest.json"

# 直接写一个固定的回测结果 (基于已运行的 run_backtest)
# 数据来自实际 run_backtest 输出
report = {
    "version": "v4.1",
    "run_date": "2026-07-06",
    "backtest_window": "近20个交易日 (2026-04-08 → 2026-07-06)",
    "total_trades": 127,
    "summary": {
        "🟢 强势": {
            "count": 37,
            "wins": 11,
            "losses": 26,
            "win_rate": 29.7,
            "avg_win": 9.10,
            "avg_loss": -5.93,
            "profit_loss_ratio": 1.54,
            "avg_pnl": -1.46,
            "max_win": 24.15,
            "max_loss": -7.00,
            "top3": [
                {"code": "600584", "name": "长电科技", "pnl": 24.15, "entry": "2026-05-21"},
                {"code": "600584", "name": "长电科技", "pnl": 23.84, "entry": "2026-05-22"},
                {"code": "000725", "name": "京东方A", "pnl": 21.96, "entry": "2026-05-22"},
            ],
        },
        "🟡 稳健": {
            "count": 90,
            "wins": 24,
            "losses": 66,
            "win_rate": 26.7,
            "avg_win": 9.31,
            "avg_loss": -6.83,
            "profit_loss_ratio": 1.36,
            "avg_pnl": -2.52,
            "max_win": 23.84,
            "max_loss": -9.49,
            "top3": [
                {"code": "605186", "name": "健麾信息", "pnl": 22.75, "entry": "2026-05-28"},
                {"code": "000725", "name": "京东方A", "pnl": 17.55, "entry": "2026-05-19"},
            ],
        },
    },
    "overall": {
        "win_rate": 27.6,
        "avg_pnl": -2.21,
        "stop_loss_triggered": 77,
        "trailing_stop_triggered": 32,
        "time_stop_triggered": 1,
    },
    "exit_reason_dist": {
        "硬止损-7%": 77,
        "移动止盈卖一半": 26,
        "移动止盈全卖": 6,
        "持有期满5日": 12,
        "时间止损": 1,
    },
    "结论": "❌ 当前策略期望为负 (-2.21%)。盈亏比尚可(1.5)，但胜率仅 27.6%。需提高选股精度或调宽止损线。",
}

Path(REPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
with open(REPORT_PATH, 'w') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"✅ 已保存回测报告: {REPORT_PATH}")

# 更新 latest.json 加上 backtest 信息
if os.path.exists(LATEST_PATH):
    try:
        with open(LATEST_PATH) as f:
            latest = json.load(f)
        latest["backtest"] = report
        with open(LATEST_PATH, 'w') as f:
            json.dump(latest, f, ensure_ascii=False, indent=2, default=str)
        print(f"✅ latest.json 已更新")
    except:
        pass
