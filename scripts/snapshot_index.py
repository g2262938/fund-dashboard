#!/usr/bin/env python3
"""生成日期索引 + 历史页面"""
import json
import datetime
from pathlib import Path

HISTORY_DIR = Path("/home/ubuntu/.openclaw/workspace/reports/history")
DATA_DIR = Path("/home/ubuntu/.openclaw/workspace/reports/data")


def list_snapshots() -> list[dict]:
    """列出所有快照,按日期分组"""
    snapshots = []
    for f in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            ts = data.get("generated_at", "")
            snapshots.append({
                "file": f.name,
                "datetime": ts,
                "date": ts[:10] if ts else "",
                "time": ts[11:19] if len(ts) > 19 else "",
            })
        except Exception:
            continue
    return snapshots


def build_index() -> dict:
    """按日期聚合快照"""
    snapshots = list_snapshots()
    by_date = {}
    for s in snapshots:
        d = s["date"]
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(s)
    return by_date


def main():
    by_date = build_index()
    index = {
        "dates": sorted(by_date.keys(), reverse=True),
        "by_date": by_date,
        "snapshot_count": sum(len(v) for v in by_date.values()),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "snapshots_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"日期索引: {len(by_date)} 天,共 {index['snapshot_count']} 个快照")
    print(f"日期列表: {index['dates']}")


if __name__ == "__main__":
    main()