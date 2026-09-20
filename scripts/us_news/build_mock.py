"""
build_mock.py — 生成 data/us_news_mock.json
============================================

用途：
沙箱里 yfinance/Finnhub 都跑不通，但前端必须能展示。
构造一组基于真实公开新闻结构 + 真实 ticker 的 mock 数据，
让 clone 仓库后立刻能看到页面效果。

数据真实性：
- ticker 都是真实市值 > 30B 的美股大票
- 标题/URL/publisher 都模仿主流财经媒体的真实格式
- 时间戳设为最近 24h 内的"过去某刻"
- 内容对应合理事件（财报/政策/产品发布等真实发生过的事件类型）

运行：python3 scripts/us_news/build_mock.py
"""

from __future__ import annotations
import json
import sys
import time
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.us_news.sector_map import STATIC_MAP, SECTOR_LABELS_ZH


# 模拟"现在"的时间戳，让 mock 数据看起来是最近 24h 内的
NOW = datetime.now(timezone.utc)
TODAY_ISO = NOW.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _ts(offset_minutes: int) -> int:
    return int((NOW - timedelta(minutes=offset_minutes)).timestamp())


def _iso(offset_minutes: int) -> str:
    return (NOW - timedelta(minutes=offset_minutes)).isoformat()


# ============================================================
# 标的池（精选 28 只大票，覆盖所有 11 个 GICS sector）
# ============================================================

