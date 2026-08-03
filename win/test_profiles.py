"""Named arrangements: saving one, loading one, and switching between them.

Doug, 28 July: *"I sometimes change my managed mac's landscape screen to 2k
resolution. So I need to be able to duplicate the current arrangement and then
resize/arrange all screens and devices attached to it."*

A resolution change is not a small edit. Every distance on that screen is
derived from it, the physical size that screen is drawn at is derived from it,
and the crossing bands on its edges are derived from those -- so switching the
Mac between 4K and 2K means re-entering the desk, or keeping two of them.

The things that can go wrong here are all invisible until the moment they bite:

    * a profile that carries RADIO and PORT would move a dongle assignment along
      with a picture of the desk. Bonds live on the guest per radio, so loading
      an arrangement saved when the Mac was on a different dongle would point
      the lane at a radio holding no bond for it -- a device that pairs, goes
      green, and does nothing.
    * a profile that carries a device's FEEL undoes tuning. Observed live on
      2 August: all three devices were set to the 0.75 notch, the arrangement
      was switched from "Mac 2k 2" to "Mac 2k", and all three reverted to the
      numbers that arrangement had been saved with -- 0.686, 0.747 and 1.0.
      Sensitivity, key mapping and scroll direction describe the device and the
      hand using it, not where the screens sit.
    * `ipad` and `selected` on the canvas are references to specific display
      dicts. A switch that rebuilds the config but leaves those behind keeps a
      live handle into the arrangement that is no longer on screen.

No Tk window is created and the live config is never written.

Exit 0 = all pass.
"""
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402
from openspan_targets import normalize_config  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (
        "" if cond or not detail else "\n      " + detail))
    if not cond:
        fails.append(name)


A.PROFILE_DIR = tempfile.mkdtemp(prefix="openspan-profiles-")

LIVE = [{"name": r"\\.\DISPLAY1", "x": 0, "y": 0, "w": 2560, "h": 1440,
         "primary": True}]


def desk(landscape_w=3840):
    """A desk with two devices, one of which has a resolution that changes."""
    return {
        "monitors": [dict(LIVE[0])],
        "devices": [
            {"id": "device-1", "name": "iPad", "port": 7810, "radio": "AA:11",
             "displays": [{"id": "device-1-1", "name": "Display 1",
                           "x": 2560, "y": 0, "w": 900, "h": 1200,
                           "res_w": 1668, "res_h": 2388, "rotation": 0}]},
            {"id": "device-2", "name": "Managed Mac", "port": 7811,
             "radio": "BB:22",
             "displays": [{"id": "device-2-1", "name": "Landscape",
                           "x": -3000, "y": 0, "w": 3200, "h": 1800,
                           "res_w": landscape_w, "res_h": landscape_w * 9 // 16,
                           "rotation": 0}]},
        ],
    }


# ---- what a profile carries, and what it deliberately does not -------------
config = normalize_config(desk(), LIVE)
config["portals"] = [{"edge": "right"}]
config["links"] = [{"a": "x"}]
A.save_profile(config, "Mac 4K")

with open(A._profile_path("Mac 4K"), encoding="utf-8") as handle:
    import json
    saved = json.load(handle)

check("the arrangement is saved under its name",
      A.list_profiles() == ["Mac 4K"], str(A.list_profiles()))
check("no radio or port travels with an arrangement",
      not [d for d in saved["devices"]
           if "radio" in d or "port" in d],
      str([{k: v for k, v in d.items() if k in ("radio", "port")}
           for d in saved["devices"]]))
check("and no device-scoped field of any kind is written into the file",
      not [d for d in saved["devices"]
           for f in A.DEVICE_FIELDS if f in d],
      str([{k: v for k, v in d.items() if k in A.DEVICE_FIELDS}
           for d in saved["devices"]]))
check("the derived portal list is not frozen into it",
      "portals" not in saved)
check("but `links` IS kept, because normalize_config reads its absence as "
      "\"this predates the adjacency graph\" and re-snaps every screen",
      "links" in saved)
check("every screen the desk has is in it",
      [len(d["displays"]) for d in saved["devices"]] == [1, 1])
