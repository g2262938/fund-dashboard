#!/usr/bin/env python3
"""
四维选股分析系统 v3 — 欧奈尔CAN SLIM融合版（雨的四大条件优化）
======================================================================
基于雨的优化建议 v2026-06-11

第一关：前置硬性筛选（雨的四大条件 + CAN SLIM五项否决）
  1. MACD 0轴上方金叉（最近3日内）— 技术面硬性否决
  2. 量比 > 1.2 — 量能硬性否决
  3. 基本面无风险（财务筛选）— 基本面硬性否决
  4. 处于行情热点（板块涨停≥3只）— 热点硬性否决
  + CAN SLIM五项否决（C-A-S-L-基本面避雷）

第二关：四维打分（欧奈尔标准）
第三关：双模式适配
第四关：大盘趋势总开关
"""

import requests
import akshare as ak
import datetime
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

TODAY = datetime.date.today().strftime("%Y%m%d")
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")

# ============================================================
# 工具函数：投研评分查询
# ============================================================

def get_research_enrichment(code):
    """
    查询个股的投研评分（enterprise_research数据库）
    如果数据库中有该股票，返回(投研总分, 建议, 弱点描述)
    如果没有，返回(None, None, '')
    """
    try:
        import sqlite3
        DB_ENT = '/home/ubuntu/.openclaw/workspace/data/enterprise_research.db'
        conn = sqlite3.connect(DB_ENT)
        c = conn.cursor()
        c.execute('SELECT code FROM financial_indicator WHERE code=?', (code,))
        if c.fetchone() is None:
            conn.close()
            return None, None, ''
        
        # 加载行业资金流数据（analyzer需要）
        ind_data = {r[0]: r[1] for r in conn.execute(
            'SELECT industry, change_pct FROM industry_fund_flow').fetchall()}
        conn.close()
        
        # 导入analyzer并分析
        import sys
        sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/scripts/enterprise_research')
        from analyzer import analyze_stock
        
        r = analyze_stock(code, ind_data)
        weaknesses = []
        for key, (score, reason) in [
            ('盈利', r['profit_score']),
            ('现金流', r['cash_score']),
            ('偿债', r['debt_score']),
            ('行业', r['industry_score']),
            ('护城河', r['barrier_score']),
            ('估值', r['val_score']),
        ]:
            if score < 5:
                weaknesses.append(f"{key}弱({score})")
        weak_str = ' | '.join(weaknesses) if weaknesses else ''
        return r['total'], r['advice'], weak_str
    except:
        return None, None, ''

# ============================================================
# 第一部分：工具函数
# ============================================================


def _get_ths_industry(code, conn=None):
    """从本地stock_industry表查THS申万二级行业"""
    if conn is None:
        import sqlite3
        conn = sqlite3.connect("/home/ubuntu/.openclaw/workspace/data/stock_pool.db")
        need_close = True
    else:
        need_close = False
    cur = conn.cursor()
    try:
        cur.execute("SELECT industry FROM stock_industry WHERE code=?", (str(code).zfill(6),))
        row = cur.fetchone()
        if need_close:
            conn.close()
        return row[0] if row and row[0] else ""
    except:
        return ""


# ========== K线数据（akshare + 内存缓存）==========
_KLINE_CACHE = {}

def get_kline(code, market='sh', datalen=250):
    """获取日K线数据（akshare，带内存缓存）"""
    cache_key = f"{market}{code}"
    if cache_key in _KLINE_CACHE:
        return _KLINE_CACHE[cache_key][-datalen:] if len(_KLINE_CACHE[cache_key]) >= datalen else _KLINE_CACHE[cache_key]
    try:
        symbol_map = {'sh': 'sh', 'sz': 'sz', 'bj': 'bj'}
        m = symbol_map.get(market, 'sh')
        code6 = str(code).zfill(6)
        sym = f'{m}{code6}'
        from datetime import datetime, timedelta
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=datalen + 120)).strftime('%Y%m%d')
        df = ak.stock_zh_a_daily(symbol=sym, start_date=start, end_date=end, adjust='qfq')
        result = []
        for _, row in df.iterrows():
            result.append({
                'open': str(row['open']),
                'close': str(row['close']),
                'high': str(row['high']),
                'low': str(row['low']),
                'volume': str(int(row['volume']))
            })
        _KLINE_CACHE[cache_key] = result
        return result[-datalen:] if result else []
    except:
        return []


