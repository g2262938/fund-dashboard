#!/usr/bin/env python3
"""生成仪表板 HTML + JSON 数据"""
import json
import urllib.request
import re
import datetime
from pathlib import Path

REPORTS_DIR = Path("/home/ubuntu/.openclaw/workspace/reports")
DATA_DIR = REPORTS_DIR / "data"
HISTORY_DIR = REPORTS_DIR / "history"


def fetch_tencent(codes: str) -> str:
    """腾讯接口取数据(GBK)"""
    url = f"https://qt.gtimg.cn/q={codes}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("gbk", errors="ignore")


def parse_qt_line(raw: str, code: str) -> dict | None:
    """解析单条 v_xxx="..." 数据"""
    m = re.search(rf'v_{code}="([^"]+)"', raw)
    if not m:
        return None
    f = m.group(1).split("~")
    if len(f) < 35 or not f[3] or not f[32]:
        return None
    return {
        "name": f[1] if len(f) > 1 else code,
        "current": float(f[3]),
        "prev_close": float(f[4]) if f[4] else 0,
        "open": float(f[5]) if f[5] else 0,
        "change": float(f[31]) if len(f) > 31 and f[31] else 0,
        "change_pct": float(f[32]),
        "high": float(f[33]) if len(f) > 33 and f[33] else 0,
        "low": float(f[34]) if len(f) > 34 and f[34] else 0,
        "time": f[30] if len(f) > 30 else "",
    }


def get_a_indices() -> list[dict]:
    codes = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "sh000688": "科创50",
        "sh000300": "沪深300",
        "sh000905": "中证500",
        "sh000852": "中证1000",
        "sh000016": "上证50",
    }
    try:
        raw = fetch_tencent(",".join(codes.keys()))
        results = []
        for code, name in codes.items():
            d = parse_qt_line(raw, code)
            if d:
                d["code"] = code
                d["name"] = name
                results.append(d)
        return results
    except Exception as e:
        print(f"A股指数失败: {e}")
        return []


def get_us_indices() -> list[dict]:
    codes = {"usSPY": "SPY", "usQQQ": "QQQ", "usDIA": "DIA"}
    try:
        raw = fetch_tencent(",".join(codes.keys()))
        results = []
        for code, name in codes.items():
            d = parse_qt_line(raw, code)
            if d:
                d["code"] = name
                d["name"] = name
                results.append(d)
        return results
    except Exception as e:
        print(f"美股指数失败: {e}")
        return []


def get_holdings() -> list[dict]:
    """持仓(从持仓信息.md 解析)"""
    path = Path("/home/ubuntu/inherit/知识传承包/02_股票知识/持仓信息.md")
    if not path.exists():
        return []
    try:
        content = path.read_text()
        holdings = []
        # 解析 markdown 表格
        for line in content.split("\n"):
            if "|" not in line or line.startswith("#") or line.startswith("|---"):
                continue
            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p]
            if len(parts) < 2:
                continue
            code_raw = parts[0]
            # A 股:6 位数字;港股:5 位数字 + .HK
            market = None
            code = None
            if code_raw.isdigit() and len(code_raw) == 6:
                market = "A"
                code = code_raw
            elif code_raw.endswith(".HK") and code_raw[:5].isdigit():
                market = "HK"
                code = code_raw[:5]
            if not market:
                continue
            holdings.append({
                "code": code,
                "name": parts[1],
                "sector": parts[2] if len(parts) > 2 else "",
                "note": parts[3] if len(parts) > 3 else "",
                "market": market,
            })
        return holdings
    except Exception as e:
        print(f"持仓解析失败: {e}")
        return []


def get_holdings_quotes(holdings: list[dict]) -> list[dict]:
    """补全持仓实时价格(A股 + 港股)"""
    if not holdings:
        return []
    try:
        qt_codes = []
        for h in holdings:
            if h["market"] == "A":
                if h["code"].startswith(("6", "9")):
                    qt_codes.append(f"sh{h['code']}")
                else:
                    qt_codes.append(f"sz{h['code']}")
            elif h["market"] == "HK":
                qt_codes.append(f"hk{h['code']}")
        raw = fetch_tencent(",".join(qt_codes))
        for h in holdings:
            if h["market"] == "A":
                code = f"sh{h['code']}" if h["code"].startswith(("6", "9")) else f"sz{h['code']}"
            else:
                code = f"hk{h['code']}"
            d = parse_qt_line(raw, code)
            if d:
                h.update(d)
        return holdings
    except Exception as e:
        print(f"持仓价格失败: {e}")
        return holdings


