#!/usr/bin/env python3
"""
每日复盘 - 整合情绪日历+消息面+四维选股+持仓评分
生成简洁清晰的复盘报告
"""
import requests
import akshare as ak
import concurrent.futures
import datetime

TODAY = datetime.date.today().strftime("%Y%m%d")

# ========== 辅助函数 ==========
def get_market(code):
    if code.startswith('92'): return 'bj'
    elif code[0] in ('0', '3'): return 'sz'
    return 'sh'

def get_today_price_from_kline(code):
    """从新浪K线接口获取今日行情（含开盘/收盘/最高/最低/涨跌）"""
    market = get_market(code)
    data = get_kline(code, market, 6)  # 取6天: 今日+前5日
    if data and len(data) >= 2:
        today = data[-1]
        yesterday = data[-2]
        cur_close = float(today['close'])
        pre_close = float(yesterday['close'])
        pct = (cur_close - pre_close) / pre_close * 100 if pre_close > 0 else 0
        return {
            'open': float(today['open']),
            'close': cur_close,
            'high': float(today['high']),
            'low': float(today['low']),
            'pct': pct,
        }
    elif data and len(data) == 1:
        today = data[-1]
        return {
            'open': float(today['open']),
            'close': float(today['close']),
            'high': float(today['high']),
            'low': float(today['low']),
            'pct': 0,
        }
    return {}


# ========== K线数据（akshare + 内存缓存）==========
_KLINE_CACHE = {}

def get_kline(code, market='sh', datalen=60):
    """获取日K线数据（akshare，带内存缓存）"""
    cache_key = f"{market}{code}"
    if cache_key in _KLINE_CACHE:
        return _KLINE_CACHE[cache_key][-datalen:]
    try:
        symbol_map = {'sh': 'sh', 'sz': 'sz', 'bj': 'bj'}
        m = symbol_map.get(market, 'sh')
        code6 = str(code).zfill(6)
        sym = f'{m}{code6}'
        from datetime import datetime, timedelta
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=datalen + 60)).strftime('%Y%m%d')
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


def get_open_change_pct(code):
    """获取竞价涨跌幅 (今日开盘价对比昨日收盘价的百分比)"""
    market = get_market(code)
    data = get_kline(code, market, 6)  # 今日+前5日
    if data and len(data) >= 2:
        today_open = float(data[-1]['open'])
        yesterday_close = float(data[-2]['close'])
        if yesterday_close > 0:
            return (today_open - yesterday_close) / yesterday_close * 100
    return None

def four_dimension_score(code, board_zt_count=0, board_name='板块待确认'):
    """四维评分一只股票"""
    market = get_market(code)
    data = get_kline(code, market, 60)
    
    result = {'code': code, 'total': 0, 'pos_score': 0, 'ma_score': 0, 'vol_score': 0, 'board_score': 0, 'risk_flags': []}
    
    if not data or len(data) < 20:
        return result
    
    try:
        closes = [float(d['close']) for d in data]
        volumes = [int(d['volume']) for d in data]
        cur_price = closes[-1]
        
        # 1. 位置 (30分)
        low30, high30 = min(closes[-30:]), max(closes[-30:])
        pos_pct = (cur_price - low30) / (high30 - low30) * 100 if high30 > low30 else 0
        if pos_pct < 20: p1, pd = 30, "低位(<20%)"
        elif pos_pct < 30: p1, pd = 28, "低(20-30%)"
        elif pos_pct < 40: p1, pd = 22, "中低(30-40%)"
        elif pos_pct < 60: p1, pd = 14, "中间(40-60%)"
        elif pos_pct < 75: p1, pd = 7, "偏高(60-75%)"
        else: p1, pd = 0, "高位(>75%)"

        result['pos_pct'] = pos_pct
        # 位置风控：30日高位>=75%剔除（稳健）；>=90%剔除（强势）
        if pos_pct >= 90:
            result['risk_flags'].append('🔴30日高位>=90%')
        elif pos_pct >= 75:
            result['risk_flags'].append('⚠️30日高位>=75%')
        result['pos_score'] = p1
        result['pos_detail'] = f"{pd}({pos_pct:.0f}%)"
        
        # 2. 均线 (25分)
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        if ma5 > ma10 > ma20: m1, md = 25, "完美多头"
        elif ma5 > ma10: m1, md = 18, "短期多头"
        elif ma5 > ma20 and closes[-6] < ma20: m1, md = 15, "反转启动"
        elif cur_price > ma20: m1, md = 10, "稳在20日线"
        else: m1, md = 0, "均线空头"
        result['ma_score'] = m1
        result['ma_detail'] = md
        
        # 3. 量价 (25分)
        vol5 = sum(volumes[-5:]) / 5
        vol20 = sum(volumes[-20:]) / 20
        vol_ratio = vol5 / vol20 if vol20 > 0 else 0
        if vol_ratio >= 1.3: v1, vd = 25, "放量健康"
        elif vol_ratio >= 1.1: v1, vd = 20, "温和放量"
        elif vol_ratio >= 0.9: v1, vd = 12, "量能平稳"
        elif vol_ratio >= 0.7: v1, vd = 6, "缩量调整"
        else: v1, vd = 0, "无量"
        result['vol_score'] = v1
        result['vol_detail'] = vd
        
        # 4. 板块 (20分)
        if board_zt_count >= 10: b1, bd = 20, "强板块"
        elif board_zt_count >= 7: b1, bd = 16, "次强板块"
        elif board_zt_count >= 5: b1, bd = 12, "中板块"
        elif board_zt_count >= 3: b1, bd = 8, "初具板块"
        elif board_zt_count >= 1: b1, bd = 4, "散板"
        else: b1, bd = 0, "冷门孤股"
        result['board_score'] = b1
        result['board_detail'] = bd
        
        result['total'] = p1 + m1 + v1 + b1
        
    except:
        pass
    
    return result

