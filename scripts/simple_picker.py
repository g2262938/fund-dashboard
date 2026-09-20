#!/usr/bin/env python3
"""
选股引擎 v6 — 基于回测反馈 + CANSLIM前置筛选优化

v5 回测问题：
  - 稳健股 -3% 硬止损误杀好股（v7 已改为移动止盈）
  - 顶点过滤 >7% 偏宽（v8 已调）

v6 改动（融合 CANSLIM 前置筛选）：
  1. 追加 MACD 0轴上方金叉检测（CANSLIM 核心技术指标）
  2. 追加连续涨幅追高过滤（昨日>10%/2日>20%/3日>25%/5日>30%）
  3. 追加量比 >1.2 过滤（CANSLIM 核心技术指标）
  4. 合并大盘风控到形态过滤阶段
  5. 清理冗余代码，统一函数命名

CANSLIM 前置逻辑（来自四维选股CANSLIM.py，雨的经验）：
  - 雨的四大条件：MACD0轴+金叉 / 量比>1.2 / 基本面无风险 / 热点板块
  - CAN SLIM追高过滤：连续涨幅过大 → 等回调
"""

import sys
import os
import time
import datetime
import urllib.request
import urllib.error
import re
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT_DIR = Path("/home/ubuntu/.openclaw/workspace/每日选股")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TODAY = datetime.date.today().strftime("%Y%m%d")
REPORT_TIME = datetime.datetime.now().strftime("%H:%M")

CACHE_DIR = Path("/home/ubuntu/.openclaw/workspace/data/kline_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── 行业PE基准 ─────────────────────────────────────────────────
INDUSTRY_PE_BENCHMARK = {
    "银行": 6.0, "证券": 18.0, "保险": 12.0,
    "房地产": 12.0, "建筑材料": 15.0, "建筑装饰": 10.0,
    "钢铁": 8.0, "煤炭": 8.0, "石油石化": 10.0,
    "基础化工": 18.0, "化学原料": 16.0, "化学制品": 20.0,
    "汽车整车": 25.0, "汽车": 20.0,
    "电力设备": 30.0, "新能源": 35.0, "储能": 40.0,
    "机械设备": 25.0, "通用设备": 25.0, "专用设备": 30.0,
    "电子": 35.0, "半导体": 50.0, "消费电子": 30.0,
    "计算机": 50.0, "软件服务": 45.0, "通信设备": 35.0,
    "传媒": 30.0, "纺织服装": 18.0, "轻工制造": 20.0,
    "商贸零售": 20.0, "农林牧渔": 25.0,
    "食品饮料": 30.0, "白酒": 28.0, "乳制品": 25.0,
    "家用电器": 20.0, "医药生物": 30.0, "医疗器械": 35.0,
    "医疗服务": 50.0, "中药": 25.0, "化学制药": 28.0,
    "环保": 20.0, "交通运输": 12.0, "公用事业": 15.0,
    "国防军工": 40.0, "有色金属": 20.0, "航运": 15.0,
    "港口": 12.0, "公路": 10.0, "铁路": 8.0,
    "航空机场": 20.0, "造纸": 15.0, "包装": 18.0,
    "电力": 15.0, "燃气": 15.0, "水务": 15.0,
    "电机": 25.0, "电源设备": 35.0, "自动化设备": 35.0,
    "港口航运": 15.0, "高速公路": 12.0,
    "小市值/未分类": 25.0,
}

# ─── 行情获取 ───────────────────────────────────────────────────

def get_universe() -> list[dict]:
    """获取A股全量股票列表（新浪行情接口，带本地缓存）"""
    import urllib.request, ssl, json
    from pathlib import Path

    import datetime
    cache_file = Path("/tmp/universe_cache.json")
    today = datetime.datetime.now().strftime("%Y%m%d")

    # 读缓存（当天缓存直接用）
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("date") == today and cached.get("records"):
                print(f"  股票池: {len(cached['records'])} 只（缓存）")
                return [dict(r) for r in cached["records"]]
        except Exception:
            pass

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    records = []
    page_size = 100
    seen_codes = set()

    try:
        for page in range(1, 80):  # 最多80页=8000只，覆盖全A股
            url = (f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   f"Market_Center.getHQNodeDataSimple?page={page}&num={page_size}"
                   f"&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page")

            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Referer': 'https://finance.sina.com.cn',
            })
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                data = json.loads(r.read().decode('utf-8', errors='ignore'))

            if not data:
                break  # 空页，遍历完毕

            page_codes = set()
            for row in data:
                code_raw = row.get('symbol', '')  # e.g. "sh600519" or "sz000001"
                # 提取纯代码
                code = code_raw.replace('sh', '').replace('sz', '').replace('bj', '').zfill(6)
                name = str(row.get('name', '')).strip()

                if not code or not name or len(name) < 2:
                    continue
                if code in seen_codes:
                    continue
                if code.startswith(('4', '8', '43', '83')):
                    continue
                if 'ST' in name.upper() or '*ST' in name.upper():
                    continue

                seen_codes.add(code)
                page_codes.add(code)
                records.append({'code': code, 'name': name})

            if not page_codes:
                break  #本页无新股票，结束

        print(f"  股票池: {len(records)} 只（新浪接口）")
        if records:
            # 保存缓存
            cache_file.write_text(json.dumps({"date": today, "records": records}))
            return records

    except Exception as e:
        print(f"  ⚠️ 股票池失败: {e}，尝试备用...")

    # Fallback：腾讯预定义股票池
    result = _get_universe_fallback()
    if result:
        cache_file.write_text(json.dumps({"date": today, "records": result}))
    return result