def get_latest_picks() -> dict | None:
    """最新选股结果"""
    today = datetime.date.today().strftime("%Y%m%d")
    files = [
        REPORTS_DIR.parent / "每日选股" / f"推荐-{today}.md",
        REPORTS_DIR.parent / "每日选股" / f"综合选股-{today}.md",
    ]
    for f in files:
        if f.exists():
            return {"date": today, "content": f.read_text(errors="ignore")[:3000]}
    # 取最近一个文件
    pick_dir = REPORTS_DIR.parent / "每日选股"
    if pick_dir.exists():
        all_files = sorted(pick_dir.glob("推荐-*.md"), reverse=True)
        if all_files:
            f = all_files[0]
            return {
                "date": f.stem.replace("推荐-", ""),
                "content": f.read_text(errors="ignore")[:3000],
            }
    return None


def get_latest_review() -> dict | None:
    """最新复盘报告"""
    today = datetime.date.today().strftime("%Y%m%d")
    review_dir = REPORTS_DIR.parent / "每日选股"
    if not review_dir.exists():
        return None
    f = review_dir / f"复盘-{today}.md"
    if f.exists():
        return {"date": today, "content": f.read_text(errors="ignore")[:3000]}
    all_files = sorted(review_dir.glob("复盘-*.md"), reverse=True)
    if all_files:
        f = all_files[0]
        return {
            "date": f.stem.replace("复盘-", ""),
            "content": f.read_text(errors="ignore")[:3000],
        }
    return None


# 默认持仓（从代码内抽到持仓文件 / 从环境变量注入，避免硬编码到公开仓库）
DEFAULT_HOLDINGS_HINT = (
    "- 持仓列表请通过 --holdings 传入 JSON 文件，或从 data/holdings.json 读取；\n"
    "  当前仓库不再硬编码个人持仓，避免隐私泄露。"
)