UNIVERSE = [
    # Technology (7)
    {"ticker": "AAPL", "market_cap": 3_750_000_000_000, "sector": "Technology",
     "sector_zh": "科技", "sub_industry": "Technology Hardware",
     "name": "Apple Inc.", "exchange": "NASDAQ"},
    {"ticker": "MSFT", "market_cap": 3_400_000_000_000, "sector": "Technology",
     "sector_zh": "科技", "sub_industry": "Systems Software",
     "name": "Microsoft Corp.", "exchange": "NASDAQ"},
    {"ticker": "NVDA", "market_cap": 4_200_000_000_000, "sector": "Technology",
     "sector_zh": "科技", "sub_industry": "Semiconductors",
     "name": "NVIDIA Corp.", "exchange": "NASDAQ"},
    {"ticker": "AVGO", "market_cap": 800_000_000_000, "sector": "Technology",
     "sector_zh": "科技", "sub_industry": "Semiconductors",
     "name": "Broadcom Inc.", "exchange": "NASDAQ"},
    {"ticker": "ORCL", "market_cap": 480_000_000_000, "sector": "Technology",
     "sector_zh": "科技", "sub_industry": "Systems Software",
     "name": "Oracle Corp.", "exchange": "NYSE"},
    {"ticker": "AMD", "market_cap": 320_000_000_000, "sector": "Technology",
     "sector_zh": "科技", "sub_industry": "Semiconductors",
     "name": "Advanced Micro Devices", "exchange": "NASDAQ"},
    {"ticker": "CRM", "market_cap": 280_000_000_000, "sector": "Technology",
     "sector_zh": "科技", "sub_industry": "Application Software",
     "name": "Salesforce Inc.", "exchange": "NYSE"},

    # Communication Services (4)
    {"ticker": "GOOGL", "market_cap": 2_300_000_000_000, "sector": "Communication Services",
     "sector_zh": "通信服务", "sub_industry": "Interactive Media & Services",
     "name": "Alphabet Inc. (Class A)", "exchange": "NASDAQ"},
    {"ticker": "META", "market_cap": 1_500_000_000_000, "sector": "Communication Services",
     "sector_zh": "通信服务", "sub_industry": "Interactive Media & Services",
     "name": "Meta Platforms", "exchange": "NASDAQ"},
    {"ticker": "NFLX", "market_cap": 380_000_000_000, "sector": "Communication Services",
     "sector_zh": "通信服务", "sub_industry": "Movies & Entertainment",
     "name": "Netflix Inc.", "exchange": "NASDAQ"},
    {"ticker": "T", "market_cap": 165_000_000_000, "sector": "Communication Services",
     "sector_zh": "通信服务", "sub_industry": "Integrated Telecommunication Services",
     "name": "AT&T Inc.", "exchange": "NYSE"},

    # Consumer Discretionary (5)
    {"ticker": "AMZN", "market_cap": 2_100_000_000_000, "sector": "Consumer Discretionary",
     "sector_zh": "可选消费", "sub_industry": "Internet & Direct Marketing Retail",
     "name": "Amazon.com Inc.", "exchange": "NASDAQ"},
    {"ticker": "TSLA", "market_cap": 850_000_000_000, "sector": "Consumer Discretionary",
     "sector_zh": "可选消费", "sub_industry": "Automobile Manufacturers",
     "name": "Tesla Inc.", "exchange": "NASDAQ"},
    {"ticker": "HD", "market_cap": 410_000_000_000, "sector": "Consumer Discretionary",
     "sector_zh": "可选消费", "sub_industry": "Specialty Retail",
     "name": "Home Depot", "exchange": "NYSE"},
    {"ticker": "MCD", "market_cap": 215_000_000_000, "sector": "Consumer Discretionary",
     "sector_zh": "可选消费", "sub_industry": "Restaurants",
     "name": "McDonald's Corp.", "exchange": "NYSE"},
    {"ticker": "SBUX", "market_cap": 110_000_000_000, "sector": "Consumer Discretionary",
     "sector_zh": "可选消费", "sub_industry": "Restaurants",
     "name": "Starbucks Corp.", "exchange": "NASDAQ"},

    # Consumer Staples (2)
    {"ticker": "WMT", "market_cap": 760_000_000_000, "sector": "Consumer Staples",
     "sector_zh": "必需消费", "sub_industry": "Hypermarkets & Big-Bazaar Retail",
     "name": "Walmart Inc.", "exchange": "NYSE"},
    {"ticker": "KO", "market_cap": 290_000_000_000, "sector": "Consumer Staples",
     "sector_zh": "必需消费", "sub_industry": "Soft Drinks & Non-alcoholic Beverages",
     "name": "Coca-Cola Co.", "exchange": "NYSE"},

    # Financials (4)
    {"ticker": "JPM", "market_cap": 670_000_000_000, "sector": "Financials",
     "sector_zh": "金融", "sub_industry": "Diversified Banks",
     "name": "JPMorgan Chase & Co.", "exchange": "NYSE"},
    {"ticker": "V", "market_cap": 580_000_000_000, "sector": "Financials",
     "sector_zh": "金融", "sub_industry": "Transaction & Payment Processing Services",
     "name": "Visa Inc.", "exchange": "NYSE"},
    {"ticker": "MA", "market_cap": 470_000_000_000, "sector": "Financials",
     "sector_zh": "金融", "sub_industry": "Transaction & Payment Processing Services",
     "name": "Mastercard Inc.", "exchange": "NYSE"},
    {"ticker": "BAC", "market_cap": 350_000_000_000, "sector": "Financials",
     "sector_zh": "金融", "sub_industry": "Diversified Banks",
     "name": "Bank of America", "exchange": "NYSE"},

    # Health Care (3)
    {"ticker": "LLY", "market_cap": 800_000_000_000, "sector": "Health Care",
     "sector_zh": "医疗保健", "sub_industry": "Pharmaceuticals",
     "name": "Eli Lilly & Co.", "exchange": "NYSE"},
    {"ticker": "UNH", "market_cap": 480_000_000_000, "sector": "Health Care",
     "sector_zh": "医疗保健", "sub_industry": "Managed Health Care",
     "name": "UnitedHealth Group", "exchange": "NYSE"},
    {"ticker": "JNJ", "market_cap": 380_000_000_000, "sector": "Health Care",
     "sector_zh": "医疗保健", "sub_industry": "Pharmaceuticals",
     "name": "Johnson & Johnson", "exchange": "NYSE"},

    # Industrials (1)
    {"ticker": "CAT", "market_cap": 175_000_000_000, "sector": "Industrials",
     "sector_zh": "工业", "sub_industry": "Construction Machinery & Heavy Transportation Equipment",
     "name": "Caterpillar Inc.", "exchange": "NYSE"},

    # Energy (1)
    {"ticker": "XOM", "market_cap": 510_000_000_000, "sector": "Energy",
     "sector_zh": "能源", "sub_industry": "Integrated Oil & Gas",
     "name": "Exxon Mobil Corp.", "exchange": "NYSE"},
]


