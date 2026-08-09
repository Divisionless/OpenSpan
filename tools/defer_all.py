"""Flip every board item to 'deferred' — the opt-in inversion.

Doug's selection model: everything starts culled; he promotes the rows
he wants (one click: deferred -> todo). Milestone statuses reset to todo
(they are derived, not chosen)."""
import json
import pathlib

p = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
doc = json.loads(p.read_text(encoding="utf-8"))
for it in doc["items"]:
    it["status"] = "deferred"
for m in doc["milestones"]:
    m["status"] = "todo"
doc["updated"] = "2026-08-09"
doc["log"].append({
    "date": "2026-08-09",
    "entry": "Inverted to opt-in: all 85 items deferred; Doug promotes "
             "his starters (status chip: deferred -> todo)."})
p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print("OK:", sum(1 for i in doc["items"] if i["status"] == "deferred"),
      "items deferred")
