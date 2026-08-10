"""Guards for the Window management wiring in the app.

The hook is the hazard: the portal already captures this keyboard and a
rival KVM hooks it too, so the app must never install one on its own
initiative, and must never leave one behind."""

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
    return next(n for n in app.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


check("the Window management section exists",
      '_section(pane_system, "Window management")' in source)
check("the pane says plainly that chords are off",
      "off — chords are not intercepted" in source)

toggle = ast.get_source_segment(source, method("_toggle_window_chords"))
check("start() is called only from the toggle",
      source.count(".start()") >= 1 and "host.start()" in toggle)
starts = [n for n in ast.walk(app)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "start" and isinstance(n.func.value, ast.Name)
          and n.func.value.id == "host"]
check("no other method starts the hook", len(starts) == 1)
check("a refused start is reported, never swallowed",
      "refused" in toggle and "_emit" in toggle)
check("the toggle can also stop", "host.stop()" in toggle)

full_stop = ast.get_source_segment(source, method("_full_stop"))
check("shutdown releases the hook before anything else",
      "_wm_host" in full_stop
      and full_stop.index("_wm_host") < full_stop.index("clip_server"))
check("shutdown also unzooms: exiting magnified is unrecoverable",
      "_zoom" in full_stop
      and full_stop.index("_zoom") < full_stop.index("clip_server"))

zoom_toggle = ast.get_source_segment(source, method("_toggle_screen_zoom"))
check("the mouse hook starts only from the zoom toggle",
      "zoom.start()" in zoom_toggle
      and len([n for n in ast.walk(app)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "start"
               and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "zoom"]) == 1)
check("a refused zoom start says why and installs nothing",
      "refused" in zoom_toggle and "last_error" in zoom_toggle)
check("screen_zoom is imported lazily",
      "import screen_zoom" in ast.get_source_segment(
          source, method("_screen_zoom"))
      and not re.search(r"^import screen_zoom", source, re.M))

host_builder = ast.get_source_segment(source, method("_window_host"))
check("hotkey_host is imported lazily inside its builder",
      "import hotkey_host" in host_builder
      and not re.search(r"^import hotkey_host", source, re.M))
check("construction failure disables the button instead of crashing",
      "unavailable" in source and 'self.wm_btn.state(["disabled"])' in source)

check("startup paints the bindings but never starts the hook",
      "self._wm_host = None" in source
      and "self.ui(self._show_window_chords)" in source
      and "host.start()" not in ast.get_source_segment(
          source, method("_show_window_chords")))
