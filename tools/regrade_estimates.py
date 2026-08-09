"""Re-grade the board's estimates against five completed rows.

The signal is not "everything is cheaper" — it splits cleanly by whether
a working reference exists:

  reference-backed ports   v1.01 .48x  v1.02 .40x  v1.03 .96x  v1.04 .74x
  novel design (no ref)    v2.86 1.6x OVER (two slices, invented contract)

Time ran 0.08–0.33x across the board: the original hour figures assumed a
human writing every line, not Codex writing and Claude reviewing.

So: reference rows come down 40% on tokens, spec-only rows go UP 25%,
every row's time is rebased on measured throughput, and the model column
tells the truth about who does the work."""
import json
import pathlib
import re

p = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
doc = json.loads(p.read_text(encoding="utf-8"))

DASH = "–"


def scale_tokens(text, factor):
    nums = re.findall(r"(\d+)k", text or "")
    if len(nums) != 2:
        return text
    lo, hi = (max(20, int(round(int(n) * factor / 10.0)) * 10) for n in nums)
    return f"{lo}k{DASH}{hi}k"


def rebase_time(text, factor):
    hours = re.findall(r"([\d.]+)", text or "")
    if not hours:
        return text
    top = max(float(h) for h in hours)
    mins = int(round(top * 60 * factor / 5.0)) * 5
    return f"{mins}m" if mins < 60 else f"{mins / 60:.1f}h"


changed = 0
for it in doc["items"]:
    if it["status"] in ("done", "doing"):
        continue
    drivers = it.get("costDrivers", "")
    if "working C# reference" in drivers:
        tok_factor, time_factor = 0.6, 0.25
    elif "spec only" in drivers:
        tok_factor, time_factor = 1.25, 0.4
    else:
        tok_factor, time_factor = 0.8, 0.3
    before = (it["estTokens"], it["estTime"])
    it["estTokens"] = scale_tokens(it["estTokens"], tok_factor)
    it["estTime"] = rebase_time(it["estTime"], time_factor)
    if it["model"] == "Opus subagent":
        it["model"] = "Codex exec + Claude review"
    if (it["estTokens"], it["estTime"]) != before:
        changed += 1

doc["rev"] = doc.get("rev", 0) + 1
doc["updated"] = "2026-08-09"
doc["log"].append({
    "date": "2026-08-09",
    "entry": f"Estimates re-graded against 5 actuals ({changed} rows): "
             "reference-backed ports -40% tokens (measured .40-.96x), "
             "spec-only rows +25% (v2.86 novel work ran 1.6x OVER), all "
             "times rebased to Codex-writes/Claude-reviews throughput."})
p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print(f"OK: {changed} rows re-graded, rev {doc['rev']}")