def _load_holdings_prompt() -> str:
    """从 data/holdings.json（若存在）读取持仓列表用于 prompt。
    文件不存在或格式错误时使用空字符串（调用方决定是否注入默认提示）。"""
    import json
    import os
    candidates = [
        os.environ.get("FUND_HOLDINGS_FILE", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "holdings.json"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    arr = json.load(f)
                if isinstance(arr, list):
                    lines = []
                    for h in arr:
                        code = h.get("code", "?")
                        name = h.get("name", "?")
                        sector = h.get("sector", h.get("industry", ""))
                        lines.append(f"- {name}({code})：{sector}")
                    if lines:
                        return "\n".join(lines)
            except Exception:
                pass
    return ""


def _interpret_news_item(title: str, source: str) -> str:
    """用 LLM 生成一句话解读。
    注意：API Key 必须通过环境变量 MINIMAX_API_KEY 提供，不要写进代码。"""
    import os
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        # 静默跳过，不向 stderr 输出（避免污染生成结果）
        return ""
    try:
        import requests as _req
        holdings_block = _load_holdings_prompt() or DEFAULT_HOLDINGS_HINT
        resp = _req.post(
            "https://api.minimaxi.com/anthropic/v1/messages",
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "MiniMax-M3",
                "max_tokens": 160,
                "temperature": 0.2,
                "messages": [{
                    "role": "user",
                    "content": f"""你是一位A股短线交易员。基于新闻直接给出交易建议。

持仓参考（仅在新闻与持仓有直接或合理关联时才提及，否则不提及）：
{holdings_block}

注意：
- 新闻必须与持仓板块有直接或合理关联才能推荐操作持仓，否则只推荐相关板块ETF/龙头股
- 例如：OpenAI软件更新→不推芯片股；大宗商品价格→可推化工股；地缘冲突→视具体影响而定
- 没有关联就老老实实说"持仓暂无关联"，不要强行关联

输出格式（严格按此顺序，每条一行，不要任何前缀）：
[情绪] 利好/利空/中性 | 30字内理由
[板块] 受影响的具体板块 | 对应ETF或龙头股（最多3个）+ 理由
[操作] 标的+代码 | 操作（买入/卖出/观望/加仓/减仓） | 仓位（轻仓/标配/空仓） | 20字理由
[风控] 止损位或当日注意事项（最多30字）

新闻：{title}
来源：{source}"""
                }]
            },
            timeout=15
        )
        if resp.status_code == 200:
            return resp.json()["content"][0]["text"].strip()
    except Exception:
        pass
    return ""


def _interpret_news_batch(news: list[dict]) -> list[dict]:
    """批量生成解读（每条独立调用，保持容错）"""
    for item in news:
        if item.get("url"):
            interp = _interpret_news_item(item["title"], item["source"])
            item["interpretation"] = interp
    return news


def get_news() -> list[dict]:
    import requests
    results = []

    # ========== 过滤关键词 ==========
    BLOCK_SRC = {'南方财经网', '央广财经', '东方财富Choice数据', '每日经济新闻',
                 '第一财经', '界面新闻', '证券时报', '上海证券报', '中国证券报'}
    KEEP = [
        '央行', '美联储', '财政部', '证监会', '银保监会', '国务院', '发改委', '商务部',
        'CPI', 'PPI', 'PMI', 'GDP', 'LPR', '利率', '降准', '降息', '汇率',
        '美股', '纳斯达克', '道琼斯', '标普', '加息', '缩表', 'QE',
        '原油', '黄金', '白银', '铜', '铝', '铁矿石', '螺纹钢', '煤炭', '天然气', 'EIA', 'OPEC', '油价',
        '涨停', '跌停', '退市', 'IPO', '注册制', '量化', '做空',
        '证监会', '交易所', '券商', '公募', '私募', '基金', 'ETF',
        '补贴', '税收优惠', '关税', '出口管制', '芯片', '半导体', '新能源', '光伏', '储能',
        '电动车', '汽车补贴', '房地产松绑', '医药集采', '医疗改革',
        '航天', '军工', '国防', '低空经济', 'AI', '人工智能', '大模型',
        '中东', '伊朗', '以色列', '沙特', '胡塞', '红海', '霍尔木兹',
        '俄乌', '俄罗斯', '乌克兰', '北约',
        '美中', '中美', '贸易战',
        '台湾', '南海', '台海',
    ]
    DROP = [
        '自然灾害', '洪水', '台风', '地震', '泥石流', '森林火灾',
        '交通事故', '刑事案件', '娱乐', '明星', '网红', '选秀',
        '天气预报', '体育赛事', '电影票房',
        'EIA', '天然气库存', '原油库存', '库存报告', '周报',
        '集体高开', '集体低开', '盘中新高', '盘中新低', '美股开盘',
        '开盘：', '合作备忘录', '签署.*协议', '版权局', '知识产权局',
        '债市收盘', '债券', '收益率', '净回笼', '净投放', '同业拆借',
        '国债', '地方债', '信用债', '可转债',
        '工作访问', '会见.*总统', '双边合作',
        # 新浪财经过滤
        '将于.*发布.*财报', '将于.*发布业绩',
        '外长.*通电话', '外长.*通话',
        '.*讨论.*加息.*吸取.*教训',
        '战略合作', '达成合作',
        '制定.*价格策略', '价格策略',
        '加磅.*投资', '宣布.*投资',
        # 中性/无交易信号
        '维持.*级别', '维持.*水平',
        '发布.*视频', '视频画面',
        '谴责.*美军', '谴责.*美方',
        '代理型.*任务', 'token效率',
    ]

    def relevant(title: str, src: str) -> bool:
        import re
        if src in BLOCK_SRC:
            return False
        has_k = any(k in title for k in KEEP)
        has_d = any(re.search(k, title) for k in DROP)
        return has_k and not has_d

    # 东方财富(akshare) - A股快讯
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol='A股')
        if df is not None:
            for _, r in df.iterrows():
                t = str(r.get('新闻标题', '') or '')[:80]
                s = str(r.get('文章来源', '') or '').strip()
                if t and relevant(t, s):
                    results.append({'title': t, 'source': s or '东方财富', 'tag': '🚨',
                                    'url': str(r.get('新闻链接', '')) or ''})
    except Exception:
        pass

    # 财联社(akshare)
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol='财联社')
        if df is not None:
            for _, r in df.iterrows():
                t = str(r.get('新闻标题', '') or '')[:80]
                s = str(r.get('文章来源', '') or '').strip()
                if s == '财联社' and t and relevant(t, s):
                    results.append({'title': t, 'source': '财联社', 'tag': '🚨',
                                    'url': str(r.get('新闻链接', '')) or ''})
    except Exception:
        pass

    # 新浪财经 - 环球市场播报
    try:
        import requests as _req
        r = _req.get(
            'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=10&page=1',
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'},
            timeout=8
        )
        d = r.json()['result']['data']
        for item in d:
            t = str(item.get('title', ''))[:80]
            s = str(item.get('media_name', '') or '新浪财经').strip()
            if t and relevant(t, s):
                results.append({'title': t, 'source': f'新浪财经-{s}', 'tag': '🚨',
                                'url': str(item.get('url', '')) or ''})
    except Exception:
        pass

    # 同花顺快讯
    try:
        r = requests.get(
            'https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&tag=&track=website&pagesize=20',
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.10jqka.com.cn'}, timeout=8
        )
        if r.status_code == 200:
            items = r.json().get('data', {}).get('list', []) if isinstance(r.json(), dict) else []
            for it in items:
                t = (it.get('title') or '')[:80]
                if t and relevant(t, '同花顺'):
                    url = it.get('url', '') or ''
                    results.append({'title': t, 'source': '同花顺', 'tag': '🚨', 'url': url})
    except Exception:
        pass

    # 去重
    seen, unique = set(), []
    for n in results:
        key = n['title'][:40]
        if key not in seen:
            seen.add(key)
            unique.append(n)
    unique = unique[:20]
    return _interpret_news_batch(unique)


