"""
sector_map.py — 美股 Ticker → GICS Sector 映射
=================================================

数据来源：
1. 优先用 yfinance Ticker.info['sector']（实时，覆盖全，但要求联网）
2. 静态映射作为离线 fallback（覆盖市值 > 300B 美金的主要大票）

GICS 11 大类（Sector）：
  Technology, Financials, Health Care, Consumer Discretionary,
  Consumer Staples, Communication Services, Industrials,
  Energy, Utilities, Materials, Real Estate
"""

from __future__ import annotations
import json
import os
from pathlib import Path

# ============================================================
# 静态映射（市值 > 30B USD 的主要大票，约 110 只）
# 数据来源：参考 2026 年初 S&P 500 + NASDAQ 100 成分股权重
# ============================================================

# 格式：ticker -> (sector, sub_industry, 中文板块名)
STATIC_MAP = {
    # === Technology ===
    "AAPL": ("Technology", "Technology Hardware", "苹果/消费电子"),
    "MSFT": ("Technology", "Systems Software", "微软/操作系统"),
    "NVDA": ("Technology", "Semiconductors", "英伟达/GPU"),
    "AVGO": ("Technology", "Semiconductors", "博通/芯片"),
    "ORCL": ("Technology", "Systems Software", "甲骨文/数据库"),
    "CRM": ("Technology", "Application Software", "Salesforce/CRM"),
    "AMD": ("Technology", "Semiconductors", "AMD/CPU"),
    "QCOM": ("Technology", "Semiconductors", "高通/芯片"),
    "ADBE": ("Technology", "Application Software", "Adobe/创意软件"),
    "CSCO": ("Technology", "Communications Equipment", "思科/网络设备"),
    "TXN": ("Technology", "Semiconductors", "德州仪器/模拟芯片"),
    "MU": ("Technology", "Semiconductors", "美光/存储"),
    "LRCX": ("Technology", "Semiconductor Equipment", "拉姆研究/半导体设备"),
    "AMAT": ("Technology", "Semiconductor Equipment", "应用材料/半导体设备"),
    "IBM": ("Technology", "IT Services", "IBM/服务"),
    "INTU": ("Technology", "Application Software", "Intuit/财务软件"),
    "NOW": ("Technology", "Systems Software", "ServiceNow/ITSM"),
    "PANW": ("Technology", "Systems Software", "Palo Alto/网络安全"),
    "SHOP": ("Technology", "Application Software", "Shopify/电商SaaS"),
    "PLTR": ("Technology", "Application Software", "Palantir/大数据"),

    # === Communication Services ===
    "GOOGL": ("Communication Services", "Interactive Media & Services", "谷歌/搜索"),
    "GOOG": ("Communication Services", "Interactive Media & Services", "谷歌/搜索"),
    "META": ("Communication Services", "Interactive Media & Services", "Meta/社交"),
    "NFLX": ("Communication Services", "Movies & Entertainment", "奈飞/流媒体"),
    "DIS": ("Communication Services", "Movies & Entertainment", "迪士尼/影视"),
    "CMCSA": ("Communication Services", "Cable & Satellite", "康卡斯特/有线"),
    "T": ("Communication Services", "Integrated Telecommunication Services", "AT&T/电信"),
    "VZ": ("Communication Services", "Integrated Telecommunication Services", "Verizon/电信"),
    "TMUS": ("Communication Services", "Wireless Telecommunication Services", "T-Mobile/无线"),

    # === Consumer Discretionary ===
    "AMZN": ("Consumer Discretionary", "Internet & Direct Marketing Retail", "亚马逊/电商云"),
    "TSLA": ("Consumer Discretionary", "Automobile Manufacturers", "特斯拉/电动车"),
    "HD": ("Consumer Discretionary", "Specialty Retail", "家得宝/家居零售"),
    "MCD": ("Consumer Discretionary", "Restaurants", "麦当劳/餐饮"),
    "NKE": ("Consumer Discretionary", "Footwear", "耐克/运动"),
    "LOW": ("Consumer Discretionary", "Specialty Retail", "劳氏/家居零售"),
    "SBUX": ("Consumer Discretionary", "Restaurants", "星巴克/咖啡"),
    "TJX": ("Consumer Discretionary", "Specialty Retail", "TJX/折扣零售"),
    "BKNG": ("Consumer Discretionary", "Hotels Resorts & Cruise Lines", "Booking/旅游"),
    "CMG": ("Consumer Discretionary", "Restaurants", "Chipotle/快餐"),
    "ABNB": ("Consumer Discretionary", "Hotels Resorts & Cruise Lines", "爱彼迎/民宿"),
    "MAR": ("Consumer Discretionary", "Hotels Resorts & Cruise Lines", "万豪/酒店"),
    "F": ("Consumer Discretionary", "Automobile Manufacturers", "福特/汽车"),
    "GM": ("Consumer Discretionary", "Automobile Manufacturers", "通用/汽车"),

    # === Consumer Staples ===
    "WMT": ("Consumer Staples", "Hypermarkets & Big-Bazaar Retail", "沃尔玛/超市"),
    "COST": ("Consumer Staples", "Consumer Staples Distribution & Retail", "好市多/会员零售"),
    "PG": ("Consumer Staples", "Household Products", "宝洁/日化"),
    "KO": ("Consumer Staples", "Soft Drinks & Non-alcoholic Beverages", "可口可乐"),
    "PEP": ("Consumer Staples", "Soft Drinks & Non-alcoholic Beverages", "百事"),
    "PM": ("Consumer Staples", "Tobacco", "Philip Morris/烟草"),
    "MO": ("Consumer Staples", "Tobacco", "Altria/烟草"),
    "MDLZ": ("Consumer Staples", "Packaged Foods & Meats", "亿滋/食品"),

    # === Financials ===
    "BRK.B": ("Financials", "Multi-Sector Holdings", "伯克希尔/多元控股"),
    "JPM": ("Financials", "Diversified Banks", "摩根大通/银行"),
    "V": ("Financials", "Transaction & Payment Processing Services", "Visa/支付"),
    "MA": ("Financials", "Transaction & Payment Processing Services", "万事达/支付"),
    "BAC": ("Financials", "Diversified Banks", "美国银行"),
    "WFC": ("Financials", "Diversified Banks", "富国银行"),
    "MS": ("Financials", "Investment Banking & Brokerage", "摩根士丹利"),
    "GS": ("Financials", "Investment Banking & Brokerage", "高盛"),
    "AXP": ("Financials", "Consumer Finance", "运通/消费金融"),
    "BLK": ("Financials", "Asset Management & Custody Banks", "贝莱德/资管"),
    "SCHW": ("Financials", "Investment Banking & Brokerage", "嘉信理财"),
    "C": ("Financials", "Diversified Banks", "花旗银行"),
    "PGR": ("Financials", "Property & Casualty Insurance", "Progressive/保险"),
    "MMC": ("Financials", "Insurance Brokers", "Marsh McLennan/保险经纪"),
    "CB": ("Financials", "Property & Casualty Insurance", "Chubb/保险"),

    # === Health Care ===
    "LLY": ("Health Care", "Pharmaceuticals", "礼来/GLP-1"),
    "UNH": ("Health Care", "Managed Health Care", "联合健康/HMO"),
    "JNJ": ("Health Care", "Pharmaceuticals", "强生/医药"),
    "ABBV": ("Health Care", "Pharmaceuticals", "艾伯维/医药"),
    "MRK": ("Health Care", "Pharmaceuticals", "默沙东/医药"),
    "TMO": ("Health Care", "Life Sciences Tools & Services", "赛默飞/仪器"),
    "ABT": ("Health Care", "Health Care Equipment", "雅培/医疗器械"),
    "PFE": ("Health Care", "Pharmaceuticals", "辉瑞/医药"),
    "DHR": ("Health Care", "Life Sciences Tools & Services", "丹纳赫/仪器"),
    "AMGN": ("Health Care", "Biotechnology", "安进/生物科技"),
    "GILD": ("Health Care", "Biotechnology", "吉利德/生物科技"),
    "ISRG": ("Health Care", "Health Care Equipment", "直觉外科/手术机器人"),
    "VRTX": ("Health Care", "Biotechnology", "Vertex/生物科技"),
    "REGN": ("Health Care", "Biotechnology", "再生元/生物科技"),
    "ELV": ("Health Care", "Managed Health Care", "Elevance/HMO"),

    # === Industrials ===
    "GE": ("Industrials", "Industrial Conglomerates", "GE/工业集团"),
    "CAT": ("Industrials", "Construction Machinery & Heavy Transportation Equipment", "卡特彼勒/工程机械"),
    "BA": ("Industrials", "Aerospace & Defense", "波音/航空"),
    "HON": ("Industrials", "Industrial Conglomerates", "霍尼韦尔/工业"),
    "UNP": ("Industrials", "Rail Transportation", "联合太平洋/铁路"),
    "RTX": ("Industrials", "Aerospace & Defense", "RTX/防务"),
    "LMT": ("Industrials", "Aerospace & Defense", "洛克希德/防务"),
    "DE": ("Industrials", "Agricultural & Farm Machinery", "迪尔/农机"),
    "ADP": ("Industrials", "Human Resource & Employment Services", "ADP/HR"),
    "MMM": ("Industrials", "Industrial Conglomerates", "3M/工业"),
    "ETN": ("Industrials", "Electrical Components & Equipment", "伊顿/电气"),

    # === Energy ===
    "XOM": ("Energy", "Integrated Oil & Gas", "埃克森美孚/石油"),
    "CVX": ("Energy", "Integrated Oil & Gas", "雪佛龙/石油"),
    "COP": ("Energy", "Oil & Gas Exploration & Production", "康菲/油气"),

    # === Consumer Staples (extra) ===
    "LIN": ("Materials", "Industrial Gases", "林德/工业气体"),
    "APD": ("Materials", "Industrial Gases", "空气产品/工业气体"),

    # === Utilities ===
    "NEE": ("Utilities", "Electric Utilities", "NextEra/电力"),
    "SO": ("Utilities", "Electric Utilities", "南方公司/电力"),

    # === Real Estate ===
    "PLD": ("Real Estate", "Industrial REITs", "安博/物流地产"),
    "AMT": ("Real Estate", "Specialized REITs", "American Tower/通信铁塔"),
    "EQIX": ("Real Estate", "Specialized REITs", "Equinix/数据中心REIT"),
    "WELL": ("Real Estate", "Health Care REITs", "Welltower/医疗REIT"),
    "PSA": ("Real Estate", "Specialized REITs", "公共存储/REIT"),
    "O": ("Real Estate", "Specialized REITs", "Realty Income/净租REIT"),
    "SPG": ("Real Estate", "Retail REITs", "Simon Property/零售REIT"),

    # === 中国概念股（美股上市）===
    "BABA": ("Consumer Discretionary", "Internet & Direct Marketing Retail", "阿里/电商"),
    "JD": ("Consumer Discretionary", "Internet & Direct Marketing Retail", "京东/电商"),
    "PDD": ("Consumer Discretionary", "Internet & Direct Marketing Retail", "拼多多/电商"),
    "BIDU": ("Communication Services", "Interactive Media & Services", "百度/搜索"),
    "NTES": ("Communication Services", "Movies & Entertainment", "网易/游戏"),
    "TME": ("Communication Services", "Movies & Entertainment", "腾讯音乐"),
    "NOC": ("Industrials", "Aerospace & Defense", "诺格/防务"),
}