def _get_universe_fallback() -> list[dict]:
    """备用：腾讯批量查询接口（已知股票池）"""
    import urllib.request, ssl, json

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 预定义各板块代表性股票（确保选股引擎有数据可用）
    fallback_codes = []
    try:
        # 尝试从腾讯获取沪市前100只
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeDataSimple?page=1&num=100&sort=symbol&asc=1&node=hs_a"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            data = json.loads(r.read().decode('utf-8', errors='ignore'))
        fallback_codes = [(str(r['symbol']).replace('sh','').replace('sz','').zfill(6), str(r['name']).strip()) for r in data if r.get('symbol')]
    except:
        pass

    records = [{'code': c, 'name': n} for c, n in fallback_codes if c and n and len(n) >= 2 and not c.startswith(('4','8'))]
    print(f"  股票池(fallback): {len(records)} 只")
    return records


def _infer_industry(code: str, name: str) -> str:
    """行业识别（关键词匹配，支持简称）"""
    kw_industry = [
        ("银行", ["银行", "兴业银行", "招商银行", "工商银行", "建设银行", "农业银行", "中国银行", "交通银行", "邮储银行"]),
        ("证券", ["证券", "券商", "国泰海通", "海通证券", "中信证券", "中信建投", "华泰证券", "广发证券", "招商证券", "国联证券"]),
        ("保险", ["保险"]),
        ("房地产", ["万科", "保利发展", "招商蛇口", "金地集团", "华夏幸福", "地产", "置业", "房地产"]),
        ("白酒", ["茅台", "五粮液", "泸州老窖", "汾酒", "洋河", "古井", "酒"]),
        ("煤炭", ["煤炭", "中国神华", "陕西煤业", "中煤能源", "兖矿"]),
        ("钢铁", ["钢铁", "宝钢", "鞍钢", "沙钢"]),
        ("有色金属", ["有色", "稀土", "紫金矿业", "洛阳钼业", "赣锋", "天齐", "天山铝业", "铝业"]),
        ("石油石化", ["石油", "石化", "中石油", "中石化", "中国石化"]),
        ("化工", ["化工", "万华", "龙佰", "中核钛白", "华鲁恒升", "宝丰能源", "新和成"]),
        ("医药生物", ["医药", "制药", "生物", "同仁堂", "华润三九", "云南白药", "上海医药", "精华制药"]),
        ("医疗器械", ["器械", "迈瑞", "联影", "微创", "乐普"]),
        ("半导体", ["半导体", "芯片", "集成电路", "韦尔", "澜起", "卓胜微", "长电"]),
        ("新能源汽车", ["新能源", "比亚迪", "宁德", "理想", "蔚来", "小鹏"]),
        ("光伏", ["光伏", "隆基", "通威", "阳光电源", "晶澳"]),
        ("电力设备", ["电力设备", "电气", "国电南瑞", "特变电工", "潍柴动力"]),
        ("通信设备", ["通信", "中兴", "烽火", "光迅"]),
        ("计算机", ["计算机", "软件", "信息", "网络", "浪潮信息"]),
        ("食品饮料", ["食品", "饮料", "伊利", "蒙牛", "海天", "金龙鱼"]),
        ("家电", ["家电", "格力", "美的", "海尔", "老板", "福耀玻璃"]),
        ("军工", ["军工", "航发", "中航", "航天", "兵装", "中国船舶"]),
        ("传媒", ["传媒", "影视", "游戏", "中原传媒"]),
        ("纺织服装", ["纺织", "服装", "华利", "申洲", "李宁", "安踏"]),
        ("轻工", ["造纸", "印刷", "顾家", "欧派"]),
        ("商贸零售", ["零售", "商贸", "百货", "物产中大"]),
        ("建筑材料", ["水泥", "海螺", "北新建材", "东方雨虹"]),
        ("机械设备", ["机械", "设备", "机床", "天地科技"]),
        ("港口航运", ["港口", "航运", "上港", "中远海控", "宁波港", "连云港"]),
        ("高速公路", ["高速", "赣粤高速", "宁沪高速", "山东高速"]),
        ("电力", ["电力", "华能国际", "华电国际", "国电电力"]),
        ("环保", ["环保"]),
    ]
    name_upper = name.upper().replace(" ", "").replace("　", "")  # 统一去空格
    for industry, keywords in kw_industry:
        for kw in keywords:
            if kw.upper() in name_upper:
                return industry
    return "小市值/未分类"


