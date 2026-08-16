"""Guards on reading a panel's physical size out of its EDID.

The arrangement draws to real inches, and until now every diagonal was typed in
by hand because Windows does not report physical size. EDID does, so the number
can be read instead of asked for -- but only if the reading is exact. A decoder
that is merely usually right is worse than the typing it replaces, because a
wrong size looks like a correct one on the canvas.

So the decode is pinned against hand-built base blocks, including the two EDID
conventions that would otherwise silently fabricate a measurement: a size of
zero, and the aspect-ratio encoding that lives in the same two bytes.

The live half runs against whatever is plugged into this machine, and it is
deliberately arranged so the negative-origin monitors are covered rather than
only the primary at (0,0). Doug's desk puts two of three panels at negative
coordinates; a check that only ever exercised the primary would have proved
nothing about the two screens most likely to be mis-sized. The fixed desk below
makes that coverage machine-independent, and the live checks then confirm the
same shape against the real hardware.
"""

import ast
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import monitor_edid as me            # noqa: E402
import monitor_identity as mi        # noqa: E402


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def edid(width_cm, height_cm, header=HEADER, length=128):
    """A 128-byte base block carrying nothing but a header and an image size --
    every other byte is zero, because no other byte is read."""
    block = bytearray(length)
    block[0:len(header)] = header
    if length > 0x16:
        block[0x15] = width_cm
        block[0x16] = height_cm
    return bytes(block)


# ---- decoding a base block -----------------------------------------------------

check("a valid base block yields its image size in whole centimetres",
      me.decode_edid_size(edid(38, 21)) == (38, 21))
check("the other two panels on this desk decode the same way",
      me.decode_edid_size(edid(35, 19)) == (35, 19)
      and me.decode_edid_size(edid(70, 39)) == (70, 39))

check("a block that does not open with the EDID header is not EDID",
      me.decode_edid_size(edid(38, 21, header=b"\x00\xff\xff\xff\xff\xff\xff\x01"))
      is None
      and me.decode_edid_size(edid(38, 21, header=b"\x00" * 8)) is None)

check("a panel that declares no size at all is unknown, not zero",
      me.decode_edid_size(edid(0, 0)) is None)

# One zero byte is not a small panel: EDID reuses the pair to encode an aspect
# ratio, so reading the surviving byte as centimetres would invent a size.
check("the aspect-ratio encoding is rejected rather than read as a size",
      me.decode_edid_size(edid(0, 21)) is None
      and me.decode_edid_size(edid(38, 0)) is None)

check("anything shorter than the 128-byte base block is unreadable",
      me.decode_edid_size(edid(38, 21, length=127)) is None
      and me.decode_edid_size(b"") is None
      and me.decode_edid_size(HEADER) is None)

check("an implausible edge is corruption, not an unusual monitor",
      me.decode_edid_size(edid(250, 1)) is None
      and me.decode_edid_size(edid(1, 250)) is None
      and me.decode_edid_size(edid(2, 2)) is None)
check("the plausibility floor is inclusive, so 3 cm still decodes",
      me.decode_edid_size(edid(3, 3)) == (3, 3))

check("nothing at all decodes to a size",
      me.decode_edid_size(None) is None
      and me.decode_edid_size(object()) is None)


# ---- centimetres to the number the user recognises -----------------------------

# These three are measured, not derived: they are the panels attached to this
# machine, and each rounds to the diagonal printed on the box.
check("38x21cm is the 17-inch panel", me.diagonal_inches(38, 21) == 17.1)
check("35x19cm is the CF15T", me.diagonal_inches(35, 19) == 15.7)
check("70x39cm is the 32-inch panel", me.diagonal_inches(70, 39) == 31.5)
check("the diagonal is a hypotenuse in inches, to one decimal",
      me.diagonal_inches(60, 34)
      == round(math.hypot(60.0, 34.0) / 2.54, 1))


# ---- the join, over a desk with monitors at negative origins -------------------
#
# Substituting the identity source pins the arrangement instead of inheriting
# whatever is plugged in, so the negative-origin cases are exercised on every
# machine this ever runs on -- not only on the desk they were found on.

def panel(device, manufacturer, product, x, y):
    return mi.MonitorIdentity(
        manufacturer_id=manufacturer, product_code=product,
        friendly_name="", native_width=1920, native_height=1080,
        device_name=device, virtual_x=x, virtual_y=y)


DESK = [
    panel(r"\\.\DISPLAY5", "CXK", "C004", 0, 0),          # primary, at origin
    panel(r"\\.\DISPLAY1", "CMN", "1760", 4, -1080),      # negative y
    panel(r"\\.\DISPLAY4", "CXK", "C004", -1920, 0),      # negative x
]
DESK_SIZES = {"CXKC004": (35, 19), "CMN1760": (38, 21)}

real_identities = mi.attached_identities
real_sizes = me.registry_sizes
try:
    mi.attached_identities = lambda: list(DESK)
    me.registry_sizes = lambda: dict(DESK_SIZES)
    fixed = me.physical_diagonals()
finally:
    mi.attached_identities = real_identities
    me.registry_sizes = real_sizes

