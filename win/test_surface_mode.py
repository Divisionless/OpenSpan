# SPDX-License-Identifier: AGPL-3.0-or-later

"""Checks for surface_mode.py and its wiring into openspan.py.

No real session, no real HWND: the probes are parameters, so every branch of
"who is the shell" is driven from here. The structural checks read openspan.py
as source, which is how this file can assert that the chrome and the close path
are gated without importing tkinter or building a window.
"""

import ast
import os
import sys

import surface_mode as sm


failures = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)


# ---- decide_mode: the flags win -------------------------------------------

check("--window forces window mode even under Cairo",
      sm.decide_mode(["--window"], lambda: "cairodesktop.exe") == sm.WINDOW)
check("--surface forces surface mode even under Explorer",
      sm.decide_mode(["--surface"], lambda: "explorer.exe") == sm.SURFACE)
check("--window beats --surface (the escape hatch is not overrulable)",
      sm.decide_mode(["--surface", "--window"],
                     lambda: "cairodesktop.exe") == sm.WINDOW)
check("a flag anywhere in argv is seen, not just first",
      sm.decide_mode(["--portal", "--window"],
                     lambda: "cairodesktop.exe") == sm.WINDOW)


# ---- decide_mode: the shell decides ---------------------------------------

check("legacy shell -> surface",
      sm.decide_mode([], lambda: "CairoDesktop.exe") == sm.SURFACE)
check("canonical EsotericOS shell -> surface",
      sm.decide_mode([], lambda: "EsotericOS.Shell.exe") == sm.SURFACE)
check("Explorer shell -> window",
      sm.decide_mode([], lambda: "explorer.exe") == sm.WINDOW)
check("unknown shell -> window (uncertainty stays closeable)",
      sm.decide_mode([], lambda: "") == sm.WINDOW)
check("some third shell -> window",
      sm.decide_mode([], lambda: "litestep.exe") == sm.WINDOW)


def _boom():
    raise OSError("probe exploded")


check("a probe that throws -> window, never a crash",
      sm.decide_mode([], _boom) == sm.WINDOW)
check("is_surface agrees with decide_mode",
      sm.is_surface(sm.SURFACE) and not sm.is_surface(sm.WINDOW))


# ---- session_shell_image: the order of evidence ---------------------------

check("a live shell window is decisive",
      sm.session_shell_image(lambda: "explorer.exe",
                             lambda: ["cairodesktop.exe"],
                             lambda: "cairodesktop.exe") == "explorer.exe")
check("no shell window + canonical shell running -> canonical shell",
      sm.session_shell_image(lambda: "", lambda: ["svchost.exe",
                                                   "EsotericOS.Shell.exe"],
                             lambda: "") == sm.ESOTERICOS_SHELL_IMAGE)
check("no shell window + legacy shell running -> legacy shell",
      sm.session_shell_image(lambda: "", lambda: ["svchost.exe",
                                                  "CairoDesktop.exe".lower()],
                             lambda: "") == sm.LEGACY_SHELL_IMAGE)
check("canonical shell wins if both transition images are present",
      sm.session_shell_image(lambda: "", lambda: ["cairodesktop.exe",
                                                   "esotericos.shell.exe"],
                             lambda: "") == sm.ESOTERICOS_SHELL_IMAGE)
check("no shell window, Explorer running -> Explorer",
      sm.session_shell_image(lambda: "", lambda: ["explorer.exe"],
                             lambda: "cairodesktop.exe") == sm.EXPLORER_IMAGE)
check("nothing running -> the registry intention, last",
      sm.session_shell_image(lambda: "", lambda: [],
                             lambda: "cairodesktop.exe") == sm.LEGACY_SHELL_IMAGE)
check("nothing anywhere -> empty, which decides window",
      sm.session_shell_image(lambda: "", lambda: [], lambda: "") == "")
