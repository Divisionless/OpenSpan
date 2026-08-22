# SPDX-License-Identifier: AGPL-3.0-or-later
"""The GUI publishes one atomic Desktop-role signal for shell-owned surfaces."""
import ast
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import openspan as A  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (
        "" if condition or not detail else "\n      " + detail))
    if not condition:
        fails.append(name)


SOURCE = open(os.path.join(HERE, "openspan.py"), encoding="utf-8").read()
MODULE = ast.parse(SOURCE, filename="openspan.py")


def method(class_name, method_name):
    cls = next(node for node in MODULE.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body
                if isinstance(node, ast.FunctionDef)
                and node.name == method_name)


print("\n---- atomic cross-process contract ----")
scratch = tempfile.mkdtemp(prefix="esotericos-desktop-role-")
try:
    role_file = os.path.join(scratch, "nested", "desktop-monitor.txt")
    written = A.publish_desktop_monitor(r"\\.\DISPLAY7", role_file)
    check("publisher creates the canonical parent and returns its path",
          written == role_file and os.path.isfile(role_file))
    with open(role_file, encoding="utf-8") as stream:
        check("the contract is exactly one effective GDI name",
              stream.read() == "\\\\.\\DISPLAY7\n")
    A.publish_desktop_monitor(r"\\.\DISPLAY2", role_file)
    with open(role_file, encoding="utf-8") as stream:
        check("a new selection atomically replaces the old role",
              stream.read() == "\\\\.\\DISPLAY2\n")
    check("no publication temporary survives", not os.path.exists(role_file + ".new"))
    try:
        A.publish_desktop_monitor("", role_file)
        empty_rejected = False
    except ValueError:
        empty_rejected = True
    check("an empty role is rejected instead of erasing shell placement",
          empty_rejected)
finally:
    shutil.rmtree(scratch, ignore_errors=True)


print("\n---- startup, selection and hot-plug all publish ----")
sync_src = ast.unparse(method("App", "_sync_desktop_monitor"))
controller_src = ast.unparse(method("App", "_desktop_controller"))
init_src = ast.unparse(method("App", "__init__"))
menu_src = ast.unparse(method("App", "_menu_set_desktop"))
check("display reconciliation publishes before re-docking the GUI",
      sync_src.index("self._publish_desktop_role(name)")
      < sync_src.index("self._desktop.set_monitor(name)"))
check("ordinary startup publishes before constructing the Desktop controller",
      "self.root.after(300, self._apply_window_placement)" in init_src
      and controller_src.index("self._publish_desktop_role(monitor_name)")
      < controller_src.index("on_desktop.OnDesktop"))
check("the monitor menu persists the durable request then uses one sync path",
      menu_src.index("save_setting")
      < menu_src.index("self._sync_desktop_monitor()")
      and "publish_desktop_monitor" not in menu_src)
check("the default contract is per-user and product-wide",
      os.path.basename(A.DESKTOP_ROLE_FILE) == "desktop-monitor.txt"
      and os.path.basename(os.path.dirname(A.DESKTOP_ROLE_FILE)) == "EsotericOS")


print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
