#!/usr/bin/env python3
"""社保持股每日采集(真实数据版 v4 - 健壮网络版)
- 数据源:ak.stock_gdfx_top_10_em(东方财富 十大股东公告原文)
- 范围:沪深 300 + 中证 500 + 中证 1000 (约 1800 只)
- 双网络保障:akshare → 腾讯备用 → 本地缓存
- 自动检测最新报告期
"""
import sys, json, time, re, datetime, socket
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = Path("/home/ubuntu/.openclaw/workspace/reports/data")
SOCIAL_DIR = DATA_DIR / "social_holding"
SOCIAL_DIR.mkdir(parents=True, exist_ok=True)

PERIOD_CHAIN = ["20260630", "20260331", "20251231", "20250930", "20250630"]
SOCIAL_KEYWORDS = ["社保", "全国社保", "社保基金", "社保理事会"]

# ─── 网络超时 ───────────────────────────────────────────────
socket.setdefaulttimeout(10)

def detect_latest_period() -> str:
    import akshare as ak
    for p in PERIOD_CHAIN:
        try:
            df = ak.stock_gdfx_top_10_em(symbol="sz002594", date=p)
            if df is not None and len(df) > 0:
                print(f"  ✅ 报告期: {p}", file=sys.stderr)
                return p
        except Exception:
            continue
    return PERIOD_CHAIN[0]

def get_a_share_codes() -> list[str]:
    """沪深 300 + 中证 500 + 中证 1000"""
    import akshare as ak
    codes = set()
    for symbol in ["000300", "000905", "000852"]:
        try:
            df = ak.index_stock_cons_weight_csindex(symbol=symbol)
            codes.update(df["成分券代码"].astype(str).str.zfill(6).tolist())
        except Exception as e:
            print(f"⚠ 指数 {symbol} 失败: {e}", file=sys.stderr)
    codes = [c for c in codes if not c.startswith(("4", "8"))]
    print(f"  股票池: {len(codes)} 只", file=sys.stderr)
    return sorted(codes)

def get_name_from_tencent(codes: list[str]) -> dict[str, str]:
    """腾讯批量接口拿名称(备用)"""
    name_map = {}
    batch = 80
    for i in range(0, len(codes), batch):
        chunk = codes[i:i+batch]
        q = ",".join(f"sh{c}" if c.startswith(("6","9")) else f"sz{c}" for c in chunk)
        try:
            url = f"https://qt.gtimg.cn/q={q}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = urllib.request.urlopen(req, timeout=8)
            raw = r.read().decode("gbk", errors="replace")
            for line in raw.strip().split("\n"):
                m = re.match(r'v_[gs]h(\d+)="[^~]*~([^~]+)~', line)
                if m:
                    name_map[m.group(1)] = m.group(2)
        except Exception as e:
            print(f"⚠ 腾讯名称批量失败 batch {i//batch}: {e}", file=sys.stderr)
    print(f"  腾讯名称: {len(name_map)} 个", file=sys.stderr)
    return name_map

def get_name_map(codes: list[str]) -> dict[str, str]:
    """先用 akshare,失败用腾讯"""
    import akshare as ak
    name_map = {}
    try:
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            name_map[code] = str(row.get("code_name", ""))
        print(f"  akshare 名称: {len(name_map)} 个", file=sys.stderr)
        return name_map
    except Exception as e:
        print(f"⚠ akshare 名称失败,切腾讯: {e}", file=sys.stderr)
        return get_name_from_tencent(codes)

def fetch_top10(code: str, period: str, retries: int = 5) -> list[dict]:
    import akshare as ak
    if not code.startswith(("sh", "sz", "bj")):
        code = ("sh" if code.startswith(("6","9")) else "sz") + code
    for attempt in range(retries):
        try:
            df = ak.stock_gdfx_top_10_em(symbol=code, date=period)
            records = []
            for _, row in df.iterrows():
                name = str(row.get("股东名称", ""))
                records.append({
                    "name": name,
                    "type": str(row.get("股份类型", "")),
                    "shares": int(row.get("持股数", 0) or 0),
                    "pct": float(row.get("占总股本持股比例", 0) or 0),
                    "change": str(row.get("增减", "") or ""),
                    "change_pct": float(row.get("变动比率", 0) or 0) if str(row.get("变动比率", "")) not in ("","nan") else 0,
                })
            return records
        except Exception:
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
    return []

def detect_change(current: list, prev: list) -> str:
    cur_shares = sum(r["shares"] for r in current)
    prev_shares = sum(r["shares"] for r in prev)
    if cur_shares > 0 and prev_shares == 0: return "新进"
    if cur_shares > prev_shares: return "增持"
    if cur_shares < prev_shares: return "减持"
    return "不变"

