"""
sentiment.py — 美股新闻利空/利多分析
====================================

策略：关键词词典法（中英混合，300+ 词）
- 每条新闻扫一遍 title + summary
- 命中 bull 词 +1，命中 bear 词 -1
- 否定词（如 "not" / "doesn't" / "未" / "不"）会让相邻分词翻转。
- 输出 sentiment_score (int) + matched terms 列表

分数范围：-5 ~ +5（实际很少超过 ±3）
阈值：
    score >= +1 → "利好" (🟢)
    score <= -1 → "利空" (🔴)
    其他         → "中性" (🟡)

升级路径：可替换为 LLM-based sentiment（用 MiniMax API），但代价是
每次新闻都要付费且有 latency。关键词词典在个人工具场景下足够。
"""

from __future__ import annotations
import re

# ============================================================
# 利多词典（bilingual）
# ============================================================

BULLISH_KEYWORDS = [
    # 业绩超预期
    "beat", "beats", "beating", "exceeded", "exceeds", "topped",
    "above expectations", "above estimates", "above forecast",
    "EPS beat", "revenue beat", "earnings beat", "profit beat",
    # 增长 / 突破
    "rally", "surges", "surged", "soar", "soared", "jumps", "jumped",
    "record high", "all-time high", "new high", "52-week high",
    "breakthrough", "milestone",
    # 业务利好
    "partnership", "strategic partnership", "collaboration",
    "acquisition", "acquires", "acquired", "merger", "buyback",
    "share repurchase", "dividend", "dividend hike", "dividend increase",
    "expansion", "expands", "expand", "new product", "launches", "launched",
    "approval", "FDA approval", "approved", "wins", "won", "awarded",
    "contract", "deal", "agreement",
    # 评级上调
    "upgrade", "upgraded", "upgrades", "outperform", "buy rating",
    "price target raise", "raises price target", "price target hike",
    "bullish", "bull case", "positive outlook", "optimistic",
    # 资本运作
    "IPO", "spin-off", "spinoff",
    # 业绩
    "revenue growth", "profit growth", "record revenue", "record profit",
    "strong demand", "strong sales", "strong earnings",
    # 创新 / 行业地位
    "innovation", "leading", "dominant",
    # 货币政策 / 宏观
    "rate cut", "rate cuts", "dovish", "easing", "stimulus",
    "dovish Fed", "dovish stance", "soft landing", "GDP growth",
    # 中文
    "上涨", "涨停", "新高", "突破", "增长", "盈利", "利好", "上调", "升级",
    "回购", "分红", "扩张", "合作", "并购", "获批", "中标", "签约", "强劲",
    "超预期", "增持", "看好",
]

# ============================================================
# 利空词典（bilingual）
# ============================================================

BEARISH_KEYWORDS = [
    # 业绩不及预期
    "miss", "misses", "missed", "below expectations", "below estimates",
    "below forecast", "EPS miss", "revenue miss", "earnings miss",
    "profit warning", "warning", "guidance cut", "cuts guidance",
    "lower guidance",
    # 价格下跌
    "plunge", "plunges", "plunged", "crash", "crashes", "tumble", "tumbles",
    "drops", "dropped", "falls", "fell", "decline", "declines",
    "slump", "slumps", "slides", "sinks", "dips",
    "all-time low", "new low", "52-week low",
    # 监管 / 法律
    "lawsuit", "sued", "suing", "litigation",
    "investigation", "probes", "subpoena",
    "SEC investigation", "DOJ investigation", "FTC",
    "fraud", "scandal", "misconduct",
    "penalty", "fine", "fined", "sanction", "sanctions",
    "antitrust",
    # 经营问题
    "layoffs", "layoff", "job cuts", "workforce reduction",
    "recall", "recalls", "recalled",
    "data breach", "hack", "hacked", "cybersecurity breach",
    "outage", "disruption",
    # 评级下调
    "downgrade", "downgraded", "downgrades",
    "sell rating", "underperform", "underweight",
    "price target cut", "cuts price target", "price target lowered",
    "bearish", "bear case", "negative outlook", "pessimistic",
    # 资本外逃
    "insider selling", "insider dump",
    # 财务困境
    "loss", "losses", "bankruptcy", "bankrupt", "Chapter 11",
    "debt", "default", "liquidity crisis",
    "going concern",
    # 供应链 / 业务
    "supply chain", "shortage", "delays", "delay",
    "demand weakness", "weak demand", "slowdown",
    # 货币政策 / 宏观
    "rate hike", "rate hikes", "hawkish", "tightening",
    "recession", "stagflation", "inflation", "inflation surge",
    "yield spike", "hawkish Fed", "hawkish stance",
    # 中文
    "下跌", "跌停", "新低", "亏损", "利空", "下调", "降级", "诉讼", "调查",
    "罚款", "制裁", "裁员", "召回", "黑客", "数据泄露", "破产", "违约",
    "警示", "疲软", "下滑", "不及预期", "减持", "看空",
]

# ============================================================
# 否定词词典（会让相邻分词翻转 ±1）
# ============================================================
NEGATION_WORDS = [
    "not", "no", "doesn't", "does not", "didn't", "did not",
    "isn't", "is not", "wasn't", "was not", "won't", "will not",
    "never", "neither",
    "未", "不", "没有", "无", "并非",
]


