"""Physical panel size, read from EDID.

The arrangement canvas draws every screen to real inches, so it needs to know
how big each panel actually is. Windows will not tell it. Nothing in GDI or
DisplayConfig reports physical size -- the pixel count is all Windows knows,
and a diagonal is a property of the glass, not of the mode. That is why the
diagonal has been typed in by hand up to now.

The panel itself does know. Every EDID base block carries the maximum
horizontal and vertical image size in whole centimetres at bytes 0x15 and
0x16, and Windows keeps each attached panel's raw EDID in the registry under
the DISPLAY enum key. So the size is already on this machine; it simply is not
exposed through any display API.

Two EDID conventions matter. A zero in both bytes means the panel declined to
state a size (projectors do this). A zero in exactly one byte is a different
thing entirely: the pair then encodes an ASPECT RATIO, not a size, so reading
the surviving byte as centimetres would invent a measurement. Both cases are
reported as unknown here.

The join from a Windows device name to a panel already exists in
monitor_identity: an attached identity carries the EDID manufacturer id and
product code, and the registry key under DISPLAY is exactly those concatenated
("CXK" + "C004" -> "CXKC004"). Two identical panels share that key, but they
also share a size, so the ambiguity costs nothing here.

Everything in this module is advisory. A caller that cannot get a size falls
back to the typed diagonal, so no failure in here is allowed to propagate.
"""

from __future__ import annotations

import math

# The eight-byte fixed header that opens every valid EDID base block.
_EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
_EDID_BASE_BLOCK = 128
_MAX_H_IMAGE_SIZE = 0x15
_MAX_V_IMAGE_SIZE = 0x16
# Bounds on a believable panel edge, in centimetres. A watch-sized 3 cm and a
# 300 cm wall are both well outside anything that sits on a desk, so a value
# beyond them is corruption rather than an unusual monitor.
_MIN_PLAUSIBLE_CM = 3
_MAX_PLAUSIBLE_CM = 300

_DISPLAY_ENUM = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
_DEVICE_PARAMETERS = "Device Parameters"

_CM_PER_INCH = 2.54


def decode_edid_size(edid: bytes) -> tuple[int, int] | None:
    """(width_cm, height_cm) from an EDID base block, or None if unknowable.

    None covers every case where a number would have to be invented: a block
    that is not EDID, one shorter than the 128-byte base block, a panel that
    declared no size, the aspect-ratio encoding where exactly one byte is zero,
    and a size outside plausible bounds."""
    if edid is None:
        return None
    try:
        raw = bytes(edid)
    except (TypeError, ValueError):
        return None
    if len(raw) < _EDID_BASE_BLOCK or raw[:8] != _EDID_HEADER:
        return None

    width_cm = raw[_MAX_H_IMAGE_SIZE]
    height_cm = raw[_MAX_V_IMAGE_SIZE]
    # Either byte alone being zero disqualifies the pair: both zero is "size
    # not stated", one zero is an aspect ratio wearing the size field's clothes.
    if width_cm == 0 or height_cm == 0:
        return None
    for value in (width_cm, height_cm):
        if value < _MIN_PLAUSIBLE_CM or value > _MAX_PLAUSIBLE_CM:
            return None
    return (width_cm, height_cm)


def diagonal_inches(w_cm: int, h_cm: int) -> float:
    """The diagonal of a w_cm by h_cm panel, in inches, to one decimal.

    One decimal is the honest precision: EDID states the edges in whole
    centimetres, so the diagonal is already carrying about half a centimetre of
    rounding before it is converted."""
    return round(math.hypot(float(w_cm), float(h_cm)) / _CM_PER_INCH, 1)


def _subkey_names(winreg, key) -> list[str]:
    """Every subkey name under an open registry key, best effort.

    Enumeration runs to exhaustion rather than to the reported subkey count,
    because a driver can rewrite the key mid-walk; a short list is a perfectly
    good answer where an exception is not."""
    names = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            break
        except Exception:                                       # noqa: BLE001
            break
        index += 1
    return names


def registry_sizes() -> dict[str, tuple[int, int]]:
    """PNP id ("CXKC004") -> (width_cm, height_cm) for every panel Windows has
    stored an EDID for.

    Reads only, and never raises: a denied key, a missing hive or a corrupt
    EDID subtracts that one panel from the result and nothing else. Silent by
    design -- this runs behind a UI that has a working fallback, so a log line
    per unreadable key would be noise about a condition the user cannot act
    on."""
    sizes: dict[str, tuple[int, int]] = {}
    try:
        import winreg
    except Exception:                                           # noqa: BLE001
        return sizes

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_ENUM) as root:
            for pnp in _subkey_names(winreg, root):
                if pnp.upper() in sizes:
                    continue
                try:
                    panel = winreg.OpenKey(root, pnp)
                except OSError:
                    continue
                with panel:
                    for instance in _subkey_names(winreg, panel):
                        size = _instance_size(winreg, panel, instance)
                        if size is not None:
                            sizes[pnp.upper()] = size
                            # Every instance of one PNP id is the same model,
                            # so the first readable EDID settles the size.
                            break
    except Exception:                                           # noqa: BLE001
        return sizes
    return sizes


def _instance_size(winreg, panel_key, instance: str) -> tuple[int, int] | None:
    """The decoded size behind one instance of a panel, or None.

    An instance that has never been attached on this machine has no Device
    Parameters key at all, which is ordinary rather than exceptional."""
    try:
        with winreg.OpenKey(panel_key,
                            instance + "\\" + _DEVICE_PARAMETERS) as params:
            edid, _kind = winreg.QueryValueEx(params, "EDID")
    except OSError:
        return None
    except Exception:                                           # noqa: BLE001
        return None
    return decode_edid_size(edid)


def physical_diagonals() -> dict[str, dict]:
    """GDI device name -> what is known about that panel's physical size.

    Each value is {"diagonal_in", "width_cm", "height_cm", "pnp", "source"}.
    The three measurements are None when the panel's size could not be
    established; the entry still appears, because "attached and unmeasured" is
    a different fact from "not attached" and the caller distinguishes them.

    Returns {} on any failure whatsoever -- monitor_identity missing, the
    DisplayConfig walk failing, the registry denied. The caller falls back to
    the typed diagonal, and that fallback must never be reached through an
    exception this module raised."""
    try:
        import monitor_identity

        sizes = registry_sizes()
        found: dict[str, dict] = {}
        for identity in monitor_identity.attached_identities():
            if not identity.device_name:
                continue
            pnp = (identity.manufacturer_id + identity.product_code).upper()
            size = sizes.get(pnp) if pnp else None
            width_cm, height_cm = size if size else (None, None)
            found[identity.device_name] = {
                "diagonal_in": (diagonal_inches(width_cm, height_cm)
                                if size else None),
                "width_cm": width_cm,
                "height_cm": height_cm,
                "pnp": pnp,
                "source": "edid",
            }
        return found
    except Exception:                                           # noqa: BLE001
        return {}


if __name__ == "__main__":
    for name, panel in sorted(physical_diagonals().items()):
        if panel["diagonal_in"] is None:
            print(f"{name}  {panel['pnp'] or 'no-edid'}  size unknown")
        else:
            print(f"{name}  {panel['pnp']}  "
                  f"{panel['width_cm']}x{panel['height_cm']}cm  "
                  f"{panel['diagonal_in']}\"")
