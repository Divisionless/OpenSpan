# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add one row to the board. Dry run by default; atomic write; backup kept.

Allocates sid from nextSid / hcidx from max(hcidx)+1, appends to the
top-level items array only. Embedded history snapshots untouched.

  python tools/board_add.py --ver v3 --milestone v3.M9 --title "..." \
      --why "..." --est-tokens "80k-150k" --est-time "1h" --model "Opus subagent" \
      [--deps v3.110,v3.114] [--status todo] [--note "..."] [--apply]
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "plan" / "plan.json"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", default="v3")
    ap.add_argument("--milestone", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--why", required=True)
    ap.add_argument("--est-tokens", dest="est_tokens", required=True)
    ap.add_argument("--est-time", dest="est_time", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--cost-drivers", dest="cost", default="")
    ap.add_argument("--deps", default="")
    ap.add_argument("--status", default="todo",
                    choices=["todo", "doing", "done", "blocked", "deferred"])
    ap.add_argument("--note", default="")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    data = json.loads(PLAN.read_text(encoding="utf-8"))
    items = data["items"]
    msids = {m["sid"] for m in data["milestones"]}
    if a.milestone not in msids:
        print(f"no such milestone: {a.milestone}")
        return 2
    deps = [d for d in a.deps.split(",") if d]
    known = {i["sid"] for i in items}
    bad = [d for d in deps if d not in known]
    if bad:
        print(f"unknown deps: {bad}")
        return 2

    sid = f"{a.ver}.{data['nextSid']}"
    hcidx = max(i.get("hcidx", 0) for i in items) + 1
    row = {
        "sid": sid, "hcidx": hcidx, "milestone": a.milestone, "deps": deps,
        "title": a.title, "why": a.why, "estTokens": a.est_tokens,
        "costDrivers": a.cost, "estTime": a.est_time, "model": a.model,
        "status": a.status, "actualTokens": None, "actualTime": None,
        "extra": ({"note": a.note} if a.note else {}),
    }
    print(json.dumps(row, indent=2, ensure_ascii=False))
    if not a.apply:
        print("\ndry run — pass --apply to write")
        return 0

    before = len(items)
    items.append(row)
    data["nextSid"] += 1
    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    check = json.loads(out)
    assert len(check["items"]) == before + 1
    assert check["items"][-1]["sid"] == sid

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = PLAN.with_suffix(f".json.{stamp}.bak")
    shutil.copy2(PLAN, backup)
    tmp = PLAN.with_suffix(".json.tmp")
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, PLAN)
    print(f"\nwrote {sid} (hc{hcidx}) -> {PLAN}  (backup: {backup.name}, nextSid={data['nextSid']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