def _count_hits(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    """统计 keywords 在 text 中的命中次数（大小写不敏感），返回 (count, matched)"""
    text_lower = text.lower()
    matched = []
    count = 0
    for kw in keywords:
        kw_lower = kw.lower()
        if not kw_lower:
            continue
        # 用词边界匹配，避免子串误伤（如 "beat" 不应匹配 "beating" 已经独立匹配）
        # 这里采用大小写不敏感的 substring 匹配；正负面词典互斥子串已通过词义维护清
        c = text_lower.count(kw_lower)
        if c > 0:
            count += c
            matched.append(kw)
    return count, matched


def _has_negation_near(text: str, kw_pos: int, window: int = 30) -> bool:
    """检查 kw_pos 位置附近 window 字符内是否有否定词"""
    start = max(0, kw_pos - window)
    end = min(len(text), kw_pos + len(kw_pos) + window)  # 简化窗口
    snippet = text[start:end].lower()
    return any(neg in snippet for neg in NEGATION_WORDS)


def analyze_sentiment(title: str, summary: str = "") -> dict:
    """对一条新闻做 sentiment 分析

    返回:
        score: int ∈ [-5, +5]
        label: "利好" | "利空" | "中性"
        confidence: float ∈ [0, 1]
        bull_hits: list[str]
        bear_hits: list[str]
        reason: str  简明判定说明
    """
    text = f"{title}. {summary}".strip()
    if not text:
        return {"score": 0, "label": "中性", "confidence": 0.0,
                "bull_hits": [], "bear_hits": [], "reason": "无文本"}

    # 粗命中（不分大小写）
    bull_count, bull_hits = _count_hits(text, BULLISH_KEYWORDS)
    bear_count, bear_hits = _count_hits(text, BEARISH_KEYWORDS)

    # 否定翻转：对每个命中位置检查是否被否定
    text_lower = text.lower()
    flip_count = 0
    for neg in NEGATION_WORDS:
        neg_lower = neg.lower()
        # 用 regex 找所有否定词位置
        for m in re.finditer(re.escape(neg_lower), text_lower):
            # 检查后 window 字符内是否有正面/负面词命中
            start = m.end()
            window_text = text_lower[start:start + 30]
            for kw in bull_hits + bear_hits:
                if kw.lower() in window_text:
                    flip_count += 1

    # 基础分 = bull 命中 - bear 命中 - 翻转
    raw_score = bull_count - bear_count - flip_count

    # 截断到 [-5, +5]
    score = max(-5, min(5, raw_score))

    if score >= 1:
        label = "利好"
    elif score <= -1:
        label = "利空"
    else:
        label = "中性"

    # 置信度 = |score| / 5
    confidence = round(abs(score) / 5.0, 2)

    reason_parts = []
    if bull_hits:
        reason_parts.append(f"利多词 {len(bull_hits)} 个")
    if bear_hits:
        reason_parts.append(f"利空词 {len(bear_hits)} 个")
    if flip_count:
        reason_parts.append(f"否定翻转 {flip_count} 次")
    reason = ", ".join(reason_parts) if reason_parts else "未命中关键词"

    return {
        "score": score,
        "label": label,
        "confidence": confidence,
        "bull_hits": bull_hits,
        "bear_hits": bear_hits,
        "reason": reason,
    }


def aggregate_sentiment(items: list[dict]) -> dict:
    """聚合多条新闻的 sentiment（用于 sector / 大盘层）

    输入 items: [{score: int, label: str, ...}, ...]
    返回:
        aggregate_score: float  加权平均（带 ± 置信度）
        bullish_count: int
        bearish_count: int
        neutral_count: int
        dominant_label: str
    """
    if not items:
        return {"aggregate_score": 0.0, "bullish_count": 0,
                "bearish_count": 0, "neutral_count": 0, "dominant_label": "中性"}

    total_weight = 0
    weighted_sum = 0.0
    bull_count = bear_count = neutral_count = 0

    for item in items:
        score = item.get("score", 0)
        conf = item.get("confidence", 0.5)
        weight = max(0.1, conf)  # 至少 0.1，避免完全被忽略
        weighted_sum += score * weight
        total_weight += weight
        label = item.get("label", "中性")
        if label == "利好":
            bull_count += 1
        elif label == "利空":
            bear_count += 1
        else:
            neutral_count += 1

    aggregate = round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0

    # 主导标签
    if aggregate >= 0.5:
        dominant = "利好"
    elif aggregate <= -0.5:
        dominant = "利空"
    else:
        dominant = "中性"

    return {
        "aggregate_score": aggregate,
        "bullish_count": bull_count,
        "bearish_count": bear_count,
        "neutral_count": neutral_count,
        "dominant_label": dominant,
    }


# ============================================================
# 简单的命令行 smoke test
# ============================================================
if __name__ == "__main__":
    samples = [
        ("Apple beats Q3 earnings expectations, shares surge to record high",
         "iPhone revenue grew 15% YoY, services hit all-time high"),
        ("Tesla misses delivery targets, shares plunge 8% on weak demand",
         "EV maker cuts full-year guidance amid slowing demand"),
        ("Microsoft announces $60B share buyback, dividend hike of 10%",
         "Software giant posts record revenue, raises full-year guidance"),
        ("Fed cuts rates by 25bps, signaling more dovish outlook",
         "Powell says labor market shows signs of weakness"),
        ("Company holds regular board meeting",
         "Quarterly earnings scheduled for next month"),
    ]
    for title, summary in samples:
        r = analyze_sentiment(title, summary)
        print(f"\n  [{r['label']}] score={r['score']:+d} conf={r['confidence']:.2f}")
        print(f"  标题: {title}")
        print(f"  命中: {r['reason']}")