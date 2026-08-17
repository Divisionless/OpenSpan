# SPDX-License-Identifier: AGPL-3.0-or-later
"""Correct three drifted fields on the canonical board. Dry run by default.

  1. milestone v2.M3 (Window control)  : todo -> done   (6/6 items done)
  2. milestone v2.M2 (Platform spine)  : todo -> doing   (4 done, 1 doing)
  3. item v1.38 (finder-shell)         : deps ["v3.111"] -> ["v3.119"]
     (alphas A and B landed; the Dock item is not its predecessor)

Touches ONLY the top-level milestones/items arrays. Embedded history
snapshots are left exactly as they are. Writes temp + os.replace.

  python tools/board_fix_drift.py            # show what would change
  python tools/board_fix_drift.py --apply    # write it
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "plan" / "plan.json"

EDITS = []


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    apply = "--apply" in sys.argv
    raw = PLAN.read_text(encoding="utf-8")
    data = json.loads(raw)

    ms = {m["sid"]: m for m in data["milestones"]}
    its = {i["sid"]: i for i in data["items"]}
    before_counts = (len(data["milestones"]), len(data["items"]))

    def set_field(obj, label, key, new):
        old = obj.get(key)
        if old == new:
            print(f"  SKIP {label}: {key} already {new!r}")
            return
        EDITS.append((label, key, old, new))
        print(f"  EDIT {label}: {key} {old!r} -> {new!r}")
        obj[key] = new

    # guard: only promote v2.M3 if its items really are all done
    m3_items = [i for i in data["items"] if i.get("milestone") == "v2.M3"]
    m3_open = [i["sid"] for i in m3_items if i.get("status") != "done"]
    if m3_open:
        print(f"  ABORT v2.M3: still open -> {m3_open}")
        return 2

    m2_items = [i for i in data["items"] if i.get("milestone") == "v2.M2"]
    if not any(i.get("status") == "doing" for i in m2_items):
        print("  ABORT v2.M2: no item is 'doing'")
        return 2

    set_field(ms["v2.M3"], "milestone v2.M3", "status", "done")
    set_field(ms["v2.M2"], "milestone v2.M2", "status", "doing")
    set_field(its["v1.38"], "item v1.38", "deps", ["v3.119"])

    if not EDITS:
        print("\nno drift — nothing to write")
        return 0
    if not apply:
        print("\ndry run — pass --apply to write")
        return 0

    out = json.dumps(data, indent=2, ensure_ascii=False)
    if not out.endswith("\n"):
        out += "\n"
    # round-trip guard before anything is replaced
    check = json.loads(out)
    assert (len(check["milestones"]), len(check["items"])) == before_counts
    assert check["milestones"] == data["milestones"]
    assert check["items"] == data["items"]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = PLAN.with_suffix(f".json.{stamp}.bak")
    shutil.copy2(PLAN, backup)

    tmp = PLAN.with_suffix(".json.tmp")
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, PLAN)

    reread = json.loads(PLAN.read_text(encoding="utf-8"))
    rm = {m["sid"]: m for m in reread["milestones"]}
    ri = {i["sid"]: i for i in reread["items"]}
    print(f"\nwrote {PLAN}  (backup: {backup.name})")
    print(f"  verify v2.M3.status = {rm['v2.M3']['status']!r}")
    print(f"  verify v2.M2.status = {rm['v2.M2']['status']!r}")
    print(f"  verify v1.38.deps   = {ri['v1.38']['deps']!r}")
    print(f"  items={len(reread['items'])} milestones={len(reread['milestones'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
