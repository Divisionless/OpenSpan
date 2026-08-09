"""Regroup the board: 16 alphabetical domains -> 9 meaningful clusters.

Material change per the plan format: prior state snapshots into history[],
version bumps to 2, milestones get v2 SIDs. Item SIDs are untouched (SIDs
are identity; only their milestone assignment moves)."""
import copy
import json
import pathlib
import subprocess

p = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
doc = json.loads(p.read_text(encoding="utf-8"))

GROUPS = [
    ("Foundations", ["Foundations — platform services"]),
    ("Window management", ["Windows"]),
    ("Spaces & displays", ["Workspaces", "Displays"]),
    ("Keyboard & pointer", ["Keyboard", "Pointer"]),
    ("Capture & files", ["Capture", "Files"]),
    ("Launcher & clipboard", ["Launcher", "Continuity"]),
    ("Shell & desktop", ["Shell", "Desktop", "Notifications"]),
    ("System controls", ["Controls", "Audio", "Appearance"]),
    ("Platform & plumbing", ["Platform"]),
]

old_title_by_sid = {m["sid"]: m["title"] for m in doc["milestones"]}
doc["history"].append({"version": doc["version"],
                       "snapshot": copy.deepcopy(
                           {k: doc[k] for k in ("milestones", "items")})})
doc["version"] = 2

new_ms, ms_sid_by_old_title = [], {}
for i, (title, olds) in enumerate(GROUPS, 1):
    sid = f"v2.M{i}"
    new_ms.append({"sid": sid, "hcidx": i, "title": title,
                   "deps": [], "status": "todo"})
    for o in olds:
        ms_sid_by_old_title[o] = sid

unmapped = []
for it in doc["items"]:
    old = old_title_by_sid.get(it.get("milestone"), "")
    new = ms_sid_by_old_title.get(old)
    if new is None:
        unmapped.append((it["sid"], old))
    else:
        it["milestone"] = new

doc["milestones"] = new_ms
doc["nextMilestoneSid"] = len(GROUPS) + 1
anchor = subprocess.run(
    ["git", "-C", str(p.parent.parent.parent), "rev-parse", "--short",
     "HEAD"], capture_output=True, text=True).stdout.strip()
if anchor:
    doc["anchor"] = f"git:{anchor}"
doc["updated"] = "2026-08-09"
doc["log"].append({
    "date": "2026-08-09",
    "entry": "v2: regrouped 16 domains into 9 clusters for the rail nav; "
             "item SIDs unchanged; v1 snapshot in history."})
if unmapped:
    raise SystemExit(f"UNMAPPED: {unmapped}")
p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
counts = {}
for it in doc["items"]:
    counts[it["milestone"]] = counts.get(it["milestone"], 0) + 1
print("OK v2:", {m["title"]: counts.get(m["sid"], 0) for m in new_ms})