check("saving does not disturb the arrangement in use",
      config["devices"][0]["radio"] == "AA:11"
      and config["devices"][0]["port"] == 7810)

# ---- loading wears THIS machine's hardware ---------------------------------
current = normalize_config(desk(), LIVE)
current["devices"][0]["radio"] = "CC:33"      # dongles moved since the save
current["devices"][0]["port"] = 7899
loaded = A.load_profile("Mac 4K", current)
check("the radio in use is kept, not the one that was saved",
      loaded["devices"][0]["radio"] == "CC:33",
      str(loaded["devices"][0].get("radio")))
check("the lane in use is kept too",
      loaded["devices"][0]["port"] == 7899,
      str(loaded["devices"][0].get("port")))
check("loading names the arrangement", loaded.get("profile") == "Mac 4K")

unknown = {"devices": [], "monitors": []}
kept = A.load_profile("Mac 4K", unknown)
check("a device this machine no longer has still loads",
      len(kept["devices"]) == 2
      and not kept["devices"][0].get("radio"))

# ---- two arrangements of the same desk ------------------------------------
two_k = normalize_config(desk(landscape_w=2560), LIVE)
A.save_profile(two_k, "Mac 2K")
check("both arrangements exist side by side",
      A.list_profiles() == ["Mac 2K", "Mac 4K"], str(A.list_profiles()))
four = A.load_profile("Mac 4K", current)
two = A.load_profile("Mac 2K", current)
check("and they really do hold different resolutions",
      four["devices"][1]["displays"][0]["res_w"] == 3840
      and two["devices"][1]["displays"][0]["res_w"] == 2560,
      f"{four['devices'][1]['displays'][0]['res_w']} vs "
      f"{two['devices'][1]['displays'][0]['res_w']}")

check("a name that is not a filename cannot escape the folder",
      os.path.dirname(os.path.abspath(A._profile_path("../../evil")))
      == os.path.abspath(A.PROFILE_DIR),
      A._profile_path("../../evil"))

check("deleting removes one and leaves the other",
      A.delete_profile("Mac 2K") and A.list_profiles() == ["Mac 4K"],
      str(A.list_profiles()))
check("deleting one that is gone is not an error",
      A.delete_profile("Mac 2K") is False)

# ---- adopting one rebuilds every derived handle ---------------------------
canvas = A.MultiArrangeCanvas.__new__(A.MultiArrangeCanvas)
canvas._told_portal = None
canvas.target_states = {}
canvas.adopt(desk(), LIVE)
canvas.target_states["device-2"] = "live"
before_ipad = canvas.ipad
before_res = canvas.targets[1]["displays"][0]["res_w"]

canvas.adopt(A.load_profile("Mac 4K", canvas.config), LIVE)
check("adopting an arrangement replaces the screens",
      canvas.monitors is canvas.config["monitors"]
      and canvas.targets is canvas.config["devices"])
check("no handle is left pointing into the previous arrangement",
      canvas.ipad is not before_ipad
      and any(canvas.ipad is d for t in canvas.targets
              for d in t.get("displays", [])))
check("the selection points at something that exists",
      canvas.selected is None
      or any(canvas.selected[2] == d["id"] for t in canvas.targets
             for d in t.get("displays", [])),
      str(canvas.selected))
check("a device that is connected stays connected across the switch",
      canvas.target_states.get("device-2") == "live",
      str(canvas.target_states))
check("the arrangement's name survives normalisation",
      canvas.config.get("profile") == "Mac 4K",
      str(canvas.config.get("profile")))

canvas.adopt(A.load_profile("Mac 4K", canvas.config), LIVE)
canvas.adopt(desk(landscape_w=2560), LIVE)
check("and switching back changes the resolution the desk is drawn from",
      canvas.targets[1]["displays"][0]["res_w"] == 2560
      and before_res == 3840,
      str(canvas.targets[1]["displays"][0]["res_w"]))

# ---- a switch must move the portal ----------------------------------------
canvas.adopt(desk(), LIVE)
wide = A.portal_signature(canvas.config)
canvas.adopt(desk(landscape_w=2560), LIVE)
check("the portal is told, because the signature really changed",
      A.portal_signature(canvas.config) != wide)

