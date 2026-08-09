"""Derive delivery milestones from Doug's 21 promoted picks (v2).

Selection is over: the browsing clusters give way to real milestones in
dependency order over exactly the survivors. The 64 deferred rows keep
their SIDs and estimates in one catalog milestone at the bottom.

Note: Doug's tab autosaved the v1-shape doc back over the earlier cluster
regroup — his picks are intact, the clusters were only a browsing aid, so
this derives v2 straight from the current file."""
import copy
import json
import pathlib
import subprocess

p = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
doc = json.loads(p.read_text(encoding="utf-8"))

todo = [it["sid"] for it in doc["items"] if it["status"] == "todo"]
assert len(todo) == 21, f"expected 21 picks, found {len(todo)}"

# (title, deps by index into this list, member item SIDs)
MS = [
    ("Foundations — platform services", [],
     ["v1.01", "v1.02", "v1.03", "v1.04"]),
    ("Platform spine — settings, config, plugins", [],
     ["v1.62", "v1.64", "v1.60"]),
    ("Window control", [0],
     ["v1.74", "v1.78", "v1.79", "v1.30"]),
    ("Spaces", [0, 2],
     ["v1.82", "v1.85", "v1.83", "v1.80"]),
    ("Desktop & input", [0],
     ["v1.25", "v1.26", "v1.47", "v1.06"]),
    ("Overviews — Mission Control, Stage Manager", [3],
     ["v1.81", "v1.76"]),
    ("Deferred catalog", [], None),  # None = all deferred items
]

assigned = [s for _, _, sids in MS if sids for s in sids]
assert sorted(assigned) == sorted(todo), (
    set(assigned) ^ set(todo))

doc["history"].append({"version": doc["version"],
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

doc["milestones"] = new_ms
doc["nextMilestoneSid"] = len(MS) + 1
anchor = subprocess.run(
    ["git", "-C", str(p.parent.parent.parent), "rev-parse", "--short",
     "HEAD"], capture_output=True, text=True).stdout.strip()
if anchor:
    doc["anchor"] = f"git:{anchor}"
doc["updated"] = "2026-08-09"
doc["log"].append({
    "date": "2026-08-09",
    "entry": "v2: milestones derived from Doug's 21 picks in dependency "
             "order; 64 deferred rows kept in the catalog; v1 snapshot "
             "in history."})
p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
counts = {}
for it in doc["items"]:
    counts.setdefault(it["milestone"], []).append(it["status"])
print("OK v2:")
for m in new_ms:
    sts = counts.get(m["sid"], [])
    print(f"  {m['sid']} {m['title']}: {len(sts)} items "
          f"({sts.count('todo')} todo, {sts.count('deferred')} deferred)")
