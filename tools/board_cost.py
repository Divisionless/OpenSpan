# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sum estimated token cost of live board work (doing + todo), by milestone.

Parses estTokens strings like "250k-560k" / "440k–880k" / "0 (landed)".
Unparseable entries are listed so no cost hides behind a dash.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "plan" / "plan.json"
NUM = re.compile(r"(\d+(?:\.\d+)?)\s*([km]?)", re.I)


def parse(est):
    if not isinstance(est, str):
        return None
    hits = NUM.findall(est.replace("–", "-").replace("—", "-"))
    vals = []
    for n, unit in hits:
        f = {"k": 1_000, "m": 1_000_000, "": 1}[unit.lower()]
        vals.append(float(n) * f)
    vals = [v for v in vals if v >= 1000] or vals
    if not vals:
        return None
    return (min(vals), max(vals))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    data = json.loads(PLAN.read_text(encoding="utf-8"))
    items = data.get("items", [])
    mt = {m["sid"]: m.get("title", "") for m in data.get("milestones", [])}

    live = [i for i in items if i.get("status") in ("doing", "todo", "blocked")]
    agg = defaultdict(lambda: [0.0, 0.0, 0])
    unparsed = []
    for i in live:
        p = parse(i.get("estTokens"))
        key = i.get("milestone", "?")
        if p is None:
            unparsed.append((i["sid"], i.get("estTokens")))
            agg[key][2] += 1
            continue
        agg[key][0] += p[0]
        agg[key][1] += p[1]
        agg[key][2] += 1

    tot = [0.0, 0.0, 0]
    print("LIVE WORK (doing+todo+blocked) — estimated tokens by milestone")
    for k in sorted(agg, key=lambda s: (s.split(".")[0], s)):
        lo, hi, n = agg[k]
        tot[0] += lo
        tot[1] += hi
        tot[2] += n
        print(f"  {k:7} {mt.get(k,'')[:42]:42} n={n:<3} "
              f"{lo/1000:6.0f}k - {hi/1000:6.0f}k")
    print(f"  {'TOTAL':7} {'':42} n={tot[2]:<3} "
          f"{tot[0]/1000:6.0f}k - {tot[1]/1000:6.0f}k")
    if unparsed:
        print("\n  unparsed estTokens: " + ", ".join(f"{s}={e!r}" for s, e in unparsed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