# ---- nothing the app keeps at the top of the config may be lost ------------
# The blocker this closes: the two side-button crossing settings live at the top
# level, and normalize_config builds its result from a whitelist. Every load
# dropped them and the next save wrote the config back without them, while the
# checkboxes went on reading the same config and showing whatever was left. Doug
# called press-to-jump "flawless"; it was being switched off behind him.
canvas = A.MultiArrangeCanvas.__new__(A.MultiArrangeCanvas)
canvas._told_portal = None
canvas.target_states = {}
loud = dict(desk(), cross_requires_side_button=True,
            side_button_jumps_nearest=True, some_future_setting="keep me")
canvas.adopt(loud, LIVE)
check("a top-level setting survives being loaded",
      canvas.config.get("cross_requires_side_button") is True
      and canvas.config.get("side_button_jumps_nearest") is True,
      str({k: v for k, v in canvas.config.items()
           if "button" in k}))
check("including one this test invented, because they are carried by "
      "difference and not by name",
      canvas.config.get("some_future_setting") == "keep me")
check("and the portal is told about it",
      A.portal_signature(canvas.config)
      != A.portal_signature(dict(canvas.config,
                                 cross_requires_side_button=False)))
A.save_profile(canvas.config, "Loud")
carried = A.load_profile("Loud", canvas.config)
canvas.adopt(carried, LIVE)
check("and it still survives a round-trip through an arrangement",
      canvas.config.get("cross_requires_side_button") is True)
A.delete_profile("Loud")

# a switch can therefore CHANGE them, so the checkboxes must be re-read
import inspect  # noqa: E402
src = inspect.getsource(A.App._switch_profile)
check("switching re-reads both crossing checkboxes from the new arrangement",
      "self.cross_button.set(" in src and "self.button_jumps.set(" in src,
      "the boxes would keep showing the previous arrangement's settings")

# ---- a name and its filename are the same string ---------------------------
for typed, expect in (("Mac 4K (day)", "Mac 4K _day_"), ("Desk 2.0", "Desk 2_0"),
                      ("  padded  ", "padded"), ("///", "___"), ("", "unnamed"), ("...", "___"),
                      ("Mac 4K", "Mac 4K")):
    check(f"“{typed}” is known as “{expect}”",
          A.profile_name(typed) == expect, A.profile_name(typed))

A.save_profile(normalize_config(desk(), LIVE), "Mac 4K (day)")
check("a punctuated name is listed under the name it was given",
      A.profile_name("Mac 4K (day)") in A.list_profiles(), str(A.list_profiles()))
check("so the write-through guard matches it",
      A.profile_name("Mac 4K (day)") in A.list_profiles())
check("and deleting it works", A.delete_profile("Mac 4K (day)")
      and A.profile_name("Mac 4K (day)") not in A.list_profiles())

# ---- an arrangement must not move the screens it was saved to keep ----------
tight = normalize_config(desk(), LIVE)
tight["devices"][0]["displays"][0]["x"] = 2560 + 14   # a hair off touching
tight = normalize_config(tight, LIVE)
was = [(d["id"], s["x"], s["y"]) for d in tight["devices"]
       for s in d["displays"]]
A.save_profile(tight, "Tight")
again = normalize_config(A.load_profile("Tight", tight), LIVE)
now = [(d["id"], s["x"], s["y"]) for d in again["devices"]
       for s in d["displays"]]
check("loading an arrangement leaves every screen exactly where it was",
      was == now, f"{was} -> {now}")
A.delete_profile("Tight")

# ---- an edit made to a selected arrangement belongs to it ------------------
# The trap this closes: duplicate, spend ten minutes arranging screens, switch
# to the other one to compare -- and come back to find the ten minutes gone,
# because the file still held the desk as it was at the moment of duplication.
scratch = os.path.join(tempfile.mkdtemp(prefix="openspan-live-"), "live.json")
real_config, A.CONFIG = A.CONFIG, scratch