# ========== 1. 情绪日历 ==========
def get_emotion():
    try:
        zt = ak.stock_zt_pool_em(date=TODAY)
        zt_count = len(zt) if zt is not None else 0
    except:
        zt_count = 0
        zt = None
    
    zhadan_count = 0
    if zt is not None and '炸板次数' in zt.columns:
        zhadan_count = int(zt['炸板次数'].sum())
    total = len(zt) + zhadan_count if zt is not None else 0
    zhadan_rate = zhadan_count / total * 100 if total > 0 else 0
    
    try:
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")
        zt_y = ak.stock_zt_pool_em(date=yesterday)
        avg_chg = zt_y['涨跌幅'].mean() if zt_y is not None and len(zt_y) > 0 else 0
    except:
        avg_chg = 0
    
    max_lianban = int(zt['连板数'].max()) if zt is not None and '连板数' in zt.columns else 0
    
    score = 0
    signals = []
    
    if zt_count >= 50: phase, pos, sig = "发酵期", "40-60%", 1
    elif zt_count >= 30: phase, pos, sig = "退潮期", "20-40%", 0
    else: phase, pos, sig = "冰点期", "0-20%", -1
    signals.append(("涨停", zt_count, sig))
    
    if zhadan_rate > 50: signals.append(("炸板", f"{zhadan_rate:.0f}%", -2))
    elif zhadan_rate > 40: signals.append(("炸板", f"{zhadan_rate:.0f}%", -1))
    
    if avg_chg > 5: signals.append(("昨涨", f"{avg_chg:.1f}%", 1))
    elif avg_chg < -3: signals.append(("昨涨", f"{avg_chg:.1f}%", -2))
    
    if max_lianban >= 5: signals.append(("连板", f"{max_lianban}板", 1))
    
    total_score = sum(s[2] for s in signals)
    
    return {
        'phase': phase, 'position': pos, 'score': total_score,
        'zt_count': zt_count, 'zhadan_rate': zhadan_rate,
        'avg_chg': avg_chg, 'max_lianban': max_lianban,
        'signals': signals,
        'zt': zt
    }