def get_monitor_status() -> list[dict]:
    """从 cron list 推断监控状态"""
    import subprocess
    try:
        r = subprocess.run(["hermes", "cron", "list"], capture_output=True, text=True, timeout=10)
        status = []
        for line in r.stdout.split("\n"):
            if "active" in line and "Name:" in line:
                # 解析 cron list 输出
                pass
        # 简化版:直接读状态文件
        status = [
            {"name": "美股暴跌监控", "freq": "10分钟", "status": "active",
             "last_push": "见 us_crash_monitor.log"},
            {"name": "宽基指数监控", "freq": "5分钟", "status": "active",
             "last_push": "见 index_monitor.log"},
        ]
        return status
    except Exception:
        return []


def render_metric(d: dict) -> str:
    pct = d.get("change_pct", 0)
    if pct > 0:
        cls, arrow, sign, bar_cls = "up", "▲", "+", "up"
    elif pct < 0:
        cls, arrow, sign, bar_cls = "down", "▼", "", "down"
    else:
        cls, arrow, sign, bar_cls = "flat", "—", "", ""
    return f'''<div class="metric {bar_cls}">
        <div class="name">{d["name"]}</div>
        <div class="value mono">{d["current"]:,.2f}</div>
        <div class="change {cls} mono">{arrow} {sign}{pct:.2f}%</div>
    </div>'''


def render_md_table(table_lines: list[str]) -> str:
    """把 markdown 表格行渲染成 HTML 表格"""
    rows = []
    for line in table_lines:
        if set(line.replace("|", "").replace("-", "").strip()) == set():
            continue  # 分隔行
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.match(r"^-+$", c) for c in cells if c):
            continue
        rows.append(cells)
    if not rows:
        return ""
    header, *body = rows
    thead = "<thead><tr>" + "".join(f"<th>{h}</th>" for h in header) + "</tr></thead>"
    tbody_rows = []
    for r in body:
        tds = []
        for i, cell in enumerate(r):
            # 检测涨跌类单元格(包含 % 或 +/-)
            cls = ""
            if "%" in cell or "+" in cell or "−" in cell:
                if "+" in cell and "%" in cell:
                    cls = "change-up"
                elif "−" in cell or (cell.startswith("-") and "%" in cell):
                    cls = "change-down"
            # 第一列(名称)
            if i == 0 and len(header) > 1 and not cell.isdigit():
                cls = (cls + " name-cell").strip()
            tds.append(f'<td class="{cls} mono">{cell}</td>' if cls else f"<td>{cell}</td>")
        tbody_rows.append("<tr>" + "".join(tds) + "</tr>")
    return f'<table class="data-table">{thead}<tbody>{"".join(tbody_rows)}</tbody></table>'


