#!/usr/bin/env python3
"""中报业绩预告预亏标的扫描 — 生成 JSON 数据供 HTML 使用
包含智能原因分析：关键主题归集 + 核心摘要提炼
"""
import akshare as ak
import json
import re
import pandas as pd
from datetime import datetime

OUTPUT = "/home/ubuntu/.openclaw/workspace/reports/data/yjyg_loss_latest.json"

# ── 原因关键词归类 ─────────────────────────────────────────────────
LOSS_THEMES = [
    ("房地产景气下行",   ["房地产", "房产", "地产", "商品房", "楼盘", "土储", "房开", "建筑施工"]),
    ("生猪价格下跌",     ["生猪", "猪价", "肥猪", "养殖成本", "猪周期", "活禽", "饲料", "动物"]),
    ("光伏/新能源寒冬",  ["光伏", "太阳能", "锂电", "储能", "新能源", "多晶硅", "硅片", "组件", "电池片"]),
    ("钢铁/建材承压",   ["钢铁", "水泥", "建材", "螺纹钢", "煤炭", "煤炭价格", "煤焦", "大宗建材"]),
    ("消费电子/半导体", ["消费电子", "半导体", "芯片", "面板", "显示屏", "存储器", "OLED", "手机", "PC"]),
    ("航空/出行受损",   ["民航", "航空", "出行", "旅游", "客座率", "机场"]),
    ("市场竞争激烈",     ["竞争加剧", "竞争激烈", "量价齐跌", "量价双跌", "盈利空间压缩"]),
    ("原材料涨价",       ["原材料", "大宗商品", "大宗原辅", "铝", "铜", "锂", "钴", "贵金属", "原料价格", "成本上涨"]),
    ("资产/信用减值",   ["减值", "计提减值", "跌价", "坏账", "信用减值", "资产减值损失", "存货跌价", "商誉"]),
    ("财务费用高企",     ["财务费用", "利息支出", "有息负债", "借款规模", "融资成本", "汇兑损失", "汇率波动"]),
    ("需求不足/订单下滑",["需求不足", "订单下滑", "订单减少", "市场疲软", "有效需求", "开工率", "产能利用率", "收入下降", "收入下滑"]),
    ("费用/成本增加",   ["费用增加", "期间费用", "折旧", "摊销", "运营成本", "成本增加", "固定成本", "管理费用", "销售费用"]),
    ("毛利率下降",       ["毛利率", "毛利额", "毛利减少", "毛利润", "毛利下滑"]),
    ("国际局势/地缘",   ["地缘", "中美", "贸易摩擦", "关税", "出口退", "逆全球化", "外部宏观", "国际环境", "外销", "出口业务"]),
]

def detect_themes(text):
    """返回匹配到的主题标签列表"""
    themes = []
    for label, keywords in LOSS_THEMES:
        for kw in keywords:
            if kw in text:
                themes.append(label)
                break
    return themes[:4]

def summarize_reason(text):
    """从完整原因文本中提炼核心分句"""
    if not text:
        return ""
    # 清理 URL/邮箱等碎片
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # 按中文章节符号切分句子
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
    # 优先保留含关键因果词的分句
    KEY_KWS = [
        "主要因", "主要原因", "所致", "导致", "造成", "由于",
        "毛利", "净利润", "收入", "成本", "售价", "销量",
        "资产减值", "信用减值", "计提", "跌价",
        "竞争", "需求", "价格", "市场",
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

# ── 2. 过滤预亏类型（合并归属净利润 + 扣非净利润，按股票代码去重） ─
loss_types = ["首亏", "增亏", "续亏", "预减"]
df_loss = df_raw[
    df_raw["预告类型"].isin(loss_types) &
    df_raw["预测指标"].str.contains(
        "归属于上市公司股东的净利润|扣除非经常性损益后的净利润", na=False
    )
].copy()
# 同一股票优先保留"归属净利润"那条
df_loss = df_loss.drop_duplicates(subset="股票代码", keep="first")
print(f"  预亏记录: {len(df_loss)} 条")

# ── 3. 提取亏损金额（支持小数，保留所有记录） ─────────────────────
def extract_loss(text):
    if not isinstance(text, str):
        return None
    # 支持小数的金额
    m = re.search(r"亏损[约]?[:：]?([\d,.，]+)万元至([\d,.，]+)万元", text)
    if m:
        return float(m.group(2).replace(",", "").replace("，", ""))
    m = re.search(r"亏损[约]?[:：]?([\d,.，]+)万元", text)
    if m:
        return float(m.group(1).replace(",", "").replace("，", ""))
    return None

df_loss["loss_wan"] = df_loss["业绩变动"].apply(extract_loss)
# 不再 dropna，保留无金额的记录（金额显示"—"）
df_loss = df_loss.sort_values("loss_wan", ascending=False)

# ── 4. 行业板块 ────────────────────────────────────────────────────
def guess_board(code):
    c = str(code).zfill(6)
    if c.startswith("688"): return "科创板"
    elif c.startswith("002"): return "中小板"
    elif c.startswith("000"): return "主板"
    elif c.startswith("300"): return "创业板"
    elif c.startswith(("9", "2")): return "B股"
    else: return "主板"

df_loss["板块"] = df_loss["股票代码"].apply(guess_board)

# ── 5. 亏损范围文本 ────────────────────────────────────────────────
def extract_range(text):
    if not isinstance(text, str): return None
    m = re.search(r"亏损[约]?[:：]?([\d,.，]+)万元至([\d,.，]+)万元", text)
    if m:
        lo = float(m.group(1).replace(",","").replace("，",""))
        hi = float(m.group(2).replace(",","").replace("，",""))
        return f"{lo/10000:.0f}亿~{hi/10000:.0f}亿"
    m = re.search(r"亏损[约]?[:：]?([\d,.，]+)万元", text)
    if m:
        val = float(m.group(1).replace(",","").replace("，",""))
        return f"{val/10000:.0f}亿"
    return None

df_loss["亏损范围"] = df_loss["业绩变动"].apply(extract_range)

# ── 6. 组装输出 ────────────────────────────────────────────────────
records = []
for _, row in df_loss.iterrows():
    raw_reason = str(row["业绩变动原因"]) if pd.notna(row["业绩变动原因"]) else ""
    themes = detect_themes(raw_reason)
    summary = summarize_reason(raw_reason)
    records.append({
        "code":       str(row["股票代码"]).zfill(6),
        "name":       str(row["股票简称"]),
        "board":      row["板块"],
        "type":       str(row["预告类型"]),
        "loss_wan":   int(row["loss_wan"]) if pd.notna(row["loss_wan"]) else None,
        "loss_text":  row["亏损范围"] if pd.notna(row["亏损范围"]) else "—",
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
    "首亏":           sum(1 for r in records if r["type"] == "首亏"),
    "增亏":           sum(1 for r in records if r["type"] == "增亏"),
    "续亏":           sum(1 for r in records if r["type"] == "续亏"),
    "预减":           sum(1 for r in records if r["type"] == "预减"),
    "total_loss_wan": sum(r["loss_wan"] for r in records if r["loss_wan"] is not None),
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

print(f"\n✅ 已保存 {len(records)} 条预亏记录")
print(f"   预亏总额: {stats['total_loss_wan']/1e4:.0f} 亿元")
print(f"   首亏/增亏/续亏/预减: {stats['首亏']}/{stats['增亏']}/{stats['续亏']}/{stats['预减']}")
print(f"   亏损主因分布: {dict(top_themes)}")
