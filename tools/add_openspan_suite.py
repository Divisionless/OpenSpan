"""Put the OpenSpan-era roadmap ON the board.

The multidevice control suite's roadmap lived in the session task list,
invisible on the board — which made the board read as if the product were
only the C# import catalog. These rows are the living product's own work,
under their own milestone, ahead of the deferred catalog."""
import json
import pathlib

p = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
doc = json.loads(p.read_text(encoding="utf-8"))

ROWS = [
    ("Peer-keyed device identity — kill positional identity",
     "devices are who, not where", "150k-300k", "2h",
     "every lane/bond keyed by peer; live re-resolution", "todo"),
    ("Portable installer — provision a fresh Windows machine",
     "shippable beyond this desk", "200k-400k", "3h",
     "VM import, tasks, drivers, first-run wizard", "todo"),
    ("Second laptop controls iPad #2 — first multi-node deploy",
     "prove the multi-node story", "150k-300k", "2h",
     "needs the personal laptop + Doug's hands", "todo"),
    ("Replace Input Director on this PC",
     "one tool owns the desk", "200k-400k", "3h",
     "likely a 4th dongle; laptop hoisted in BT range", "todo"),
    ("Full workspace capture — one continuous desk",
     "the whole desk, one map", "150k-300k", "2h",
     "arrangement model beyond current capture", "todo"),
    ("Remove portal edge-hop on Mac lanes",
     "mac handles its own edges", "80k-200k", "1h",
     "delete anticipation, don't add machinery", "todo"),
    ("Per-headphone volume normalization",
     "each pair remembers its level", "120k-250k", "2h",
     "per-device gain in the audio lane", "todo"),
    ("UI polish batch (2026-08-08 checklist)",
     "the small frictions list", "80k-150k", "1h",
     "batched; no behavior changes", "todo"),
    ("Audio lane survives HID connect flows — self-heal",
     "music must not die quietly", "150k-300k", "2h",
     "watchdog on adv/GATT re-register; 4th-dongle option", "todo"),
    ("Recovery cycle — live radio pass",
     "code landed, needs Doug's pass", "0 (landed)", "30m",
     "explicit handoff/gentle release shipped; verification only", "doing"),
]

for m in doc["milestones"]:
    if m["sid"] == "v2.M7":
        m["hcidx"] = 8  # catalog stays visually last
doc["milestones"].append({"sid": "v2.M8", "hcidx": 7,
                          "title": "OpenSpan suite — multidevice control",
                          "deps": [], "status": "doing"})
doc["nextMilestoneSid"] = 9

hc = max(i["hcidx"] for i in doc["items"])
for title, why, tok, hrs, drivers, status in ROWS:
    sid = f"v2.{doc['nextSid']:02d}"
    doc["nextSid"] += 1
    hc += 1
    doc["items"].append({
        "sid": sid, "hcidx": hc, "milestone": "v2.M8", "deps": [],
        "title": title, "why": why, "estTokens": tok,
        "costDrivers": drivers, "estTime": hrs,
        "model": "Codex exec + Claude review", "status": status,
        "actualTokens": None, "actualTime": None})

doc["rev"] = doc.get("rev", 0) + 1
doc["updated"] = "2026-08-09"
doc["log"].append({
    "date": "2026-08-09",
    "entry": "OpenSpan suite (the multidevice control roadmap) added as "
             "v2.M8 with 10 rows v2.87-v2.96 — it lived only in the "
             "session task list and the board misread as import-only."})
p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print("OK: v2.M8 +", len(ROWS), "rows, rev", doc["rev"])