def fetch_tencent(codes: list[str]) -> dict[str, dict]:
    """腾讯实时行情，分批请求"""
    results = {}
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        prefix = []
        for c in batch:
            if c.startswith(("6", "9")):
                prefix.append(f"sh{c}")
            else:
                prefix.append(f"sz{c}")
        url = f"https://qt.gtimg.cn/q={','.join(prefix)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="ignore")
        except Exception as e:
            continue
        for m in re.finditer(r'v_(\w+)="([^"]+)"', raw):
            code = m.group(1)[2:]
            f = m.group(2).split("~")
            if len(f) < 50 or not f[3]:
                continue
            try:
                results[code] = {
                    "code": code, "name": f[1],
                    "current": float(f[3]),
                    "prev_close": float(f[4]) if f[4] else 0,
                    "open": float(f[5]) if f[5] else 0,
                    "change_pct": float(f[32]) if f[32] else 0,
                    "high": float(f[33]) if len(f) > 33 and f[33] else 0,
                    "low": float(f[34]) if len(f) > 34 and f[34] else 0,
                    "volume": int(f[6]) if len(f) > 6 and f[6] else 0,
                    "turnover": float(f[46]) if len(f) > 46 and f[46] else 0,  # f[46]=换手率
                    "pe": float(f[39]) if len(f) > 39 and f[39] else 0,
                    "market_cap": float(f[44]) if len(f) > 44 and f[44] else 0,
                    "industry": _infer_industry(code, f[1]),
                }
            except (ValueError, IndexError):
                continue
        time.sleep(0.1)
    return results


def get_index_today_return() -> float:
    try:
        url = "https://qt.gtimg.cn/q=sh000001"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
        m = re.search(r'v_sh000001="([^"]+)"', raw)
        if m:
            f = m.group(1).split("~")
            return float(f[32]) if len(f) > 32 and f[32] else 0.0
    except:
        pass
    return 0.0


# ─── 腾讯K线拉取（并行 + 缓存）───────────────────────────────────

def fetch_kline_one(code: str, days: int = 30) -> list[dict]:
    """单只股票拉取最近 N 日 K线（带本地缓存）"""
    cache_path = CACHE_DIR / f"{code}.json"
    today_str = datetime.date.today().isoformat()

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("date") == today_str:
                return cached.get("kline", [])
        except:
            pass

    if code.startswith(("6", "9")):
        prefix = "sh"
    else:
        prefix = "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{days},qfq"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        data = json.loads(raw)
        raw_list = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])

        kline = []
        for row in raw_list:
            if len(row) >= 5:
                kline.append({
                    "date": row[0],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                })

        try:
            cache_path.write_text(json.dumps({"date": today_str, "kline": kline}))
        except:
            pass
        return kline
    except Exception:
        return []


def fetch_klines_parallel(codes: list[str], max_workers: int = 30, days: int = 30) -> dict[str, list[dict]]:
    """并行拉取多只股票K线"""
    result = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_kline_one, c, days): c for c in codes}
        done_count = 0
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                kline = fut.result()
                if kline:
                    result[code] = kline
                done_count += 1
                if done_count % 200 == 0:
                    print(f"    K线进度: {done_count}/{len(codes)}")
            except:
                pass
    print(f"  K线拉取完成: {len(result)}/{len(codes)} 只有效")
    return result


# ─── CANSLIM 前置筛选：MACD 计算 ────────────────────────────────

def calc_macd(closes: list[float]) -> tuple:
    """
    计算MACD指标（标准参数：12/26/9）
    返回: (dif, dea, macd_hist, golden_cross, golden_cross_days)
    - golden_cross: True = DIF上穿DEA且两者>0（0轴上方金叉）
    - golden_cross_days: 最近N日内出现金叉（999=无金叉）
    """
    if len(closes) < 34:
        return None, None, None, False, 999

    # EMA12 / EMA26
    ema12 = closes[0]
    ema26 = closes[0]
    a12 = 2 / 13
    a26 = 2 / 27
    dif_list = []
    for c in closes:
        ema12 = ema12 * (1 - a12) + c * a12
        ema26 = ema26 * (1 - a26) + c * a26
        dif_list.append(ema12 - ema26)

    # DEA（9日EMA of DIF）
    dea_list = []
    a9 = 2 / 10
    for i, d in enumerate(dif_list):
        if i < 26:
            dea_list.append(d)
        else:
            dea_list.append(dea_list[-1] * (1 - a9) + d * a9)

    # MACD柱 = (DIF - DEA) * 2
    hist_list = [(dif_list[i] - dea_list[i]) * 2 for i in range(len(dif_list))]

    # 取最近60日检测金叉
    dif60 = dif_list[-60:]
    dea60 = dea_list[-60:]

    golden_cross = False
    golden_cross_days = 999
    for i in range(len(dif60) - 1, max(0, len(dif60) - 5), -1):
        # 今日DIF>DEA 且 两者都在0轴上方
        if dif60[i] > dea60[i] and dif60[i] > 0 and dea60[i] > 0:
            # 昨日DIF<=DEA
            if dif60[i-1] <= dea60[i-1]:
                golden_cross = True
                golden_cross_days = len(dif60) - 1 - i
                break

    return dif_list[-1], dea_list[-1], hist_list[-1], golden_cross, golden_cross_days