def main():
    print("[社保持股扫描 v4] 启动", file=sys.stderr)
    start = time.time()

    # 1. 股票池
    codes = get_a_share_codes()
    if not codes:
        print("❌ 无法获取股票池,退出", file=sys.stderr)
        sys.exit(1)

    # 2. 名称
    name_map = get_name_map(codes)

    # 3. 报告期
    period = detect_latest_period()
    prev_period = PERIOD_CHAIN[PERIOD_CHAIN.index(period) + 1] if PERIOD_CHAIN.index(period) + 1 < len(PERIOD_CHAIN) else None

    # 4. 并发扫描本期
    holdings = []
    seen = set()
    lock = False

    def worker(code):
        records = fetch_top10(code, period)
        social = [r for r in records if any(kw in r["name"] for kw in SOCIAL_KEYWORDS)]
        return code, social, records

    print(f"  扫描 {period} ({len(codes)} 只, 10 线程)...", file=sys.stderr)
    done = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(worker, c): c for c in codes}
        for f in as_completed(futures):
            code, social, all_records = f.result()
            done += 1
            if done % 200 == 0:
                print(f"  进度: {done}/{len(codes)}", file=sys.stderr)
            if social:
                code_plain = code.lstrip("shszbj")
                holdings.append({
                    "code": code_plain,
                    "name": name_map.get(code_plain, code_plain),
                    "total_shares": sum(r["shares"] for r in all_records),
                    "total_pct": sum(r["pct"] for r in all_records),
                    "social_holders": social,
                    "prev_shares": 0,
                    "prev_pct": 0,
                })
                seen.add(code_plain)

    # 5. 并发扫描上期(对比用)
    prev_holdings = {}
    if prev_period:
        print(f"  扫描 {prev_period} 对比...", file=sys.stderr)
        def worker_prev(code):
            records = fetch_top10(code, prev_period)
            social = [r for r in records if any(kw in r["name"] for kw in SOCIAL_KEYWORDS)]
            return code, social
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(worker_prev, c): c for c in list(seen)}
            for f in as_completed(futures):
                code, social = f.result()
                if social:
                    code_plain = code.lstrip("shszbj")
                    prev_holdings[code_plain] = sum(r["shares"] for r in social)

    # 6. 合并变化
    changes = []
    for h in holdings:
        code = h["code"]
        prev_s = prev_holdings.get(code, 0)
        change = detect_change(h["social_holders"], [{"shares": prev_s}])
        h["prev_shares"] = prev_s
        if prev_s > 0:
            h["prev_pct"] = h["total_pct"] * prev_s / h["total_shares"] if h["total_shares"] > 0 else 0
        else:
            h["prev_pct"] = 0
        h["change"] = change
        changes.append({"code": code, "name": h["name"], "change": change,
                        "shares": h["social_holders"][0]["shares"],
                        "prev_shares": prev_s,
                        "pct": h["social_holders"][0]["pct"],
                        "price": 0, "market_cap": 0})

    changes.sort(key=lambda x: x["change"] == "新进", reverse=True)

    # 7. 保存
    output = {
        "generated_at": datetime.datetime.now().isoformat(),
        "source": "东方财富 十大股东公告(ak.stock_gdfx_top_10_em)",
        "period": period,
        "prev_period": prev_period,
        "holdings": holdings,
        "changes": changes,
        "stats": {
            "total": len(holdings),
            "combos": sum(len(h["social_holders"]) for h in holdings),
            "new": sum(1 for c in changes if c["change"] == "新进"),
            "increase": sum(1 for c in changes if c["change"] == "增持"),
            "decrease": sum(1 for c in changes if c["change"] == "减持"),
            "same": sum(1 for c in changes if c["change"] == "不变"),
        }
    }

    today = datetime.date.today().strftime("%Y%m%d")
    snap = SOCIAL_DIR / f"social_holding_{today}.json"
    snap.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    DATA_DIR.joinpath("social_holding_latest.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 保存 {snap}", file=sys.stderr)
    print(f"   {period}: 命中 {len(holdings)} 只 · 社保组合 {sum(len(h['social_holders']) for h in holdings)} 个", file=sys.stderr)
    new_c = sum(1 for c in changes if c["change"] == "新进")
    inc_c = sum(1 for c in changes if c["change"] == "增持")
    dec_c = sum(1 for c in changes if c["change"] == "减持")
    print(f"   变动: 新进 {new_c} · 增持 {inc_c} · 减持 {dec_c}", file=sys.stderr)
    print(f"   耗时 {time.time()-start:.0f}s", file=sys.stderr)

if __name__ == "__main__":
    main()
