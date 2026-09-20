#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
A+H 股 dual-list data collector.
获取在境内（A股）和香港（H股）同时上市的股票配对列表，
包括实时行情、板块、总市值、溢价率等。
"""
import json
import time
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import akshare as ak

DATA_DIR = '/home/ubuntu/.openclaw/workspace/reports/data'
OUTPUT_JSON = f'{DATA_DIR}/ah_dual_latest.json'
OUTPUT_HTML = '/home/ubuntu/.openclaw/workspace/reports/ah_dual_list.html'


def fetch_ah_dual_list():
    """主函数：抓取 A+H 配对数据"""
    print("📡 抓取 A+H 配对数据...")

    # 1. 获取 AH 名称配对（H股端）
    print("  [1/3] 获取 H股代码列表 (腾讯 AH)...", end=" ")
    try:
        df_hk_name = ak.stock_zh_ah_name()  # 返回 ['代码', '名称'] 220条
        print(f"✅ {len(df_hk_name)} 条")
    except Exception as e:
        print(f"❌ {e}")
        return None

    # 2. 获取 A股全市场实时行情（用于匹配A股代码）
    print("  [2/3] 获取 A股全市场实时行情 (新浪)...", end=" ")
    try:
        df_a_spot = ak.stock_zh_a_spot()  # 返回全A股 5500+ 条
        print(f"✅ {len(df_a_spot)} 条")
    except Exception as e:
        print(f"❌ {e}")
        return None

    # 3. 获取 H股实时行情（腾讯）
    print("  [3/3] 获取 H股实时行情 (腾讯)...", end=" ")
    try:
        df_hk_spot = ak.stock_zh_ah_spot()  # 返回 220 条
        print(f"✅ {len(df_hk_spot)} 条")
    except Exception as e:
        print(f"❌ {e}")
        return None

    # 4. 构建名称匹配表（A股名称 -> A股代码）
    #    A股名称含交易所后缀如"招商银行(600036)", H股名称不带后缀如"招商银行"
    #    需要模糊匹配
    a_name_to_code = {}
    for _, row in df_a_spot.iterrows():
        a_code = str(row['代码']).strip()
        a_name = str(row['名称']).strip()
        # 去掉前缀标志(bj/sh/sz)
        if a_code.startswith('sh'):
            a_code = a_code[2:]
        elif a_code.startswith('sz'):
            a_code = a_code[2:]
        elif a_code.startswith('bj'):
            a_code = a_code[2:]
        a_name_to_code[a_name] = a_code

    # 5. 构建配对
    pairs = []
    matched_a_names = set()

    for _, hk_row in df_hk_name.iterrows():
        hk_code = str(hk_row['代码']).strip()
        hk_name = str(hk_row['名称']).strip()

        # 从H股实时行情里拿价格
        hk_spot = df_hk_spot[df_hk_spot['代码'] == hk_code]
        if not hk_spot.empty:
            hk_spot = hk_spot.iloc[0]
            hk_price = hk_spot.get('最新价')
            hk_change_pct = hk_spot.get('涨跌幅')
            hk_volume = hk_spot.get('成交量')
            hk_turnover = hk_spot.get('成交额')
        else:
            hk_price = hk_change_pct = hk_volume = hk_turnover = None

        # 匹配A股：遍历A股找名称含H股名称的
        a_code = None
        a_name = None
        a_price = None
        a_change_pct = None
        a_market_cap = None
        a_board = None

        # 精确匹配（名称完全一致）
        if hk_name in a_name_to_code:
            a_code = a_name_to_code[hk_name]
        else:
            # 模糊匹配：A股名称包含H股名称
            for a_nm, a_cd in a_name_to_code.items():
                if hk_name in a_nm or a_nm in hk_name:
                    if hk_name not in matched_a_names:  # 避免一个A股匹配多个H股
                        a_code = a_cd
                        a_name = a_nm
                        break

        if a_code:
            matched_a_names.add(a_name or hk_name)
            # 从A股实时数据里取完整信息
            a_rows = df_a_spot[df_a_spot['代码'].str.contains(a_code, na=False)]
            if not a_rows.empty:
                a_row = a_rows.iloc[0]
                a_code_full = str(a_row['代码'])
                a_name = str(a_row['名称'])
                a_price = a_row.get('最新价')
                a_change_pct = a_row.get('涨跌幅')
                # 成交额/成交量转市值估算（简化处理）
                a_turnover = a_row.get('成交额', 0) or 0
                if pd.notna(a_turnover) and a_turnover > 0:
                    # 用换手率估算市值：市值 ≈ 成交额/换手率（万分比）* 10000
                    # 这里直接用成交额字段作为市值代理
                    a_market_cap = a_turnover
                # 判断板块
                if a_code_full.startswith('sh6') or a_code_full.startswith('600'):
                    a_board = '沪市主板'
                elif a_code_full.startswith('sh68') or a_code_full.startswith('688'):
                    a_board = '科创板'
                elif a_code_full.startswith('sz000'):
                    a_board = '深市主板'
                elif a_code_full.startswith('sz300'):
                    a_board = '创业板'
                elif a_code_full.startswith('bj'):
                    a_board = '北交所'

        pair = {
            'hk_code': hk_code,
            'hk_name': hk_name,
            'hk_price': float(hk_price) if pd.notna(hk_price) else None,
            'hk_change_pct': float(hk_change_pct) if pd.notna(hk_change_pct) else None,
            'hk_volume': float(hk_volume) if pd.notna(hk_volume) else None,
            'hk_turnover': float(hk_turnover) if pd.notna(hk_turnover) else None,
            'a_code': a_code,
            'a_name': a_name,
            'a_price': float(a_price) if pd.notna(a_price) else None,
            'a_change_pct': float(a_change_pct) if pd.notna(a_change_pct) else None,
            'a_market_cap': float(a_market_cap) if pd.notna(a_market_cap) else None,
            'a_board': a_board,
        }
        pairs.append(pair)

    # 6. 过滤掉没匹配到A股的
    matched = [p for p in pairs if p['a_code']]
    unmatched = [p for p in pairs if not p['a_code']]

    print(f"\n✅ 配对成功: {len(matched)} / {len(pairs)}")
    if unmatched:
        print(f"⚠️ 未匹配A股（名称差异）: {', '.join([u['hk_name'] for u in unmatched[:5]])}{'...' if len(unmatched)>5 else ''}")

    return {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'total_pairs': len(matched),
        'total_hk': len(pairs),
        'records': matched
    }


def generate_html(data):
    """生成独立的 HTML 页面"""
    records = data['records']

    # 格式化数据行
    rows = []
    for r in records:
        hk_p = f"{r['hk_price']:.2f}" if r['hk_price'] else '—'
        hk_c = f"{r['hk_change_pct']:+.2f}%" if r['hk_change_pct'] else '—'
        a_p = f"{r['a_price']:.2f}" if r['a_price'] else '—'
        a_c = f"{r['a_change_pct']:+.2f}%" if r['a_change_pct'] else '—'

        # 溢价率（需要换算，暂时用H/A价格比）
        if r['hk_price'] and r['a_price']:
            ratio = r['hk_price'] / r['a_price']
            premium = f"{ratio:.2f}"
        else:
            premium = '—'

        # 格式化成交额
        def fmt_amt(v):
            if v is None: return '—'
            if v >= 1e8: return f"{v/1e8:.2f}亿"
            if v >= 1e4: return f"{v/1e4:.2f}万"
            return f"{v:.0f}"

        hk_amt = fmt_amt(r['hk_turnover'])

        cls = 'gain' if (r['hk_change_pct'] or 0) > 0 else 'loss' if (r['hk_change_pct'] or 0) < 0 else ''

        rows.append(f"""<tr>
  <td class="code">{r['hk_code']}</td>
  <td>{r['hk_name']}</td>
  <td>{r['hk_price'] if r['hk_price'] else '—'}</td>
  <td class="{cls}">{hk_c}</td>
  <td>{hk_amt}</td>
  <td class="code">{(r['a_code'] or '—')}</td>
  <td>{(r['a_name'] or '—')}</td>
  <td>{a_p}</td>
  <td class="{'gain' if (r['a_change_pct'] or 0) > 0 else 'loss' if (r['a_change_pct'] or 0) < 0 else ''}">{a_c}</td>
  <td>{(r['a_board'] or '—')}</td>
  <td>{premium}</td>
