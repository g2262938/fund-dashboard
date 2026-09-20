#!/usr/bin/env python3
"""每日凌晨更新：中报预亏 + 预增数据（含披露日期），并注入到 HTML 文件中
配合 cronjob: 0 3 * * * python3 /home/ubuntu/.openclaw/workspace/scripts/yjyg_daily_update.py
"""
import subprocess, json, sys, os, requests, pandas as pd
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(SCRIPT_DIR)  # /home/ubuntu/.openclaw/workspace
REPORTS = os.path.join(WORKSPACE, "reports")

DISCLOSURE_CSV = os.path.join(REPORTS, "data/yjyg_disclosure_2026h1.csv")
DISCLOSURE_URL = "http://www.cninfo.com.cn/new/information/getPrbookInfo"
COLS = ['股票代码','股票简称','首次预约','初次变更','二次变更','三次变更',
        '实际披露','报告期','预留','orgId']

def fetch_disclosure_dates():
    """从巨潮获取2026年中报预约披露日期"""
    all_data = []
    for market in ['szsh', 'sh', 'bj']:
        params = {
            "sectionTime": "2026-06-30", "firstTime": "", "lastTime": "",
            "market": market, "stockCode": "", "pagesize": "10000", "pagenum": "1",
        }
        try:
            r = requests.post(DISCLOSURE_URL, params=params,
                              headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            text_json = r.json()
            rows = text_json.get("prbookinfos", [])
            if rows:
                df = pd.DataFrame(rows)
                df.columns = COLS
                all_data.append(df)
                print(f"  [{market}] {len(rows)} 条")
        except Exception as e:
            print(f"  [{market}] 获取失败: {e}")
    if not all_data:
        return {}
    df_all = pd.concat(all_data, ignore_index=True)
    df_all['股票代码'] = df_all['股票代码'].astype(str).str.zfill(6)
    df_all['首次预约'] = pd.to_datetime(
        df_all['首次预约'], errors='coerce'
    ).dt.date.astype(str).str.replace('NaT', '')
    disc_map = dict(zip(df_all['股票代码'], df_all['首次预约']))
    df_all.to_csv(DISCLOSURE_CSV, index=False)
    print(f"  合计 {len(disc_map)} 条，已保存")
    return disc_map

def merge_disclosure(records, disc_map):
    for r in records:
        code = r['code']
        date = disc_map.get(code, '')
        r['disclosure_date'] = date if date and date != 'NaT' else ''
    return records

def run_script(name, path):
    print(f"\n{'='*50}")
    print(f"▶ 运行 {name}")
    print('='*50)
    result = subprocess.run(
        [sys.executable, path],
        capture_output=False,
        cwd=SCRIPT_DIR
    )
    if result.returncode != 0:
        print(f"❌ {name} 执行失败，returncode={result.returncode}")
        return False
    print(f"✅ {name} 完成")
    return True

def inject_into_html(json_path, html_path):
    """将 JSON 数据内嵌到 HTML 的 EMBEDDED_DATA 占位符中（brace-matching 精确替换）"""
    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)

    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    marker = 'const EMBEDDED_DATA = '
    start = html.find(marker)
    if start < 0:
        print(f"  ⚠️ 未找到 {marker}，跳过 {html_path}")
        return False

    json_start = start + len(marker)
    # Find matching closing brace
    depth = 0
    for i in range(json_start, len(html)):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                break
    json_end = i + 1

    new_json = json.dumps(data, ensure_ascii=False)
    new_html = html[:start] + marker + new_json + ';' + html[json_end:]

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    size = os.path.getsize(html_path)
    print(f"  ✅ {os.path.basename(html_path)} 数据已注入 ({size//1024}KB, {len(data['records'])}条)")
    return True

def main():
    print(f"=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 中报业绩预告每日更新 ===")

    # 1. 获取披露日期
    print("\n[1/4] 获取2026年中报预约披露日期...")
    disc_map = fetch_disclosure_dates()

    # 2. 跑预亏脚本
    print("\n[2/4] 抓取预亏数据...")
    ok1 = run_script("预亏数据采集", os.path.join(SCRIPT_DIR, "yjyg_loss_report.py"))

    # 3. 跑预增脚本
    print("\n[3/4] 抓取预增数据...")
    ok2 = run_script("预增数据采集", os.path.join(SCRIPT_DIR, "yjyg_gain_report.py"))

    if not (ok1 and ok2):
        print("\n⚠️ 数据采集有错误，但继续注入...")
    else:
        print("\n✅ 两份数据采集全部成功")

    # 4. 合并披露日期 + 注入 HTML
    print("\n[4/4] 合并披露日期并注入HTML...")
    json_loss = os.path.join(REPORTS, "data", "yjyg_loss_latest.json")
    json_gain = os.path.join(REPORTS, "data", "yjyg_gain_latest.json")
    html_loss = os.path.join(REPORTS, "yjyg_loss.html")
    html_gain = os.path.join(REPORTS, "yjyg_gain.html")

    for jp, hp in [(json_loss, html_loss), (json_gain, html_gain)]:
        if not os.path.exists(jp):
            print(f"  ⚠️ {jp} 不存在，跳过")
            continue
        # 合并披露日期
        with open(jp, encoding='utf-8') as f:
            data = json.load(f)
        merge_disclosure(data['records'], disc_map)
        with open(jp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 注入 HTML
        inject_into_html(jp, hp)

    print("\n✅ 每日更新完成")

if __name__ == "__main__":
    main()