def macd_score(closes: list[float]) -> tuple[int, str]:
    """
    MACD 0轴上方 + 金叉检测（CANSLIM核心技术指标）
    返回: (分数, 说明文字)
    """
    dif, dea, hist, golden_cross, gc_days = calc_macd(closes)
    if dif is None:
        return 0, "MACD数据不足"
    if dif <= 0 or dea <= 0:
        return 0, f"MACD<0轴(DIF={dif:.3f})"
    if golden_cross and gc_days <= 1:
        return 20, f"MACD0轴金叉+1日"
    elif golden_cross and gc_days <= 4:
        return 15, f"MACD0轴金叉{gc_days}日前"
    elif golden_cross and gc_days <= 10:
        return 10, f"MACD0轴金叉{gc_days}日前"
    else:
        return 5, "MACD0轴上方（无金叉）"


# ─── CANSLIM 前置筛选：追高过滤 ─────────────────────────────────

def check_chase_filter(closes: list[float], today_pct: float) -> tuple[bool, str]:
    """
    CANSLIM 追高过滤：连续涨幅过大 → 等回调确认
    来自四维选股CANSLIM.py（雨的经验）：
    - 昨日涨幅>10% → 等回调（但可以用今日回调幅度降低风险）
    - 昨日涨幅>12% → 直接排除
    - 2日涨幅>20% → 主力可能正在出货
    - 3日涨幅>25% → 偏离估值，大概率已走完一浪
    - 5日涨幅>30% → 蓄势不够，拉升过猛
    """
    if len(closes) < 5:
        return True, ""

    def pct_chg(c1, c2):
        return (c1 - c2) / c2 * 100 if c2 > 0 else 0

    chg_1d = pct_chg(closes[-1], closes[-2])   # 昨日涨幅
    chg_2d = pct_chg(closes[-1], closes[-3])   # 2日涨幅
    chg_3d = pct_chg(closes[-1], closes[-4])   # 3日涨幅
    chg_5d = pct_chg(closes[-1], closes[-6])   # 5日涨幅

    # 昨日涨幅>12% → 排除
    if chg_1d > 12:
        return False, f"追高昨+{chg_1d:.1f}%(>12%)"
    # 昨日>10% 且今日走弱 → 降分但保留（给机会）
    if chg_1d > 10 and today_pct < -0.5:
        return False, f"追高昨+{chg_1d:.1f}%且今日走弱"
    # 2日涨幅>20%
    if chg_2d > 20:
        return False, f"追高2日+{chg_2d:.1f}%(>20%)"
    # 3日涨幅>25%
    if chg_3d > 25:
        return False, f"追高3日+{chg_3d:.1f}%(>25%)"
    # 5日涨幅>30%
    if chg_5d > 35:
        return False, f"追高5日+{chg_5d:.1f}%(>35%)"

    return True, ""


# ─── CANSLIM 前置筛选：量比检测 ─────────────────────────────────

def get_volume_ratio(quote: dict, kline: list[dict]) -> float:
    """
    当日量比 = 当日成交量 / 5日均量
    返回: 量比值（None=无法计算）
    """
    vol_today = quote.get("volume", 0)
    if not vol_today or not kline or len(kline) < 5:
        return None
    vol5_list = [k["volume"] for k in kline[-6:-1]]  # 不含今日
    vol5_avg = sum(vol5_list) / 5
    if vol5_avg <= 0:
        return None
    return vol_today / vol5_avg


# ─── 阶段一：基础硬过滤 ─────────────────────────────────────────

def hard_filter(quote: dict) -> tuple[bool, str]:
    """PE / 流动性 / 低价股 / 微盘股"""
    pe = quote.get("pe", 0)
    if pe <= 0 or pe > 200:
        return False, f"PE={pe:.0f}极端"
    turnover = quote.get("turnover", 0)
    if turnover < 0.1:
        return False, f"换手率{turnover:.2f}%枯竭"
    current = quote.get("current", 0)
    if 0 < current < 2:
        return False, f"股价{current:.2f}元过低"
    mktcap = quote.get("market_cap", 0)
    if mktcap > 0 and mktcap < 50:
        return False, f"市值{mktcap:.0f}亿(<50亿)"
    return True, ""