</tr>""")

    rows_html = '\n'.join(rows)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A+H 双边行情 · 信科移动内参</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
        background: #0d1117; color: #e6edf3; font-size: 13px; }}

.header {{ background: linear-gradient(135deg,#1a1f35,#16213e); padding: 20px 28px;
            border-bottom: 1px solid #30363d; }}
.header h1 {{ font-size: 18px; font-weight: 600; color: #58a6ff; margin-bottom: 6px; }}
.header .sub {{ color: #8b949e; font-size: 12px; }}
.stats-bar {{ display: flex; gap: 32px; padding: 14px 28px; background: #161b22;
              border-bottom: 1px solid #21262d; flex-wrap: wrap; }}
.stat {{ text-align: center; }}
.stat .v {{ font-size: 22px; font-weight: 700; color: #58a6ff; }}
.stat .l {{ font-size: 11px; color: #8b949e; margin-top: 2px; }}

.toolbar {{ padding: 12px 28px; background: #161b22; display: flex; gap: 10px;
            border-bottom: 1px solid #21262d; flex-wrap: wrap; align-items: center; }}
.toolbar .info {{ color: #8b949e; font-size: 12px; margin-left: auto; }}
select, input {{ background: #21262d; color: #e6edf3; border: 1px solid #30363d;
                border-radius: 6px; padding: 6px 12px; font-size: 13px; outline: none; }}
select:focus, input:focus {{ border-color: #58a6ff; outline: none; }}
input {{ width: 180px; }}
.btns {{ display: flex; gap: 8px; }}
.btn {{ background: #238636; color: #fff; border: none; border-radius: 6px;
        padding: 6px 14px; font-size: 13px; cursor: pointer; }}
.btn:hover {{ background: #2ea043; }}
.btn.secondary {{ background: #21262d; border: 1px solid #30363d; }}

table {{ width: 100%; border-collapse: collapse; }}
thead {{ position: sticky; top: 0; z-index: 10; background: #1c2128; }}
th {{ padding: 10px 8px; text-align: left; font-size: 11px; font-weight: 600;
      color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em;
      border-bottom: 1px solid #30363d; white-space: nowrap; cursor: pointer; user-select: none; }}
th:hover {{ color: #58a6ff; }}
td {{ padding: 9px 8px; border-bottom: 1px solid #21262d; white-space: nowrap; }}
tr:hover td {{ background: #1c2128; }}
.code {{ font-family: 'SF Mono', 'Cascadia Code', monospace; color: #79c0ff; font-size: 12px; }}
.gain {{ color: #3fb950; }}
.loss {{ color: #f85149; }}
.pagination {{ display: flex; justify-content: center; align-items: center; gap: 8px;
               padding: 16px; background: #161b22; border-top: 1px solid #21262d; }}
.page-btn {{ background: #21262d; border: 1px solid #30363d; color: #e6edf3;
             border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }}
.page-btn:hover {{ border-color: #58a6ff; }}
.page-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
#page-info {{ color: #8b949e; font-size: 13px; min-width: 120px; text-align: center; }}
#table-wrap {{ max-height: calc(100vh - 220px); overflow: auto; }}
.loading {{ text-align: center; padding: 60px; color: #8b949e; }}
</style>
</head>
<body>

<div class="header">
  <h1>📊 A+H 双边行情监控</h1>
  <div class="sub">同步展示在境内（A股）和香港（H股）双重上市股票的实时行情</div>
</div>

<div class="stats-bar">
  <div class="stat"><div class="v" id="s-total">{data['total_pairs']}</div><div class="l">A+H 标的数</div></div>
  <div class="stat"><div class="v" id="s-up">-</div><div class="l">H股上涨</div></div>
  <div class="stat"><div class="v" id="s-down">-</div><div class="l">H股下跌</div></div>
  <div class="stat"><div class="v">{data['generated_at'][:10]}</div><div class="l">数据日期</div></div>
</div>

<div class="toolbar">
  <select id="board-filter">
    <option value="">全部板块</option>
    <option value="沪市主板">沪市主板</option>
    <option value="科创板">科创板</option>
    <option value="深市主板">深市主板</option>
    <option value="创业板">创业板</option>
    <option value="北交所">北交所</option>
  </select>
  <select id="sort-field">
    <option value="hk_change_pct">涨跌幅(H股)</option>
    <option value="hk_price">H股价格</option>
    <option value="a_code">A股代码</option>
    <option value="hk_name">名称</option>
  </select>
  <select id="sort-dir">
    <option value="desc">降序 ↓</option>
    <option value="asc">升序 ↑</option>
  </select>
  <input type="text" id="search-input" placeholder="搜索代码 / 名称...">
  <div class="btns">
    <button class="btn" onclick="exportCSV()">📥 导出CSV</button>
    <button class="btn secondary" onclick="location.reload()">🔄 刷新</button>
  </div>
  <span class="info">数据来源：腾讯财经（港股）+ 新浪财经（A股）</span>
</div>

<div id="table-wrap">
<table>
<thead>
<tr>
  <th data-col="hk_code">H股代码</th>
  <th data-col="hk_name">H股名称</th>
  <th data-col="hk_price">H股价格(港元)</th>
  <th data-col="hk_change_pct">H股涨跌%</th>
  <th data-col="hk_turnover">H股成交额(港元)</th>
  <th data-col="a_code">A股代码</th>
  <th data-col="a_name">A股名称</th>
  <th data-col="a_price">A股价格(元)</th>
  <th data-col="a_change_pct">A股涨跌%</th>
  <th data-col="a_board">A股板块</th>
  <th data-col="premium">H/A比价</th>
</tr>
</thead>
<tbody id="table-body">
</tbody>
</table>
</div>

<div class="pagination">
  <button class="page-btn" id="prev-btn" onclick="prevPage()" disabled>◀ 上一页</button>
  <span id="page-info"></span>
  <button class="page-btn" id="next-btn" onclick="nextPage()">下一页 ▶</button>
</div>

<script>
const RAW_DATA = {json.dumps(records, ensure_ascii=False)};

let currentPage = 1;
const PAGE_SIZE = 50;
let filtered = [...RAW_DATA];
let sortField = 'hk_change_pct';
let sortDir = 'desc';

function getVal(r, field) {{
  if (field === 'hk_change_pct') return r.hk_change_pct ?? -999;
  if (field === 'hk_price') return r.hk_price ?? 0;
  if (field === 'hk_turnover') return r.hk_turnover ?? 0;
  if (field === 'a_price') return r.a_price ?? 0;
  if (field === 'a_change_pct') return r.a_change_pct ?? -999;
  if (field === 'hk_name') return r.hk_name || '';
  if (field === 'hk_code') return r.hk_code || '';
  if (field === 'a_code') return r.a_code || '';
  if (field === 'a_board') return r.a_board || '';
  if (field === 'premium') {{
    if (r.hk_price && r.a_price) return r.hk_price / r.a_price;
    return null;
  }}
  return 0;
}}

function applySort(arr) {{
  return arr.sort((a, b) => {{
    const va = getVal(a, sortField);
    const vb = getVal(b, sortField);
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    const cmp = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
    return sortDir === 'asc' ? cmp : -cmp;
  }});
}}

function applyFilter() {{
  const board = document.getElementById('board-filter').value;
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  filtered = RAW_DATA.filter(r => {{
    if (board && r.a_board !== board) return false;
    if (q) {{
      const s = (r.hk_code + r.hk_name + (r.a_code||'') + (r.a_name||'')).toLowerCase();
      if (!s.includes(q)) return false;
    }}
    return true;
  }});
  filtered = applySort(filtered);
  currentPage = 1;
  render();
  updateStats();
}}

function render() {{
  const tbody = document.getElementById('table-body');
  const start = (currentPage - 1) * PAGE_SIZE;
  const page = filtered.slice(start, start + PAGE_SIZE);

  if (page.length === 0) {{
    tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:#8b949e;">暂无数据</td></tr>';
    return;
  }}

  tbody.innerHTML = page.map(r => {{
    const hkCls = (r.hk_change_pct || 0) > 0 ? 'gain' : (r.hk_change_pct || 0) < 0 ? 'loss' : '';
    const aCls = (r.a_change_pct || 0) > 0 ? 'gain' : (r.a_change_pct || 0) < 0 ? 'loss' : '';
    const premium = (r.hk_price && r.a_price) ? (r.hk_price / r.a_price).toFixed(2) : '—';
    const fmtAmt = v => {{ if (!v) return '—'; if (v >= 1e8) return (v/1e8).toFixed(2)+'亿'; if (v >= 1e4) return (v/1e4).toFixed(2)+'万'; return v.toFixed(0); }};
    return `<tr>
      <td class="code">${{r.hk_code || '—'}}</td>
      <td>${{r.hk_name || '—'}}</td>
      <td>${{r.hk_price ? r.hk_price.toFixed(2) : '—'}}</td>
      <td class="${{hkCls}}">${{r.hk_change_pct != null ? (r.hk_change_pct>0?'+':'')+r.hk_change_pct.toFixed(2)+'%' : '—'}}</td>
      <td>${{fmtAmt(r.hk_turnover)}}</td>
      <td class="code">${{r.a_code || '—'}}</td>
      <td>${{r.a_name || '—'}}</td>
      <td>${{r.a_price ? r.a_price.toFixed(2) : '—'}}</td>
      <td class="${{aCls}}">${{r.a_change_pct != null ? (r.a_change_pct>0?'+':'')+r.a_change_pct.toFixed(2)+'%' : '—'}}</td>
      <td>${{r.a_board || '—'}}</td>
      <td>${{premium}}</td>
    </tr>`;
  }}).join('');

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  document.getElementById('page-info').textContent = `第 ${{currentPage}} / ${{totalPages}} 页，共 ${{filtered.length}} 条`;
  document.getElementById('prev-btn').disabled = currentPage <= 1;
  document.getElementById('next-btn').disabled = currentPage >= totalPages;
}}

function prevPage() {{ if (currentPage > 1) {{ currentPage--; render(); }} }}
function nextPage() {{ currentPage++; render(); }}

function updateStats() {{
  const up = RAW_DATA.filter(r => (r.hk_change_pct || 0) > 0).length;
  const down = RAW_DATA.filter(r => (r.hk_change_pct || 0) < 0).length;
  document.getElementById('s-up').textContent = up;
  document.getElementById('s-down').textContent = down;
}}

// 排序
document.querySelectorAll('th[data-col]').forEach(th => {{
  th.addEventListener('click', () => {{
    const col = th.dataset.col;
    if (sortField === col) {{
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    }} else {{
      sortField = col;
      sortDir = 'desc';
    }}
    filtered = applySort(filtered);
    currentPage = 1;
    render();
  }});
}});

// 搜索
document.getElementById('search-input').addEventListener('input', applyFilter);
document.getElementById('board-filter').addEventListener('change', applyFilter);
document.getElementById('sort-field').addEventListener('change', e => {{ sortField = e.target.value; applyFilter(); }});
document.getElementById('sort-dir').addEventListener('change', e => {{ sortDir = e.target.value; applyFilter(); }});

// 导出CSV
function exportCSV() {{
  const hdrs = ['H股代码','H股名称','H股价格(港元)','H股涨跌%','H股成交额(港元)','A股代码','A股名称','A股价格(元)','A股涨跌%','A股板块','H/A比价'];
  const rows = filtered.map(r => [r.hk_code, r.hk_name, r.hk_price, r.hk_change_pct, r.hk_turnover, r.a_code, r.a_name, r.a_price, r.a_change_pct, r.a_board, (r.hk_price && r.a_price) ? (r.hk_price/r.a_price).toFixed(2) : '']);
  const csv = [hdrs, ...rows].map(r => r.join(',')).join('\\n');
  const blob = new Blob(['\\ufeff'+csv], {{ type: 'text/csv;charset=utf-8' }});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'ah_dual_list_{time.strftime("%Y%m%d")}.csv';
  a.click();
}}

// 初始化
applyFilter();
</script>
</body>
</html>"""
    return html


if __name__ == '__main__':
    data = fetch_ah_dual_list()
    if not data:
        print("❌ 数据获取失败")
        exit(1)

    # 保存 JSON
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON 已保存: {OUTPUT_JSON}")

    # 生成 HTML
    html = generate_html(data)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ HTML 已保存: {OUTPUT_HTML}")
    print(f"共 {data['total_pairs']} 个 A+H 配对")
