#!/usr/bin/env python3
"""
盘后复盘 v3 — 双源交叉验证 + 物理边界检查 + 发布前现场确认
"""
import urllib.request, re, json, datetime, os, glob, subprocess

TODAY = datetime.date.today().strftime("%Y%m%d")

# ── 数据源A：腾讯实时行情 ──────────────────────────────────────
def fetch_tencent(codes):
    url = f'https://qt.gtimg.cn/q={codes}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode('gbk', errors='ignore')

def parse_tencent(raw, code):
    m = re.search(rf'v_{code}="([^"]+)"', raw)
    if not m: return None
    f = m.group(1).split('~')
    if len(f) < 40: return None
    prev = float(f[4]); op = float(f[5]); price = float(f[3])
    high = float(f[33]); low = float(f[34])
    return {
        'prev_close': prev, 'open': op, 'price': price,
        'high': high, 'low': low,
        'auction_pct':  round((op - prev) / prev * 100, 2) if prev else None,
        'close_pct':   round((price - prev) / prev * 100, 2) if prev else None,
    }

# ── 数据源B：新浪行情（备用）───────────────────────────────────
def fetch_sina(codes):
    url = f'https://hq.sinajs.cn/list={codes}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://finance.sina.com.cn',
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode('gbk', errors='ignore')
    except Exception:
        return ''  # 返回空字符串，调用方处理

def parse_sina(raw, code):
    if not raw: return None
    m = re.search(rf'"{code}"\s*=\s*"([^"]+)"', raw)
    if not m: return None
    f = m.group(1).split(',')
    if len(f) < 32: return None
    prev = float(f[2]); op = float(f[1]); price = float(f[3])
    high = float(f[4]);  low = float(f[5])
    return {
        'prev_close': prev, 'open': op, 'price': price,
        'high': high, 'low': low,
        'auction_pct':  round((op - prev) / prev * 100, 2) if prev else None,
        'close_pct':   round((price - prev) / prev * 100, 2) if prev else None,
    }

# ── 物理边界检查 ──────────────────────────────────────────────
def sanity_check(r, source=''):
    """返回 (是否通过, 错误信息)"""
    a, c = r.get('auction_pct'), r.get('close_pct')
    prev = r.get('prev_close', 0)
    price = r.get('price', 0)

    if prev <= 0 or price <= 0:
        return False, f'[{source}] 价格数据异常 prev={prev} price={price}'
    if c is not None and abs(c) > 20:
        return False, f'[{source}] 收盘涨跌幅超限 {c}%'
    if a is not None and abs(a) > 20:
        return False, f'[{source}] 竞价涨跌幅超限 {a}%'
    if price > prev * 1.15 or price < prev * 0.85:
        return False, f'[{source}] 现价{price}与昨收{prev}差异超15%'
    return True, ''