def render_report_content(text: str) -> str:
    """把 markdown 报告渲染成结构化 HTML"""
    lines = text.split("\n")
    out = []
    in_table = []
    in_summary = False

    def flush_table():
        if in_table:
            tbl = render_md_table(in_table)
            if tbl:
                out.append(tbl)
            in_table.clear()

    for line in lines:
        stripped = line.strip()

        # 表格行
        if stripped.startswith("|") and stripped.endswith("|"):
            in_table.append(stripped)
            continue
        else:
            flush_table()

        # 标题(emoji + 文字)
        if stripped and not stripped.startswith("|") and len(stripped) < 60:
            # 复盘特殊标题
            if stripped.startswith("📊") or stripped.startswith("🕐"):
                out.append(f'<h3>{stripped}</h3>')
                continue
            # 简短标题(无冒号、无数字)
            if "━━━━━━━━━━━━━━━" in stripped:
                continue
            if "今日总结" in stripped:
                out.append(f'<div class="summary">{stripped}</div>')
                continue
            if "仅供参考" in stripped:
                out.append(f'<div class="summary">{stripped}</div>')
                continue
            # 普通文本
            out.append(f'<p style="margin: 6px 0; color: var(--text-secondary);">{stripped}</p>')
            continue

        if not stripped:
            out.append('<div style="height: 8px;"></div>')

    flush_table()
    return "".join(out)


def render_html(data: dict) -> str:
    template = (REPORTS_DIR / "index.template.html").read_text()

    indices_html = "\n".join(render_metric(d) for d in data["indices"])
    us_html = "\n".join(render_metric(d) for d in data["us"])

    if data["holdings"]:
        def _mk_h_row(h):
            code = str(h.get("code", ""))
            if h.get('market') == 'HK':
                link = f"https://gu.qq.com/hk{code}/gp"
            elif code.startswith(('6', '9')):
                link = f"https://gu.qq.com/sh{code}/gp"
            else:
                link = f"https://gu.qq.com/sz{code}/gp"
            return (
                f'<tr><td class="name-cell">'
                f'<a href="{link}" target="_blank" rel="noopener" style="color:var(--ef-blue);text-decoration:none;font-weight:600;">'
                f'{h["name"]}</a></td>'
                f'<td class="code-cell mono">{code}{".HK" if h.get("market") == "HK" else ""}</td>'
                f'<td class="mono">{h.get("current", "-") if isinstance(h.get("current"), (int, float)) else "-"}</td>'
                f'<td class="{"change-up" if h.get("change_pct", 0) > 0 else "change-down"} mono">'
                f'{h.get("change_pct", 0):+.2f}%</td>'
                f'<td class="sector-cell">{h.get("sector", "-")}</td></tr>'
            )
        rows = "\n".join(_mk_h_row(h) for h in data["holdings"])
        holdings_section = f'''<div class="section-title">当前持仓 <span class="badge">{len(data["holdings"])} 只</span></div>
        <div class="card"><table class="data-table">
            <thead><tr><th>名称</th><th>代码</th><th>现价</th><th>涨跌</th><th>板块</th></tr></thead>
            <tbody>{rows}</tbody>
        </table></div>'''
    else:
        holdings_section = ""

    if data.get("picks"):
        picks_html = render_report_content(data["picks"]["content"])
        picks_section = f'''<div class="section-title">今日选股 <span class="badge">{data["picks"]["date"]}</span></div>
        <div class="card"><div class="card-padded report-content">{picks_html}</div></div>'''
    else:
        picks_section = ""

    if data.get("review"):
        review_html = render_report_content(data["review"]["content"])
        review_section = f'''<div class="section-title">盘后复盘 <span class="badge">{data["review"]["date"]}</span></div>
        <div class="card"><div class="card-padded report-content">{review_html}</div></div>'''
    else:
        review_section = ""

    monitor_rows = "\n".join(
        f'<tr><td class="name-cell">{m["name"]}</td>'
        f'<td class="code-cell mono">{m["freq"]}</td>'
        f'<td><span class="badge badge-active">{m["status"]}</span></td>'
        f'<td class="sector-cell mono">{m["last_push"]}</td></tr>'
        for m in data["monitor_status"]
    )

    # 去掉仪表板内的新闻区块（已迁移到独立页面）
    news_rows = ""

    now = datetime.datetime.now()
    # 把完整数据注入 JS(INITIAL_DATA)
    initial_data_json = json.dumps(data, ensure_ascii=False)
    return (template
        .replace("{{DATE}}", now.strftime("%Y-%m-%d"))
        .replace("{{TIME}}", now.strftime("%H:%M"))
        .replace("{{INDEX_COUNT}}", str(len(data["indices"])))
        .replace("{{MONITOR_COUNT}}", str(len(data["monitor_status"])))
        .replace("{{INDICES}}", indices_html)
        .replace("{{US}}", us_html)
        .replace("{{HOLDINGS_SECTION}}", holdings_section)
        .replace("{{PICKS_SECTION}}", picks_section)
        .replace("{{REVIEW_SECTION}}", review_section)
        .replace("{{MONITOR_STATUS}}", monitor_rows)
        .replace("{{NEWS_ROWS}}", "")
        .replace("{{REFRESH_TIME}}", now.strftime("%Y-%m-%d %H:%M:%S"))
        .replace("{{INITIAL_DATA}}", initial_data_json)
    )


