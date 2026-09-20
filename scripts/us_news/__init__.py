# scripts/us_news/__init__.py
"""美股新闻信息中心

数据流：fetcher → sentiment → build → data/us_news_latest.json → us_news.html

数据源策略（两层 fallback）：
1. Finnhub（推荐，需注册 free key，环境变量 FINNHUB_API_KEY）
2. Yahoo Finance via yfinance（无需 key，作为 fallback）

Sentiment 分析：
- 关键词词典法（300+ 词，中英混合）
- 标题 + summary 扫描
- 输出 -3 ~ +3 分 + 标签（利多/中性/利空）

大盘聚合：
- ticker → GICS sector（11 大类）
- sector 内 sentiment 平均 → 板块影响排名
"""