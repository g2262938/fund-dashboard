#!/usr/bin/env python3
"""
选股结果推送脚本 v2
读取 simple_picker.py 生成的报告，推送微信 + 写日志

数据源（simple_picker.py 输出）：
  - 每日选股/综合选股-{date}.md   ← 完整报告（含强势+稳健+观察）
  - 每日选股/推荐-{date}.md         ← 5只最终推荐
"""

import subprocess, sys, logging, os
from pathlib import Path
from datetime import datetime, date

LOG_FILE  = '/tmp/push_pick.log'
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    encoding='utf-8', filemode='a'
)

REPORT_DIR   = Path('/home/ubuntu/.openclaw/workspace/每日选股')
WECHAT_TARGET = 'o9cq80z4Nv9VbLwPfTbahT8986V0@im.wechat'


def read_report(date_str: str, filename: str) -> str | None:
    """读取指定日期的报告文件，返回内容或None"""
    f = REPORT_DIR / filename.replace('{date}', date_str)
    if f.exists():
        return f.read_text(errors='replace')
    return None


def extract_from_rec_file(rec_content: str) -> str:
    """
    从推荐文件（推荐-{date}.md）提取前5只股，行业列直接保留
    格式：| 名称 | 代码 | 评分 | 模式 | 行业 |
    """
    lines = rec_content.strip().split('\n')
    result = []
    for line in lines:
        if line.startswith('|') and '名称' not in line and '---' not in line and line.count('|') >= 4:
            result.append(line)
        if len(result) >= 5:
            break
    return '\n'.join(result)


def extract_5_recs(content: str) -> str:
    """
    从综合选股报告提取前5只推荐股（强势+稳健）
    用于微信推送的简要版
    """
    lines = content.strip().split('\n')
    result_lines = []
    capture = False
    rows = 0

    for line in lines:
        # 检测到强势或稳健模式表格开始
        if ('🟢 强势' in line or '**🟢 强势' in line) and '模式' in line:
            capture = True
            result_lines.append(line)
            continue
        if capture and rows < 6:  # 最多6行（表头+5只）
            result_lines.append(line)
            if line.startswith('|'):  # 表格行
                rows += 1
            if '**⚪' in line or '🔴' in line:  # 遇到下一档就停
                break
        # 稳健模式单独追加
        if '🟡 稳健' in line and '模式' in line and not capture:
            result_lines.append('')
            result_lines.append(line)
            capture = True
            rows = 0

    return '\n'.join(result_lines).strip()


def build_wechat_msg(date_str: str, full_report: str, rec_report: str) -> str:
    """构建微信推送消息"""
    parts = []

    # 标题
    parts.append(f"📊 **每日选股 {date_str}**")
    parts.append("")

    # 简要推荐（5只）— 优先读推荐文件（行业列更干净）
    rec_lines = ''
    if rec_report:
        rec_lines = extract_from_rec_file(rec_report)
    if not rec_lines and full_report:
        rec_lines = extract_5_recs(full_report)

    if rec_lines:
        parts.append("**【今日推荐】**")
        parts.append(rec_lines)
    else:
        # 如果综合报告解析失败，尝试读推荐文件
        if rec_report:
            parts.append("**【今日推荐】**")
            parts.append(rec_report[:500])
        else:
            parts.append("⚠️ 今日选股结果暂未生成")

    parts.append("")
    parts.append("🕐 来源：simple_picker v6")

    msg = '\n'.join(parts)

    # 微信限制2000字
    if len(msg) > 1950:
        msg = msg[:1950] + '\n…（详见Web看板）'

    return msg


def main():
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logging.info(f"[{ts}] 推送选股脚本 v2 启动 | PID={os.getpid()}")

    today = date.today().strftime('%Y%m%d')
    full_report  = read_report(today, '综合选股-{date}.md')
    rec_report   = read_report(today, '推荐-{date}.md')

    if not full_report and not rec_report:
        msg = f"⚠️ 每日选股 {today} 报告尚未生成"
        logging.warning(msg)
    else:
        msg = build_wechat_msg(today, full_report or '', rec_report or '')
        logging.info(f"报告已读取 | 综合{len(full_report or '')}字符 | 推荐{len(rec_report or '')}字符")

    # 推送微信
    cmd = ['hermes', 'send', '--to', f'weixin:{WECHAT_TARGET}', msg]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=55)
        if r.returncode == 0:
            logging.info(f"[推送选股] 成功 | stdout: {r.stdout[:80]}")
            sys.stdout.write(f"[推送选股] ✅ 成功\n")
        else:
            logging.error(f"[推送选股] 失败 rc={r.returncode} stderr: {r.stderr[:100]}")
            sys.stdout.write(f"[推送选股] ❌ 失败 rc={r.returncode} {r.stderr[:50]}\n")
    except subprocess.TimeoutExpired:
        logging.error("[推送选股] 超时55秒")
        sys.stdout.write("[推送选股] ❌ 超时55秒\n")
    except Exception as e:
        logging.error(f"[推送选股] 异常: {e}")
        sys.stdout.write(f"[推送选股] ❌ 异常: {e}\n")

    sys.stdout.flush()


if __name__ == '__main__':
    main()