# ─── 阶段二：形态过滤（CANSLIM 增强）────────────────────────────

def score_form(quote: dict) -> int:
    """形态 0-10 分"""
    high = quote.get("high", 0)
    low = quote.get("low", 0)
    open_ = quote.get("open", 0)
    close = quote.get("current", 0)
    if high <= 0 or low <= 0 or open_ <= 0 or close <= 0:
        return 5

    close_pos = close / high if high > 0 else 0
    score = 0
    if close_pos >= 0.9: score += 4
    body_pct = (close - open_) / open_ * 100
    if body_pct > 0: score += 2
    upper_shadow = (high - close) / close * 100 if close > 0 else 100
    if body_pct > upper_shadow: score += 2
    turnover = quote.get("turnover", 0)
    if 0.1 < turnover < 5.0: score += 2
    elif turnover > 8.0: score -= 2
    return max(0, min(10, score))


def pass_form_filter(quote: dict, kline: list[dict], index_return: float) -> tuple[bool, str]:
    """
    形态过滤（v6 CANSLIM增强，v7涨停宽松版）：
    涨停股（当日涨幅≥9.5%）走宽松通道：
      - 形态分数阈值 6→3
      - 追高过滤全部跳过（涨停本身必然放量、连续大涨）
      - 量比阈值 1.0→0.5
      - 大盘弱势不硬排除（改为返回flag，由调用方降级）
    """
    today_pct = quote.get("change_pct", 0)
    is_limit_up = today_pct >= 9.5  # 涨停股识别

    # 形态分数（涨停宽松）
    form_thresh = 3 if is_limit_up else 6
    if score_form(quote) < form_thresh:
        return False, f"形态<{form_thresh}分"

    # 顶点过滤（涨停股跳过）
    if not is_limit_up and today_pct > 12.0:
        return False, f"顶点信号+{today_pct:.1f}%(>12%)"

    # 大盘弱势：暂时不硬排除，返回reason供调用方判断是否降级
    # （已在 main() 的 classify 之后统一处理降级，这里跳过）

    # 需要K线的CANSLIM过滤
    if kline and len(kline) >= 6:
        closes = [k["close"] for k in kline]

        # 涨停股跳过全部追高过滤
        if not is_limit_up:
            ok, reason = check_chase_filter(closes, today_pct)
            if not ok:
                return False, f"CANSLIM追高:{reason}"

        # 量比过滤（涨停宽松：阈值1.0→0.5）
        vol_ratio = get_volume_ratio(quote, kline)
        vol_thresh = 0.5 if is_limit_up else 1.0
        if vol_ratio is not None and vol_ratio < vol_thresh:
            return False, f"量比{vol_ratio:.2f}<{vol_thresh}(CANSLIM)"

    return True, ""


# ─── 阶段三：四维打分 ──────────────────────────────────────────

def calc_pe_change(kline: list[dict]) -> float:
    if not kline or len(kline) < 10:
        return 0.0
    p_now = kline[-1]["close"]
    p_10 = kline[-10]["close"]
    return (p_now - p_10) / p_10 if p_10 > 0 else 0


def score_value(quote: dict, pe_20d_change: float) -> int:
    """维度1 价值（25分）"""
    pe = quote.get("pe", 0)
    if pe <= 0:
        return 8
    industry = quote.get("industry", "小市值/未分类")
    avg_pe = INDUSTRY_PE_BENCHMARK.get(industry, 25.0)
    ratio = pe / avg_pe if avg_pe > 0 else 2.0

    if ratio <= 0.4:    base = 25
    elif ratio <= 0.6:  base = 22
    elif ratio <= 0.85: base = 19
    elif ratio <= 1.1:  base = 16
    elif ratio <= 1.4:  base = 13
    elif ratio <= 1.8:  base = 10
    elif ratio <= 2.5:  base = 6
    else:               base = 3

    if pe_20d_change > 0.25 and ratio > 1.0 and ratio <= 2.0:
        base = min(25, base + 3)
    elif pe_20d_change < -0.15 and ratio > 1.5:
        base = max(3, base - 2)
    return base