def main():
    now = datetime.datetime.now()
    print(f"[{now:%H:%M:%S}] 开始生成仪表板...")

    # 采集数据
    indices = get_a_indices()
    print(f"  A股指数: {len(indices)} 条")
    us = get_us_indices()
    print(f"  美股: {len(us)} 条")
    holdings = get_holdings()
    print(f"  持仓: {len(holdings)} 只")
    if holdings:
        holdings = get_holdings_quotes(holdings)
    # 同步生成 holdings_compare.json(给 social_holding.html 用)
    holdings_compare = []
    for h in holdings:
        code = h.get('code', '')
        # 港股代码加 .HK 后缀
        if 'hk' in str(h.get('market', '')).lower() or code in ('02432', '9988', '00700'):
            code_display = code.lstrip('0') + '.HK' if code.startswith('0') else code + '.HK'
        else:
            code_display = code
        holdings_compare.append({
            'code': code_display,
            'name': h.get('name', ''),
            'sector': h.get('sector', ''),
        })
    DATA_DIR.joinpath('holdings_compare.json').write_text(
        json.dumps(holdings_compare, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f"  持仓对比 JSON: {len(holdings_compare)} 条")
    picks = get_latest_picks()
    print(f"  选股: {'有' if picks else '无'}")
    review = get_latest_review()
    print(f"  复盘: {'有' if review else '无'}")
    monitor_status = get_monitor_status()
    news = get_news()
    print(f"  资讯: {len(news)} 条")

    # 保存 JSON 数据
    data = {
        "generated_at": now.isoformat(),
        "indices": indices,
        "us": us,
        "holdings": holdings,
        "picks": picks,
        "review": review,
        "monitor_status": monitor_status,
        "news": news,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    latest_json = DATA_DIR / "latest.json"
    latest_json.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # 历史 JSON(按天)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_json = HISTORY_DIR / f"{now.strftime('%Y%m%d-%H%M')}.json"
    history_json.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # 刷新日期索引
    try:
        import subprocess
        subprocess.run(["python3", str(Path(__file__).parent / "snapshot_index.py")],
                      capture_output=True, timeout=10)
    except Exception:
        pass

    # 渲染 HTML
    html = render_html(data)
    index_html = REPORTS_DIR / "index.html"
    index_html.write_text(html)

    print(f"✅ 已生成: {index_html}")
    print(f"   JSON: {latest_json}")


if __name__ == "__main__":
    main()