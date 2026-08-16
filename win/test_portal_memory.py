"""The portal remembers: a click is intent, and intent survives a relaunch.

Only the two user surfaces (every _portal_button, the tray item) record
`portal_wanted`; the pair-edge auto-start and the shutdown stop do not. At
most one restore attempt per launch, and only once the bridge is READY with a
device daemon answering and nothing mid-pair.
"""
import ast
import os
import pathlib
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


# ---- structure: who records intent, who does not -----------------------------
src = pathlib.Path(A.__file__).read_text(encoding="utf-8")
tree = ast.parse(src)
funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def calls_in(fn, name):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                 or (isinstance(n.func, ast.Name) and n.func.id == name))]


def command_targets(fn):
    return [kw.value.attr for n in ast.walk(fn) if isinstance(n, ast.Call)
            for kw in n.keywords if kw.arg == "command"
            and isinstance(kw.value, ast.Attribute)]


check("every _portal_button binds the USER wrapper, not toggle_portal",
      command_targets(funcs["_portal_button"]) == ["toggle_portal_by_user"])
check("the tray item binds the USER wrapper too",
      "toggle_portal_by_user" in command_targets(funcs["_post_tray_menu"])
      and "toggle_portal" not in command_targets(funcs["_post_tray_menu"]))
saves = calls_in(funcs["toggle_portal_by_user"], "save_setting")
check("the wrapper is the ONE place portal_wanted is written",
      len(saves) == 1 and saves[0].args[0].value == "portal_wanted"
      and len([c for c in ast.walk(tree) if isinstance(c, ast.Call)
               and isinstance(c.func, ast.Name) and c.func.id == "save_setting"
               and c.args and isinstance(c.args[0], ast.Constant)
               and c.args[0].value == "portal_wanted"]) == 1)
check("toggle_portal itself, the pair-edge and the shutdown stop record nothing",
      not calls_in(funcs["toggle_portal"], "save_setting")
      and not calls_in(funcs["_stop_portal_if_running"], "save_setting"))
check("_apply_poll asks for the restore exactly once",
      len(calls_in(funcs["_apply_poll"], "_restore_portal_if_wanted")) == 1)


# ---- behaviour: the restore guard, fake-driven -------------------------------
def make_self(ready="ready", live=False, busy=False, done=False):
    started, emitted = [], []
    s = types.SimpleNamespace(
        _ready_state=ready,
        _portal_live=lambda: live,
        _any_device_busy=lambda: busy,
        _start_portal_process=lambda: started.append(1),
    )
    if done:
        s._portal_restore_done = True
    return s, started


def run(self_obj, running=True, reachable=1, wanted=True):
    saved = A.load_setting
    A.load_setting = lambda key, default=None: (wanted if key == "portal_wanted"
                                                else default)
    try:
        return A.App._restore_portal_if_wanted(self_obj, running,
                                               {"reachable": reachable})
    finally:
        A.load_setting = saved


s, started = make_self()
check("READY + a daemon answering + wanted -> the portal starts once",
      run(s) is True and started == [1])
check("and never again this launch",
      run(s) is False and started == [1] and s._portal_restore_done)

s, started = make_self(ready="booting")
check("not READY yet -> waits, attempt not spent",
      run(s) is False and not started and not getattr(s, "_portal_restore_done", False))

s, started = make_self()
check("no daemon reachable -> waits, attempt not spent",
      run(s, reachable=0) is False and not started
      and not getattr(s, "_portal_restore_done", False))

s, started = make_self()
check("not wanted -> nothing, but the attempt is spent (no polling forever)",
      run(s, wanted=False) is False and not started and s._portal_restore_done)

s, started = make_self(live=True)
check("already up -> nothing", run(s) is False and not started)

s, started = make_self(busy=True)
check("a device mid-pair -> stays out of its way", run(s) is False and not started)

s, started = make_self()
check("VM not running -> waits", run(s, running=False) is False and not started)

if failures:
    print(f"RESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("RESULT: ALL PORTAL MEMORY TESTS PASSED")