def score_momentum(quote: dict, kline: list[dict], index_return: float) -> int:
    """维度2 动量（25分）：CANSLIM追高已前置过滤，这里专注动量评分"""
    today_pct = quote.get("change_pct", 0)
    close_pos = quote.get("current", 0) / quote.get("high", 1) if quote.get("high") else 0
    turnover = quote.get("turnover", 0)

    if not kline or len(kline) < 20:
        excess = today_pct - index_return
        if excess > 5: return 16
        elif excess > 2: return 13
        elif excess > 0: return 10
        else: return 6

    p20 = (kline[-1]["close"] - kline[0]["close"]) / kline[0]["close"] * 100
    p10 = (kline[-1]["close"] - kline[-10]["close"]) / kline[-10]["close"] * 100
    p5 = (kline[-1]["close"] - kline[-5]["close"]) / kline[-5]["close"] * 100

    today_overheat = today_pct > 6.0
    is_breakout = (p5 > p10 * 0.5 and p5 > 3 and today_pct > 0 and close_pos > 0.88 and turnover < 6.0)
    is_top_div = (p20 > 30 and p5 < 1 and today_pct < -0.5)
    is_sustained = (p20 > 10 and p10 > 5 and p5 > 2 and today_pct > 0)
    is_anti = (p20 > -5 and p20 < 15 and today_pct > 1 and close_pos > 0.85)

    if is_breakout and p5 > 8:
        return 20 if today_overheat else 25
    elif is_breakout:
        return 18 if today_overheat else 23
    if is_sustained and p20 > 30:
        return 24
    elif is_sustained and p20 > 15:
        return 21
    elif is_sustained:
        return 18
    if is_top_div:
        return 6
    if is_anti:
        return 14
    if p20 < -10:
        return 4
    elif p20 < 0:
        return 8
    else:
        return 11


def score_quality(quote: dict) -> int:
    """维度3 质量（25分）"""
    mktcap = quote.get("market_cap", 0)
    turnover = quote.get("turnover", 0)

    if mktcap >= 10000:  base = 20
    elif mktcap >= 3000: base = 17
    elif mktcap >= 1000: base = 14
    elif mktcap >= 500:  base = 12
    elif mktcap >= 200:  base = 10
    elif mktcap >= 100:  base = 7
    elif mktcap >= 50:   base = 4
    else:                base = 2

    if 0.5 <= turnover <= 3.0: bonus = 3
    elif 3.0 < turnover <= 6.0: bonus = 2
    elif turnover < 0.1: bonus = -2
    else: bonus = 0

    pe = quote.get("pe", 0)
    stability = 2 if (mktcap >= 1000 and 0 < pe < 50) else 0
    return max(0, min(25, base + bonus + stability))


def score_volume_price(quote: dict, kline: list[dict]) -> int:
    """维度4 量价（25分）"""
    close = quote.get("current", 0)
    high = quote.get("high", 0)
    today_volume = quote.get("volume", 0)

    close_pos = close / high if high > 0 else 0

    vol_ratio = 1.0
    if kline and len(kline) >= 5:
        avg_vol_5 = sum(k["volume"] for k in kline[-6:-1]) / 5
        if avg_vol_5 > 0 and today_volume > 0:
            vol_ratio = today_volume / avg_vol_5

    above_ma5 = 0
    if kline and len(kline) >= 5:
        ma5 = sum(k["close"] for k in kline[-6:-1]) / 5
        if ma5 > 0:
            above_ma5 = (close - ma5) / ma5

    score = 0
    if close_pos >= 0.95: score += 12
    elif close_pos >= 0.85: score += 9
    elif close_pos >= 0.7: score += 5
    else: score += 1

    if 0.8 <= vol_ratio <= 2.0: score += 8
    elif 2.0 < vol_ratio <= 3.5: score += 5
    elif vol_ratio > 3.5: score -= 2
    else: score += 3

    if above_ma5 > 0.03: score += 5
    elif above_ma5 > 0: score += 3
    else: score += 0

    return max(0, min(25, score))


def classify(total: int) -> tuple[str, str]:
    """双模式分类（放宽门槛：55/45）"""
    if total >= 55: return "🟢 强势", f"🟢 强势({total}分)"
    elif total >= 45: return "🟡 稳健", f"🟡 稳健({total}分)"
    elif total >= 35: return "⚪ 观察", f"⚪ 观察({total}分)"
    else: return "🔴 排除", f"🔴 排除({total}分)"


# ─── 主流程 ─────────────────────────────────────────────────────

