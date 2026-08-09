"""Pure-core and structural guards for the standalone window tracker."""

import ast
import dataclasses
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import window_tracker as wt


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def identity(executable, window_class, title="", aumid=None, sibling=0):
    return wt.WindowIdentity(
        executable_path=wt.WindowIdentity.normalize_path(executable),
        window_class=window_class,
        title=title,
        app_user_model_id=aumid,
        sibling_index=sibling,
    )


# ---- import and hook structure -------------------------------------------------

check("importing the pure core does not pull in ctypes", "ctypes" not in sys.modules)

source_path = pathlib.Path(wt.__file__)
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
parents = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parents[child] = node


def enclosing_function(node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


ctypes_imports = [node for node in ast.walk(tree)
                  if ((isinstance(node, ast.Import)
                       and any(alias.name == "ctypes"
                               or alias.name.startswith("ctypes.")
                               for alias in node.names))
                      or (isinstance(node, ast.ImportFrom)
                          and node.module
                          and (node.module == "ctypes"
                               or node.module.startswith("ctypes."))))]
check("every ctypes import sits inside a function",
      ctypes_imports and all(enclosing_function(node) for node in ctypes_imports))

hook_calls = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "SetWinEventHook"]
check("start() is the only place SetWinEventHook is called",
      len(hook_calls) == 1 and enclosing_function(hook_calls[0]) == "start")
check("construction neither loads native bindings nor starts tracking",
      "_load_native" not in ast.get_source_segment(
          source, next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "__init__")))
check("the tracker hooks foreground, move-end, object lifetime, and name changes",
      wt.WindowTracker._HOOK_RANGES == (
          (wt.EVENT_SYSTEM_FOREGROUND, wt.EVENT_SYSTEM_FOREGROUND),
          (wt.EVENT_SYSTEM_MOVESIZEEND, wt.EVENT_SYSTEM_MOVESIZEEND),
          (wt.EVENT_OBJECT_CREATE, wt.EVENT_OBJECT_HIDE),
          (wt.EVENT_OBJECT_NAMECHANGE, wt.EVENT_OBJECT_NAMECHANGE)))
check("the callback boundary contains every exception before native return",
      "except BaseException" in ast.get_source_segment(
          source, next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "_callback_boundary")))
check("bounds use DWM extended frames and fall back to GetWindowRect",
      "DwmGetWindowAttribute" in source
      and "_DWMWA_EXTENDED_FRAME_BOUNDS" in source
      and "GetWindowRect" in source)


# ---- every WindowIdentityTests case -------------------------------------------

before = identity(r"C:\Apps\Editor.exe", "EditorMain", "draft.txt - Editor")
after = dataclasses.replace(before, title="final.txt - Editor")
check("stable key survives a title change", before.stable_key == after.stable_key)

first = identity(r"C:\Windows\explorer.exe", "CabinetWClass", sibling=0)
second = dataclasses.replace(first, sibling_index=1)
check("stable key distinguishes sibling windows", first.stable_key != second.stable_key)

check("path normalization is case and separator insensitive",
      identity(r"C:\Apps\Editor.exe", "X").executable_path
      == identity(r"c:/APPS/editor.EXE", "X").executable_path)

remembered = identity(r"C:\Apps\Editor.exe", "EditorMain", "notes")
candidate = identity(r"C:\Apps\Other.exe", "EditorMain", "notes")
check("a different application never matches",
      wt.score(remembered, candidate) == wt.MatchStrength.NONE)

remembered = identity(r"C:\Apps\Editor.exe", "EditorMain", "notes", sibling=1)
check("match strength ranks durable facts above volatile ones",
      wt.score(remembered, remembered) == wt.MatchStrength.EXACT
      and wt.score(remembered, dataclasses.replace(
          remembered, sibling_index=3)) == wt.MatchStrength.TITLE
      and wt.score(remembered, dataclasses.replace(
          remembered, title="something else")) == wt.MatchStrength.POSITION
      and wt.score(remembered, dataclasses.replace(
          remembered, title="x", sibling_index=9)) == wt.MatchStrength.CLASS
      and wt.score(remembered, dataclasses.replace(
          remembered, window_class="EditorDialog")) == wt.MatchStrength.APPLICATION)