check("every screen on the desk is sized, not only the primary at (0,0)",
      sorted(fixed) == [r"\\.\DISPLAY1", r"\\.\DISPLAY4", r"\\.\DISPLAY5"]
      and fixed[r"\\.\DISPLAY5"]["diagonal_in"] == 15.7
      and fixed[r"\\.\DISPLAY1"]["diagonal_in"] == 17.1
      and fixed[r"\\.\DISPLAY4"]["diagonal_in"] == 15.7)
check("a monitor at a negative origin carries its centimetres and PNP id",
      fixed[r"\\.\DISPLAY4"]["width_cm"] == 35
      and fixed[r"\\.\DISPLAY4"]["height_cm"] == 19
      and fixed[r"\\.\DISPLAY4"]["pnp"] == "CXKC004"
      and fixed[r"\\.\DISPLAY1"]["pnp"] == "CMN1760")
# Twins share a PNP key, so the join cannot tell them apart -- and does not need
# to, because two panels of one model are two panels of one size.
check("twin panels either side of the origin agree on their size",
      fixed[r"\\.\DISPLAY4"]["diagonal_in"]
      == fixed[r"\\.\DISPLAY5"]["diagonal_in"])

try:
    mi.attached_identities = lambda: list(DESK)
    me.registry_sizes = lambda: {}
    unmeasured = me.physical_diagonals()
finally:
    mi.attached_identities = real_identities
    me.registry_sizes = real_sizes

check("a panel with no stored EDID is present and unmeasured, never guessed",
      len(unmeasured) == 3
      and all(row["diagonal_in"] is None and row["width_cm"] is None
              and row["height_cm"] is None for row in unmeasured.values()))


def exploding():
    raise OSError("registry denied")


try:
    mi.attached_identities = exploding
    broken = me.physical_diagonals()
finally:
    mi.attached_identities = real_identities

check("a failure anywhere underneath yields {}, never an exception",
      broken == {})


# ---- the live machine ----------------------------------------------------------

sizes = me.registry_sizes()
check("registry_sizes returns a dict", isinstance(sizes, dict))
check("its keys are PNP ids: three letters and four hex digits",
      all(isinstance(key, str) and re.fullmatch(r"[A-Z]{3}[0-9A-F]{4}", key)
          for key in sizes))
check("its values are plausible centimetre pairs",
      all(isinstance(value, tuple) and len(value) == 2
          and all(isinstance(n, int) and 3 <= n <= 300 for n in value)
          for value in sizes.values()))

live = me.physical_diagonals()
check("physical_diagonals returns a dict", isinstance(live, dict))
check("every entry carries the full record, whether or not it is measured",
      all(set(row) == {"diagonal_in", "width_cm", "height_cm", "pnp", "source"}
          and row["source"] == "edid" and isinstance(row["pnp"], str)
          for row in live.values()))
check("no measured diagonal is outside the range a display can occupy",
      all(3 <= row["diagonal_in"] <= 120 for row in live.values()
          if row["diagonal_in"] is not None))

# Guarded on presence so the file stays honest on another machine: where this
# panel is attached, its size is the one measured off its own EDID.
if r"\\.\DISPLAY5" in live:
    check("the attached CF15T reads 15.7 inches off its own EDID",
          live[r"\\.\DISPLAY5"]["diagonal_in"] == 15.7)

attached = mi.attached_identities()
negatives = [one for one in attached if one.virtual_x < 0 or one.virtual_y < 0]
if negatives:
    check("the screens at negative origins are read here too, not skipped",
          all(one.device_name in live for one in negatives))
    check("a negatively-placed panel with an EDID on file is measured",
          all(live[one.device_name]["diagonal_in"] is not None
              for one in negatives
              if (one.manufacturer_id + one.product_code).upper() in sizes))


# ---- structural ----------------------------------------------------------------

source = pathlib.Path(me.__file__).read_text(encoding="utf-8")
tree = ast.parse(source)

imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and not node.level:
        imported.add((node.module or "").split(".")[0])

# This is read at startup and from a probe, so it must not drag in the app or a
# toolkit to answer a question about centimetres.
check("the module pulls in neither the app nor a UI toolkit",
      "openspan" not in imported and "tkinter" not in imported
      and not any(name.startswith("openspan") for name in imported)
      and "tkinter" not in sys.modules)

func = next(node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "physical_diagonals")
statements = [node for node in func.body
              if not (isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Constant)
                      and isinstance(node.value.value, str))]
guarded = (len(statements) == 1 and isinstance(statements[0], ast.Try)
           and bool(statements[0].handlers)
           and all(any(isinstance(inner, ast.Return)
                       and isinstance(inner.value, ast.Dict)
                       and not inner.value.keys
                       for inner in ast.walk(handler))
                   for handler in statements[0].handlers))
check("the whole of physical_diagonals sits under one try that returns {}",
      guarded)

check("the platform imports stay inside functions, as elsewhere in win/",
      all(line.startswith(" ") for line in source.splitlines()
          if re.match(r"\s*import (winreg|monitor_identity)", line)))

print("RESULT: ALL PASS")
