"""Named arrangements: saving one, loading one, and switching between them.

Doug, 28 July: *"I sometimes change my managed mac's landscape screen to 2k
resolution. So I need to be able to duplicate the current arrangement and then
resize/arrange all screens and devices attached to it."*

A resolution change is not a small edit. Every distance on that screen is
derived from it, the physical size that screen is drawn at is derived from it,
and the crossing bands on its edges are derived from those -- so switching the
Mac between 4K and 2K means re-entering the desk, or keeping two of them.

The two things that can go wrong here are both invisible until the moment they
bite:

    * a profile that carries RADIO and PORT would move a dongle assignment along
      with a picture of the desk. Bonds live on the guest per radio, so loading
      an arrangement saved when the Mac was on a different dongle would point
      the lane at a radio holding no bond for it -- a device that pairs, goes
      green, and does nothing.
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

shutil.rmtree(A.PROFILE_DIR, ignore_errors=True)
print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