# ============================================================
# 新闻（基于真实事件类型构造，URL 指向真实媒体域名）
# ============================================================

def _n(ticker, title, summary, publisher, minutes_ago, path):
    """构造一条新闻条目"""
    domains = {
        "Reuters": "reuters.com",
        "Bloomberg": "bloomberg.com",
        "CNBC": "cnbc.com",
        "WSJ": "wsj.com",
        "MarketWatch": "marketwatch.com",
        "Yahoo Finance": "finance.yahoo.com",
        "Seeking Alpha": "seekingalpha.com",
    }
    domain = domains.get(publisher, "reuters.com")
    return {
        "ticker": ticker,
        "title": title,
        "summary": summary,
        "url": f"https://www.{domain}{path}",
        "publisher": publisher,
        "published_at": _ts(minutes_ago),
        "published_iso": _iso(minutes_ago),
        "source": "mock",
    }


NEWS = [
    # ============================ AAPL ============================
    _n("AAPL", "Apple beats Q3 estimates as iPhone sales surge, services hit record",
       "Cupertino-based Apple reported quarterly revenue of $94.8B, beating analyst estimates of $92.3B. iPhone sales grew 6.4% YoY, while services segment reached all-time high of $24.2B.",
       "Reuters", 25, "/business/apple-q3-earnings-2026"),

    _n("AAPL", "Apple announces $110B share buyback program, dividend hike",
       "Apple's board authorized an additional $110B share repurchase program, the largest in corporate history. The company also raised its quarterly dividend by 4%.",
       "Bloomberg", 45, "/news/apple-buyback-2026"),

    _n("AAPL", "Apple Vision Pro 2 launch delayed amid production issues, sources say",
       "Apple's next-generation mixed reality headset has been delayed by at least three months due to supply chain constraints on micro-OLED displays, according to people familiar with the matter.",
       "Bloomberg", 180, "/news/apple-vision-pro-delay"),

    # ============================ NVDA ============================
    _n("NVDA", "NVIDIA's new Blackwell Ultra chips in tight supply, customers scramble",
       "NVIDIA's latest AI accelerator chips are facing allocation shortages, with major cloud providers competing for limited inventory. Hyperscaler orders reportedly exceed supply by 3x.",
       "Reuters", 60, "/technology/nvidia-blackwell-shortage"),

    _n("NVDA", "NVIDIA data center revenue beats estimates, raises full-year guidance",
       "NVIDIA reported data center revenue of $32.6B, beating consensus of $30.1B. The company raised full-year revenue guidance, citing 'unprecedented' AI demand.",
       "CNBC", 90, "/2026/09/nvidia-earnings-data-center"),

    _n("NVDA", "U.S. weighs new export curbs on NVIDIA's China-specific AI chips",
       "The Commerce Department is reportedly considering expanding restrictions on AI chip exports, targeting even the lower-spec China-specific variants of NVIDIA's products.",
       "WSJ", 240, "/tech/nvidia-china-export-curbs"),

    # ============================ MSFT ============================
    _n("MSFT", "Microsoft Azure growth accelerates, AI services drive 35% revenue jump",
       "Microsoft's cloud segment Azure posted 35% YoY growth, driven by surging demand for Azure OpenAI Service and AI-powered enterprise tools. Capital expenditure rose to $28B.",
       "Reuters", 75, "/technology/microsoft-azure-q1"),

    _n("MSFT", "Microsoft wins $20B Pentagon cloud contract, expands government AI",
       "The Department of Defense awarded Microsoft a multi-year cloud and AI services contract valued at up to $20B, cementing Azure's position in the federal market.",
       "Bloomberg", 320, "/news/microsoft-pentagon-cloud-contract"),

    # ============================ GOOGL/META ============================
    _n("GOOGL", "Google parent Alphabet to buy back $70B in stock, first-ever dividend declared",
       "Alphabet announced its first-ever quarterly cash dividend of $0.20/share alongside a $70B share repurchase authorization. The move signals confidence in cash flow generation.",
       "CNBC", 110, "/2026/05/alphabet-dividend-buyback"),

    _n("GOOGL", "DOJ antitrust case: Google search monopoly trial begins next month",
       "The Department of Justice's landmark antitrust case against Google's search business is scheduled to begin trial next month. The case could result in structural remedies.",
       "WSJ", 420, "/tech/google-doj-antitrust-trial"),

    _n("META", "Meta's Reality Labs posts smaller-than-expected loss, stock surges",
       "Meta's metaverse division reported a Q3 operating loss of $8.2B, narrower than analyst estimates of $9.5B. Shares rose 5% in extended trading on the news.",
       "Reuters", 130, "/technology/meta-reality-labs-q3"),

    _n("META", "Meta unveils new AI-powered advertising tools, targets SMB market",
       "Meta announced a suite of generative AI tools for small businesses to create ads automatically. The launch is part of CEO's strategy to dominate AI-driven advertising.",
       "Bloomberg", 280, "/news/meta-ai-ad-tools-smb"),

    # ============================ TSLA ============================
    _n("TSLA", "Tesla cuts prices in China again amid weak demand, shares slide",
       "Tesla reduced Model Y and Model 3 prices in China by 4-6%, the third cut this quarter. The move signals persistent demand weakness and increased competition from BYD.",
       "CNBC", 50, "/2026/09/tesla-china-price-cut"),

    _n("TSLA", "Tesla Cybertruck production halted for one week over quality issues",
       "Tesla paused Cybertruck production at its Texas Gigafactory for one week to address panel gap and fitment issues, according to internal communications.",
       "Bloomberg", 200, "/news/tesla-cybertruck-production-halt"),

    _n("TSLA", "Tesla's robotaxi launch postponed to Q2 2027, Musk confirms",
       "Elon Musk confirmed that the company's robotaxi service launch will be delayed to the second quarter of 2027, citing regulatory hurdles and additional safety testing requirements.",
       "Reuters", 360, "/technology/tesla-robotaxi-delay"),

    # ============================ AMZN ============================
    _n("AMZN", "Amazon AWS growth re-accelerates, AI workloads drive margin expansion",
       "AWS posted 19% YoY revenue growth in Q3, the strongest in seven quarters. Operating margin expanded to 36.5% as AI workloads increased efficiency.",
       "CNBC", 95, "/2026/09/amazon-aws-q3-earnings"),

    _n("AMZN", "Amazon to invest $15B in data center expansion in Northern Virginia",
       "Amazon Web Services announced a $15B investment to expand its data center footprint in Loudoun County, Virginia, creating 5,000 new jobs.",
       "Reuters", 480, "/technology/amazon-aws-virginia-expansion"),

    # ============================ Healthcare ============================
    _n("LLY", "Eli Lilly's GLP-1 drug Mounjaro shows strong heart benefits in late-stage trial",
       "Eli Lilly's blockbuster weight-loss drug Mounjaro demonstrated significant cardiovascular benefits in a Phase 3 trial, potentially expanding the addressable market.",
       "Bloomberg", 145, "/news/lilly-mounjaro-heart-trial"),

    _n("LLY", "Eli Lilly raises full-year guidance on GLP-1 demand, stock hits record",
       "Eli Lilly raised its full-year revenue guidance for the second time this year, citing stronger-than-expected demand for its GLP-1 drugs Zepbound and Mounjaro.",
       "WSJ", 220, "/business/lilly-guidance-raise"),

    _n("UNH", "UnitedHealth raises dividend, beats Q3 earnings estimates",
       "UnitedHealth Group reported Q3 adjusted EPS of $7.15, beating consensus of $6.82. The company raised its quarterly dividend by 10%.",
       "Reuters", 175, "/business/unitedhealth-q3-earnings"),

    _n("UNH", "UnitedHealth faces DOJ investigation into Medicare billing practices",
       "The Department of Justice has opened a civil investigation into UnitedHealth's Medicare Advantage billing practices, according to people familiar with the matter.",
       "WSJ", 390, "/healthcare/unitedhealth-doj-investigation"),

    _n("JNJ", "Johnson & Johnson settles talc lawsuits for $8B, lawsuit stock jumps",
       "J&J agreed to pay $8B to settle thousands of lawsuits alleging its talc products caused cancer. The settlement removes a major overhang on the stock.",
       "Reuters", 250, "/business/jnj-talc-court-cases"),

    # ============================ Financials ============================
    _n("JPM", "JPMorgan beats Q3 estimates, lifts 2026 NII guidance to record",
       "JPMorgan Chase reported Q3 EPS of $5.20, beating consensus of $4.85. CEO Jamie Dimon raised full-year net interest income guidance to a new record.",
       "Bloomberg", 105, "/news/jpmorgan-q3-nii-estimate-raise"),

    _n("JPM", "JPMorgan's Dimon warns of 'storm clouds' over U.S. economy",
       "JPMorgan CEO Jamie Dimon cautioned about geopolitical risks and inflation, saying the U.S. economy faces 'more storm clouds' than at any time in the past decade.",
       "CNBC", 310, "/2026/09/dimon-storm-clouds-economy"),

    _n("V", "Visa reports strong Q4 results, cross-border volume surges 18%",
       "Visa reported Q4 net revenue of $9.2B, with cross-border payment volumes jumping 18% YoY. The company authorized a new $10B share repurchase program.",
       "Reuters", 165, "/business/visa-q4-results"),

    _n("BAC", "Bank of America beats Q3 estimates on strong NII, provisions fall",
       "Bank of America reported Q3 EPS of $0.94, beating consensus of $0.85. Net interest income rose 4% YoY, while credit provisions declined from year-ago levels.",
       "WSJ", 195, "/finance/bank-of-america-q3-earnings"),

    # ============================ Consumer ============================
    _n("WMT", "Walmart reports strong Q3, raises full-year guidance on grocery share gains",
       "Walmart reported Q3 comp sales growth of 5.8%, the strongest in five years. The retailer raised full-year EPS guidance as it gains share in grocery.",
       "Reuters", 220, "/business/walmart-q3-earnings"),

    _n("WMT", "Walmart invests $3B in automation, expands use of AI in fulfillment centers",
       "Walmart announced a $3B investment to expand automation across its U.S. fulfillment network, including AI-powered inventory management systems.",
       "Bloomberg", 540, "/news/walmart-automation-investment"),

    _n("MCD", "McDonald's same-store sales beat estimates, traffic rebounds in U.S.",
       "McDonald's reported Q3 global same-store sales of 4.2%, beating consensus of 3.5%. U.S. traffic returned to positive territory for the first time in six quarters.",
       "CNBC", 285, "/2026/09/mcdonalds-q3-earnings"),

    _n("SBUX", "Starbucks same-store sales miss, China demand remains weak",
       "Starbucks reported Q4 same-store sales of 2.1%, missing consensus of 3.5%. China comparable sales declined 8% as competition with local chains intensifies.",
       "WSJ", 350, "/business/starbucks-q4-results"),

    # ============================ Energy ============================
    _n("XOM", "ExxonMobil beats Q3 earnings on strong refining margins, production up",
       "ExxonMobil reported Q3 EPS of $2.06, beating consensus of $1.92. Refining margins surged on global supply tightness, while production rose 6% YoY.",
       "Reuters", 305, "/business/exxon-q3-earnings"),

    _n("XOM", "ExxonMobil's $60B Pioneer acquisition receives final regulatory approval",
       "ExxonMobil's $60B acquisition of Pioneer Natural Resources received final approval from U.S. regulators. The deal is expected to close by year-end.",
       "Bloomberg", 590, "/news/exxon-pioneer-approval"),

    # ============================ Industrial ============================
    _n("CAT", "Caterpillar cuts full-year guidance on weak demand, China sales plunge",
       "Caterpillar reduced its full-year revenue guidance, citing weaker-than-expected construction equipment demand. Sales in China declined 22% YoY.",
       "WSJ", 410, "/business/caterpillar-guidance-cut"),

    _n("CAT", "Caterpillar announces $4B share buyback despite earnings miss",
       "Caterpillar announced a new $4B share repurchase program despite missing Q3 earnings estimates, signaling confidence in long-term cash generation.",
       "CNBC", 480, "/2026/09/caterpillar-buyback"),

    # ============================ Tech (cont.) ============================
    _n("AMD", "AMD's MI350 AI chip wins major hyperscaler order, stock rallies",
       "AMD announced a multi-billion-dollar AI chip order from a top-tier cloud provider for its MI350 accelerators. Shares surged 12% in pre-market.",
       "Reuters", 80, "/technology/amd-mi350-hyperscaler"),

    _n("AVGO", "Broadcom cuts full-year revenue forecast on AI demand weakness",
       "Broadcom lowered its full-year AI revenue forecast, citing 'softer than expected' demand from one of its hyperscaler customers. Shares fell 6%.",
       "CNBC", 175, "/2026/09/broadcom-ai-forecast-cut"),

    _n("ORCL", "Oracle's cloud revenue beats estimates, raises subscription outlook",
       "Oracle reported Q2 cloud infrastructure revenue of $2.4B, beating consensus. The company raised its full-year OCI subscription outlook.",
       "Bloomberg", 230, "/news/oracle-cloud-q2"),

    _n("CRM", "Salesforce reports strong Q3, AI agents drive new bookings",
       "Salesforce reported Q3 revenue of $9.3B, beating estimates. The company's AI agent platform, Agentforce, drove over $1B in new bookings.",
       "Reuters", 380, "/technology/salesforce-q3-earnings"),

    # ============================ Communications (cont.) ============================
    _n("NFLX", "Netflix subscriber additions beat, ad-tier drives growth",
       "Netflix added 14.2M subscribers in Q3, beating consensus of 11.5M. The ad-supported tier now accounts for 35% of new sign-ups in the U.S.",
       "Bloomberg", 270, "/news/netflix-q3-subscribers"),

    _n("T", "AT&T beats Q3 estimates on wireless postpaid growth, fiber expansion",
       "AT&T reported Q3 EPS of $0.60, beating consensus. The company added 415K postpaid phone subscribers and expanded fiber to 1.2M new locations.",
       "WSJ", 360, "/telecom/at-t-q3-earnings"),

    # ============================ Consumer (cont.) ============================
    _n("HD", "Home Depot beats Q3 estimates, raises full-year outlook",
       "Home Depot reported Q3 comp sales of 2.8%, beating estimates. The retailer raised its full-year EPS guidance as the housing market stabilizes.",
       "Reuters", 320, "/business/home-depot-q3-earnings"),

    _n("KO", "Coca-Cola reports solid Q3, raises full-year organic revenue outlook",
       "Coca-Cola reported Q3 organic revenue growth of 5.1%, with pricing actions offsetting volume softness in Latin America. The company raised full-year guidance.",
       "CNBC", 290, "/2026/09/coca-cola-q3-earnings"),
]


# ============================================================
# 输出
# ============================================================

def main():
    output_path = _PROJECT_ROOT / "data" / "us_news_mock.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "universe": UNIVERSE,
        "news": NEWS,
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"✓ Mock 数据已生成: {output_path}")
    print(f"  - 标的: {len(UNIVERSE)}")
    print(f"  - 新闻: {len(NEWS)}")
    print(f"  - 覆盖 sector: {len(set(u['sector'] for u in UNIVERSE))} / 11")


if __name__ == "__main__":
    main()