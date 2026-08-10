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

spaces_toggle = ast.get_source_segment(source, method("_toggle_spaces"))
check("spaces is imported lazily inside its toggle",
      "import spaces" in spaces_toggle
      and not re.search(r"^import spaces", source, re.M))
check("a failed enable restores rather than leaving windows hidden",
      spaces_toggle.count("disable()") >= 2
      and "except Exception as exc" in spaces_toggle)
check("shutdown disables spaces: exiting with windows hidden loses them",
      "_spaces" in full_stop
      and full_stop.index("_spaces") < full_stop.index("clip_server"))
check("the spaces switch says plainly that nothing is hidden while off",
      "off — every window stays visible" in source)

host_builder = ast.get_source_segment(source, method("_window_host"))
check("hotkey_host is imported lazily inside its builder",
      "import hotkey_host" in host_builder
      and not re.search(r"^import hotkey_host", source, re.M))
check("construction failure disables the button instead of crashing",
      "unavailable" in source and 'self.wm_btn.state(["disabled"])' in source)

check("painting the binding table never starts a hook",
      "self._wm_host = None" in source
      and "self.ui(self._show_window_chords)" in source
      and "host.start()" not in ast.get_source_segment(
          source, method("_show_window_chords")))

# Doug tested both live and asked for them ON at launch. Autostart must go
# through the SAME toggles, so the single-start-site guarantee above holds.
autostart = ast.get_source_segment(source, method("_autostart_window_features"))
check("autostart arms chords and zoom through their own toggles",
      "_toggle_window_chords" in autostart
      and "_toggle_screen_zoom" in autostart
      and "host.start()" not in autostart
      and "zoom.start()" not in autostart)
check("autostart arms Spaces too, at Doug's explicit request",
      "_toggle_spaces" in autostart)
# Spaces hides windows. Alt+<n> is the only keyboard route back, so enabling
# without attaching those chords is the one way this feature costs work.
spaces_toggle_src = ast.get_source_segment(source, method("_toggle_spaces"))
check("enabling Spaces attaches the Alt+<n> switch chords",
      "_attach_space_chords" in spaces_toggle_src)
attach = ast.get_source_segment(source, method("_attach_space_chords"))
check("the switch chords act on the display under the pointer",
      "_pointer_monitor" in attach and "switch_to_ordinal" in attach)
# enable() snapshots once; without a catch-up, windows opened later never
# join a space and Alt+<n> appears to work on only some applications.
check("a switch re-syncs windows opened since Spaces was enabled",
      "window_appeared" in attach
      and attach.index("window_appeared") < attach.index("switch_to_ordinal"))
# A window dragged to another screen belonged to the screen it LEFT, so that
# screen's Alt+n kept reclaiming it. rehome only fires on a real monitor
# change, so re-syncing every window cannot disturb one that has not moved.
check("a window dragged to another screen re-homes to it",
      "window_moved" in attach)
# macOS carries the window you are holding when you change space.
check("a held window is carried to the target space",
      "_dragged_window" in attach and "model.assign" in attach)
dragged = ast.get_source_segment(source, method("_attach_space_chords"))
check("the drag is read from the physical button, not a hooked event",
      "GetAsyncKeyState" in dragged and "GetForegroundWindow" in dragged)
check("carrying happens before the switch, so the window travels with it",
      dragged.index("model.assign") < dragged.rindex("switch_to_ordinal"))

# No window may span two displays while Spaces is on: a window across a
# boundary has no unambiguous owner, which is where the ownership bugs come
# from. The one window that must NOT be snapped is the one in hand.
confine = ast.get_source_segment(source, method("_confine"))
check("straddling windows are pulled onto their owning display",
      "straddles" in confine and "confine_to_work_area" in confine)
check("the window being dragged is never snapped out from under the user",
      "window.handle != handle" in attach)
check("confinement runs before the switch decides what to show",
      attach.index("_confine") < attach.rindex("switch_to_ordinal"))
check("a switch with Spaces disabled does nothing rather than raising",
      "module.enabled" in attach)
check("one feature failing to arm cannot stop the other or the app",
      "except Exception" in autostart)
check("autostart can be disabled without a rebuild",
      "WINDOW_FEATURE_AUTOSTART" in autostart)
