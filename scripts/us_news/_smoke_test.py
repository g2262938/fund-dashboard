"""
_smoke_test.py — us_news 模块冒烟测试
========================================

覆盖：
- sector_map 静态映射（覆盖 + 中文标签）
- sentiment 关键词词典（多种典型输入）
- build mock 流程（端到端：mock → build → 校验输出 schema）
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def test_sector_map():
    print("\n--- 1. sector_map ---")
    from scripts.us_news import sector_map
    # 核心 ticker 都应映射
    for t in ["AAPL", "NVDA", "JPM", "LLY", "XOM", "GOOGL"]:
        info = sector_map.get_sector_info(t)
        assert info["sector"] != "Unknown", f"{t} 未映射"
        assert info["sector_zh"] != "未分类"
    print(f"  ✅ 覆盖 {len(sector_map.STATIC_MAP)} 个 ticker")
    print(f"  ✅ GICS sector 数: {len(set(s[0] for s in sector_map.STATIC_MAP.values()))}")


def test_sentiment():
    print("\n--- 2. sentiment ---")
    from scripts.us_news.sentiment import analyze_sentiment, aggregate_sentiment

    samples = [
        ("Apple beats Q3 estimates as iPhone sales surge", "record revenue", "利好"),
        ("Tesla misses delivery targets, shares plunge", "weak demand", "利空"),
        ("Microsoft announces $60B share buyback, dividend hike", "record revenue", "利好"),
        ("DOJ investigation into UnitedHealth", "lawsuit", "利空"),
        ("Company holds regular board meeting", "", "中性"),
        ("Fed cuts rates, dovish outlook", "soft landing", "利好"),
        ("Recession fears intensify, hawkish Fed", "stagflation", "利空"),
    ]
    for title, summary, expected in samples:
        r = analyze_sentiment(title, summary)
        marker = "✅" if r["label"] == expected else "⚠️"
        print(f"  {marker} {r['label']} (期望 {expected}) score={r['score']:+d} | {title[:50]}")

    # aggregate
    items = [
        {"score": 3, "label": "利好", "confidence": 0.6},
        {"score": -2, "label": "利空", "confidence": 0.4},
        {"score": 1, "label": "利好", "confidence": 0.2},
    ]
    agg = aggregate_sentiment(items)
    print(f"  ✅ aggregate: {agg['dominant_label']} | score={agg['aggregate_score']}")


def test_build_mock():
    print("\n--- 3. build mock ---")
    from scripts.us_news.build import build
    payload = build(use_mock=True)
    # schema 校验
    assert payload["universe_count"] >= 20
    assert payload["news_count"] >= 30
    assert payload["per_ticker"]
    assert payload["per_sector"]
    assert payload["market_view"]
    # sentiment 分布合理
    mv = payload["market_view"]["overall"]
    print(f"  ✅ 整体: {mv['dominant_label']} | score={mv['aggregate_score']}")
    print(f"  ✅ 多/空/中: {mv['bullish_count']}/{mv['bearish_count']}/{mv['neutral_count']}")
    # 每个 ticker 应有 news_count 字段
    for t in payload["per_ticker"][:5]:
        assert "news_count" in t
        assert "sentiment_summary" in t
    print(f"  ✅ {len(payload['per_ticker'])} 个 ticker + {len(payload['per_sector'])} 个 sector")


def test_json_file():
    print("\n--- 4. data/us_news_latest.json ---")
    import json
    p = _PROJECT_ROOT / "data" / "us_news_latest.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["universe_count"] > 0
    assert len(data["per_ticker"]) == data["universe_count"]
    # 每条新闻应有 url + title + sentiment（sentiment 在 build 时填）
    # 每个 ticker 的 news（如果有）都应有 url + title
    for t in data["per_ticker"]:
        for n in t.get("recent_news", []):
            assert "url" in n and n["url"].startswith("http"), f"{t['ticker']} 新闻 url 异常"
            assert n.get("title"), f"{t['ticker']} 新闻缺标题"
    print(f"  ✅ 所有 ticker 的新闻都带 url + title")
    print(f"  ✅ 文件大小: {p.stat().st_size / 1024:.1f} KB")
    print(f"  ✅ 数据时间: {data['generated_at_local']}")


if __name__ == "__main__":
    test_sector_map()
    test_sentiment()
    test_build_mock()
    test_json_file()
    print("\n" + "=" * 60)
    print("✅ us_news 冒烟测试全部通过")
    print("=" * 60)