# ── 双源合并 ─────────────────────────────────────────────────
def get_stock_data(code, name):
    prefix = 'sh' + code if code.startswith('6') else 'sz' + code
    sina_code = ('sh' if code.startswith('6') else 'sz') + code

    # 同时请求双源
    raw_t = fetch_tencent(prefix)
    raw_s = fetch_sina(sina_code)

    t = parse_tencent(raw_t, prefix)
    s = parse_sina(raw_s, sina_code)

    result = {'name': name, 'code': code,
              'auction_pct': None, 'close_pct': None,
              'prev_close': None, 'open': None,
              'price': None, 'high': None, 'low': None,
              'result': '', 'result_type': '',
              'warnings': []}

    # 腾讯源
    if t:
        ok, err = sanity_check(t, '腾讯')
        if ok:
            result.update({k: t[k] for k in ['auction_pct','close_pct','prev_close','open','price','high','low']})
        else:
            result['warnings'].append(err)

    # 新浪源（交叉核对）
    if s:
        ok, err = sanity_check(s, '新浪')
        if ok and t:
            # 双源一致性检查
            diff_a = abs((s['auction_pct'] or 0) - (t['auction_pct'] or 0))
            diff_c = abs((s['close_pct']   or 0) - (t['close_pct']   or 0))
            if diff_a > 0.5:
                result['warnings'].append(f'双源竞价分歧: 腾讯{t["auction_pct"]}% vs 新浪{s["auction_pct"]}%')
            if diff_c > 0.5:
                result['warnings'].append(f'双源收盘分歧: 腾讯{t["close_pct"]}% vs 新浪{s["close_pct"]}%')
            # 差异大时取均值的四舍五入
            if diff_c <= 0.5:
                result['close_pct'] = round((t['close_pct'] + s['close_pct']) / 2, 2)
                result['auction_pct'] = round((t['auction_pct'] + s['auction_pct']) / 2, 2)
        elif ok:
            result.update({k: s[k] for k in ['auction_pct','close_pct','prev_close','open','price','high','low']})

    if not result['price']:
        result['result'] = '数据获取失败'
        result['result_type'] = 'fail'
        return result

    # 判定
    a, c = result['auction_pct'], result['close_pct']
    high = result['high']; prev = result['prev_close']

    if c is None:
        result['result'] = '数据异常'
        result['result_type'] = 'fail'
        return result

    if c >= 9.5:
        result['result'] = f"强势涨停 {c:.2f}%"
        result['result_type'] = 'success'
    elif c >= 5:
        result['result'] = f"大幅上涨 {c:.2f}%"
        result['result_type'] = 'success'
    elif c > 0:
        result['result'] = f"小幅上涨 {c:.2f}%"
        result['result_type'] = 'success'
    elif c > -3:
        result['result'] = f"冲高回落 {c:.2f}%"
        result['result_type'] = 'neutral'
    elif c <= -9.5:
        result['result'] = f"跌停 {c:.2f}%"
        result['result_type'] = 'fail'
    else:
        result['result'] = f"下跌 {c:.2f}%"
        result['result_type'] = 'fail'

    # 炸板检测
    if high and prev and (high - prev) / prev > 0.095 and c < 0:
        result['result'] = f"炸板回落 {c:.2f}%"
        result['result_type'] = 'fail'

    return result

# ── 发布前现场确认（市场关闭后查一次实时）─────────────────────
def live_verify(results):
    """推送前用腾讯实时数据做最终确认"""
    codes = [r['code'] for r in results]
    prefix_list = ['sh' + c if c.startswith('6') else 'sz' + c for c in codes]
    raw = fetch_tencent(','.join(prefix_list))
    verified = []
    for r, pre in zip(results, prefix_list):
        d = parse_tencent(raw, pre)
        if d:
            diff = abs((d['close_pct'] or 0) - (r['close_pct'] or 0))
            if diff > 1.0:
                r['warnings'].append(f'⚠️ 发布前现场核查发现差异: 记录{r["close_pct"]}% vs 实时{d["close_pct"]}%，以实时为准')
                r['close_pct'] = d['close_pct']
                r['auction_pct'] = d['auction_pct']
                r['price'] = d['price']
                # 重新判定
                c = r['close_pct']
                if c >= 9.5:
                    r['result'] = f"强势涨停 {c:.2f}%"
                    r['result_type'] = 'success'
                elif c >= 5:
                    r['result'] = f"大幅上涨 {c:.2f}%"
                    r['result_type'] = 'success'
                elif c > 0:
                    r['result'] = f"小幅上涨 {c:.2f}%"
                    r['result_type'] = 'success'
                elif c > -3:
                    r['result'] = f"冲高回落 {c:.2f}%"
                    r['result_type'] = 'neutral'
                elif c <= -9.5:
                    r['result'] = f"跌停 {c:.2f}%"
                    r['result_type'] = 'fail'
                else:
                    r['result'] = f"下跌 {c:.2f}%"
                    r['result_type'] = 'fail'
        verified.append(r)
    return verified

