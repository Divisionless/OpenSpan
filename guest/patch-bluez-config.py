#!/usr/bin/env python3
"""Apply the documented system-wide-instance settings to the bluez WirePlumber
config. Each replacement is asserted so a silent miss is impossible."""
import sys

PATH = "/usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua"

# (needle, replacement, human label)
EDITS = [
    ('["with-logind"] = true',
     '["with-logind"] = false',
     "with-logind -> false (system-wide instance; no per-session BT arbitration)"),
    ('--["session.suspend-timeout-seconds"] = 5,',
     '["session.suspend-timeout-seconds"] = 0,',
     "suspend-timeout -> 0 (never suspend; keep A2DP transport open)"),
    ('--["node.pause-on-idle"] = false,',
     '["node.pause-on-idle"] = false,',
     "pause-on-idle -> false (don't pause the BT node when idle)"),
]

with open(PATH) as f:
    src = f.read()

for needle, repl, label in EDITS:
    if repl in src and needle not in src:
        print(f"already applied: {label}")
        continue
    n = src.count(needle)
    if n != 1:
        print(f"ABORT: expected exactly 1 match for {needle!r}, found {n}",
              file=sys.stderr)
        sys.exit(2)
    src = src.replace(needle, repl)
    print(f"applied: {label}")

with open(PATH, "w") as f:
    f.write(src)

print("\n--- effective settings now ---")
for line in src.splitlines():
    s = line.strip()
    if any(k in s for k in ("with-logind", "suspend-timeout", "pause-on-idle")) \
            and not s.startswith("--"):
        print("  " + s)