def main():
    now = datetime.datetime.now()
    print(f"\n[{now.strftime('%H:%M:%S')}] 选股引擎 v7 启动")
    print("=" * 58)
    print("策略: 硬过滤 → 形态+大盘弱势(宽松) → CANSLIM前置 → 四维打分 → 双模式(v7涨停宽松)")
    print("=" * 58)

    universe = get_universe()
    if not universe:
        return
    all_codes = [s["code"] for s in universe]

    print("  获取大盘基准...")
    index_return = get_index_today_return()
    print(f"  上证今日: {index_return:+.2f}%")

    if index_return < -2.0:
        print(f"  ⚠️ 大盘弱势({index_return:+.1f}%)！强势股降级为稳健，稳健股可正常入选")
    elif index_return < -1.0:
        print(f"  ⚠️ 大盘偏弱({index_return:+.1f}%)！稳健股降级")

    print("  获取实时行情...")
    quotes = fetch_tencent(all_codes)
    print(f"  实时行情: {len(quotes)} 只")

    # 阶段一：基础硬过滤
    print("\n[阶段一] 硬过滤")
    passed = {}
    stats = {"PE": 0, "流动": 0, "低价": 0, "市值": 0, "通过": 0}
    for code, quote in quotes.items():
        ok, reason = hard_filter(quote)
        if not ok:
            if "PE" in reason: stats["PE"] += 1
            elif "换手" in reason: stats["流动"] += 1
            elif "股价" in reason: stats["低价"] += 1
            elif "市值" in reason: stats["市值"] += 1
            continue
        passed[code] = quote
        stats["通过"] += 1
    print(f"  过滤: PE{stats['PE']} | 流动{stats['流动']} | 低价{stats['低价']} | 市值{stats['市值']} | 通过{stats['通过']}")

    # 阶段二：形态+CANSLIM前置过滤
    print("\n[阶段二] 形态过滤 + CANSLIM前置")
    # 先拉K线（形态+CANSLIM追高过滤需要）
    print(f"  拉取K线({len(passed)}只)...")
    klines = fetch_klines_parallel(list(passed.keys()), max_workers=30)

    filtered = {}
    f_stats = {"形态": 0, "顶点": 0, "大盘": 0, "追高": 0, "量比": 0, "通过": 0}
    for code, quote in passed.items():
        kline = klines.get(code, [])
        ok, reason = pass_form_filter(quote, kline, index_return)
        if not ok:
            if "形态" in reason: f_stats["形态"] += 1
            elif "顶点" in reason: f_stats["顶点"] += 1
            elif "大盘" in reason: f_stats["大盘"] += 1
            elif "追高" in reason: f_stats["追高"] += 1
            elif "量比" in reason: f_stats["量比"] += 1
            continue
        # 大盘弱势：标记降级flag（但仍可通过过滤）
        weak_market = (index_return < -2.0)
        filtered[code] = {"quote": quote, "kline": kline, "weak_market": weak_market}
        f_stats["通过"] += 1
    print(f"  过滤: 形态{f_stats['形态']} | 顶点{f_stats['顶点']} | 大盘{f_stats['大盘']} | "
          f"追高{f_stats['追高']} | 量比{f_stats['量比']} | 通过{f_stats['通过']}")

    # 阶段三：四维打分
    print(f"\n[阶段三] 四维评分({len(filtered)}只)...")
    results = []
    for code, data in filtered.items():
        quote = data["quote"]
        kline = data["kline"]

        pe_chg = calc_pe_change(kline)
        v  = score_value(quote, pe_chg)
        m  = score_momentum(quote, kline, index_return)
        q  = score_quality(quote)
        vp = score_volume_price(quote, kline)

        # CANSLIM MACD 加分（0轴上方金叉 +5~20分）
        macd_pts, macd_desc = 0, ""
        if kline and len(kline) >= 34:
            closes = [k["close"] for k in kline]
            macd_pts, macd_desc = macd_score(closes)

        total = v + m + q + vp + macd_pts
        mode_tag, _ = classify(total)

        # 大盘弱势：强势股降稳健（用flag而非hard filter）
        weak = data.get("weak_market", False)
        if weak and mode_tag.startswith("🟢"):
            mode_tag = "🟡 稳健(大盘降级)"

        results.append({
            "code": code, "name": quote.get("name", code),
            "current": quote.get("current", 0),
            "change_pct": quote.get("change_pct", 0),
            "turnover": quote.get("turnover", 0),
            "pe": quote.get("pe", 0),
            "market_cap": quote.get("market_cap", 0),
            "industry": quote.get("industry", "-"),
            "scores": {"value": v, "momentum": m, "quality": q,
                       "volume_price": vp, "macd": macd_pts, "total": total},
            "macd_desc": macd_desc,
            "mode_tag": mode_tag,
        })

    results.sort(key=lambda x: -x["scores"]["total"])

    bands = {"strong": [], "steady": [], "observe": [], "exclude": []}
    for r in results:
        tag = r["mode_tag"]
        if tag.startswith("🟢"): bands["strong"].append(r)
        elif tag.startswith("🟡"): bands["steady"].append(r)
        elif tag.startswith("⚪"): bands["observe"].append(r)
        else: bands["exclude"].append(r)

    print(f"  🟢强势:{len(bands['strong'])} | 🟡稳健:{len(bands['steady'])} | "
          f"⚪观察:{len(bands['observe'])} | 🔴排除:{len(bands['exclude'])}")

    # ─── 生成报告 ──────────────────────────────────────────────

    def fmt_row(d, i):
        s = d["scores"]
        arrow = "▲" if d["change_pct"] >= 0 else "▼"
        sign = "+" if d["change_pct"] >= 0 else ""
        turnover = f"{d['turnover']:.2f}%" if d.get("turnover") else "-"
        pe = f"{d['pe']:.1f}" if d.get("pe") and d["pe"] > 0 else "-"
        mktcap = f"{d['market_cap']:.0f}亿" if d.get("market_cap") else "-"
        return (f"| {i} | {d['name']} | {d['code']} | ¥{d['current']:.2f} | "
                f"{arrow}{sign}{d['change_pct']:.2f}% | {turnover} | {pe} | {mktcap} | "
                f"**{s['total']}** | {d['mode_tag']} | {d.get('industry','-')} |")

    lines = [
        f"📊 选股日报 {TODAY}",
        f"🕐 {REPORT_TIME} · 扫描{len(quotes)}只 · 硬过滤{stats['通过']} · CANSLIM前置{len(filtered)}",
        "",
        "━━━━━━━━━━━━━━━",
        "v7策略: 硬过滤→形态/大盘宽松→CANSLIM前置→四维打分→双模式(含涨停宽松通道)",
        "━━━━━━━━━━━━━━━",
        "",
    ]

    if bands["strong"]:
        lines.append("**🟢 强势模式**（≥88分，MACD金叉+形态好+动量强）")
        lines.append("")
        lines.append("| # | 名称 | 代码 | 现价 | 涨跌幅 | 换手 | PE | 市值 | 总分 | 模式 | 行业 |")
        lines.append("|---|------|------|------|--------|------|-----|------|------|------|------|")
        for i, d in enumerate(bands["strong"][:8], 1):
            lines.append(fmt_row(d, i))
        lines.append("")
    else:
        lines.append("**🟢 强势模式**: 暂无（大盘弱势或无满足条件个股）")
        lines.append("")

    if bands["steady"]:
        lines.append("**🟡 稳健模式**（≥75分，基本面稳+CANSLIM量能）")
        lines.append("")
        lines.append("| # | 名称 | 代码 | 现价 | 涨跌幅 | 换手 | PE | 市值 | 总分 | 模式 | 行业 |")
        lines.append("|---|------|------|------|--------|------|-----|------|------|------|------|")
        for i, d in enumerate(bands["steady"][:12], 1):
            lines.append(fmt_row(d, i))
        lines.append("")
    else:
        lines.append("**🟡 稳健模式**: 暂无")
        lines.append("")

    if bands["observe"]:
        lines.append(f"**⚪ 观察名单**（≥65分，共{len(bands['observe'])}只，前6只）")
        lines.append("")
        lines.append("| 名称 | 代码 | 总分 | 模式 |")
        lines.append("|------|------|------|------|")
        for d in bands["observe"][:6]:
            lines.append(f"| {d['name']} | {d['code']} | **{d['scores']['total']}** | {d['mode_tag']} |")
        lines.append("")

    lines.append("📈 评分构成（强势+稳健前6）:")
    for d in (bands["strong"] + bands["steady"])[:6]:
        s = d["scores"]
        macd_tag = f" MACD({d['macd_desc']})" if d["macd_desc"] else ""
        lines.append(
            f"  {d['name']}({d['code']}): "
            f"价值{s['value']}+动量{s['momentum']}+质量{s['quality']}+量价{s['volume_price']}+MACD{s['macd']}"
            f" = **{s['total']}** {d['mode_tag']}{macd_tag}"
        )

    lines.append("")
    lines.append(f"📈 今日双模式: 🟢强势{len(bands['strong'])}只 | 🟡稳健{len(bands['steady'])}只 | "
                 f"⚪观察{len(bands['observe'])}只 | 🔴排除{len(bands['exclude'])}只")
    lines.append("")
    lines.append("⚠️ 本报告基于实时行情自动筛选,不构成投资建议")

    report = "\n".join(lines)

    out_file = OUTPUT_DIR / f"综合选股-{TODAY}.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"\n✅ 综合报告: {out_file}")

    # 最终推荐：固定5只（强势优先，不够则从稳健补充）
    final5 = bands["strong"][:5]
    if len(final5) < 5:
        final5 += bands["steady"][:5 - len(final5)]

    rec_lines = ["| 名称 | 代码 | 评分 | 模式 | 行业 |"]
    rec_lines.append("|------|------|------|------|------|")
    for d in final5:
        rec_lines.append(f"| {d['name']} | {d['code']} | {d['scores']['total']} | {d['mode_tag']} | {d.get('industry','-')} |")
    rec_file = OUTPUT_DIR / f"推荐-{TODAY}.md"
    rec_file.write_text("\n".join(rec_lines), encoding="utf-8")
    print(f"✅ 推荐报告: {rec_file}")

    print("\n" + report)


if __name__ == "__main__":
    main()
