"""Generate docs/plan/plan.json — the EsotericOS feature board.

Born from D:\\EsotericOS_Antigrav\\docs\\FEATURE_STATUS.md (the generated
ground truth of the old C# program) so every row carries its real state:
a feature with working C# reference code ports for a fraction of a
spec-only build, and the estimate bands say so.

Foundations come first: the platform services the old program's features
stand on (keyboard interception, window tracking, monitor identity, tray
runtime) do not exist on the Python side; hook- and window-class features
depend on them by SID, so a pick shows what it drags in.

Doug's gesture on the board: DELETE the rows you don't want. Survivors
are the backlog.
"""
import json
import pathlib
import re
import subprocess

STATUS_MD = pathlib.Path(
    r"D:\EsotericOS_Antigrav\docs\FEATURE_STATUS.md").read_text(
        encoding="utf-8")
OUT = pathlib.Path(__file__).parent.parent / "docs" / "plan" / "plan.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

# Foundations: sid placeholders resolved after numbering.
FOUNDATIONS = [
    ("Keyboard interception service + router",
     "one lawful hook, chord routing", "250k–400k", "3h",
     "hook edge cases; Win-release masking"),
    ("Window tracker + window service",
     "WinEvent-driven, no polling, DWM placement", "250k–400k", "3h",
     "DWM frame bounds; cloaked-window filtering"),
    ("Stable monitor identity + topology",
     "hashed keys, never DISPLAY names", "80k–150k", "1h",
     "reconnect semantics; debounced topology"),
    ("Tray runtime (V4 + NIF_SHOWTIP)",
     "brand tray icons come alive", "60k–120k", "1h",
     "explorer-restart re-add; tooltip law"),
]

# Which foundation a domain's features lean on (by index into FOUNDATIONS).
DOMAIN_DEPS = {
    "Keyboard": [0], "Windows": [1, 2], "Workspaces": [1, 2],
    "Displays": [2], "Shell": [1, 3], "Pointer": [], "Capture": [],
    "Files": [], "Launcher": [], "Appearance": [], "Audio": [],
    "Continuity": [], "Controls": [], "Desktop": [1], "Notifications": [3],
    "Platform": [],
}

BAND = {  # (estTokens, estTime) by old status
    "implemented": ("80k–200k", "1–2h"),
    "partial": ("120k–250k", "2h"),
    "degraded": ("120k–250k", "2h"),
    "blocked": ("150k–300k", "2h"),
    "not-started": ("200k–450k", "3–5h"),
}
BIG_SPEC = {"dock", "top-menu-bar", "notification-center", "control-center",
            "finder-shell", "global-app-menu", "stage-manager",
            "mission-control"}

domain = None
features = []
for line in STATUS_MD.splitlines():
    m = re.match(r"^## (\w[\w ]*)$", line)
    if m:
        domain = m.group(1)
        continue
    if domain == "Excluded by the product plan":
        continue
    m = re.match(r"^\| `([a-z0-9-]+)`[^—]*— ([^|]+?) \| ([^|]+?) \| "
                 r"([a-z-]+) \|", line)
    if m and domain:
        fid, title, decision, status = (m.group(1), m.group(2).strip(),
                                        m.group(3).strip(), m.group(4))
        features.append((domain, fid, title, decision, status))

anchor = subprocess.run(
    ["git", "-C", str(OUT.parent.parent.parent), "rev-parse", "--short",
     "HEAD"], capture_output=True, text=True).stdout.strip() or None

milestones, items = [], []
mil_by_domain = {}
msid = 1
milestones.append({"sid": f"v1.M{msid}", "hcidx": msid,
                   "title": "Foundations — platform services",
                   "deps": [], "status": "todo"})
found_msid = f"v1.M{msid}"
for d in dict.fromkeys(d for d, *_ in features):
    msid += 1
    mil_by_domain[d] = f"v1.M{msid}"
    milestones.append({"sid": f"v1.M{msid}", "hcidx": msid,
                       "title": d, "deps": [found_msid], "status": "todo"})

sid = 0
foundation_sids = []
for title, why, tok, hrs, drivers in FOUNDATIONS:
    sid += 1
    fsid = f"v1.{sid:02d}"
    foundation_sids.append(fsid)
    items.append({"sid": fsid, "hcidx": sid, "milestone": found_msid,
                  "deps": [], "title": title, "why": why,
                  "estTokens": tok, "costDrivers": drivers,
                  "estTime": hrs, "model": "Opus subagent",
                  "status": "todo", "actualTokens": None,
                  "actualTime": None})

for d, fid, title, decision, status in features:
    sid += 1
    tok, hrs = BAND[status]
    if fid in BIG_SPEC:
        tok, hrs = "350k–700k", "4–8h"
    ref = ("working C# reference module"
           if status in ("implemented", "partial", "degraded")
           else "spec only — full build")
    why = {"implemented": "proven in the old program",
           "partial": "proven core, hook awaits live drive",
           "degraded": "works with a documented limitation",
           "blocked": "needs an excluded prerequisite rethought",
           "not-started": "never built; plan spec only"}[status]
    items.append({
        "sid": f"v1.{sid:02d}", "hcidx": sid,
        "milestone": mil_by_domain[d],
        "deps": [foundation_sids[i] for i in DOMAIN_DEPS.get(d, [])],
        "title": f"{title}  [{fid}]",
        "why": why,
        "estTokens": tok,
        "costDrivers": f"{ref}; was {decision}",
        "estTime": hrs, "model": "Opus subagent",
        "status": "todo", "actualTokens": None, "actualTime": None})

plan = {
    "plan": "EsotericOS feature board",
    "project": "D:\\OpenSpan",
    "version": 1, "anchor": f"git:{anchor}" if anchor else None,
    "created": "2026-08-09", "updated": "2026-08-09",
    "nextSid": sid + 1, "nextMilestoneSid": msid + 1,
    "milestones": milestones, "items": items,
    "history": [], "log": [
        {"date": "2026-08-09",
         "entry": "Board generated from FEATURE_STATUS.md ground truth: "
                  f"{len(features)} features + {len(FOUNDATIONS)} "
                  "foundations. Doug culls by deleting rows."}],
}
OUT.write_text(json.dumps(plan, indent=2), encoding="utf-8")
print(f"OK -> {OUT}: {len(items)} items, {len(milestones)} milestones")
