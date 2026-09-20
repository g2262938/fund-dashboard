#!/usr/bin/env python3
"""中报业绩预告预增标的扫描 — 生成 JSON 数据供 HTML 使用
包含智能原因分析：关键主题归集 + 核心摘要提炼
"""
import akshare as ak
import json
import re
import pandas as pd
from datetime import datetime

OUTPUT = "/home/ubuntu/.openclaw/workspace/reports/data/yjyg_gain_latest.json"

# ── 增长原因关键词归类 ─────────────────────────────────────────────
GAIN_THEMES = [
    ("猪周期反转/养殖改善",["生猪", "猪价", "猪周期", "养殖", "出栏", "成本下降", "养殖成本", "活禽"]),
    ("光伏/新能源景气回暖",["光伏", "太阳能", "新能源", "储能", "组件", "硅片", "电池片", "多晶硅"]),
    ("消费电子/半导体复苏",["消费电子", "半导体", "芯片", "面板", "存储器", "OLED", "手机", "AI终端"]),
    ("市场需求回暖",        ["需求回暖", "需求恢复", "市场需求", "订单增长", "订单增加", "销售增长", "收入增长", "市场复苏", "销量增长"]),
    ("产品结构优化/毛利率提升",["毛利率", "毛利", "产品结构", "高附加值", "高端产品", "产品升级", "售价提升"]),
    ("降本增效",           ["降本", "成本控制", "费用管控", "期间费用", "管理费用", "销售费用", "精益管理", "提质增效"]),
    ("业务扩张/新项目投产",["新项目", "投产", "产能释放", "产能爬坡", "产能提升", "业务扩张", "规模扩大", "新增产能"]),
    ("资产处置/非经收益", ["资产处置", "非经常性损益", "投资收益", "政府补助", "债务重组", "股权处置", "转让股权"]),
    ("行业景气度提升",     ["行业景气", "行业复苏", "行业回暖", "周期回升", "行业修复", "景气度"]),
    ("汇兑收益/财务优化",  ["汇兑收益", "汇率", "财务费用", "利息收入", "融资成本"]),
    ("政策受益",           ["政策", "政策支持", "补贴", "税收优惠", "政策红利"]),
    ("出海/海外业务增长",  ["海外", "出口", "境外", "国际化", "海外市场", "外销"]),
]

def detect_themes(text):
    themes = []
    for label, keywords in GAIN_THEMES:
        for kw in keywords:
            if kw in text:
                themes.append(label)
                break
    return themes[:4]

def summarize_reason(text):
    """从完整原因文本中提炼核心分句"""
    if not text:
        return ""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = re.split(r'([。；！？\n])', text)
    parts = []
    i = 0
    while i < len(sentences):
        seg = sentences[i].strip()
        if seg:
            if i + 1 < len(sentences) and sentences[i + 1].strip():
                parts.append(seg + sentences[i + 1])
            else:
                parts.append(seg)
        i += 2 if i + 1 < len(sentences) else 1
    KEY_KWS = [
        "主要因", "主要原因", "所致", "导致", "受益", "由于",
        "毛利", "净利润", "收入", "成本", "售价", "销量",
        "订单", "销售", "市场", "需求", "产能", "项目",
        "降本", "增效", "结构", "产品", "海外",
    ]
    keep = []
    for p in parts:
        p = p.strip()
        if len(p) < 8:
            continue
        for kw_pat in KEY_KWS:
            if kw_pat in p:
                keep.append(p)
                break
    if not keep:
        keep = [p.strip() for p in parts if len(p.strip()) >= 10][:3]
    result = "；".join(keep)
    if len(result) > 400:
        result = result[:400] + "…"
    return result

# ── 1. 取数据 ──────────────────────────────────────────────────────
print("正在获取 2026年中报业绩预告...")
try:
    df_raw = ak.stock_yjyg_em(date="20260630")
    print(f"  原始记录: {len(df_raw)} 条")
except Exception as e:
    print(f"  获取失败: {e}")
    exit(1)

# ── 2. 过滤预增类型（合并归属净利润 + 扣非净利润，按股票代码去重） ─
gain_types = ["预增", "扭亏", "首盈", "续盈", "略增"]
df_gain = df_raw[
    df_raw["预告类型"].isin(gain_types) &
    df_raw["预测指标"].str.contains(
        "归属于上市公司股东的净利润|扣除非经常性损益后的净利润", na=False
    )
].copy()
# 同一股票优先保留"归属净利润"那条
df_gain = df_gain.drop_duplicates(subset="股票代码", keep="first")
print(f"  预增记录: {len(df_gain)} 条")

