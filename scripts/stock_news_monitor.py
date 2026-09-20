#!/usr/bin/env python3

import fcntl
import os

LOCK_FILE = '/tmp/stock_news_monitor.lock'
try:
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("另一个实例已在运行，退出")
    exit(0)

"""
股票资讯实时监控守护进程
- 每60分钟扫描财经新闻
- 仅推送真正重要的：政策/数据/黑天鹅/大幅波动
- 每日23:00-07:00静默（不推送，但继续监测）
- 同标题24小时内不重复推送
"""
import time, json, datetime, requests, akshare as ak
from pathlib import Path

CHECK_INTERVAL = 60 * 60
LOG_FILE = '/tmp/stock_news_monitor.log'
STATE_FILE = '/tmp/stock_news_state.json'
SEEN_FILE = '/tmp/stock_news_seen.json'
QUIET_START, QUIET_END = 23, 7

HEADERS = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)', 'Referer': 'https://finance.eastmoney.com/'}


def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a', errors='replace') as f:
        f.write(line + '\n')


def in_quiet_hours():
    h = datetime.datetime.now().hour
    return h >= QUIET_START or h < QUIET_END


def load_seen():
    """加载已推送的新闻标题集合（24小时有效）"""
    try:
        data = json.loads(Path(SEEN_FILE).read_text())
        # 清理超过24小时的条目
        now = datetime.datetime.now()
        cleaned = {}
        for title, ts_str in data.items():
            try:
                ts = datetime.datetime.fromisoformat(ts_str)
                age_hours = (now - ts).total_seconds() / 3600
                if age_hours < 24:
                    cleaned[title] = ts_str
            except:
                pass
        return cleaned
    except:
        return {}


def save_seen(seen_dict):
    """保存已推送记录"""
    Path(SEEN_FILE).write_text(json.dumps(seen_dict, ensure_ascii=False))


