#!/bin/bash
# 美股财报监控 watchdog
# no_agent 模式：stdout 直接 deliver 到微信
# 策略：
#   - 有更新才输出内容（触发 deliver）
#   - 无更新输出 "无更新"（静默，不触发）
# 首次运行（state 文件不存在）：初始化状态，不推送
# 后续运行：检测到新财报立即推送微信

set -uo pipefail

STATE_FILE="${FUND_STATE_FILE:-/home/ubuntu/.openclaw/workspace/scripts/seen_earn_state.json}"
PYTHON="${FUND_PYTHON_BIN:-/home/ubuntu/.hermes/hermes-agent/venv/bin/python}"
LOGDIR="${FUND_LOG_DIR:-/tmp}"

# flock 互斥
LOCKFILE="$LOGDIR/us_earnings_watchdog.lock"
mkdir -p "$(dirname "$STATE_FILE")" "$LOGDIR"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo "[$(date)] ⏳ 上一次美股财报监控仍在运行，跳过本次" >> "$LOGDIR/us_earnings.log"
    exit 0
fi

if [ ! -f "$STATE_FILE" ]; then
    # 首次运行：初始化状态
    echo "首次运行，初始化状态..."
    $PYTHON -c "
import yfinance as yf, json, math, concurrent.futures
import sys
sys.stderr = open('/dev/null', 'w')
from datetime import date

WATCHLIST = ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','AMD','INTC','NFLX','JPM','BAC','GS','MS','V','MA','AXP','BLK','SCHW','AVGO','ORCL','CRM','ADBE','QCOM','MU','TXN','LRCX','AMAT','CSCO','PEP','KO','MCD','SBUX','WMT','COST','NKE','HD','TGT','LOW','TJX','JNJ','UNH','LLY','ABBV','ABT','MRK','PFE','TMO','DHR','DIS','CMCSA','BA','T','VZ','CAT','GE','MMM','LMT','RTX','PLTR','LCID','SOFI','RIVN','COIN','HOOD','BABA','JD','NTES','PDD','BIDU','DIA','QQQ','SPY','IWM','SMH','XLE']

def fetch_one(sym):
    try:
        t = yf.Ticker(sym)
        ed = t.get_earnings_dates(limit=3)
        if ed is None or ed.empty: return None
        for _, row in ed.iterrows():
            s = row.get('Surprise(%)')
            a = row.get('Reported EPS')
            if s is None or (isinstance(s, float) and math.isnan(s)): continue
            if a is None or (isinstance(a, float) and math.isnan(a)): continue
            edate = row.get('Date')
            ds = str(edate)[:10] if edate else ''
            return {'symbol': sym, 'actual': float(a), 'surprise': float(s), 'date': ds}
    except: pass
    return None

state = {'seen': {}, 'init': True}
results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    futures = {ex.submit(fetch_one, s): s for s in WATCHLIST}
    for fut in concurrent.futures.as_completed(futures, timeout=40):
        r = fut.result()
        if r: results.append(r)

for rec in results:
    sym = rec['symbol']
    state['seen'][sym] = {'actual': rec['actual'], 'surprise': rec['surprise'], 'date': rec['date']}

with open('$STATE_FILE', 'w') as f:
    json.dump(state, f)
print(f'初始化完成，已记录 {len(results)} 只股票状态')
" 2>&1
    echo "INIT"
    exit 0
fi

# 增量检测
RESULT=$($PYTHON -c "
import yfinance as yf, json, math, concurrent.futures
import sys
from datetime import date

# Suppress stderr noise from yfinance
sys.stderr = open('/dev/null', 'w')

STATE_FILE = '$STATE_FILE'
WATCHLIST = ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','AMD','INTC','NFLX','JPM','BAC','GS','MS','V','MA','AXP','BLK','SCHW','AVGO','ORCL','CRM','ADBE','QCOM','MU','TXN','LRCX','AMAT','CSCO','PEP','KO','MCD','SBUX','WMT','COST','NKE','HD','TGT','LOW','TJX','JNJ','UNH','LLY','ABBV','ABT','MRK','PFE','TMO','DHR','DIS','CMCSA','BA','T','VZ','CAT','GE','MMM','LMT','RTX','PLTR','LCID','SOFI','RIVN','COIN','HOOD','BABA','JD','NTES','PDD','BIDU','DIA','QQQ','SPY','IWM','SMH','XLE']

def fetch_one(sym):
    try:
        t = yf.Ticker(sym)
        ed = t.get_earnings_dates(limit=3)
        if ed is None or ed.empty: return None
        for _, row in ed.iterrows():
            s = row.get('Surprise(%)')
            a = row.get('Reported EPS')
            if s is None or (isinstance(s, float) and math.isnan(s)): continue
            if a is None or (isinstance(a, float) and math.isnan(a)): continue
            est = row.get('EPS Estimate')
            est_val = float(est) if est and not (isinstance(est, float) and math.isnan(est)) else None
            edate = row.get('Date')
            ds = str(edate)[:10] if edate else ''
            return {'symbol': sym, 'actual': float(a), 'estimate': est_val, 'surprise': float(s), 'date': ds}
    except: pass
    return None

with open(STATE_FILE) as f:
    state = json.load(f)

results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    futures = {ex.submit(fetch_one, s): s for s in WATCHLIST}
    for fut in concurrent.futures.as_completed(futures, timeout=40):
        r = fut.result()
        if r: results.append(r)

new_items = []
for rec in results:
    sym = rec['symbol']
    seen = state['seen'].get(sym, {})
    prev_actual = seen.get('actual')
    curr_actual = rec['actual']
    if prev_actual is None:
        state['seen'][sym] = {'actual': curr_actual, 'surprise': rec['surprise'], 'date': rec['date']}
    elif prev_actual != curr_actual:
        surprise = rec['surprise']
        verdict = 'beat' if surprise > 5 else ('miss' if surprise < -5 else 'inline')
        new_items.append({**rec, 'verdict': verdict})
        state['seen'][sym] = {'actual': curr_actual, 'surprise': rec['surprise'], 'date': rec['date']}

with open(STATE_FILE, 'w') as f:
    json.dump(state, f)

# 输出：有更新才输出内容
if new_items:
    lines = ['📋 美股财报提醒 ' + str(date.today())]
    for item in new_items:
        verdict = item['verdict']
        label = '✅超预期' if verdict == 'beat' else ('❌不及预期' if verdict == 'miss' else '📊符合预期')
        est_str = '\$' + f'{item[\"estimate\"]:.2f}' if item.get('estimate') else '?'
        lines.append(f'{label} {item[\"symbol\"]} | 实际EPS \${item[\"actual\"]:.2f} vs 预期{est_str} | {item[\"surprise\"]:+.1f}%')
    print('\n'.join(lines))
else:
    print('NO_UPDATE')
" 2>&1)

if [ "$RESULT" = "NO_UPDATE" ]; then
    exit 0
fi

if [ "$RESULT" != "INIT" ] && [ -n "$RESULT" ]; then
    echo "$RESULT"
fi
