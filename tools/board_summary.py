# SPDX-License-Identifier: AGPL-3.0-or-later
"""Summarise the canonical board (docs/plan/plan.json) — top level only.

The file carries embedded history snapshots; this reads ONLY the top-level
milestones/items arrays so counts are the live board, not the archive.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "plan" / "plan.json"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    data = json.loads(PLAN.read_text(encoding="utf-8"))

    milestones = data.get("milestones", [])
    items = data.get("items", [])
    mtitle = {m["sid"]: m.get("title", "") for m in milestones}
    mstatus = {m["sid"]: m.get("status", "") for m in milestones}

    print(f"plan={data.get('plan')} version={data.get('version')} "
          f"updated={data.get('updated')} anchor={data.get('anchor')}")
    print(f"milestones={len(milestones)} items={len(items)}")
    print()

    counts = Counter(i.get("status", "?") for i in items)
    print("ITEM STATUS: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("MILESTONE STATUS: " + "  ".join(
        f"{k}={v}" for k, v in sorted(Counter(mstatus.values()).items())))
    print()

    print("== MILESTONES ==")
    for m in sorted(milestones, key=lambda x: x.get("hcidx", 0)):
        kids = [i for i in items if i.get("milestone") == m["sid"]]
        kc = Counter(i.get("status", "?") for i in kids)
        line = "  ".join(f"{k}={v}" for k, v in sorted(kc.items()))
        deps = ",".join(m.get("deps") or []) or "-"
        print(f"  [{m.get('status','?'):8}] {m['sid']:6} hc{m.get('hcidx',0):<3} "
              f"{m.get('title','')}")
        print(f"             deps={deps}  items({len(kids)}): {line or '-'}")
    print()

    done_sids = {i["sid"] for i in items if i.get("status") == "done"}

    def block(status: str, show_all: bool) -> None:
        rows = [i for i in items if i.get("status") == status]
        rows.sort(key=lambda x: x.get("hcidx", 0))
        print(f"== {status.upper()} ({len(rows)}) ==")
        for i in rows:
            deps = i.get("deps") or []
            unmet = [d for d in deps if d not in done_sids]
            flag = f"  BLOCKED-BY={','.join(unmet)}" if unmet else ""
            print(f"  {i['sid']:7} hc{i.get('hcidx',0):<4} "
                  f"{mtitle.get(i.get('milestone',''),'?')[:28]:28} | "
                  f"{i.get('title','')}")
            print(f"          est={i.get('estTokens')} / {i.get('estTime')} "
                  f"model={i.get('model')}{flag}")
            note = (i.get("extra") or {}).get("note")
            if note:
                print(f"          note: {note}")
            if i.get("actualTokens"):
                print(f"          actual={i.get('actualTokens')} / {i.get('actualTime')}")
        print()

    block("doing", True)
    block("todo", True)

    dfr = [i for i in items if i.get("status") == "deferred"]
    print(f"== DEFERRED ({len(dfr)}) — catalog, not scheduled ==")
    by_m = defaultdict(list)
    for i in dfr:
        by_m[i.get("milestone", "?")].append(i.get("sid"))
    for k in sorted(by_m):
        print(f"  {k}: {len(by_m[k])}")
    print()

    dn = [i for i in items if i.get("status") == "done"]
    print(f"== DONE ({len(dn)}) ==")
    for i in sorted(dn, key=lambda x: x.get("hcidx", 0)):
        print(f"  {i['sid']:7} {i.get('title','')[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
