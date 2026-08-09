"""Repair the board after the second stale-tab clobber (2026-08-09).

A pre-derive tab autosaved v1 milestones back over the v2 structure while
items/logs kept accruing on top. Items, statuses, actuals and logs are all
current — only the milestone structure, version, history and anchor were
lost. This re-derives v2 from the same mapping as derive_milestones.py,
snapshots the mixed state honestly, and introduces `rev` (optimistic-lock
counter): every writer bumps it; the page refuses to autosave over a rev
it did not load."""
import copy
import json
import pathlib
import subprocess

p = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
doc = json.loads(p.read_text(encoding="utf-8"))
assert doc["version"] == 1 and len(doc["milestones"]) == 17, "not the clobbered state"

MS = [
    ("Foundations — platform services", [],
     ["v1.01", "v1.02", "v1.03", "v1.04"]),
    ("Platform spine — settings, config, plugins", [],
     ["v1.62", "v1.64", "v1.60", "v2.86"]),
    ("Window control", [0],
     ["v1.74", "v1.78", "v1.79", "v1.30"]),
    ("Spaces", [0, 2],
     ["v1.82", "v1.85", "v1.83", "v1.80"]),
    ("Desktop & input", [0],
     ["v1.25", "v1.26", "v1.47", "v1.06"]),
    ("Overviews — Mission Control, Stage Manager", [3],
     ["v1.81", "v1.76"]),
    ("Deferred catalog", [], None),
]

active = {i["sid"] for i in doc["items"] if i["status"] != "deferred"}
assigned = {s for _, _, sids in MS if sids for s in sids}
assert active <= assigned, f"active rows unmapped: {active - assigned}"

doc["history"].append({"version": doc["version"], "note": "clobbered mixed state",
                       "snapshot": copy.deepcopy(
                           {k: doc[k] for k in ("milestones", "items")})})
doc["version"] = 2

new_ms, ms_by_item = [], {}
for i, (title, dep_idx, sids) in enumerate(MS, 1):
    sid = f"v2.M{i}"
    new_ms.append({"sid": sid, "hcidx": i, "title": title,
                   "deps": [f"v2.M{d + 1}" for d in dep_idx],
                   "status": "todo"})
    for s in (sids or []):
        ms_by_item[s] = sid

catalog = new_ms[-1]["sid"]
for it in doc["items"]:
    it["milestone"] = ms_by_item.get(it["sid"], catalog)

done_m1 = all(
    next(i for i in doc["items"] if i["sid"] == s)["status"] == "done"
    for s in ("v1.01", "v1.02", "v1.03", "v1.04"))
new_ms[0]["status"] = "done" if done_m1 else "doing"

doc["milestones"] = new_ms
doc["nextMilestoneSid"] = len(MS) + 1
doc["rev"] = doc.get("rev", 0) + 1
anchor = subprocess.run(
    ["git", "-C", str(p.parent.parent.parent), "rev-parse", "--short",
     "HEAD"], capture_output=True, text=True).stdout.strip()
if anchor:
    doc["anchor"] = f"git:{anchor}"
doc["updated"] = "2026-08-09"
doc["log"].append({
    "date": "2026-08-09",
    "entry": "Repaired second stale-tab clobber: v2 milestones re-derived "
             "(v2.86 joins M2), mixed state snapshotted to history, `rev` "
             "optimistic lock introduced — stale tabs can no longer save."})
p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print("OK v2 restored: rev", doc["rev"],
      "| M1", new_ms[0]["status"],
      "|", {m["title"][:12]: sum(1 for i in doc["items"]
                                 if i["milestone"] == m["sid"])
            for m in new_ms})