def read_pick_file():
    pick_dir = "/home/ubuntu/.openclaw/workspace/每日选股"
    candidates = sorted(glob.glob(f"{pick_dir}/推荐-{TODAY}*.md"), reverse=True)
    for c in candidates:
        if not os.path.exists(c): continue
        with open(c) as f:
            content = f.read()
        stocks = []
        for line in content.split('\n'):
            if '|' in line and not line.startswith('|---') and not line.startswith('#'):
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3:
                    name = parts[1]; code = parts[2]
                    if code.isdigit() and len(code) == 6:
                        stocks.append({'name': name, 'code': code})
        if stocks:
            return stocks
    return []

def format_report(results):
    success = sum(1 for r in results if r['result_type'] == 'success')
    fail    = sum(1 for r in results if r['result_type'] == 'fail')
    now = datetime.datetime.now()

    warn_lines = []
    for r in results:
        for w in r.get('warnings', []):
            warn_lines.append(f"  ⚠️ {r['name']}: {w}")

    report = f"📊 盘后复盘 {TODAY}\n"
    report += f"🕐 {now.strftime('%H:%M')} · 复盘{len(results)}只股票\n\n"
    report += "━━━━━━━━━━━━━━━\n"
    report += f"今日总结: {len(results)}只, 成功{success}只, 失败{fail}只\n"
    report += "━━━━━━━━━━━━━━━\n\n"
    report += "| 名称 | 代码 | 竞价涨幅 | 收盘涨幅 | 结果 |\n"
    report += "|------|------|---------|---------|------|\n"
    for r in results:
        auction = f"{r['auction_pct']:.2f}%" if r['auction_pct'] is not None else "-"
        close   = f"{r['close_pct']:.2f}%"  if r['close_pct']  is not None else "-"
        emoji = {'success': '✅', 'fail': '❌', 'neutral': '🔸'}.get(r['result_type'], '  ')
        report += f"| {r['name']} | {r['code']} | {auction} | {close} | {emoji}{r['result']} |\n"

    if warn_lines:
        report += "\n⚠️ 核查警告（已自动修正）:\n" + '\n'.join(warn_lines) + "\n"

    report += "\n🟢 仅供参考，不构成投资建议"
    return report

WECHAT_TARGET = 'o9cq80z4Nv9VbLwPfTbahT8986V0@im.wechat'

if __name__ == "__main__":
    stocks = read_pick_file()
    if not stocks:
        print(f"未找到{TODAY}选股记录")
        exit(1)

    print(f"[复盘] 读取到 {len(stocks)} 只股票\n")
    results = []
    for s in stocks:
        r = get_stock_data(s['code'], s['name'])
        print(f"  {r['name']}({r['code']}) 竞价={r['auction_pct']}% 收盘={r['close_pct']}% → {r['result']}")
        if r.get('warnings'):
            for w in r['warnings']:
                print(f"    ⚠️ {w}")
        results.append(r)

    print(f"\n[发布前验证] 用腾讯实时数据做最终确认...")
    results = live_verify(results)

    report = format_report(results)
    print("\n" + report)

    # 写入文件
    os.makedirs("/home/ubuntu/.openclaw/workspace/每日选股", exist_ok=True)
    with open(f"/home/ubuntu/.openclaw/workspace/每日选股/复盘-{TODAY}.md", "w") as f:
        f.write(report)

    # 微信推送
    push_msg = report if len(report) <= 1800 else report[:1800] + "\n...(内容过长已截断)"
    try:
        r = subprocess.run(
            ['hermes', 'send', '--to', f'weixin:{WECHAT_TARGET}', push_msg],
            capture_output=True, text=True, timeout=30
        )
        print(f"\n[推送] {'成功 ✅' if r.returncode == 0 else '失败: ' + r.stderr[:80]}")
    except Exception as e:
        print(f"\n[推送] 异常: {e}")
