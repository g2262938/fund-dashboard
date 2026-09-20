# 易方达基金仪表板 + 选股系统

## 目录结构

```
fund-dashboard/
├── index.html          # 主仪表板
├── menu.html           # 导航菜单
├── reports/            # 报表页面
├── scripts/           # 后端脚本（与前端交互）
│   ├── simple_picker.py          # 选股引擎
│   ├── gen_report.py             # 报告生成（生成 latest.json）
│   ├── picker_watchdog.sh        # 每日选股 cron
│   ├── 推送选股结果.py           # 微信推送
│   ├── social_holding_cron.py   # 社保持仓更新
│   ├── us_earnings_watchdog.sh  # 美股财报监控
│   ├── concert_watchdog.sh      # 演唱会抢票监控
│   ├── yjyg_*.py                # 中报业绩报告
│   ├── snapshot_index.py         # 指数快照
│   └── ah_dual_list.py          # A/H股双板
├── data/              # 实时数据 JSON
└── README.md