# ========== 2. 消息面 ==========
def get_news():
    url = 'https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&tag=&track=website&pageSize=30'
    headers = {'User-Agent': 'Mozilla/5.0 Chrome/120', 'Referer': 'https://www.10jqka.com.cn/'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data.get('code') == '200' and data.get('data', {}).get('list'):
            return data['data']['list']
    except:
        pass
    return []

def extract_hot_sectors(news_list):
    keywords = {
        '电网设备': ['电网', '电力设备', '特高压'],
        '光纤通信': ['光纤', '光通信', '光缆'],
        'AI/人工智能': ['人工智能', 'AI', 'DeepSeek'],
        '稀土': ['稀土', '永磁'],
        '芯片/半导体': ['芯片', '半导体'],
        '新能源汽车': ['新能源', '锂电池', '储能'],
    }
    
    sector_count = {k: 0 for k in keywords}
    for news in news_list:
        text = news.get('title', '') + news.get('digest', '')
        for sector, kws in keywords.items():
            for kw in kws:
                if kw in text:
                    sector_count[sector] += 1
                    break
    
    sorted_sectors = sorted(sector_count.items(), key=lambda x: x[1], reverse=True)
    return [(s, c) for s, c in sorted_sectors if c > 0]

# ========== 3. 四维选股（双模式） ==========
def get_top_picks(zt, board_stats):
    candidates = []
    
    if zt is not None and len(zt) > 0:
        for _, row in zt[zt['连板数'].between(1, 2)].iterrows():
            candidates.append(row)
    
    # 补充启动股（多页获取）
    headers = {'User-Agent': 'Mozilla/5.0 Chrome/120', 'Referer': 'https://finance.sina.com.cn/'}
    try:
        for page in range(1, 6):  # 5页=500只
            url = f'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent&asc=0&node=hs_a'
            r = requests.get(url, headers=headers, timeout=10)
            for d in r.json():
                pct = float(d['changepercent'])
                turnover = float(d.get('turnoverratio') or 0)
                nmc = float(d.get('nmc') or 0)
                mkt_yi = nmc / 10000
                price = float(d.get('trade') or 0)
                name = d['name']
                code = str(d['code']).zfill(6)
                # 入口放开：竞价-4%~+6%全进候选池
                if pct >= 0 and 'ST' not in name and 3 < price < 150 and 30 <= mkt_yi <= 800 and 3 <= turnover <= 35:
                    candidates.append({'代码': code, '名称': name, '连板数': 0, '所属行业': '启动股', '当前涨幅': pct})
    except:
        pass
    
    # 四维评分 + 竞价风控
    scored = []
    for row in candidates[:50]:
        code = str(row.get('代码', row.get('code', ''))).zfill(6)
        name = row.get('名称', row.get('name', ''))
        board = str(row.get('所属行业', '板块待确认'))
        if board == 'nan' or board == '启动股':
            board = '板块待确认'
        board_zt = board_stats.get(board, 0)
        
        result = four_dimension_score(code, board_zt, board)
        result['name'] = name
        result['code'] = code
        result['lianban'] = row.get('连板数', 0)
        result['risk_flags'] = []
        
        # 计算竞价%
        auction_pct = get_open_change_pct(code)
        result['auction_pct'] = auction_pct
        
        # 竞价风控：>10%或<-4%才剔除
        if auction_pct is not None and auction_pct > 10:
            result['risk_flags'].append('🔴竞价超高开>10%')

        elif auction_pct is not None and auction_pct < -4:
            result['risk_flags'].append('🔴竞价大幅低开<-4%')
        
        if result['total'] > 0:
            scored.append(result)
    
    # 双模式筛选
    conservative = [s for s in scored if not any('🔴' in f or '⚠️' in f for f in s.get('risk_flags', []))]
    strong = [s for s in scored if not any('🔴' in f for f in s.get('risk_flags', []))]
    
    conservative.sort(key=lambda x: x['total'], reverse=True)
    strong.sort(key=lambda x: x['total'], reverse=True)
    
    return conservative[:5], strong[:5]

# ========== 4. 持仓评分 ==========
HOLDINGS = [
    ('300398', '飞凯材料', '创业板'),
    ('300170', '汉得信息', 'AI应用/英伟达合作'),
    ('688818', '电科蓝天', '科创板'),
    ('002151', '北斗星通', '卫星导航/北斗芯片'),
    ('920640', '富士达', '射频连接器'),
    ('688333', '西安华众', '金属增材制造'),
    ('603268', '美格智能', '物联网'),
    ('001788', '国泰君安', '券商'),
    ('02202', '金风科技', '港股通'),
    ('006166', '剑桥科技', '港股通'),
]

def _save_holdings_review_to_db(holdings, date_str, db_path='/home/ubuntu/.openclaw/workspace/data/stock_pool.db'):
    """保存持仓复盘到数据库（含行业）"""
    import sqlite3
    from datetime import datetime
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for h in holdings:
        code = h.get("code","")
        # 从缓存行业表查行业
        cur.execute("SELECT industry FROM stock_industry WHERE code=?", (code,))
        row = cur.fetchone()
        board = row[0][:14] if row and row[0] else ""
        cur.execute(
            "INSERT OR REPLACE INTO stock_holdings_review (review_date, code, name, score, open_price, close_price, change_pct, status, board, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date_str, code, h.get("name",""), h.get("total",0),
             h.get("open",0), h.get("close",0), h.get("pct",0), h.get("status","") or h.get("board_detail",""), board, now))
    conn.commit()
    conn.close()