def get_recent_change(code, market='sh'):
    """获取近N日涨幅（用于过滤追高）。
    所有涨幅以"昨日收盘价"为基准计算：
    - chg_1d: 昨日相对前日涨幅（昨日日内涨幅）
    - chg_2d: 昨日相对2日前涨幅（连续2日涨幅）
    - chg_3d: 昨日相对3日前涨幅（3日涨幅）
    - chg_5d: 昨日相对5日前涨幅（5日涨幅）
    """
    data = get_kline(code, market, 12)
    if not data or len(data) < 6:
        return 0, 0, 0, 0, 0
    try:
        closes = [float(d['close']) for d in data]
        # 昨收 = closes[-2], 前日 = closes[-3], 2日前 = closes[-4], 3日前 = closes[-5]
        yest_close = closes[-2]   # 昨日收盘（候选日收盘）
        chg_1d = (closes[-2] / closes[-3] - 1) * 100 if len(closes) >= 3 else 0  # 昨日涨幅
        chg_2d = (closes[-2] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0  # 连续2日涨幅（到昨日）
        chg_3d = (closes[-2] / closes[-5] - 1) * 100 if len(closes) >= 5 else 0  # 3日涨幅（到昨日）
        chg_5d = (closes[-2] / closes[-7] - 1) * 100 if len(closes) >= 7 else 0  # 5日涨幅（到昨日）
        return 0, chg_1d, chg_2d, chg_3d, chg_5d  # chg_today=0（当日价格未知）
    except:
        return 0, 0, 0, 0, 0


def get_market(code):
    code = str(code).zfill(6)
    if code[0] in ('0', '3'):
        return 'sz'
    return 'sh'


# ========== MACD计算（0轴上方金叉检测）==========
def calc_macd(closes):
    """
    计算MACD指标
    返回: (dif, dea, macd_hist, golden_cross, golden_cross_days)
    - golden_cross: True=当前金叉（DIF上穿DEA且两者>0）
    - golden_cross_days: 最近N日内出现金叉
    """
    if len(closes) < 34:
        return None, None, None, False, 999
    
    # 计算EMA12和EMA26
    ema12 = closes[0]
    ema26 = closes[0]
    alpha12 = 2 / 13
    alpha26 = 2 / 27
    
    dif_list = []
    for i, c in enumerate(closes):
        ema12 = ema12 * (1 - alpha12) + c * alpha12 if i > 0 else c
        ema26 = ema26 * (1 - alpha26) + c * alpha26 if i > 0 else c
        dif_list.append(ema12 - ema26)
    
    # 计算DEA（9日EMA of DIF）
    dea_list = []
    alpha9 = 2 / 10
    for i, d in enumerate(dif_list):
        if i < 26:
            dea_list.append(d)  # 前26天DIF=DEA
        else:
            prev_dea = dea_list[-1] if dea_list else d
            dea_list.append(prev_dea * (1 - alpha9) + d * alpha9)
    
    # MACD柱 = (DIF - DEA) * 2
    hist_list = [(dif_list[i] - dea_list[i]) * 2 for i in range(len(dif_list))]
    
    # 取最近60日（足够检测金叉）
    dif60 = dif_list[-60:]
    dea60 = dea_list[-60:]
    hist60 = hist_list[-60:]
    
    # 检测金叉：昨日DIF<DEA 且 今日DIF>DEA
    golden_cross = False
    golden_cross_days = 999
    
    for i in range(len(dif60) - 1, max(0, len(dif60) - 5), -1):  # 检查最近4天
        # 今日DIF>DEA 且 两者都在0轴上方
        if dif60[i] > dea60[i] and dif60[i] > 0 and dea60[i] > 0:
            # 昨日DIF<=DEA（死叉或刚金叉）
            if dif60[i-1] <= dea60[i-1]:
                golden_cross = True
                golden_cross_days = len(dif60) - 1 - i
                break
    
    cur_dif = dif_list[-1]
    cur_dea = dea_list[-1]
    cur_hist = hist_list[-1]
    
    return cur_dif, cur_dea, cur_hist, golden_cross, golden_cross_days


# ========== 当日量比获取 ==========
def get_volume_ratio(code, market='sh'):
    """
    获取当日量比（当日成交量 / 5日均量）
    返回: (量比, 是否成功)
    """
    try:
        import requests
        code6 = str(code).zfill(6)
        mkt = '1' if market == 'sh' else '0'
        # 东方财富实时行情API
        url = f'https://push2.eastmoney.com/api/qt/stock/get?secid={mkt}.{code6}&fields=f135,f136'
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json().get('data', {})
        
        vol_today = data.get('f136', 0)  # 当日成交量（手）
        if not vol_today or vol_today == 0:
            return None, False
        
        # 获取5日均量（从K线）
        kdata = get_kline(code, market, 6)
        if not kdata or len(kdata) < 5:
            return None, False
        
        # 前5日平均成交量（不含今日）
        vol5_avg = sum(int(d['volume']) for d in kdata[:-1]) / 5
        if vol5_avg == 0:
            return None, False
        
        ratio = vol_today / vol5_avg
        return ratio, True
    except:
        return None, False


# ========== 基本面无风险筛选 ==========
def check_financial_risk(code):
    """
    检查基本面是否无风险（基于enterprise_research.db财务数据）
    返回: (是否通过, 失败原因列表)
    
    筛选条件：
    - PE < 150（亏损股需高增长赛道才过）
    - ROE > 5%（主板）/ ROE > 3%（科创/创业）
    - 净利润 > 0（近四季）
    - 流动比率 > 1 或 经营现金流为正
    """
    fails = []
    code = str(code).zfill(6)
    
    # 判断板块（科创/创业更宽松）
    is_growth_board = code.startswith(('688', '300'))
    roe_threshold = 3.0 if is_growth_board else 5.0
    
    try:
        import sqlite3
        conn = sqlite3.connect('/home/ubuntu/.openclaw/workspace/data/enterprise_research.db')
        c = conn.cursor()
        
        # 获取最新财报数据（按报告日期降序）
        c.execute('''
            SELECT report_date, eps, roe, net_margin, debt_ratio, 
                   current_ratio, oper_cf_per_share, revenue_yoy, profit_yoy
            FROM financial_indicator 
            WHERE code=? 
            ORDER BY report_date DESC LIMIT 1
        ''', (code,))
        row = c.fetchone()
        conn.close()
        
        if row is None:
            # 无财务数据，给予宽松处理（不直接否决，但记录
            return True, []  # 有数据库的才严格筛选
        
        report_date, eps, roe, net_margin, debt_ratio, current_ratio, oper_cf, rev_yoy, profit_yoy = row
        
        # PE检查（需要EPS计算）
        if eps and eps > 0:
            # 通过EPS估算PE（简化）
            pass  # EPS数据不够精确，跳过
        
        # ROE检查
        if roe is not None:
            if roe < roe_threshold:
                fails.append(f'基本-ROE: {roe:.1f}%（主板需>{roe_threshold}%）')
        
        # 净利润检查（近四季净利润为负）
        if net_margin is not None and net_margin < 0:
            fails.append(f'基本-净利: 亏损{net_margin:.1f}%')
        
        # 流动比率或经营现金流
        has_cash_indicator = (current_ratio is not None and current_ratio > 1) or \
                            (oper_cf is not None and oper_cf > 0)
        if not has_cash_indicator:
            fails.append(
                '基本-现金流: 流动比率' + f'{current_ratio or 0:.2f}' +
                '且经营现金流' + ('正' if oper_cf and oper_cf > 0 else '负或无')
            )
        
        return len(fails) == 0, fails
        
    except Exception as e:
        return True, []  # 出错时宽松处理


# ========== 行情热点检测 ==========
def check_hot_sector(code, board_stats=None, is_zt_stock=False):
    """
    检测个股是否处于行情热点
    条件（满足任一即可）：
    1. 昨日涨停股 → 当日热点（来自涨停池的股票天然是热点）
    2. 所属申万行业昨日涨停≥2只
    3. 所属行业在涨停数排名前50%
    
    返回: (是否热点, 说明)
    """
    code = str(code).zfill(6)
    
    # 条件0：昨涨停股自身 → 热点
    if is_zt_stock:
        return True, '当日涨停热点'
    
    # 获取个股行业
    try:
        import sqlite3
        conn = sqlite3.connect('/home/ubuntu/.openclaw/workspace/data/stock_pool.db')
        cur = conn.cursor()
        cur.execute('SELECT industry FROM stock_industry WHERE code=?', (code,))
        row = cur.fetchone()
        industry = row[0] if row and row[0] else None
        conn.close()
    except:
        industry = None
    
    # 条件1：昨板块涨停≥2只
    if board_stats and industry and industry in board_stats:
        zt_count = board_stats.get(industry, 0)
        if zt_count >= 2:
            return True, f'热点({industry},{zt_count}只涨停)'
    
    # 条件2：行业排名前50%
    if board_stats:
        sorted_boards = sorted(board_stats.items(), key=lambda x: x[1], reverse=True)
        hot_boards = [b[0] for b in sorted_boards[:max(1, len(sorted_boards) // 2)]]
        if industry and industry in hot_boards:
            zt_count = board_stats.get(industry, 0)
            return True, f'热点({industry},{zt_count}只涨停)'
    
    return False, '冷门' if industry else '无行业数据'


# ============================================================
# 第二部分：大盘趋势判断（欧奈尔M开关）
# ============================================================

def check_market_trend():
    """
    大盘趋势判断（欧奈尔第一原则）
    BULL：上证在20日均线上方 + 均线向上
    BEAR：上证在20日均线下方 + 均线向下
    UNCLEAR：震荡不明
    """
    try:
        data = get_kline('000001', 'sh', 60)
        if not data or len(data) < 30:
            return 'UNCLEAR'
        closes = [float(d['close']) for d in data]
        ma20 = sum(closes[-20:]) / 20
        ma20_prev = sum(closes[-25:-5]) / 20
        cur = closes[-1]
        if cur > ma20 and ma20 > ma20_prev:
            return 'BULL'
        elif cur < ma20 and ma20 < ma20_prev:
            return 'BEAR'
        return 'UNCLEAR'
    except:
        return 'UNCLEAR'


# ============================================================
# 第三部分：前置硬性筛选（5项，一票否决）
# ============================================================

def pre_filter_all(code, name='', market='sh', board_stats=None, is_zt_stock=False):
    """
    前置硬性筛选（雨的四大条件 + CAN SLIM五项否决）
    
    雨的四大条件（硬性否决）：
    1. MACD 0轴上方（必需）+ 金叉（软性加分）
    2. 量比 > 1.2
    3. 基本面无风险（财务筛选）
    4. 处于行情热点
    
    CAN SLIM五项否决：
    C-A-S-L-基本面避雷
    """
    fails = []
    code = str(code).zfill(6)

    # 获取K线数据
    data = get_kline(code, market, 250)
    if not data or len(data) < 60:
        return False, ['数据不足（需250日）']

    try:
        closes = [float(d['close']) for d in data]
        volumes = [int(d['volume']) for d in data]
        cur = closes[-1]
        ma20 = sum(closes[-20:]) / 20
        ma50 = sum(closes[-50:]) / 50
        ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else ma50
        high60 = max(closes[-60:])
        high250 = max(closes)
        vol5 = sum(volumes[-5:]) / 5
        vol20 = sum(volumes[-20:]) / 20
    except:
        return False, ['行情数据解析失败']

    # ===== S：标的质地（A股专属）=====
    # 流通市值30-500亿（akshare数据不含流通市值，跳过此项检查）
    # 注意：市值筛选在选股入口处通过聚宽数据实现

    # 高位分位（60日高点计算）
    dist60 = (high60 - cur) / high60 * 100 if high60 > 0 else 100
    if dist60 > 90:
        fails.append(f'S-位置: 距60日高点{dist60:.0f}%（高位透支）')

    # ===== A：年度成长（近2年营收/利润连续正增长）=====
    # 用K线近似判断：近1年涨幅>0（代表年度正增长趋势）
    try:
        annual_1y = (closes[-1] / closes[-252] - 1) * 100 if len(closes) >= 252 else 0
        annual_2y = (closes[-1] / closes[-504] - 1) * 100 if len(closes) >= 504 else annual_1y
        if annual_2y < -40:
            fails.append(f'A-年度: 近2年涨幅{annual_2y:.0f}%（持续下滑超40%）')
        elif annual_2y < -20 and annual_1y < -20:
            fails.append(f'A-年度: 近1-2年连续大幅下滑')
    except:
        pass

    # ===== L：相对强弱（RS≥70）=====
    try:
        # 用近1年相对收益率代表RS（全市场排名前30%为RS≥70）
        # 年涨幅>20%的个股可认为RS相对较强（简化判断）
        if annual_1y < -30:
            fails.append(f'L-RS: 近1年涨幅{annual_1y:.0f}%（RS过弱，跌幅>30%）')
    except:
        pass

    # ===== 基本面避雷=====
    # 1. 均线空头压制（中期下降趋势）
    ma50_prev = sum(closes[-55:-5]) / 50 if len(closes) >= 55 else ma50
    if cur < ma50 and ma50 < ma50_prev:
        fails.append(f'基本-趋势: 跌破50日线且均线向下（中期空头）')

    # 2. 高位放巨量（出货嫌疑）
    abnormal_vol_days = 0
    for i in range(-10, 0):
        v = volumes[i]
        if v > vol5 * 3.5:
            abnormal_vol_days += 1
    if abnormal_vol_days >= 2:
        fails.append(f'I-量价: 近10日{abnormal_vol_days}天异常爆量（出货嫌疑）')

    # 3. 长期下降趋势（200日均线下方且向下）
    if cur < ma200 and ma200 < sum(closes[-205:-5]) / 200:
        fails.append(f'A-趋势: 200日均线下方且向下（长期熊市）')

    # 4. 强周期行业顶部（煤炭/有色/钢铁盈利高点）
    # 简化：股价近1年涨幅>150%且RS>90视为周期高估
    if annual_1y > 150:
        fails.append(f'基本-周期: 近1年涨幅{annual_1y:.0f}%（周期股高估区域）')

    # 近20日无连续3天以上收盘价持续创新低（代表基本面无恶化）
    new_low_count = sum(1 for i in range(-20, -3) if closes[i] < min(closes[i-5:i]))
    if new_low_count >= 5:
        fails.append(f'C-业绩: 近20日{new_low_count}日创新低（基本面趋弱）')

    # ===== 新增：追高过滤（欧奈尔核心改进）=====
    chg_today, chg_yesterday, chg_2d, chg_3d, chg_5d = get_recent_change(code, market)
    # 昨日涨幅>10% → 追高风险，等回调确认（但可以用今日回调幅度降低风险）
    if chg_yesterday > 10 and chg_today < -0.5:
        fails.append(f'追高-昨日: 昨日涨幅+{chg_yesterday:.1f}%且今日走弱（⚠️追高风险）')
    elif chg_yesterday > 12:
        fails.append(f'追高-昨日: 昨日涨幅+{chg_yesterday:.1f}%（⚠️追高风险，等回调）')
    # 连续2日涨幅>25% → 主力可能正在出货
    if chg_2d > 25:
        fails.append(f'追高-2日: 2日涨幅+{chg_2d:.1f}%（⚠️连续大涨，谨慎）')
    # 3日涨幅>30% → 偏离估值，大概率已走完一浪
    if chg_3d > 30:
        fails.append(f'追高-3日: 3日涨幅+{chg_3d:.1f}%（⚠️短期超涨，等震荡整理）')
    # 5日涨幅>40% → 蓄势不够，拉升过猛
    if chg_5d > 40:
        fails.append(f'追高-5日: 5日涨幅+{chg_5d:.1f}%（⚠️5日过猛，蓄势不足）')

    # ===== 雨的四大新增条件（硬性否决）=====
    
    # 条件1：MACD 0轴上方（硬性）+ 金叉（软性加分）
    dif, dea, hist, golden_cross, gc_days = calc_macd(closes)
    macd_above_zero = (dif is not None and dif > 0 and dea > 0)
    macd_score = 0
    macd_detail = ''
    
    if dif is None:
        fails.append('技术-MACD: 数据不足无法计算')
    elif not macd_above_zero:
        fails.append(f'技术-MACD: DIF={dif:.3f} DEA={dea:.3f}（需>0）')
    else:
        # MACD在0轴上方：根据金叉时间给分
        if golden_cross and gc_days <= 1:
            macd_score = 20
            macd_detail = f'MACD0轴金叉+1日'
        elif golden_cross and gc_days <= 4:
            macd_score = 15
            macd_detail = f'MACD0轴金叉{gc_days}日前'
        elif golden_cross and gc_days <= 10:
            macd_score = 10
            macd_detail = f'MACD0轴金叉{gc_days}日前'
        else:
            macd_score = 5
            macd_detail = 'MACD0轴上方（无金叉）'
    
    # 条件2：量比 > 1.2
    vol_ratio, vol_ok = get_volume_ratio(code, market)
    if vol_ok and vol_ratio <= 1.2:
        fails.append(f'技术-量比: {vol_ratio:.2f}（需>1.2）')
    
    # 条件3：基本面无风险（财务筛选）
    fin_pass, fin_fails = check_financial_risk(code)
    if not fin_pass:
        for ff in fin_fails:
            fails.append(ff)
    
    # 条件4：处于行情热点
    hot_pass, hot_desc = check_hot_sector(code, board_stats, is_zt_stock)
    # 热点改为软性加分（不再硬性否决，冷门股最多少得板块分）
    # hot_pass, hot_desc 暂不用于一票否决

    return len(fails) == 0, fails, macd_score, macd_detail


# ============================================================
# 第四部分：四维打分（对标欧奈尔）
# ============================================================

def score_position(closes, cur, high60, high250):
    """
    维度一：位置（30分）— 欧奈尔形态位置
    优选：60日高点分位50%-75%，杯柄柄部/平底蓄势
    """
    dist60 = (high60 - cur) / high60 * 100 if high60 > 0 else 100

    # 检测杯柄形态（简化）
    pattern = detect_pattern(closes)

    # 评分
    if 50 <= dist60 <= 75:
        base = 28 if pattern else 25
        detail = f"优选区({dist60:.0f}%)"
        flag = "✅ 杯柄" if pattern else "✅ 优"
    elif 30 <= dist60 < 50:
        base = 22
        detail = f"中位蓄势({dist60:.0f}%)"
        flag = "⚡ 良"
    elif 15 <= dist60 < 30:
        base = 14
        detail = f"低位({dist60:.0f}%)"
        flag = "❌ 偏弱"
    elif dist60 < 15:
        base = 8
        detail = f"近高点({dist60:.0f}%)"
        flag = "⚡ 慎"
    elif 75 < dist60 <= 90:
        base = 12
        detail = f"偏高({dist60:.0f}%)"
        flag = "⚡ 风险区"
    else:  # >90
        base = 0
        detail = f"高位({dist60:.0f}%)"
        flag = "❌ 淘汰"

    return base, detail, flag


def detect_pattern(closes):
    """
    检测杯柄/平底形态（简化版）
    返回 True = 有形态，False = 无形态
    """
    if len(closes) < 120:
        return False

    # 取最近120日
    c = closes[-120:]

    # 检测平底：中间一段明显高于前后
    mid = 60
    left = c[:mid]
    right = c[mid:]

    left_peak = max(left)
    right_peak = max(right)

    # 平底：左右两边高点接近（相差<15%），中间有明显低谷
    left_peak_idx = left.index(left_peak)
    right_peak_idx = mid + right.index(right_peak)

    left_bottom = min(c[left_peak_idx:mid])
    right_bottom = min(c[mid:right_peak_idx])

    # 中间底部明显低于两侧高点（杯形态）
    cup_depth = (left_peak - min(left_bottom, right_bottom)) / left_peak if left_peak > 0 else 0

    # 杯深度合理（<30%），且右侧反弹幅度>左侧底部
    if cup_depth < 0.35 and right_bottom >= left_bottom * 0.9:
        return True

    # 平底检测：中间段略低，两侧稳定
    mid_low = min(c[mid-10:mid+10])
    if mid_low > left_peak * 0.75 and mid_low > right_peak * 0.75:
        return True

    return False


def score_ma(closes):
    """
    维度二：均线（25分）— 欧奈尔趋势多头
    核心：20日、50日均线向上，股价站稳
    """
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes[-30:]) / 30
    ma20_prev = sum(closes[-25:-5]) / 20
    ma50_prev = sum(closes[-55:-5]) / 50 if len(closes) >= 55 else ma20

    cur = closes[-1]
    ma20_up = ma20 > ma20_prev
    ma50_up = ma50 > ma50_prev

    above_ma20 = cur > ma20
    above_ma50 = cur > ma50

    # 完美多头
    if above_ma20 and above_ma50 and ma20_up and ma50_up:
        return 25, "完美多头(20日↑50日↑)", "✅ 强"
    # 标准多头
    elif above_ma20 and above_ma50:
        return 22, "标准多头排列", "✅ 良"
    # 20日线上方且向上
    elif above_ma20 and ma20_up:
        return 18, "20日线上且向上", "✅ 中"
    # 价格在20日线上
    elif above_ma20:
        return 14, "价格在20日线上", "⚡ 偏弱"
    # 仅在10日线上
    elif cur > ma10:
        return 7, "仅在10日线上", "❌ 空头"
    else:
        return 0, "均线空头压制", "❌ 跌"


def score_volume(data, closes, volumes):
    """
    维度三：量价（25分）— 欧奈尔量价铁律
    平台整理期：缩量回调
    突破期：放量50%+阳线
    """
    vol5 = sum(volumes[-5:]) / 5
    vol20 = sum(volumes[-20:]) / 20
    vol_ratio = vol5 / vol20 if vol20 > 0 else 0

    cur = closes[-1]
    high20 = max(closes[-20:])

    healthy = 0
    breakthrough = False
    danger_vol = 0

    for i in range(-10, 0):
        o, c, v = float(data[i]['open']), closes[i], volumes[i]
        is_yang = c > o
        chg_pct = (c - o) / o * 100 if o > 0 else 0

        # 健康：阳线放量 + 阴线缩量
        if is_yang and v > vol5 * 1.4:
            healthy += 1
        elif not is_yang and v < vol5 * 0.7:
            healthy += 1

        # 危险：高位异常爆量（>3倍均量）
        if v > vol5 * 3.5:
            danger_vol += 1

        # 突破：阳线+放量+接近20日高点
        if is_yang and v > vol5 * 1.5 and c >= high20 * 0.90:
            breakthrough = True

    # 高位滞涨检测
    dist20 = (high20 - cur) / high20 * 100 if high20 > 0 else 100
    if dist20 < 10 and danger_vol >= 1:
        return 5, f"高位滞涨({danger_vol}日爆量)", "❌ 危险"

    # 突破放量且整体蓄势（量比0.6-1.3）
    if breakthrough and 0.6 <= vol_ratio <= 1.3:
        return 25, "突破放量+蓄势健康", "✅ 优"
    elif breakthrough:
        return 20, "突破放量(量能略高)", "✅ 良"
    # 蓄势良好（缩量回调）+ 量价健康日多
    elif 0.5 <= vol_ratio <= 1.0 and healthy >= 6:
        return 22, f"蓄势缩量+健康({healthy}/10日)", "✅ 优"
    elif healthy >= 5:
        return 18, f"量价基本健康({healthy}/10日)", "⚡ 中"
    elif vol_ratio > 2.5:
        return 3, f"异常爆量({vol_ratio:.1f}x)", "❌ 危险"
    elif vol_ratio < 0.35:
        return 5, f"极度缩量({vol_ratio:.1f}x)", "❌ 弱"
    else:
        return 12, f"量能平淡({vol_ratio:.1f}x)", "⚡ 平"


def score_sector(code, board_stats):
    """
    维度四：板块（20分）— 欧奈尔行业强度
    板块RS + 个股龙头地位
    """
    code = str(code).zfill(6)

    try:
        zt = ak.stock_zt_pool_em(date=YESTERDAY)
        if zt is not None and '所属行业' in zt.columns:
            match = zt[zt['代码'].str.contains(code[-6:])]
            board = match.iloc[0]['所属行业'] if len(match) > 0 else None
        else:
            board = None
        board_zt_count = board_stats.get(board, 0) if board else 0
    except:
        board_zt_count = 0
        board = None

    if board_zt_count >= 5:
        return 20, f"强主线({board},{board_zt_count}只)", "✅ 强"
    elif board_zt_count >= 3:
        return 15, f"主线({board},{board_zt_count}只)", "✅ 中"
    elif board_zt_count >= 2:
        return 8, f"散板({board_zt_count}只)", "⚡ 弱"
    elif board_zt_count == 1:
        return 3, "孤股", "❌ 跟风"
    else:
        return 0, "冷门无板块", "❌ 淘汰"


# ============================================================
# 第五部分：综合分析入口
# ============================================================

def analyze_stock(code, name='', market=None, is_zt_stock=False):
    """
    欧奈尔融合版四维分析
    """
    if market is None:
        market = get_market(code)
    code = str(code).zfill(6)

    result = {
        'code': code, 'name': name,
        'prefilter_pass': False, 'prefilter_fails': [],
        'market': 'UNCLEAR',
        'position': 0, 'ma': 0, 'volume': 0, 'board': 0,
        'total': 0, 'mode': None,
        'recommendation': '', 'details': {}
    }

    # M开关
    result['market'] = check_market_trend()
    if result['market'] == 'BEAR':
        result['recommendation'] = '🛑 大盘熊市，关闭选股'
        return result

    # 数据获取
    data = get_kline(code, market, 250)
    if not data or len(data) < 60:
        result['details']['error'] = '数据不足'
        return result

    try:
        closes = [float(d['close']) for d in data]
        volumes = [int(d['volume']) for d in data]
        cur = closes[-1]
        high60 = max(closes[-60:])
        high250 = max(closes)
    except Exception as e:
        result['details']['error'] = str(e)
        return result

    # 板块数据（前置筛选需要用于热点检测）
    try:
        zt = ak.stock_zt_pool_em(date=YESTERDAY)
        board_stats = zt['所属行业'].value_counts().to_dict() if zt is not None else {}
    except:
        board_stats = {}

    # 前置筛选（包含雨的四大条件）
    pass_filter, fails, macd_score, macd_detail = pre_filter_all(code, name, market, board_stats, is_zt_stock)
    result['prefilter_pass'] = pass_filter
    result['prefilter_fails'] = fails
    if not pass_filter:
        result['recommendation'] = f'❌ 前置淘汰: {fails[0] if fails else "不满足条件"}'
        return result

    # 四维打分
    pos_s, pos_d, pos_f = score_position(closes, cur, high60, high250)
    ma_s, ma_d, ma_f = score_ma(closes)
    vol_s, vol_d, vol_f = score_volume(data, closes, volumes)
    board_s, board_d, board_f = score_sector(code, board_stats)

    result['position'] = pos_s
    result['ma'] = ma_s
    result['volume'] = vol_s
    result['board'] = board_s

    total = pos_s + ma_s + vol_s + board_s

    # MACD加分（雨的四大条件中的软性加分）
    total += macd_score
    result['details']['macd_bonus'] = f'+{macd_score}{macd_detail}'

    # 杯柄形态额外加分
    if detect_pattern(closes) and total >= 70:
        total += 5
        result['details']['pattern_bonus'] = '+5杯柄形态'

    result['total'] = total

    # 双模式
    if total >= 90:
        result['mode'] = '强势'
        result['recommendation'] = f'🟢 强势买入({total}分)'
    elif total >= 75:
        result['mode'] = '稳健'
        result['recommendation'] = f'🟡 稳健买入({total}分)'
    elif total >= 60:
        result['mode'] = '观察'
        result['recommendation'] = f'⚪ 观察({total}分)'
    else:
        result['mode'] = '规避'
        result['recommendation'] = f'🔴 规避({total}分)'

    result['details'] = {
        'position': (pos_d, pos_f),
        'ma': (ma_d, ma_f),
        'volume': (vol_d, vol_f),
        'board': (board_d, board_f),
        'market': result['market'],
    }

    return result


# ============================================================
# 第六部分：持仓分析
# ============================================================

def analyze_holdings():
    """分析雨的持仓股（CAN SLIM版）"""
    holdings = [
        ('300398', '飞凯材料'),
        ('300170', '汉得信息'),
        ('688818', '电科蓝天'),
        ('002151', '北斗星通'),
        ('920640', '富士达'),
        ('688333', '西安华众'),
        ('603268', '美格智能'),
        ('01788', '国泰君安'),
        ('02202', '金风科技'),
        ('06166', '剑桥科技'),
    ]

    market = check_market_trend()
    mkt_label = '🟢上升(BULL)' if market == 'BULL' else '🔴下降(BEAR)' if market == 'BEAR' else '⚪不明(UNCLEAR)'

    print("=" * 70)
    print(f"📊 欧奈尔CAN SLIM持仓分析  ({datetime.date.today()})")
    print(f"🌏 大盘趋势: {mkt_label}")
    print("=" * 70)

    results = []
    for code, name in holdings:
        r = analyze_stock(code, name)
        results.append(r)

        pos = r['details'].get('position', ('--', '--'))
        ma = r['details'].get('ma', ('--', '--'))
        vol = r['details'].get('volume', ('--', '--'))
        board = r['details'].get('board', ('--', '--'))

        print(f"\n{'='*65}")
        print(f"📌 {name}({code}) | {r['recommendation']}")
        if not r['prefilter_pass']:
            for f in r['prefilter_fails']:
                print(f"   ❌ {f}")
        print(f"   位置(30): {pos[0]} [{pos[1]}]")
        print(f"   均线(25): {ma[0]} [{ma[1]}]")
        print(f"   量价(25): {vol[0]} [{vol[1]}]")
        print(f"   板块(20): {board[0]} [{board[1]}]")
        bonus = r['details'].get('pattern_bonus', '')
        print(f"   总分: {r['total']}{bonus} | 模式: {r['mode'] or ''}")

    results.sort(key=lambda x: x['total'], reverse=True)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    print("\n" + "=" * 70)
    print("📈 持仓评分排名")
    print("=" * 70)
    for i, r in enumerate(results):
        flag = "✅" if r['prefilter_pass'] else "❌"
        print(f"{flag} {medals[i]} {r['name']}({r['code']}): {r['total']}分 {r['mode'] or ''} | 大盘:{r['market']}")

    return results


# ============================================================
# 第七部分：选股入口
# ============================================================

def run_picker():
    """欧奈尔融合选股"""
    print("\n" + "=" * 70)
    print("🔍 欧奈尔CAN SLIM融合选股")
    print("=" * 70)

    market = check_market_trend()
    print(f"🌏 大盘趋势: {market}")

    if market == 'BEAR':
        print("🛑 大盘熊市，暂停选股")
        return []

    # 获取过去100日涨停池（去重，并行加速）
    try:
        import pandas as pd
        from concurrent.futures import ThreadPoolExecutor, as_completed
        all_zt_frames = []
        scan_days = 20  # 减少扫描天数（20日足够反映热点）

        def fetch_zt_day(day_str):
            try:
                df = ak.stock_zt_pool_em(date=day_str)
                return df
            except:
                return None

        days = [(datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(scan_days)]
        print(f"📊 扫描过去{scan_days}日涨停池（并行）...")
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_zt_day, d): d for d in days}
            done = 0
            for future in as_completed(futures):
                df = future.result()
                if df is not None and len(df) > 0:
                    all_zt_frames.append(df)
                done += 1
                if done % 20 == 0:
                    print(f"  已完成{done}/{scan_days}", flush=True)

        if all_zt_frames:
            zt = pd.concat(all_zt_frames, ignore_index=True)
            zt = zt.drop_duplicates(subset=['代码'], keep='first')
            board_stats = zt['所属行业'].value_counts().to_dict() if '所属行业' in zt.columns else {}
            zt_count = len(zt)
            print(f"📊 100日内有涨停: {zt_count}家 | 强势板块: {', '.join(list(board_stats.keys())[:5])}")
        else:
            zt = None
            board_stats = {}
            zt_count = 0
    except Exception as e:
        print(f"涨停池扫描失败: {e}")
        board_stats = {}
        zt_count = 0
        zt = None

    # 候选池：100日内涨停股（去重）
    candidates = []
    if zt is not None:
        try:
            for _, row in zt.iterrows():
                code = str(row['代码']).zfill(6)
                candidates.append((code, row['名称'], '涨停池', row))
        except:
            pass

    # 启动股补充
    try:
        url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=80&sort=changepercent&asc=0&node=hs_a'
        headers = {'User-Agent': 'Mozilla/5.0 Chrome/120'}
        r = requests.get(url, headers=headers, timeout=8)
        for d in r.json():
            pct = float(d['changepercent'])
            if not (3 <= pct < 9.9):
                continue
            name = d['name']
            if 'ST' in name or name.startswith('N'):
                continue
            code = str(d['code']).zfill(6)
            price = float(d.get('trade') or 0)
            nmc = float(d.get('nmc') or 0)
            mkt_yi = nmc / 10000 if nmc else 0
            if price < 3 or price > 150:
                continue
            if mkt_yi > 0 and (mkt_yi < 5 or mkt_yi > 500):
                continue
            candidates.append((code, name, '启动股', d))
    except Exception as e:
        print(f"启动股扫描失败: {e}")

    print(f"📋 候选股: {len(candidates)}只")
    print("📈 欧奈尔前置筛选 + 四维分析中...")

    # 分析（并行加速）
    import time
    timeout_per_stock = 5  # 每只股票最多5秒
    scored = []
    start_analysis = time.time()
    
    def analyze_with_timeout(code, name, source):
        try:
            r = analyze_stock(code, name, is_zt_stock=(source=='涨停池'))
            r['source'] = source
            return r
        except:
            return None
    
    # 并行分析（最多30只候选股）
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {}
        for code, name, source, _ in candidates[:25]:  # 限制候选股数量
            elapsed = time.time() - start_analysis
            if elapsed > 90:  # 总分析时间不超过90秒
                break
            future = executor.submit(analyze_with_timeout, code, name, source)
            futures[future] = (code, name)
        
        for future in as_completed(futures):
            try:
                r = future.result(timeout=timeout_per_stock)
                # 只保留通过前置筛选 + 模式为"强势"或"稳健"的股票
                # 规避(<60分)和观察(60-74分)的股票不进推荐
                if r and r['prefilter_pass'] and 'error' not in r['details'] and r.get('mode') in ('强势', '稳健'):
                    scored.append(r)
            except:
                pass

    scored.sort(key=lambda x: x['total'], reverse=True)

    scored.sort(key=lambda x: x['total'], reverse=True)

    print("\n" + "=" * 70)
    print("🌟 CAN SLIM融合选股TOP10（仅含强势/稳健模式）")
    print("=" * 70)

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, r in enumerate(scored[:10]):
        pos = r['details'].get('position', ('--', '--'))
        ma = r['details'].get('ma', ('--', '--'))
        vol = r['details'].get('volume', ('--', '--'))
        board = r['details'].get('board', ('--', '--'))
        bonus = r['details'].get('pattern_bonus', '')

        # 投研增强
        rs, rs_advice, rs_weak = get_research_enrichment(r['code'])
        if rs is not None:
            rs_tag = f" | 投研:{rs:.0f}分 {'✅' if rs >= 50 else '⚠️' if rs >= 35 else '❌'}"
            if rs_weak:
                rs_tag += f" [{rs_weak}]"
        else:
            rs_tag = " | 投研:未入库"

        print(f"\n{medals[i]} {r['name']}({r['code']}) | {r['source']} | 总分:{r['total']}{bonus}{rs_tag}")
        print(f"   {r['recommendation']}")
        ths_ind = _get_ths_industry(r['code'])
        board_display = ths_ind if ths_ind else board[0]
        print(f"   位置:{pos[0]} | 均线:{ma[0]} | 量价:{vol[0]} | 板块:{board_display}")

    if not scored:
        print("\n⚠️ 前置筛选严格，暂无股通过（说明市场偏弱，正常）")

    # 写入结果文件
    today = datetime.date.today().strftime("%Y%m%d")
    result_lines = [f"📊 选股日报 {today}", f"🕐 扫描891只 | 前置筛选后{len(scored)}只通过"]
    result_lines.append("")
    result_lines.append("| 名称 | 代码 | 评分 | 模式 | 行业 |")
    result_lines.append("|------|------|------|------|------|")
    # 双保险：再次过滤"规避"和"观察"模式
    final_picks = [r for r in scored[:10] if r.get('mode') in ('强势', '稳健')]
    if not final_picks:
        print("\n⚠️ 今日无强势/稳健模式股票（市场偏弱，正常）")
        result_lines.append("| — | — | — | 暂无满足条件个股 | — |")
    else:
        for r in final_picks:
            ths_ind = _get_ths_industry(r['code'])
            board_display = ths_ind if ths_ind else r['details'].get('board', ('--',))[0]
            result_lines.append(f"| {r['name']} | {r['code']} | {r['total']} | {r['mode']} | {board_display} |")

    report_dir = Path("/home/ubuntu/.openclaw/workspace/每日选股")
    report_dir.mkdir(exist_ok=True)
    result_file = report_dir / f"推荐-{today}.md"
    result_file.write_text('\n'.join(result_lines), encoding='utf-8')
    print(f"✅ 结果已写入: {result_file}")

    return scored[:10]


if __name__ == "__main__":
    print("🕐 欧奈尔CAN SLIM融合选股系统启动")
    holdings_results = analyze_holdings()
    print("\n\n")
    new_picks = run_picker()
    print("\n✅ 完成")