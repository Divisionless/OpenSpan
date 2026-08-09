"""Structural guards for the NOTIFYICON_VERSION_4 tray contract."""

import ast
import pathlib
import re


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


ROOT = pathlib.Path(__file__).parent.parent
source = (ROOT / "win" / "openspan.py").read_text(encoding="utf-8")
tree = ast.parse(source)

tray = next(node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "TrayIcon")


def notify_calls(command):
    return sorted(
        node.lineno for node in ast.walk(tray)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Shell_NotifyIconW"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == command)


adds = notify_calls(0)
versions = notify_calls(4)
version_assignments = sorted(
    node.lineno for node in ast.walk(tray)
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Attribute) and target.attr == "uVersion"
            for target in node.targets)
    and isinstance(node.value, ast.Constant)
    and node.value.value == 4)

check("NIM_SETVERSION follows all three NIM_ADD sites",
      len(adds) == len(versions) == len(version_assignments) == 3
      and all(add_line < version_line < next_add
              and add_line < assignment_line < next_add
              for add_line, version_line, assignment_line, next_add in zip(
                  adds, versions, version_assignments, adds[1:] + [10**9])))

flags = [
    node.value.value for node in ast.walk(tray)
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Attribute) and target.attr == "uFlags"
            for target in node.targets)
    and isinstance(node.value, ast.Constant)
    and isinstance(node.value.value, int)]
check("NIF_SHOWTIP is present in the tray flags",
      len(flags) == 1 and flags[0] & 0x80 == 0x80)

proc = next(node for node in ast.walk(tray)
            if isinstance(node, ast.FunctionDef) and node.name == "proc")
proc_source = ast.get_source_segment(source, proc)
check("the WNDPROC decodes the V4 event from LOWORD(lParam)",
      re.search(r"\bl\s*&\s*0[xX]0*FFFF\b", proc_source) is not None)
check("WM_CONTEXTMENU is handled", "0x007B" in proc_source)
check("NIN_KEYSELECT is handled", "0x0401" in proc_source)
check("the immortality comment block survives",
      "tray window class and its WNDPROC thunk are registered ONCE per process"
      in source
      and "are IMMORTAL" in source
      and "_TRAY[\"proc\"] = WNDPROC(proc)  # immortal: keeps the thunk alive"
      in source)

proc_identifiers = {
    node.id.lower() for node in ast.walk(proc) if isinstance(node, ast.Name)
}
proc_identifiers.update(
    node.attr.lower() for node in ast.walk(proc) if isinstance(node, ast.Attribute)
)
check("no Tk identifier appears inside the WNDPROC function body",
      not any(name == "tk" or name.startswith("tkinter")
              for name in proc_identifiers))
