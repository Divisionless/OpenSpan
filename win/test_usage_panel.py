"""The Modules panel: the host draws, modules publish.

This file used to guard the AI-usage readout when it was two hardcoded
tk.StringVars and a `_usage_worker` welded into openspan.py. That readout is
now the agent-monitor MODULE, and the panel is generic -- it would draw a
second module the same way without knowing anything about it. So the checks
moved with it: what matters is no longer "are the two usage labels there" but
"can module output reach a widget anywhere except the one place allowed".

Behaviour of the modules themselves is in test_module_host.py. This is only
about the seam between them and the window.
"""

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
app = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "App")


def method(name):
    return next((n for n in app.body
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


check("the old hardcoded usage readout is gone, not merely hidden",
      "usage_codex" not in source and "usage_claude" not in source
      and method("_usage_worker") is None)
check("there is a Modules section", '_section(pane_system, "Modules")' in source)

worker = method("_module_worker")
check("a worker refreshes modules off the UI thread", worker is not None)
body = ast.get_source_segment(source, worker)
check("module_host is imported lazily, inside the worker",
      "import module_host" in body
      and not re.search(r"^import module_host", source, re.M))
check("the worker never touches a widget directly -- it hands rows to ui()",
      "self.ui(" in body and "tk.Label" not in body)

draw = method("_draw_modules")
check("exactly one method paints module output", draw is not None)
paint = ast.get_source_segment(source, draw)
# `box` is an ordinary local name all over this file, so counting tk.Label(box
# proves nothing. The precise question is who can reach the MODULES box.
touchers = {n.name for n in ast.walk(app)
            if isinstance(n, ast.FunctionDef)
            and "modules_box" in (ast.get_source_segment(source, n) or "")}
check("only the builder and the painter can reach the modules box",
      len(touchers) == 2 and "_draw_modules" in touchers)
check("the painter is what actually makes the widgets", "tk.Label(box" in paint)
check("a window destroyed mid-refresh is survivable, not a traceback",
      "tk.TclError" in paint)

# A module that failed to load must be VISIBLE. Silently omitting it is how
# something you rely on goes missing without anyone noticing.
check("a faulted module is shown with its reason",
      "faulted" in body and "record.reason" in body)
check("a module that reports nothing says so rather than leaving a blank",
      "reported nothing" in body)
check("no modules at all is a sentence, not an empty box",
      "none installed" in body)

# The single-hook law counts `host.start()`. The module host is deliberately
# NOT called `host` here, so it cannot dilute that count.
check("the module host does not shadow the keyboard hook's name",
      "mods = module_host.ModuleHost(" in body and "host = module_host" not in body)

# What a module OBSERVED has to outlive the process, or only the provider's
# claims survive -- which is the thing the agent monitor exists to check.
check("module settings are persisted", method("_save_module_settings") is not None
      and method("_load_module_settings") is not None)
# Read the CODE, not the comment: the docstring here explains why __file__ is
# wrong, and a naive substring search would trip on the explanation itself.
node = method("_module_settings_path")
statements = [s for s in node.body
              if not (isinstance(s, ast.Expr)
                      and isinstance(s.value, ast.Constant)
                      and isinstance(s.value.value, str))]
path = "\n".join(ast.get_source_segment(source, s) for s in statements)
check("settings anchor on ROOT, never __file__ (frozen builds delete that)",
      "ROOT" in path and "__file__" not in path)
check("the write is atomic, so a crash cannot truncate what was observed",
      "os.replace" in ast.get_source_segment(source, method("_save_module_settings")))

stop = ast.get_source_segment(source, method("_full_stop"))
check("shutdown deactivates modules and saves what they observed",
      "_module_host" in stop and "_save_module_settings" in stop)

# The build has to carry the module FOLDER: discovery reads plugin.json off
# disk, so a hidden-import would ship code nobody ever imports by name.
build = (ROOT / "build_exe.py").read_text(encoding="utf-8")
check("the build ships the modules folder as data",
      "--add-data" in build and '"modules"' in build)
