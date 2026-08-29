"""The Modules section is GONE, and this file is what stops it drifting back.

The history in three steps. It began as a hardcoded AI-usage readout welded
into openspan.py: two tk.StringVars, two labels and a `_usage_worker`. That was
replaced by the agent-monitor MODULE behind a generic host, and this file
became the seam test -- "can module output reach a widget anywhere except the
one place allowed". On 2026-08-28 Doug asked for the section itself off the
page: it was a titled section whose only content was that module's Codex and
Claude usage lines.

So the whole chain is deleted rather than hidden -- the builder, the host, the
worker, the painter and the settings that persisted what a module observed.
What is NOT deleted is `modules/`, `module_host.py` and `plugin_system.py`,
which keep their own tests (test_module_host.py, test_module_deps.py,
test_plugin_system.py). Nothing about this removal forecloses a module surface
later; re-wiring one is a builder, a worker and a painter, which is exactly the
shape that was taken out.

The checks below are therefore absence checks, and they are deliberately about
the WINDOW rather than about the module system. A module that fails must still
fail loudly in its own tests; it simply has nowhere in this window to say so.

RUNS HEADLESS AND TOUCHES NOTHING: openspan.py is parsed, never imported.
"""

import ast
import pathlib


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


print("\n---- the readout, in all three of its lives ----")

check("the original hardcoded usage readout is still gone",
      "usage_codex" not in source and "usage_claude" not in source
      and method("_usage_worker") is None)
check("and so is the generic section that replaced it",
      '_section(pane_system, "Modules")' not in source
      and "modules_box" not in source and "module_rows" not in source)

print("\n---- nothing is left half-removed ----")

for name in ("_module_worker", "_draw_modules", "_load_module_settings",
             "_save_module_settings", "_module_settings_path"):
    check(f"{name} is deleted, not orphaned", method(name) is None
          and f"self.{name}" not in source)
check("no thread is started for modules any more",
      "esotericos-modules" not in source
      and "target=self._module_worker" not in source)
check("the host is neither imported nor built",
      "ModuleHost(" not in source and "import module_host" not in source
      and "import plugin_system" not in source)
check("_module_host is not left as an attribute nobody sets",
      "self._module_host" not in source)
check("shutdown no longer deactivates a host it never started",
      "_module_host" not in (ast.get_source_segment(
          source, method("_full_stop")) or ""))

print("\n---- the module system itself is untouched on disk ----")

for name in ("module_host.py", "plugin_system.py"):
    check(f"{name} still exists, so re-wiring is a builder and a worker",
          (ROOT / "win" / name).is_file())
check("and the modules folder is still there",
      (ROOT / "win" / "modules").is_dir())
# The build shipping the folder is now dead weight in the exe, but removing it
# is a build change and not a window change, and a hidden-import would ship code
# nobody imports by name. Left as-is, deliberately, and recorded here.
build = (ROOT / "build_exe.py").read_text(encoding="utf-8")
check("the build still ships the modules folder as data (harmless, and the "
      "one line that has to change if a module surface returns)",
      "--add-data" in build and '"modules"' in build)

print("\nRESULT: ALL PASS")
