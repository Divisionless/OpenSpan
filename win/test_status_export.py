"""status.json — what the shell's menu bar reads. Pure shape checks + atomic write.

The shell fork (D:\\_EsotericOS\\shell) renders vm/portal/audio/devices from this
file and treats a `written` older than 15 s as "EsotericOS off". Keys here are
the contract; a shape change must land in both trees.
"""
import ast
import datetime
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


NOW = datetime.datetime(2026, 8, 16, 7, 0, 0)
# "peers" arrived with v3.121 (LAN nodes) and is ADDITIVE: every key above it
# keeps its name, its type and its meaning, and a reader that does not know
# about peers is unaffected. `vm` also gained a fourth value, "none", for a
# machine with no bridge at all -- see test_portable.py, which owns that case.
KEYS = {"written", "pid", "version", "vm", "daemon", "portal", "audio",
        "devices", "broadcasting", "line", "ready", "peers"}

up = A.status_document(running=True, ready_state="ready", ready_text="●  READY — connect headphones",
                       portal_on=True, audio_on=True, devices_live=2, devices_total=3,
                       advertising=False, line="VM ●  iPad ● live  portal ● ON  devices 2/3  audio ●",
                       daemon_up=True, now=NOW, pid=4242)
check("shape: exactly the contract keys", set(up) == KEYS)
check("ready bridge: vm up, portal on, audio on, devices 2/3, not broadcasting",
      up["vm"] == "up" and up["portal"] == "on" and up["audio"] == "on"
      and up["devices"] == {"connected": 2, "total": 3} and up["broadcasting"] is False)
check("written is ISO-8601 to the second, pid and version carried",
      up["written"] == "2026-08-16T07:00:00" and up["pid"] == 4242 and up["version"] == A.VERSION)

boot = A.status_document(running=True, ready_state="booting", ready_text="◐  Booting…  (~90s)",
                         portal_on=False, audio_on=False, devices_live=0, devices_total=3,
                         advertising=False, line="VM ●  iPad ○ daemon starting  portal ○ off",
                         daemon_up=False, now=NOW)
check("booting: vm starting, portal off, audio off, daemon False",
      boot["vm"] == "starting" and boot["portal"] == "off" and boot["audio"] == "off"
      and boot["daemon"] is False)

down = A.status_document(running=False, ready_state="stopped", ready_text="○  Stopped",
                         portal_on=False, audio_on=False, devices_live=0, devices_total=0,
                         advertising=False, line="VM ○", daemon_up=False, now=NOW)
check("stopped: vm down, audio none", down["vm"] == "down" and down["audio"] == "none")

first = A.status_document(running=True, ready_state=None, ready_text="", portal_on=False,
                          audio_on=False, devices_live=0, devices_total=1, advertising=False,
                          line="", daemon_up=False, now=NOW)
check("first tick before any ready state is 'starting', never a crash", first["vm"] == "starting")

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "status.json")
    ok = A.write_status(up, path)
    back = json.load(open(path, encoding="utf-8"))
    check("write is atomic (no .new left) and round-trips",
          ok and back == up and not os.path.exists(path + ".new"))
    bad = A.write_status(up, os.path.join(d, "no-such-dir", "status.json"))
    check("an unwritable path returns False and never raises", bad is False)

check("STATUS lives on ROOT (the exe's folder), never __file__",
      A.STATUS == os.path.join(A.ROOT, "status.json"))

# structural: written from the paint tick, once, after the indicators are set
src = pathlib.Path(A.__file__).read_text(encoding="utf-8")
tree = ast.parse(src)
apply_poll = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_apply_poll")
calls = [n for n in ast.walk(apply_poll) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Name) and n.func.id == "write_status"]
check("_apply_poll writes status exactly once", len(calls) == 1)
gi = next(i for i, n in enumerate(ast.walk(apply_poll)) if isinstance(n, ast.Call)
          and isinstance(n.func, ast.Name) and n.func.id == "write_status")
check(".gitignore covers status.json",
      "status.json" in pathlib.Path(A.ROOT, ".gitignore").read_text(encoding="utf-8"))

if failures:
    print(f"RESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("RESULT: ALL STATUS EXPORT TESTS PASSED")
