"""Put the wiring on the board.

Every ported module so far is dark code: correct, tested, and never
called by the app. Retiring the old program is not "port 21 features",
it is "port them AND make them live AND ship a build Doug trusts". The
wiring was invisible on the board, which made completion look closer
than it is. One wiring row per milestone, each ending in a build."""
import json
import pathlib

p = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
doc = json.loads(p.read_text(encoding="utf-8"))

ROWS = [
    ("v2.M2", "WIRE: settings + config + hotkey host into the app",
     "dark code becomes live", "150k-300k", "1h",
     "touches openspan.py; hook host OFF by default (portal + Input "
     "Director already hold the keyboard)"),
    ("v2.M3", "WIRE: window control — tiling chords, rules, presets",
     "the headline daily driver", "150k-300k", "1h",
     "chords through the landed router; every action reversible"),
    ("v2.M4", "WIRE: spaces into the app",
     "workspaces become usable", "150k-300k", "1h",
     "after the spaces subsystem lands"),
    ("v2.M5", "WIRE: desktop & input features",
     "stacks, widgets, remapping live", "150k-300k", "1h",
     "after those ports land"),
    ("v2.M6", "WIRE: overviews + retirement build",
     "the build that retires the old", "200k-400k", "1.5h",
     "full pass over Doug's 21 picks, then the build he judges"),
]

hc = max(i["hcidx"] for i in doc["items"])
for milestone, title, why, tok, hrs, drivers in ROWS:
    sid = f"v2.{doc['nextSid']:02d}"
    doc["nextSid"] += 1
    hc += 1
    doc["items"].append({
        "sid": sid, "hcidx": hc, "milestone": milestone, "deps": [],
        "title": title, "why": why, "estTokens": tok,
        "costDrivers": drivers, "estTime": hrs,
        "model": "Claude (serialized — one writer on openspan.py)",
        "status": "todo", "actualTokens": None, "actualTime": None})

doc["rev"] = doc.get("rev", 0) + 1
doc["updated"] = "2026-08-09"
doc["log"].append({
    "date": "2026-08-09",
    "entry": "Added five WIRE rows (one per milestone). Ported modules are "
             "dark code until wired; retirement of the old program is "
             "gated on wiring + builds Doug tests, not on port count."})
p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print("OK: 5 wiring rows added, rev", doc["rev"])
