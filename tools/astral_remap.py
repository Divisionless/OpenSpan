"""One-shot Astral Compass palette remap for the win/ side.

Every replacement is exact-hex. Kit tokens are used verbatim where a token
fits; values marked (derived) are interpolations that preserve the ORIGINAL
file's contrast relationships (rest < hover < press, BG < panel < card),
because those deltas are shipped UX, not decoration. Functional state colors
(the amber idle/warn family) are deliberately NOT rebranded: they carry
meaning, not identity. Idempotent: running twice changes nothing.
"""
import pathlib
import sys

REMAP = {
    # backgrounds / neutrals -> void/night/panel ladder
    "#14161c": "#080B10",  # BG -> void900
    "#1d212b": "#0D0D12",  # PANEL -> night850
    "#232936": "#17161C",  # CARD -> panel800
    "#1b1f2b": "#0B0B11",  # hover fill (derived, keeps BG<hover<PANEL)
    "#3a4358": "#4A4556",  # hover line (derived)
    "#0a0b0e": "#05070C",  # scrim -> void950
    "#39435a": "#3A3346",  # modal border (derived stardust line)
    "#0b0d12": "#05080D",  # (derived void step)
    "#0e1015": "#06090E",  # (derived void step)
    "#101014": "#070A0F",  # (derived void step)
    "#12141a": "#070A10",  # (derived void step)
    # text
    "#dfe4ee": "#E6E6F0",  # FG -> lunar100
    "#8b93a7": "#A6A1B0",  # MUTED -> mist400
    "#c9d4ec": "#F5F5F8",  # bright heading tint -> lunar50
    "#b7c0d4": "#B8B3C2",  # secondary text (derived mist-light)
    "#5b6172": "#6E687A",  # dim text/lines (derived mist-dark)
    # greens -> arcane
    "#3fdc8a": "#8A5CFF",  # ACCENT -> arcane500
    "#1f6f43": "#5F3DC4",  # ACCENT_DIM / connected fill -> arcane700
    "#2a8f5c": "#764BE2",  # accent hover (derived 700->500 midpoint)
    "#35ad70": "#8A5CFF",  # accent press -> arcane500 (top of ramp)
    "#1a7f37": "#B28DFF",  # positive text -> arcane300
    "#d6ffe9": "#D8C8FF",  # pale positive -> arcane100
    "#eafff3": "#F1EBFF",  # on-accent text (derived arcane-tinted white)
    "#6f9e83": "#8F7BC4",  # muted positive (derived)
    # danger -> alert family
    "#e06c68": "#FF5CA8",  # DANGER -> alert500
    "#c9433f": "#E04A92",  # (derived)
    "#53292a": "#4A1F38",  # danger rest (derived dark)
    "#6e3335": "#66294C",  # danger hover (derived)
    "#8b4043": "#8A3566",  # danger press (derived)
    "#ffd9d6": "#FFD9EC",  # pale danger text (derived)
    # blues / violets -> arcane-void
    "#26324c": "#1D1930",  # monitor fill (derived)
    "#4a6ea8": "#6B5CA8",  # monitor line (derived)
    "#243049": "#1C182E",  # (derived)
    "#2b2940": "#262038",  # (derived)
    "#756bb1": "#8A5CFF",  # violet accent -> arcane500
    "#a78bfa": "#B28DFF",  # PORTAL -> arcane300
    "#c8c1ef": "#D8C8FF",  # pale violet -> arcane100
    "#6cc6ff": "#B28DFF",  # cyan info -> arcane300
    "#2b313d": "#17161C",  # off fill -> panel800
    "#4a5468": "#3E3A4A",  # off line (derived)
    # neutral press ramp
    "#3d4860": "#2A203B",  # PRESS -> stardust700
    "#2d3444": "#221F2A",  # neutral hover (derived toward stardust)
}

win = pathlib.Path(__file__).parent.parent / "win"
total = 0
for path in sorted(win.glob("*.py")):
    text = original = path.read_text(encoding="utf-8")
    for old, new in REMAP.items():
        text = text.replace(f'"{old}"', f'"{new}"')
    if text != original:
        path.write_text(text, encoding="utf-8")
        changed = sum(original.count(f'"{o}"') for o in REMAP)
        total += changed
        print(f"{path.name}: {changed} literals remapped")
print(f"TOTAL: {total}")
sys.exit(0)