check("a throwing shell-window probe falls through instead of raising",
      sm.session_shell_image(_boom, lambda: ["cairodesktop.exe"],
                             lambda: "") == sm.LEGACY_SHELL_IMAGE)
check("a throwing process list falls through to the registry",
      sm.session_shell_image(lambda: "", _boom,
                             lambda: "explorer.exe") == sm.EXPLORER_IMAGE)
check("a throwing registry read ends in empty, not an exception",
      sm.session_shell_image(lambda: "", lambda: [], _boom) == "")


# ---- the wiring in openspan.py --------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = open(os.path.join(HERE, "openspan.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)

assert_control = open(os.path.join(ROOT, "assert-control.ps1"),
                      encoding="utf-8").read()
first_light = open(os.path.join(ROOT, "tools", "first-light.ps1"),
                   encoding="utf-8").read()
proc_integrity = open(os.path.join(ROOT, "tools", "proc-integrity.ps1"),
                      encoding="utf-8").read()

check("freeze and Winlogon target the canonical shell executable",
      '$stableShellExe = Join-Path $shellStable "EsotericOS.Shell.exe"'
      in assert_control)
check("assert-control carries no dead WinSparkle mutation",
      "WinSparkle" not in assert_control)
check("assert-control removes both shell autorun identities",
      '@("EsotericOS Shell","CairoShell","OpenSpan")' in assert_control)
check("First Light observes canonical and legacy shell processes",
      "EsotericOS.Shell,CairoDesktop" in first_light)
check("First Light preserves canonical and legacy shell logs",
      "EsotericOS\\Shell\\Logs" in first_light
      and "Cairo Desktop\\Logs" in first_light)
check("integrity defaults observe both transition images",
      "'EsotericOS.Shell', 'CairoDesktop'" in proc_integrity)


def _class(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _method(cls, name):
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


app = _class("App")
check("App exists", app is not None)

init = _method(app, "__init__")
check("App.__init__ takes a mode argument, so the decision can be injected",
      init is not None and "mode" in [a.arg for a in init.args.args])
init_src = ast.get_source_segment(SRC, init) or ""
check("__init__ decides the mode exactly once",
      init_src.count("decide_mode(") == 1)
check("__init__ stores self.surface", "self.surface = " in init_src)

close = _method(app, "_close_request")
check("_close_request exists -- one door for every WM_CLOSE", close is not None)
close_src = ast.get_source_segment(SRC, close) or ""
check("_close_request branches on self.surface", "self.surface" in close_src)
check("_close_request still reaches the dialog in window mode",
      "self._confirm_close()" in close_src)

check("WM_DELETE_WINDOW is bound to _close_request, not _confirm_close",
      'root.protocol("WM_DELETE_WINDOW", app._close_request)' in SRC)
check("nothing binds WM_DELETE_WINDOW straight to _confirm_close",
      'WM_DELETE_WINDOW", app._confirm_close' not in SRC)

# The three controls that take the app away must be built under the gate. The
# assertion is on the source because building them needs a real Tk window.
for label, needle in (("close button", 'command=self._confirm_close, bg=BG'),
                      ("minimize button", 'command=self._minimize, bg=BG'),
                      ("Minimize button", 'command=self._to_tray).pack')):
    idx = SRC.find(needle)
    gate = SRC.rfind("if not self.surface:", 0, idx) if idx != -1 else -1
    # the gate must be the nearest preceding statement of its kind, and close
    # by -- within the same block, not some other method's
    check(f"the {label} is built under `if not self.surface:`",
          idx != -1 and gate != -1 and SRC.count("\n", gate, idx) < 25)

# The refusal must REFUSE -- not tear anything down on the way out. Anything
# that destroys, quits or full-stops inside this handler would make the
# "un-closeable" mode closeable through the very path that denies it.
body = close_src.split('"""')[-1]
check("the refusal destroys nothing",
      not any(t in body for t in ("destroy(", ".quit(", "_full_stop",
                                  "sys.exit", "_to_tray")))

print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
