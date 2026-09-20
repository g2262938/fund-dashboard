"""
indicators.py — 选股通用指标与工具函数
=============================================

目的：消除 simple_picker.py 与 四维选股CANSLIM.py 之间重复实现的指标函数。

约定：
- 所有函数都是**纯函数**，不发起网络请求、不读文件、不写文件。
- 失败/不足数据返回 None 或特定哨兵值，调用方显式判断。
- 时间/价格都用 Python float，不做时区转换（调用方负责传入标准化序列）。
"""

from __future__ import annotations

# ============================================================
# MACD（标准参数 12/26/9）
# ============================================================

def calc_macd(closes):
    """
    计算 MACD 指标（标准参数 12/26/9）

    返回: (dif, dea, macd_hist, golden_cross, golden_cross_days)
        - dif / dea / macd_hist: 当前值（不足数据时为 None）
        - golden_cross: True = 最近 4 日内 DIF 上穿 DEA 且两者都在 0 轴上方
        - golden_cross_days: 距今天数（999 = 无金叉）

    输入 closes 至少 34 个数据点，否则返回 None 哨兵。
    """
    if not closes or len(closes) < 34:
        return None, None, None, False, 999

    # EMA 初值用第一根 K 线；后续用递归公式
    alpha12 = 2 / 13
    alpha26 = 2 / 27
    ema12 = closes[0]
    ema26 = closes[0]

    dif_list = []
    for i, c in enumerate(closes):
        if i == 0:
            ema12 = c
            ema26 = c
        else:
            ema12 = ema12 * (1 - alpha12) + c * alpha12
            ema26 = ema26 * (1 - alpha26) + c * alpha26
        dif_list.append(ema12 - ema26)

    # DEA = DIF 的 9 日 EMA；前 26 天初始化为 DIF 自身
    alpha9 = 2 / 10
    dea_list = []
    for i, d in enumerate(dif_list):
        if i < 26:
            dea_list.append(d)
        else:
            prev_dea = dea_list[-1] if dea_list else d
            dea_list.append(prev_dea * (1 - alpha9) + d * alpha9)

    # MACD 柱 = (DIF - DEA) * 2
    hist_list = [(dif_list[i] - dea_list[i]) * 2 for i in range(len(dif_list))]

    # 检测金叉：取最近 60 日，遍历倒数 4 天
    dif60 = dif_list[-60:]
    dea60 = dea_list[-60:]

    golden_cross = False
    golden_cross_days = 999
    for i in range(len(dif60) - 1, max(0, len(dif60) - 5), -1):
        if i == 0:
            continue
        if dif60[i] > dea60[i] and dif60[i] > 0 and dea60[i] > 0:
            if dif60[i - 1] <= dea60[i - 1]:
                golden_cross = True
                golden_cross_days = len(dif60) - 1 - i
                break

    return dif_list[-1], dea_list[-1], hist_list[-1], golden_cross, golden_cross_days


def macd_score(closes):
    """
    MACD 0 轴上方 + 金叉评分（CANSLIM 核心技术指标）

    返回: (分数, 说明文字)
        - dif 为 None → (0, "MACD数据不足")
        - dif <= 0 或 dea <= 0 → (0, "MACD<0轴")
        - 1 日内金叉 → 20 分
        - 4 日内金叉 → 15 分
        - 10 日内金叉 → 10 分
        - 仅在 0 轴上方 → 5 分
    """
    dif, dea, hist, golden_cross, gc_days = calc_macd(closes)
    if dif is None:
        return 0, "MACD数据不足"
    if dif <= 0 or dea <= 0:
        return 0, f"MACD<0轴(DIF={dif:.3f})"
    if golden_cross and gc_days <= 1:
        return 20, "MACD0轴金叉+1日"
    if golden_cross and gc_days <= 4:
        return 15, f"MACD0轴金叉{gc_days}日前"
    if golden_cross and gc_days <= 10:
        return 10, f"MACD0轴金叉{gc_days}日前"
    return 5, "MACD0轴上方（无金叉）"


# ============================================================
# 近期涨幅
# ============================================================

def pct_change(newer, older):
    """百分比变化（newer/older - 1）*100。older<=0 时返回 0 避免除零异常。"""
    if not older or older <= 0:
        return 0.0
    return (newer - older) / older * 100


def recent_change(closes, anchor="yesterday"):
    """
    计算近期涨幅序列

    参数:
        closes: 收盘价列表（按时间升序，最新值在末尾）
        anchor: 'yesterday' 以 closes[-2] 为基准；'today' 以 closes[-1] 为基准
                注意：盘中数据 closes[-1] 可能是当前价，并非收盘价，使用需谨慎

    返回: (chg_1d, chg_2d, chg_3d, chg_5d) 均为百分比
        数据不足时对应位置返回 0

    调用方负责决定 anchor 取哪个语义。两套选股引擎对 anchor 的选择
    目前并不一致（simple_picker 倾向 today，四维倾向 yesterday），
    本函数保留两种选项，由调用方显式指定。
    """
    if not closes:
        return 0.0, 0.0, 0.0, 0.0
    if anchor == "today":
        base_idx = -1
        base_label = "今日"
    else:
        base_idx = -2
        base_label = "昨日"
    if len(closes) < 2:
        return 0.0, 0.0, 0.0, 0.0
    base = closes[base_idx]
    chg_1d = pct_change(base, closes[-3]) if len(closes) >= 3 else 0.0
    chg_2d = pct_change(base, closes[-4]) if len(closes) >= 4 else 0.0
    chg_3d = pct_change(base, closes[-5]) if len(closes) >= 5 else 0.0
    chg_5d = pct_change(base, closes[-7]) if len(closes) >= 7 else 0.0
    return chg_1d, chg_2d, chg_3d, chg_5d


# ============================================================
# 市场识别
# ============================================================

def get_market(code):
    """
    根据股票代码识别市场。

    返回值：
        'sh'  — 上交所（A 股 6xxxxx、9xxxxx B 股、5xxxxx 基金/ETF）
        'sz'  — 深交所（A 股 0xxxxx、2xxxxx、3xxxxx）
        'hk'  — 港股（5 位数字，常见前缀 0/1/2/3/4/5/6/7/8/9）
        None  — 无法识别（调用方应抛错或显式剔除）

    旧版本只识别 A 股，港股被默认归到 'sh' 导致请求非法代码并被静默吞掉，
    评分数据全部丢失。本函数对港股显式返回 'hk'，调用方可针对性处理。
    """
    if code is None:
        return None
    s = str(code).strip().zfill(6)

    # 港股：5 位数字代码（去掉前导 0 后长度 <= 5）
    # 严格判定：A 股代码必须 >= 6 位，否则视为港股
    if len(str(code).strip()) <= 5:
        return "hk"

    # A 股深市：0、2、3 开头
    if s[0] in ("0", "2", "3"):
        return "sz"
    # A 股沪市：6、9 开头（B 股 9 开头也走沪市通道）
    if s[0] in ("6", "9"):
        return "sh"
    # 5 开头通常是基金/ETF（沪市），归到 sh
    if s[0] == "5":
        return "sh"
    return None
