# SPDX-License-Identifier: AGPL-3.0-or-later
"""Set fields on one board row. Dry run by default; atomic write; backup kept.

Touches ONLY the top-level items array — embedded history snapshots are left
exactly as they are.

  python tools/board_set.py v3.107 --status done --actual-tokens "~45k" \
      --actual-time "~35m" --note "..."            # show what would change
  python tools/board_set.py v3.107 ... --apply     # write it
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
STATUSES = {"todo", "doing", "done", "blocked", "deferred"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("sid")
    ap.add_argument("--status")
    ap.add_argument("--actual-tokens", dest="tokens")
    ap.add_argument("--actual-time", dest="time")
    ap.add_argument("--note", help="replace extra.note")
    ap.add_argument("--append-note", dest="append", help="append to extra.note")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    data = json.loads(PLAN.read_text(encoding="utf-8"))
    items = {i["sid"]: i for i in data["items"]}
    if a.sid not in items:
        print(f"no such row: {a.sid}")
        return 2
    if a.status and a.status not in STATUSES:
        print(f"bad status {a.status!r}; allowed: {sorted(STATUSES)}")
        return 2

    row = items[a.sid]
    before = (len(data["milestones"]), len(data["items"]))
    edits = []

    def put(container, key, new):
        old = container.get(key)
        if old == new:
            print(f"  SKIP {key}: already set")
            return
        print(f"  EDIT {key}:\n        - {old!r}\n        + {new!r}")
        container[key] = new
        edits.append(key)

    print(f"{a.sid}  {row.get('title','')[:80]}")
    if a.status:
        put(row, "status", a.status)
    if a.tokens:
        put(row, "actualTokens", a.tokens)
    if a.time:
        put(row, "actualTime", a.time)
    if a.note or a.append:
        row.setdefault("extra", {})
        if a.note:
            put(row["extra"], "note", a.note)
        if a.append:
            cur = row["extra"].get("note") or ""
            put(row["extra"], "note", (cur + " " + a.append).strip())

    if not edits:
        print("\nnothing to change")
        return 0
    if not a.apply:
        print("\ndry run — pass --apply to write")
        return 0

    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    check = json.loads(out)
    assert (len(check["milestones"]), len(check["items"])) == before
    assert check["items"] == data["items"]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = PLAN.with_suffix(f".json.{stamp}.bak")
    shutil.copy2(PLAN, backup)
    tmp = PLAN.with_suffix(".json.tmp")
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, PLAN)

    reread = {i["sid"]: i for i in json.loads(PLAN.read_text(encoding="utf-8"))["items"]}
    r = reread[a.sid]
    print(f"\nwrote {PLAN}  (backup: {backup.name})")
    print(f"  status={r.get('status')!r} actualTokens={r.get('actualTokens')!r} "
          f"actualTime={r.get('actualTime')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