canvas = A.MultiArrangeCanvas.__new__(A.MultiArrangeCanvas)
canvas._told_portal = None
canvas.target_states = {}
canvas.on_change = None
canvas.adopt(desk(), LIVE)
A.save_profile(canvas.config, "Working")
canvas.config["profile"] = "Working"

canvas.targets[1]["displays"][0]["res_w"] = 2560     # he changes the Mac to 2K
canvas.save()
check("editing a selected arrangement writes through to it",
      A.load_profile("Working", canvas.config)["devices"][1]
      ["displays"][0]["res_w"] == 2560,
      str(A.load_profile("Working", canvas.config)["devices"][1]
          ["displays"][0]["res_w"]))

canvas.config.pop("profile", None)
canvas.targets[1]["displays"][0]["res_w"] = 1920
canvas.save()
check("and an unnamed desk writes through to nothing",
      A.load_profile("Working", canvas.config)["devices"][1]
      ["displays"][0]["res_w"] == 2560)

A.delete_profile("Working")
canvas.config["profile"] = "Working"
canvas.save()
check("a deleted arrangement is not resurrected by the next save",
      A.list_profiles() == ["Mac 4K"], str(A.list_profiles()))
A.CONFIG = real_config

# ---- a device's own settings do not travel with the desk -------------------
# The bug this closes, live on 2 August: three devices tuned to the 0.75 notch,
# one arrangement switch, all three reverted to that arrangement's stored
# numbers. MACHINE_FIELDS excluded only radio and port, so every other field in
# a device record -- sensitivity, key mapping, scroll direction, the device's
# own name -- was saved into the picture of the desk and restored from it.
#
# Everything below is bound to the SHIPPED constants and the SHIPPED whitelist.
# Nothing here re-states the field list: a copy typed into a test passes while
# the real record grows a field nobody classified, which is the failure mode
# this file exists to make impossible.
import ast  # noqa: E402
import json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def device_whitelist():
    """Every key a device record can hold, read out of normalize_config.

    normalize_config builds each device from one dict literal, so that literal
    IS the complete field list. Reading it here rather than listing the fields
    means a field added there without being classified fails this test on the
    day it is added, not on the day it loses somebody's tuning."""
    with open(os.path.join(HERE, "openspan_targets.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "normalize_config")
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and getattr(node.func.value, "id", "") == "devices"
                and node.args and isinstance(node.args[0], ast.Dict)):
            return {key.value for key in node.args[0].keys
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)}
    return set()


WHITELIST = device_whitelist()
classified = {A.DEVICE_KEY} | set(A.DEVICE_FIELDS) | set(A.ARRANGEMENT_FIELDS)

check("the shipped whitelist was actually found",
      len(WHITELIST) > 5, str(sorted(WHITELIST)))
check("every field a device record can hold is classified",
      WHITELIST <= classified,
      "unclassified: " + str(sorted(WHITELIST - classified))
      + " -- add it to DEVICE_FIELDS or ARRANGEMENT_FIELDS in openspan.py")
check("and nothing is classified that a device record cannot hold",
      classified <= WHITELIST,
      "named but never built: " + str(sorted(classified - WHITELIST)))
check("no field is claimed by both sides at once",
      not (set(A.DEVICE_FIELDS) & set(A.ARRANGEMENT_FIELDS)),
      str(sorted(set(A.DEVICE_FIELDS) & set(A.ARRANGEMENT_FIELDS))))


