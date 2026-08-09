"""Astral Compass rebrand guards.

The identity is tokens, not vibes: these checks pin the app to the brand
kit's values and keep the old palette from creeping back in a merge."""

import pathlib
import re


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


ROOT = pathlib.Path(__file__).parent.parent
app = (ROOT / "win" / "openspan.py").read_text(encoding="utf-8")

check("core kit tokens are the palette",
      all(t in app for t in
          ("#080B10", "#0D0D12", "#17161C", "#8A5CFF", "#5F3DC4",
           "#B28DFF", "#E6E6F0", "#A6A1B0", "#FF5CA8", "#2A203B")))
check("the old green accent family is gone",
      not any(t in app for t in ("#3fdc8a", "#1f6f43", "#2a8f5c", "#35ad70")))
check("the functional amber idle family survives the rebrand",
      "#f5c451" in app and "#413615" in app)
check("the product name defaults to EsotericOS",
      '"app_label", "EsotericOS"' in app)
check("the wordmark is a two-tone lockup",
      'text="Esoteric"' in app and 'text="OS"' in app)
check("fonts resolve Inter with a Segoe fallback, per root",
      app.count("_resolve_brand_fonts(root)") == 3  # def + both Tk roots
      and '"Inter"' in app)
check("no widget still hardcodes Segoe",
      '("Segoe UI"' not in app and '("Segoe UI Semibold"' not in app)
check("the app icon is the kit export with a legacy fallback",
      'brand", "esotericos-app.ico"' in app and "openspan.ico" in app)
check("plumbing names stay OpenSpan (bonds, units, VM, guest paths)",
      '"vm_name", "OpenSpan"' in app and "/opt/openspan/" in app)

for asset in ("brand/esotericos-app.ico", "brand/esotericos-tray.ico"):
    check(f"{asset} ships in the tree", (ROOT / asset).is_file())

tray = (ROOT / "brand" / "esotericos-tray.ico").read_bytes()
check("tray ico carries all six size-specific frames",
      tray[4] == 6 and tray[6] == 16 and tray[6 + 16] == 20)