def fetch_upcoming_launches():
    """抓取未来72小时内即将发射的火箭预告（NextSpaceFlight API）"""
    try:
        url = 'https://ll.thespacedevs.com/2.2.0/launch/upcoming/?limit=10&mode=list'
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        results = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for item in data.get('results', []):
            net = item.get('net')  # net estimated time
            if not net:
                continue
            try:
                launch_time = datetime.datetime.fromisoformat(net.replace('Z', '+00:00'))
            except:
                continue
            # 只关注未来72小时内
            delta = (launch_time - now).total_seconds()
            if delta < 0 or delta > 72 * 3600:
                continue
            name = item.get('name', '')
            status = item.get('status', {}).get('name', '')
            rocket_obj = item.get('rocket') or {}
            conf = rocket_obj.get('configuration') or {}
            rocket = conf.get('name') or ''
            pad = item.get('pad')
            if isinstance(pad, dict):
                location = pad.get('location', {})
                loc_name = location.get('name') if isinstance(location, dict) else str(location)
            else:
                loc_name = str(pad) if pad else ''
            # 格式化时间（转为北京时）
            try:
                local_time = launch_time.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
                time_str = local_time.strftime('%m-%d %H:%M')
            except:
                time_str = net[:16]
            hours_left = int(delta // 3600)
            minutes_left = int((delta % 3600) // 60)
            time_desc = f"{hours_left}小时{minutes_left}分后" if hours_left < 24 else f"{hours_left//24}天后"
            results.append({
                'name': name,
                'rocket': rocket,
                'status': status,
                'location': loc_name,
                'time': time_str,
                'countdown': time_desc,
                'delta_seconds': delta
            })
        # 按时间排序
        results.sort(key=lambda x: x['delta_seconds'])
        return results
    except Exception as e:
        log(f'发射预告异常: {e}')
        return []


def is_important(title):
    t = title.lower()
    t_full = title  # 保留原始大小写用于精确匹配

    # ===== 用户指定监控主题（最高优先级） =====
    # 1. 智谱 GLM 新模型发布（5.3 / 5.4 ...）
    zhipu_model_kws = [
        "智谱", "GLM-5.3", "GLM5.3", "GLM-5.4", "GLM5.4",
        "GLM-6", "GLM6", "zhipu", "z.ai", "Z.ai",
        "智谱华章", "智谱GLM", "GLM新版本", "GLM新版",
    ]
    for k in zhipu_model_kws:
        if k.lower() in t or k in t_full:
            return 3, f"🤖智谱GLM"

    # 2. Kimi IPO（月之暗面）
    kimi_kws = ["Kimi", "kimi", "月之暗面", "Moonshot AI", "Moonshot",
                 "Kimi IPO", "Kimi上市", "kimi上市", "月之暗面IPO", "Moonshot IPO"]
    for k in kimi_kws:
        if k.lower() in t or k in t_full:
            return 3, f"📌KimiIPO"

    # 3. MiniMax 模型更新
    minimax_kws = ["MiniMax", "minimax", "MiniMax模型", "minimax模型",
                    "MiniMax更新", "minimax更新", "MiniMax发布", "minimax发布",
                    "海螺AI", "海螺ai", "MiniMax利", "minimax利"]
    for k in minimax_kws:
        if k.lower() in t or k in t_full:
            return 3, f"🤖MiniMax"

    # 🚨重大新闻：系统性风险、市场崩盘、战争、航天重大突破
    for k in ["紧急", "突发", "崩盘", "历史性", "制裁", "停牌", "退市", "监管", "处罚", "黑天鹅", "危机"]:
        return 3, f"🚨{k}"
    for k in ["战争", "袭击", "军事冲突", "政权更迭", "金融海啸", "债务危机", "银行倒闭"]:
        return 3, f"🚨{k}"
    for k in ["火箭发射成功", "火箭回收成功", "可回收火箭", "载人航天", "卫星发射", "航天器着陆", "商业航天", "星舰", "猎鹰火箭", "神舟", "天宫", "嫦娥", "长征火箭", "SpaceX", "Blue Origin"]:
        return 3, f"🚀{k}"

    # 📌市场相关：涨跌/政策/公司
    for k in ["利好","利空","涨停","跌停","暴涨","护盘","救市","IPO","问询函","立案","财务造假","业绩暴雷","大幅亏损","暂停上市","美股暴跌","美股大涨","A股"]:
        return 2, f"📌{k}"

    # 🌍国际宏观：央行、政策、数据、关键人物讲话
    for k in ["美联储","鲍威尔","英伟达","特斯拉","马斯克","特朗普","关税","CPI","PPI","GDP","非农","降息","加息","北约","欧盟","联合国","外交","耶伦","沙利文","布林肯","王毅","普京","普京"]:
        return 1, f"🌍{k}"
    for k in ["讲话","发言","声明","警告","谈判","会晤","访华","访问","峰会","G20","APEC"]:
        return 1, f"🌍{k}"

    return 0, ""


def fetch_lanjing():
    """蓝鲸财经首页文章"""
    try:
        r = requests.get('https://www.lanjinger.com/', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        import re, json, time
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if not m:
            return []
        d = json.loads(m.group(1))
        articles = d.get('props', {}).get('pageProps', {}).get('data', {}).get('Article', [])
        results = []
        for a in articles:
            t = str(a.get('title', ''))[:80]
            if not t:
                continue
            ts = a.get('r_time', 0)
            if ts:
                dt = time.strftime('%m-%d %H:%M', time.localtime(ts))
            else:
                dt = ''
            results.append((f'【蓝鲸】{t}', dt))
        return results
    except Exception as e:
        log(f'蓝鲸异常: {e}')
        return []


def fetch_international():
    """CNBC RSS（已禁用 Yahoo Finance / Investing.com / Space.com）"""
    results = []
    feeds = [
        ('CNBC', 'https://www.cnbc.com/id/10000664/device/rss/rss.html'),
    ]
    for name, url in feeds:
        try:
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
            import re, json, time
            items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
            for item in items[:8]:
                t = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>', item, re.DOTALL)
                if not t: t = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
                if t:
                    title = t.group(1).strip()[:80]
                    results.append((f'【{name}】{title}', name))
        except Exception as e:
            log(f'国际源{name}异常: {e}')
    return results


def fetch_aerospace_news():
    """航天/科技专项新闻：36kr + NASA（已禁用 Space.com）"""
    results = []
    feeds = [
        ('36kr', 'https://36kr.com/feed'),
        ('NASA', 'https://www.nasa.gov/rss/dyn/breaking_news.rss'),
    ]
    for name, url in feeds:
        try:
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
            import re
            items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
            for item in items[:10]:
                t = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
                if not t: t = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>', item, re.DOTALL)
                if t:
                    title = t.group(1).strip()[:80]
                    results.append((f'【{name}】{title}', name))
        except Exception as e:
            log(f'航天源{name}异常: {e}')
    return results


def fetch_news_sources(seen_titles):
    """合并akshare东方财富 + 同花顺快讯 + 蓝鲸财经 + 国际源"""
    results = []
    seen_in_batch = set()

    # akshare
    try:
        df = ak.stock_news_em(symbol='A股')
        if df is not None and len(df) > 0:
            for _, r in df.iterrows():
                t = str(r.get('新闻标题',''))[:80]
                s = str(r.get('文章来源',''))
                if t and t not in seen_in_batch and t not in seen_titles:
                    seen_in_batch.add(t)
                    results.append((t, s, '东方财富'))
    except Exception as e:
        log(f"akshare异常: {e}")

    # 同花顺快讯
    try:
        r2 = requests.get(
            'https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&tag=&track=website&pagesize=15',
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.10jqka.com.cn'}, timeout=8
        )
        if r2.status_code == 200:
            items = r2.json().get('data', {}).get('list', []) if isinstance(r2.json(), dict) else []
            for it in items:
                t = (it.get('title') or it.get('content', '') or '')[:80]
                if t and t not in seen_in_batch and t not in seen_titles:
                    seen_in_batch.add(t)
                    results.append((t, '同花顺', ''))
    except Exception as e:
        log(f"同花顺异常: {e}")

    # 蓝鲸财经
    for t, dt in fetch_lanjing():
        if t and t not in seen_in_batch and t not in seen_titles:
            seen_in_batch.add(t)
            results.append((t, f'蓝鲸{dt}', ''))

    # 国际源（仅工作时间 09:00-23:00）
    try:
        h = datetime.datetime.now().hour
        if 9 <= h < 23:
            for t, _ in fetch_international():
                if t and t not in seen_in_batch and t not in seen_titles:
                    seen_in_batch.add(t)
                    results.append((t, '国际', ''))
    except Exception as e:
        log(f"国际源异常: {e}")

    # 航天/科技专项（工作时间）
    try:
        h = datetime.datetime.now().hour
        if 9 <= h < 23:
            for t, src in fetch_aerospace_news():
                if t and t not in seen_in_batch and t not in seen_titles:
                    seen_in_batch.add(t)
                    results.append((t, src, ''))
    except Exception as e:
        log(f"航天源异常: {e}")

    return results


def send_wechat(msg):
    import subprocess
    cmd = ['hermes', 'send',
           '--to', f'weixin:{WECHAT_TARGET}']
    try:
        r = subprocess.run(cmd, input=msg.encode('utf-8'),
                          capture_output=True, timeout=30)
        if r.returncode == 0:
            log(f"微信推送: 成功")
        else:
            log(f"微信推送: 失败 rc={r.returncode} err={r.stderr.decode(errors='replace')[:200]}")
    except Exception as e:
        log(f"微信推送异常: {e}")


WECHAT_TARGET = 'o9cq80z4Nv9VbLwPfTbahT8986V0@im.wechat'


def load_state():
    try:
        return json.loads(Path(STATE_FILE).read_text())
    except:
        return {}


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False))


def main():
    log("资讯监控守护进程启动（精准版）")
    seen = load_seen()

    while True:
        now = datetime.datetime.now()
        state = load_state()
        check_count = state.get('check_count', 0) + 1
        log(f"检查{check_count} | 已追踪{len(seen)}条新闻")

        try:
            # 获取新闻（传入已seen的标题进行去重）
            news = fetch_news_sources(set(seen.keys()))
            
            # 过滤重要新闻
            important = []
            for title, source, _ in news:
                score, reason = is_important(title)
                if score > 0:
                    important.append((title, source, score, reason))

            important.sort(key=lambda x: -x[2])
            
            # 推送判断
            if in_quiet_hours():
                log(f"静默时段（23:00-07:00），不推送 | 重要新闻: {len(important)}条")
            elif important:
                now_str = now.strftime('%H:%M')
                
                # 找出真正新的（不在seen中的）
                new_ones = [(t, s, sc, r) for t, s, sc, r in important if t not in seen]
                
                if not new_ones:
                    log(f"检查{check_count} | 重要新闻{len(important)}条 | 全部已推送过，跳过")
                else:
                    # 构建推送内容
                    if len(new_ones) >= 3:
                        push_tag = f"📌 {len(new_ones)}条重要发现"
                    elif new_ones[0][2] >= 3:
                        push_tag = "🚨 重大新闻"
                    else:
                        push_tag = f"⚡ {len(new_ones)}条值得关注"

                    # 新闻分类展示
                    critical = [x for x in new_ones if x[2] >= 3]
                    normal = [x for x in new_ones if x[2] < 3]

                    lines = [f"📰 资讯监控 [{now_str}] {push_tag}\n"]
                    
                    if critical:
                        lines.append("🚨 重大新闻：")
                        for title, source, score, reason in critical:
                            lines.append(f"  • {title}")
                            lines.append(f"    {reason} | {source}")
                        lines.append("")
                    
                    if normal:
                        lines.append("⚡ 其他重要：")
                        for title, source, score, reason in normal:
                            lines.append(f"  • {title}")
                            lines.append(f"    {reason} | {source}")
                        lines.append("")
                    
                    lines.append("⚠️ 自动抓取，请自行核实判断。")
                    
                    msg = '\n'.join(lines)
                    
                    # 更新seen记录
                    for t, _, _, _ in new_ones:
                        seen[t] = now.isoformat()
                    save_seen(seen)
                    
                    save_state({'last_push_time': now.isoformat(), 'check_count': check_count, 'last_new_count': len(new_ones)})
                    try:
                        send_wechat(msg)
                    except Exception as e:
                        log(f"send_wechat 调用异常: {e}")
                    log(f"推送成功（{push_tag}）| 新: {len(new_ones)}条 | 已追踪: {len(seen)}条")
            else:
                log(f"检查{check_count} | 无重要新闻")

        except Exception as e:
            log(f"异常: {e}")

        # ===== 火箭发射预告推送 =====
        try:
            launches = fetch_upcoming_launches()
            state = load_state()
            alerted_raw = state.get('launch_alerted')
            alerted = set(alerted_raw) if isinstance(alerted_raw, list) else set()
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            new_alerts = []

            for launch in launches:
                key = f"{launch['name'][:30]}|{launch['time']}"
                delta_h = launch['delta_seconds'] / 3600
                status = launch['status']

                # 发射中或刚成功（未推送过）
                if status in ('Launch in Flight', 'Launch Successful') and key not in alerted:
                    msg = (f"🚀 【发射成功】{launch['name']}\n"
                           f"   火箭: {launch['rocket']}\n"
                           f"   时间: {launch['time']} 北京时\n"
                           f"   地点: {launch['location']}\n"
                           f"   状态: {status}")
                    send_wechat(msg)
                    alerted.add(key)
                    new_alerts.append(key)
                    log(f"发射成功推送: {launch['name']}")

                # 24小时内即将发射（未推送过）
                elif 0 < delta_h <= 24 and key not in alerted:
                    msg = (f"🚀 【发射预告·{launch['countdown']}】{launch['name']}\n"
                           f"   火箭: {launch['rocket']}\n"
                           f"   时间: {launch['time']} 北京时\n"
                           f"   地点: {launch['location']}\n"
                           f"   状态: {status}")
                    send_wechat(msg)
                    alerted.add(key)
                    new_alerts.append(key)
                    log(f"发射预告推送: {launch['name']} ({launch['countdown']})")

            if new_alerts:
                state['launch_alerted'] = list(alerted)[-50:]  # 保留最近50条
                save_state(state)
        except Exception as e:
            log(f"发射预告异常: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()