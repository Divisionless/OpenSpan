"""A module ships as DATA, so nothing analyses what it imports.

2026-08-15: the agent monitor shipped, loaded, activated -- and reported
nothing but errors. Replacing `_usage_worker` removed the last statically
visible `import usage_monitor` from analysed code, so PyInstaller dropped
usage_monitor from the bundle. The plugin still imported it, because the
plugin is a file on disk that no build step ever reads.

Nothing failed loudly. The panel simply said it could not read anything, and
`module_settings.json` stayed `{}` because the code that records an observed
reset never ran.

This is the same shape as the rest of that day -- a dependency stated in one
place and relied on in another, with nothing keeping the two honest. So:
every non-stdlib import inside a shipped module must be declared as a
hidden-import in build_exe.py.
"""

import ast
import pathlib
import re
import sys


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        if detail:
            print("      " + detail)
        raise AssertionError(name)


ROOT = pathlib.Path(__file__).parent.parent
MODULES = ROOT / "win" / "modules"
build = (ROOT / "build_exe.py").read_text(encoding="utf-8")

declared = set(re.findall(r'"--hidden-import",\s*"([^"]+)"', build))
check("build_exe declares hidden imports at all", bool(declared),
      "no --hidden-import pairs found; has the arg format changed?")

# Anything a module can rely on without it being bundled explicitly.
STDLIB = set(sys.stdlib_module_names)
# Bundled by the app's own analysed code, so a module may lean on them.
HOST_PROVIDED = {"module_host", "plugin_system"}

sources = sorted(MODULES.rglob("*.py"))
check("at least one module ships", bool(sources), f"nothing under {MODULES}")

missing, seen = [], set()
for path in sources:
    if "__pycache__" in path.parts:
        continue
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            # A relative import stays inside the module's own folder, which
            # ships whole -- nothing for the bundle to miss.
            names = [] if node.level else [(node.module or "").split(".")[0]]
        else:
            continue
        for name in names:
            if not name or name in STDLIB or name in HOST_PROVIDED:
                continue
            seen.add(name)
            if name not in declared:
                missing.append(f"{path.parent.name}/{path.name} imports "
                               f"'{name}', not in build_exe hidden-imports")

check("every module's imports are declared to the build", not missing,
      "; ".join(missing))
check("the module that shipped broken is covered now",
      "usage_monitor" in declared and "usage_monitor" in seen,
      f"declared={sorted(declared)} seen={sorted(seen)}")

# ---- and the folder itself must ship ---------------------------------------

check("the modules folder is added as data",
      re.search(r'"--add-data".{0,80}modules', build, re.S) is not None)

# ---- nothing replaces a binary without keeping it, staged or not -----------
#
# The first version of this guard read `if not staged`, which is the exact
# mistake it existed to prevent: a safety with a narrow trigger. It let the
# staged path overwrite the seam-fix build.
check("preservation covers ANY existing target, not just the unstaged one",
      re.search(r"^if os\.path\.exists\(target\):", build, re.M) is not None,
      "preservation is gated again")
check("a build that cannot preserve still refuses",
      "REFUSING to overwrite" in build)