def score_holdings(price_map, board_stats):
    results = []
    for item in HOLDINGS:
        code, name, *extra = item
        result = four_dimension_score(code, board_stats.get('板块待确认', 0))
        result['name'] = name
        result['code'] = code
        
        # 获取今日价格（从K线）
        today_data = get_today_price_from_kline(code)
        if today_data:
            result['close'] = today_data.get('close', 0)
            result['open'] = today_data.get('open', 0)
            result['high'] = today_data.get('high', 0)
            result['low'] = today_data.get('low', 0)
            if result['open'] > 0:
                result['pct'] = (result['close'] - result['open']) / result['open'] * 100
        
        results.append(result)
    
    results.sort(key=lambda x: x['total'], reverse=True)
    return results

# ========== 生成报告 ==========
def generate_report(emotion, hot_sectors, conservative, strong, holdings, price_map):
    now = datetime.datetime.now()
    date_str = now.strftime("%m月%d日")
    
    # 信号描述
    sig_parts = []
    for name, val, delta in emotion['signals']:
        if delta > 0: sig_parts.append(f"{name}{val}(+{delta})")
        elif delta < 0: sig_parts.append(f"{name}{val}({delta})")
    
    report = f"""📊 **每日复盘 {date_str}**

**【市场情绪】**
退潮期 · 仓位{emotion['position']} · 打分{emotion['score']}分
炸板率{emotion['zhadan_rate']:.0f}% | 昨涨停{emotion['avg_chg']:.1f}% | 最高{emotion['max_lianban']}板

**【今日热点】**
{'/'.join([s for s, c in hot_sectors[:3]]) if hot_sectors else '数据获取中'}
    """
    report += f"""
**【今日选股】**

**稳健模式**（30日高位>=75%剔除）
| # | 名称 | 代码 | 板 | 评分 | 位置 | 竞价% | 收盘 | 振幅 |
|---|---|---|---|---|---|---|---|---|---|
"""
    medals = ["1","2","3","4","5"]
    for i, p in enumerate(conservative):
        code = p["code"]
        today = get_today_price_from_kline(code)
        close = today.get("close", 0)
        high = today.get("high", 0)
        low = today.get("low", 0)
        open_p = today.get("open", 0)
        amp = (high - low) / open_p * 100 if close > 0 and open_p > 0 and high > 0 and low > 0 else 0
        auc_val = p.get('auction_pct')
        auc_str = f"{auc_val:+0.1f}%" if auc_val is not None else "—"
        close_str = f"{close:.2f}" if close > 0 else "—"
        amp_str = f"{amp:+0.1f}%"
        auc_color = "green" if auc_val is not None and auc_val > 0 else "red" if auc_val is not None and auc_val < 0 else ""
        amp_color = "green" if amp > 0 else "red" if amp < 0 else ""
        report += f"| {medals[i]} | {p['name']} | {code} | {p['lianban']}板 | **{p['total']}** | {p['pos_detail']} | {auc_str} | {close_str} | {amp_str} |\n"

    report += f"""
**强势模式**（30日高位>=90%剔除）
| # | 名称 | 代码 | 板 | 评分 | 位置 | 竞价% | 收盘 | 振幅 |
|---|---|---|---|---|---|---|---|---|---|
"""
    for i, p in enumerate(strong[:5]):
        code = p["code"]
        today = get_today_price_from_kline(code)
        close = today.get("close", 0)
        high = today.get("high", 0)
        low = today.get("low", 0)
        open_p = today.get("open", 0)
        amp = (high - low) / open_p * 100 if close > 0 and open_p > 0 and high > 0 and low > 0 else 0
        auc_val = p.get('auction_pct')
        auc_str = f"{auc_val:+0.1f}%" if auc_val is not None else "—"
        close_str = f"{close:.2f}" if close > 0 else "—"
        amp_str = f"{amp:+0.1f}%"
        flags = "".join([f.replace('⚠️','[偏]') for f in p.get('risk_flags',[]) if '⚠️' in f])
        auc_color = "green" if auc_val is not None and auc_val > 0 else "red" if auc_val is not None and auc_val < 0 else ""
        amp_color = "green" if amp > 0 else "red" if amp < 0 else ""
        report += f"| {medals[i]} | {p['name']} | {code} | {p['lianban']}板 | **{p['total']}** | {p['pos_detail']} | {auc_str} | {close_str} | {amp_str} |\n"




    # === 持仓评分 ===
    report += f"""
**【持仓评分】**
| 名称 | 代码 | 评分 | 竞价 | 收盘 | 涨跌 | 状态 |
|---|---|---|---|---|---|---|---|
"""
    for h in holdings:
        code = h['code']
        today = get_today_price_from_kline(code)
        open_str = f"{today.get('open', 0):.2f}" if today.get('open', 0) > 0 else "—"
        close = today.get('close', 0)
        pct = today.get('pct', 0)
        
        close_str = f"{close:.2f}" if close > 0 else "—"
        pct_color = "green" if pct > 0 else "red" if pct < 0 else ""
        pct_str = f"{pct:+0.2f}%"
        
        status = "✅" if h['total'] >= 55 else "⚠️" if h['total'] >= 40 else "🔴"
        
        report += f"| {h['name']} | {code} | **{h['total']}** | {open_str} | {close_str} | {pct_str} | {status} |\n"
    
    # 明日注意
    high_pos = [h for h in holdings if h['pos_score'] == 0]
    warn_parts = []
    if high_pos:
        warn_parts.append(f"{'/'.join([h['name'] for h in high_pos])}高位注意")
    if emotion['score'] <= 0:
        warn_parts.append("情绪偏弱仓位严控")
    
    report += f"""
**【明日注意】**
{' | '.join(warn_parts) if warn_parts else '无'}
"""
    
    return report