def poison(value):
    """A value of the right shape that is definitely not the live one."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 111
    if isinstance(value, float):
        return round(value * 0.5 + 0.13, 3)
    if isinstance(value, str):
        return "POISONED-" + value
    return {"alt": "cmd"}          # None or {} -> an explicit override


# A live desk, tuned by hand the way Doug tuned his.
live = normalize_config(desk(), LIVE)
for device in live["devices"]:
    device["sensitivity"] = 0.75

# A profile written the OLD way: a full snapshot of every device field, holding
# the values the live desk has since been tuned away from. This is exactly what
# the three arrangements on his disk still contain.
legacy = json.loads(json.dumps(live))
legacy["profile"] = "Legacy"
for device in legacy["devices"]:
    for field in WHITELIST - {A.DEVICE_KEY} - set(A.ARRANGEMENT_FIELDS):
        device[field] = poison(device.get(field))
    # Not from the loop above: the loop is steered by the shipped constants, so
    # moving a field to the arrangement side would quietly stop poisoning it and
    # the test would go green on the exact mistake it exists to catch. 0.686 is
    # the real number "Mac 2k" put back on his iPad.
    device["sensitivity"] = 0.686
with open(A._profile_path("Legacy"), "w", encoding="utf-8") as handle:
    json.dump(legacy, handle, indent=2)

restored = A.load_profile("Legacy", live)
by_id = {d[A.DEVICE_KEY]: d for d in restored["devices"]}
stale = []
for was in live["devices"]:
    got = by_id.get(was[A.DEVICE_KEY], {})
    for field in WHITELIST - {A.DEVICE_KEY} - set(A.ARRANGEMENT_FIELDS):
        if got.get(field) != was.get(field):
            stale.append(f"{was[A.DEVICE_KEY]}.{field}: "
                         f"{was.get(field)!r} -> {got.get(field)!r}")
check("loading an arrangement restores NO field the live device owns",
      not stale, "; ".join(stale))
check("including the one that was actually lost -- the 0.75 notch survives a "
      "switch to an arrangement saved at 0.686",
      all(d.get("sensitivity") == 0.75 for d in restored["devices"]),
      str([d.get("sensitivity") for d in restored["devices"]]))
check("the field count is not zero, so the check above cannot pass by "
      "iterating over nothing",
      len(WHITELIST - {A.DEVICE_KEY} - set(A.ARRANGEMENT_FIELDS)) >= 10,
      str(len(WHITELIST - {A.DEVICE_KEY} - set(A.ARRANGEMENT_FIELDS))))

# ...while the arrangement itself is still restored, or the profile would be
# carrying nothing at all.
moved = json.loads(json.dumps(live))
moved["devices"][1]["displays"][0]["x"] = -9000
moved["profile"] = "Moved"
with open(A._profile_path("Moved"), "w", encoding="utf-8") as handle:
    json.dump(moved, handle, indent=2)
back = A.load_profile("Moved", live)
check("but the screens themselves ARE restored -- that is what a profile is",
      back["devices"][1]["displays"][0]["x"] == -9000,
      str(back["devices"][1]["displays"][0]["x"]))

# The one place a stale value could still have come from: a device that the
# live desk no longer has, so there is nothing to take the value from. It is
# deleted rather than left as the file found it.
orphaned = A.load_profile("Legacy", {"devices": [], "monitors": []})
leaked = [f"{d.get(A.DEVICE_KEY)}.{f}={d[f]!r}" for d in orphaned["devices"]
          for f in A.DEVICE_FIELDS if f in d]
check("a device the desk no longer has brings back none of it either",
      not leaked, "; ".join(leaked))
check("and it still loads, with its screens, ready to be set up again",
      len(orphaned["devices"]) == 2
      and all(d.get("displays") for d in orphaned["devices"]),
      str([len(d.get("displays", [])) for d in orphaned["devices"]]))
healed = normalize_config(orphaned, LIVE)
check("normalisation then gives it the defaults, not the file's numbers",
      all(d["sensitivity"] == 1.0 and d["pointer_gain"] == 1.0
          for d in healed["devices"]),
      str([(d["sensitivity"], d["pointer_gain"]) for d in healed["devices"]]))

# Re-saving is what migrates the three arrangements already on disk: the write
# path strips what the read path now ignores.
A.save_profile(live, "Legacy")
with open(A._profile_path("Legacy"), encoding="utf-8") as handle:
    rewritten = json.load(handle)
check("re-saving an old arrangement strips it, so the stale copy stops "
      "existing at all",
      not [f for d in rewritten["devices"] for f in A.DEVICE_FIELDS if f in d],
      str([{k: v for k, v in d.items() if k in A.DEVICE_FIELDS}
           for d in rewritten["devices"]]))
A.delete_profile("Legacy")
A.delete_profile("Moved")

shutil.rmtree(A.PROFILE_DIR, ignore_errors=True)
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