remembered = identity(
    r"C:\Windows\explorer.exe", "AppFrame", "Mail",
    aumid="microsoft.windowscommunicationsapps_8wekyb3d8bbwe!mail")
same_app = dataclasses.replace(
    remembered, executable_path=r"c:\windows\system32\applicationframehost.exe")
other_app = dataclasses.replace(
    remembered, app_user_model_id="microsoft.windowsstore_8wekyb3d8bbwe!app")
check("packaged apps match by AUMID, not by host executable",
      wt.score(remembered, same_app) == wt.MatchStrength.EXACT
      and wt.score(remembered, other_app) == wt.MatchStrength.NONE)

remembered_pair = [
    identity(r"C:\Apps\Editor.exe", "EditorMain", "alpha", sibling=0),
    identity(r"C:\Apps\Editor.exe", "EditorMain", "beta", sibling=1),
]
candidates = [
    identity(r"C:\Apps\Editor.exe", "EditorMain", "beta", sibling=0),
    identity(r"C:\Apps\Editor.exe", "EditorMain", "alpha", sibling=1),
]
assignment = wt.assign(remembered_pair, candidates)
check("assignment prefers stronger matches and never reuses a candidate",
      assignment == {0: 1, 1: 0})

remembered_pair = [
    identity(r"C:\Apps\Editor.exe", "EditorMain", "alpha"),
    identity(r"C:\Apps\Gone.exe", "GoneMain", "beta"),
]
assignment = wt.assign(
    remembered_pair,
    [identity(r"C:\Apps\Editor.exe", "EditorMain", "alpha")])
check("assignment leaves unmatched entries out rather than guessing",
      assignment.get(0) == 0 and 1 not in assignment)

remembered_one = [identity(r"C:\Apps\Editor.exe", "EditorMain")]
identical = [identity(r"C:\Apps\Editor.exe", "EditorMain"),
             identity(r"C:\Apps\Editor.exe", "EditorMain")]
check("assignment is deterministic for identical candidates",
      all(wt.assign(remembered_one, identical)[0] == 0 for _ in range(5)))


# ---- every ApplicationGroupingTests case --------------------------------------

a = identity(r"C:\Apps\Suite.exe", "SuiteMain")
b = identity(r"C:\Apps\Suite.exe", "SuiteDialog")
check("multi-process applications group as one",
      wt.ApplicationGrouping.resolve(a) == wt.ApplicationGrouping.resolve(b))

browser = identity(r"C:\Program Files\Chrome\chrome.exe", "Chrome_WidgetWin_1")
web_app = identity(
    r"C:\Program Files\Chrome\chrome.exe", "Chrome_WidgetWin_1",
    aumid="Chrome._crx_abcdef")
check("installed web apps group by AUMID, not by the browser",
      wt.ApplicationGrouping.resolve(browser)
      != wt.ApplicationGrouping.resolve(web_app))

packaged = wt.ApplicationGrouping.resolve(identity(
    r"C:\Windows\explorer.exe", "AppFrame",
    aumid="Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"))
check("packaged app display name comes from the family name",
      packaged.display_name == "WindowsTerminal")

check("unpackaged applications are named from the executable",
      wt.ApplicationGrouping.resolve(
          identity(r"C:\Apps\editor.exe", "Main")).display_name == "Editor")

windows = [
    ("a1", identity(r"C:\Apps\A.exe", "Main")),
    ("b1", identity(r"C:\Apps\B.exe", "Main")),
    ("a2", identity(r"C:\Apps\A.exe", "Main")),
]
groups = wt.ApplicationGrouping.group(windows, lambda window: window[1])
check("grouping preserves first-seen application order and window order",
      len(groups) == 2
      and groups[0].app.display_name == "A"
      and [window[0] for window in groups[0].windows] == ["a1", "a2"]
      and [window[0] for window in groups[1].windows] == ["b1"])

check("application identity comparison ignores case",
      wt.ApplicationGrouping.resolve(identity(r"C:\Apps\Editor.exe", "Main"))
      == wt.ApplicationGrouping.resolve(identity(r"c:\apps\EDITOR.exe", "Main")))