def run():
    print("🕐 每日复盘生成中...")
    
    # 1. 情绪
    print("  情绪日历...")
    emotion = get_emotion()
    board_stats = emotion['zt']['所属行业'].value_counts().to_dict() if emotion['zt'] is not None and '所属行业' in emotion['zt'].columns else {}
    
    # 2. 价格（需要尽早获取）
    print("  获取今日行情...")
    
    # 3. 消息面
    print("  消息面...")
    news_list = get_news()
    hot_sectors = extract_hot_sectors(news_list)
    
    # 4. 选股
    print("  四维选股...")
    conservative, strong = get_top_picks(emotion['zt'], board_stats)
    
    # 4. 持仓（含K线获取今日价格）
    print("  持仓评分...")
    holdings = score_holdings({}, board_stats)
    
    # 6. 生成报告
    report = generate_report(emotion, hot_sectors, conservative, strong, holdings, {})
    print("\n" + report)
    
    # 保存
    import os
    os.makedirs("/home/ubuntu/.openclaw/workspace/每日选股", exist_ok=True)
    fname = f"/home/ubuntu/.openclaw/workspace/每日选股/复盘-{TODAY}.md"
    with open(fname, "w") as f:
        f.write(report)
    print(f"\n✅ 已保存: {fname}")
    _save_holdings_review_to_db(holdings, TODAY)

    # 微信推送
    import subprocess
    push_msg = report if len(report) <= 1800 else report[:1800] + "\n...(内容过长已截断)"
    try:
        r = subprocess.run(
            ['hermes', 'send', '--to', f'weixin:{WECHAT_TARGET}', push_msg],
            capture_output=True, text=True, timeout=30
        )
        print(f"\n[推送] {'成功 ✅' if r.returncode == 0 else '失败: ' + r.stderr[:80]}")
    except Exception as e:
        print(f"\n[推送] 异常: {e}")

    return report


WECHAT_TARGET = 'o9cq80z4Nv9VbLwPfTbahT8986V0@im.wechat'

if __name__ == "__main__":
    run()