# ── 3. 提取增收金额（支持小数，保留所有记录） ─────────────────────
def extract_gain(text):
    if not isinstance(text, str): return None
    m = re.search(r"盈利[约]?[:：]?([\d,.，]+)万元至([\d,.，]+)万元", text)
    if m: return float(m.group(2).replace(",", "").replace("，", ""))
    m = re.search(r"盈利[约]?[:：]?([\d,.，]+)万元", text)
    if m: return float(m.group(1).replace(",", "").replace("，", ""))
    m = re.search(r"增加[约]?[:：]?([\d,.，]+)万元至([\d,.，]+)万元", text)
    if m: return float(m.group(2).replace(",", "").replace("，", ""))
    m = re.search(r"增加[约]?[:：]?([\d,.，]+)万元", text)
    if m: return float(m.group(1).replace(",", "").replace("，", ""))
    return None

df_gain["gain_wan"] = df_gain["业绩变动"].apply(extract_gain)
# 不再 dropna，保留无金额的记录
df_gain = df_gain.sort_values("gain_wan", ascending=False)

# ── 4. 行业板块 ────────────────────────────────────────────────────
def guess_board(code):
    c = str(code).zfill(6)
    if c.startswith("688"): return "科创板"
    elif c.startswith("002"): return "中小板"
    elif c.startswith("000"): return "主板"
    elif c.startswith("300"): return "创业板"
    elif c.startswith(("9", "2")): return "B股"
    else: return "主板"

df_gain["板块"] = df_gain["股票代码"].apply(guess_board)

# ── 5. 增益范围文本 ────────────────────────────────────────────────
def extract_range(text):
    if not isinstance(text, str): return None
    m = re.search(r"盈利[约]?[:：]?([\d,]+)万元至([\d,]+)万元", text)
    if m:
        lo, hi = float(m.group(1).replace(",","")), float(m.group(2).replace(",",""))
        return f"{lo/10000:.1f}亿~{hi/10000:.1f}亿"
    m = re.search(r"盈利[约]?[:：]?([\d,]+)万元", text)
    if m:
        val = float(m.group(1).replace(",",""))
        return f"{val/10000:.1f}亿"
    m = re.search(r"亏损[约]?[:：]?([\d,]+)万元至([\d,]+)万元", text)
    if m:
        lo, hi = float(m.group(1).replace(",","")), float(m.group(2).replace(",",""))
        return f"扭亏{lo/10000:.0f}亿~{hi/10000:.0f}亿"
    m = re.search(r"亏损[约]?[:：]?([\d,]+)万元", text)
    if m:
        val = float(m.group(1).replace(",",""))
        return f"扭亏{val/10000:.0f}亿"
    return None

df_gain["增益范围"] = df_gain["业绩变动"].apply(extract_range)

# ── 6. 组装输出 ────────────────────────────────────────────────────
records = []
for _, row in df_gain.iterrows():
    raw_reason = str(row["业绩变动原因"]) if pd.notna(row["业绩变动原因"]) else ""
    themes = detect_themes(raw_reason)
    summary = summarize_reason(raw_reason)
    records.append({
        "code":       str(row["股票代码"]).zfill(6),
        "name":       str(row["股票简称"]),
        "board":      row["板块"],
        "type":       str(row["预告类型"]),
        "gain_wan":   int(row["gain_wan"]) if pd.notna(row["gain_wan"]) else None,
        "gain_text":  row["增益范围"] if pd.notna(row["增益范围"]) else "—",
        "reason":     summary,
        "reason_full": raw_reason,
        "themes":     themes,
        "notice_dt":  str(row["公告日期"])[:10],
    })

# ── 7. 主题统计 ────────────────────────────────────────────────────
theme_count = {}
for r in records:
    for t in r["themes"]:
        theme_count[t] = theme_count.get(t, 0) + 1
top_themes = sorted(theme_count.items(), key=lambda x: -x[1])[:8]

stats = {
    "total":          len(records),
    "预增":           sum(1 for r in records if r["type"] == "预增"),
    "扭亏":           sum(1 for r in records if r["type"] == "扭亏"),
    "首盈":           sum(1 for r in records if r["type"] == "首盈"),
    "续盈":           sum(1 for r in records if r["type"] == "续盈"),
    "略增":           sum(1 for r in records if r["type"] == "略增"),
    "total_gain_wan": sum(r["gain_wan"] for r in records if r["gain_wan"] is not None),
    "top_themes":     [{"theme": t, "count": c} for t, c in top_themes],
}

result = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "period":       "2026年中报（1-6月）",
    "stats":        stats,
    "records":      records,
}

# ── 8. 保存 ────────────────────────────────────────────────────────
with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\n✅ 已保存 {len(records)} 条预增记录")
print(f"   预增总额: {stats['total_gain_wan']/1e4:.0f} 亿元")
print(f"   预增/扭亏/首盈/续盈/略增: {stats['预增']}/{stats['扭亏']}/{stats['首盈']}/{stats['续盈']}/{stats['略增']}")
print(f"   增长主因分布: {dict(top_themes)}")