def sector_for(ticker: str) -> str:
    """获取 ticker 的 GICS sector。fallback 时返回 'Unknown'。"""
    info = STATIC_MAP.get(ticker.upper())
    return info[0] if info else "Unknown"


def sub_industry_for(ticker: str) -> str:
    info = STATIC_MAP.get(ticker.upper())
    return info[1] if info else "Unknown"


def sector_label_zh(ticker: str) -> str:
    info = STATIC_MAP.get(ticker.upper())
    return info[2] if info else "未分类"


# GICS 11 大类的中文标签
SECTOR_LABELS_ZH = {
    "Technology": "科技",
    "Communication Services": "通信服务",
    "Consumer Discretionary": "可选消费",
    "Consumer Staples": "必需消费",
    "Financials": "金融",
    "Health Care": "医疗保健",
    "Industrials": "工业",
    "Energy": "能源",
    "Materials": "原材料",
    "Utilities": "公用事业",
    "Real Estate": "房地产",
    "Unknown": "未分类",
}


def get_sector_info(ticker: str, live_sector: str | None = None) -> dict:
    """返回 ticker 的 sector 信息，live_sector 优先（来自 yfinance）"""
    t = ticker.upper()
    info = STATIC_MAP.get(t)
    if live_sector:
        return {
            "ticker": t,
            "sector": live_sector,
            "sub_industry": info[1] if info else "Unknown",
            "sector_zh": SECTOR_LABELS_ZH.get(live_sector, live_sector),
            "static_label_zh": info[2] if info else "",
            "source": "yfinance",
        }
    if info:
        return {
            "ticker": t,
            "sector": info[0],
            "sub_industry": info[1],
            "sector_zh": SECTOR_LABELS_ZH.get(info[0], info[0]),
            "static_label_zh": info[2],
            "source": "static",
        }
    return {
        "ticker": t,
        "sector": "Unknown",
        "sub_industry": "Unknown",
        "sector_zh": "未分类",
        "static_label_zh": "",
        "source": "none",
    }


def save_map_json(path: str | os.PathLike) -> None:
    """导出为 JSON（调试 / 文档用）"""
    out = {}
    for ticker, (sector, sub, label_zh) in STATIC_MAP.items():
        out[ticker] = {"sector": sector, "sub_industry": sub, "label_zh": label_zh}
    Path(path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    print(f"静态映射覆盖 ticker 数: {len(STATIC_MAP)}")
    print(f"覆盖 GICS sector 数: {len(set(info[0] for info in STATIC_MAP.values()))}")
    save_map_json("../../data/us_sector_map.json")
    print("已导出到 data/us_sector_map.json")