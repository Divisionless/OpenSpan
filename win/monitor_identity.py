"""Durable identity for attached displays.

Monitor handles and GDI device names (``\\\\.\\DISPLAY1``) are reassigned freely by
Windows, so nothing a profile remembers may be keyed on them. What survives a
reboot, a cable swap or a dock disconnect is the EDID identity behind the port,
so that is what a display profile stores and matches on.

The module is in two halves. The pure core carries the identity record and every
matching rule and imports nothing platform-specific, so it stays importable (and
testable) off Windows. The Windows fill below reads the live identities through
user32 and imports ctypes only when called.
"""

from __future__ import annotations

import dataclasses
import hashlib
import enum

# ---- pure core -----------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class MonitorIdentity:
    """A display described by what persists, plus the volatile fields kept for
    diagnostics only."""

    manufacturer_id: str = ""
    product_code: str = ""
    serial_number: str = ""
    friendly_name: str = ""
    native_width: int = 0
    native_height: int = 0
    device_name: str = ""
    # Position in the virtual desktop at capture time. A last-resort tiebreaker
    # only, because arrangement is exactly what a display profile changes.
    virtual_x: int = 0
    virtual_y: int = 0

    @property
    def has_serial(self) -> bool:
        return len(self.serial_number) > 0

    @property
    def has_edid(self) -> bool:
        return len(self.manufacturer_id) > 0 and len(self.product_code) > 0

    @property
    def stable_key(self) -> str | None:
        """A durable key: EDID with serial, else EDID plus native size, else the
        friendly name. None when nothing durable is known — callers fall back to
        positional matching rather than inventing a key."""
        if self.has_edid and self.has_serial:
            seed = f"{self.manufacturer_id}:{self.product_code}:{self.serial_number}"
        elif self.has_edid:
            seed = (f"{self.manufacturer_id}:{self.product_code}:"
                    f"{self.native_width}x{self.native_height}")
        elif self.friendly_name:
            seed = (f"name:{self.friendly_name}:"
                    f"{self.native_width}x{self.native_height}")
        else:
            return None
        return hashlib.sha256(seed.lower().encode("utf-8")).hexdigest()[:16]

    def describe(self) -> str:
        if self.friendly_name:
            return self.friendly_name
        if self.has_edid:
            return f"{self.manufacturer_id} {self.product_code}"
        return self.device_name


class MonitorMatch(enum.IntEnum):
    NONE = 0
    POSITION = 1     # only where it sits in the virtual desktop
    NAME = 2         # same friendly name and native resolution
    MODEL = 3        # same EDID manufacturer, product and native resolution
    SERIAL = 4       # same EDID serial — this is the physical panel


def _same_native_size(a: MonitorIdentity, b: MonitorIdentity) -> bool:
    return a.native_width == b.native_width and a.native_height == b.native_height


def score(remembered: MonitorIdentity, candidate: MonitorIdentity) -> MonitorMatch:
    """How strongly a candidate answers to a remembered monitor."""
    if (remembered.has_serial and candidate.has_serial
            and remembered.serial_number.lower() == candidate.serial_number.lower()
            and remembered.manufacturer_id.lower() == candidate.manufacturer_id.lower()):
        return MonitorMatch.SERIAL

    if (remembered.has_edid and candidate.has_edid
            and remembered.manufacturer_id.lower() == candidate.manufacturer_id.lower()
            and remembered.product_code.lower() == candidate.product_code.lower()
            and _same_native_size(remembered, candidate)):
        return MonitorMatch.MODEL

    if (remembered.friendly_name
            and remembered.friendly_name.lower() == candidate.friendly_name.lower()
            and _same_native_size(remembered, candidate)):
        return MonitorMatch.NAME

    if (remembered.virtual_x == candidate.virtual_x
            and remembered.virtual_y == candidate.virtual_y
            and _same_native_size(remembered, candidate)):
        return MonitorMatch.POSITION

    return MonitorMatch.NONE


def assign(remembered: list[MonitorIdentity], attached: list[MonitorIdentity],
           minimum: MonitorMatch = MonitorMatch.POSITION) -> dict[int, int]:
    """Pair remembered monitors with attached ones, strongest matches first and
    each attached monitor claimed once. Two identical panels with no serials are
    separated by position, which is why position stays in the ladder despite
    being weak. Ties break on the remembered index then the attached index, so
    repeated runs over the same input always produce the same pairing."""
    scored = []
    for r, one in enumerate(remembered):
        for a, other in enumerate(attached):
            strength = score(one, other)
            if strength >= minimum:
                scored.append((r, a, strength))
    scored.sort(key=lambda item: (-item[2], item[0], item[1]))

    assignment: dict[int, int] = {}
    used: set[int] = set()
    for r, a, _ in scored:
        if r in assignment or a in used:
            continue
        assignment[r] = a
        used.add(a)
    return assignment


def topology_changed(remembered: list[MonitorIdentity],
                     attached: list[MonitorIdentity]) -> bool:
    """True when the attached set differs from the remembered set in a way that
    should trigger restoration — a monitor arrived, left, or was replaced."""
    if len(remembered) != len(attached):
        return True
    return len(assign(remembered, attached, MonitorMatch.NAME)) != len(remembered)


# ---- windows fill --------------------------------------------------------------

_QDC_ONLY_ACTIVE_PATHS = 0x00000002
_GET_SOURCE_NAME = 1
_GET_TARGET_NAME = 2
_ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
_MONITORINFOF_PRIMARY = 0x00000001

_native = None


def _load_native():
    """Build the user32 surface on first use, so the pure core above stays
    importable where ctypes or user32 do not exist.

    The library is opened on a private handle rather than through ``windll``:
    setting argtypes on the shared cached module would re-prototype the same
    functions for every other caller in the process."""
    global _native
    if _native is not None:
        return _native

    import ctypes
    import ctypes.wintypes as wt

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                    ("dwFlags", wt.DWORD), ("szDevice", ctypes.c_wchar * 32)]

    class DEVMODEW(ctypes.Structure):
        # The display flavour: the printer union members are replaced by
        # dmPosition/dmDisplayOrientation/dmDisplayFixedOutput over the same
        # sixteen bytes. Field order and widths follow wingdi.h exactly.
        _fields_ = [
            ("dmDeviceName", ctypes.c_wchar * 32),
            ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
            ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD),
            ("dmFields", wt.DWORD),
            ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
            ("dmDisplayOrientation", wt.DWORD), ("dmDisplayFixedOutput", wt.DWORD),
            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short),
            ("dmFormName", ctypes.c_wchar * 32),
            ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD),
            ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD),
            ("dmDisplayFlags", wt.DWORD), ("dmDisplayFrequency", wt.DWORD),
            ("dmICMMethod", wt.DWORD), ("dmICMIntent", wt.DWORD),
            ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD),
            ("dmReserved1", wt.DWORD), ("dmReserved2", wt.DWORD),
            ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD),
        ]

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wt.DWORD), ("HighPart", ctypes.c_long)]

    class SOURCE_INFO(ctypes.Structure):
        _fields_ = [("adapterId", LUID), ("id", wt.DWORD),
                    ("modeInfoIdx", wt.DWORD), ("statusFlags", wt.DWORD)]

    class RATIONAL(ctypes.Structure):
        _fields_ = [("Numerator", wt.DWORD), ("Denominator", wt.DWORD)]

    class TARGET_INFO(ctypes.Structure):
        _fields_ = [("adapterId", LUID), ("id", wt.DWORD),
                    ("modeInfoIdx", wt.DWORD), ("outputTechnology", wt.DWORD),
                    ("rotation", wt.DWORD), ("scaling", wt.DWORD),
                    ("refreshRate", RATIONAL), ("scanLineOrdering", wt.DWORD),
                    ("targetAvailable", ctypes.c_long), ("statusFlags", wt.DWORD)]

    class PATH_INFO(ctypes.Structure):
        _fields_ = [("sourceInfo", SOURCE_INFO), ("targetInfo", TARGET_INFO),
                    ("flags", wt.DWORD)]

    class MODE_UNION(ctypes.Structure):
        # The mode union is never interpreted here; only its size matters,
        # because QueryDisplayConfig writes an array of these. Six 64-bit slots
        # reproduce the 48-byte union and its 8-byte alignment exactly.
        _fields_ = [(f"slot{i}", ctypes.c_ulonglong) for i in range(6)]

    class MODE_INFO(ctypes.Structure):
        _fields_ = [("infoType", wt.DWORD), ("id", wt.DWORD),
                    ("adapterId", LUID), ("mode", MODE_UNION)]

    class DEVICE_INFO_HEADER(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("size", wt.DWORD),
                    ("adapterId", LUID), ("id", wt.DWORD)]

    class SOURCE_DEVICE_NAME(ctypes.Structure):
        _fields_ = [("header", DEVICE_INFO_HEADER),
                    ("viewGdiDeviceName", ctypes.c_wchar * 32)]

    class TARGET_DEVICE_NAME(ctypes.Structure):
        _fields_ = [("header", DEVICE_INFO_HEADER), ("flags", wt.DWORD),
                    ("outputTechnology", wt.DWORD),
                    ("edidManufactureId", wt.WORD), ("edidProductCodeId", wt.WORD),
                    ("connectorInstance", wt.DWORD),
                    ("monitorFriendlyDeviceName", ctypes.c_wchar * 64),
                    ("monitorDevicePath", ctypes.c_wchar * 128)]

    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM)

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.EnumDisplayMonitors.restype = wt.BOOL
    u32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p,
                                        monitor_enum_proc, wt.LPARAM]
    u32.GetMonitorInfoW.restype = wt.BOOL
    u32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    u32.EnumDisplaySettingsExW.restype = wt.BOOL
    u32.EnumDisplaySettingsExW.argtypes = [wt.LPCWSTR, wt.DWORD,
                                           ctypes.POINTER(DEVMODEW), wt.DWORD]
    u32.GetDisplayConfigBufferSizes.restype = ctypes.c_long
    u32.GetDisplayConfigBufferSizes.argtypes = [wt.DWORD, ctypes.POINTER(wt.UINT),
                                                ctypes.POINTER(wt.UINT)]
    u32.QueryDisplayConfig.restype = ctypes.c_long
    u32.QueryDisplayConfig.argtypes = [wt.DWORD, ctypes.POINTER(wt.UINT),
                                       ctypes.POINTER(PATH_INFO),
                                       ctypes.POINTER(wt.UINT),
                                       ctypes.POINTER(MODE_INFO), ctypes.c_void_p]
    u32.DisplayConfigGetDeviceInfo.restype = ctypes.c_long
    u32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]

    _native = {
        "ctypes": ctypes, "u32": u32, "RECT": RECT,
        "MONITORINFOEXW": MONITORINFOEXW, "DEVMODEW": DEVMODEW,
        "PATH_INFO": PATH_INFO, "MODE_INFO": MODE_INFO,
        "SOURCE_DEVICE_NAME": SOURCE_DEVICE_NAME,
        "TARGET_DEVICE_NAME": TARGET_DEVICE_NAME,
        "MONITOR_ENUM_PROC": monitor_enum_proc, "UINT": wt.UINT,
    }
    return _native


def decode_manufacturer(edid_manufacture_id: int) -> str:
    """EDID packs three letters into two bytes, five bits each, stored
    big-endian. Anything that does not decode to three capitals is reported as
    unknown rather than as mojibake."""
    if not edid_manufacture_id:
        return ""
    value = ((edid_manufacture_id & 0xFF) << 8) | (edid_manufacture_id >> 8)
    letters = []
    for i in range(3):
        code = (value >> (10 - 5 * i)) & 0x1F
        if code < 1 or code > 26:
            return ""
        letters.append(chr(ord("A") + code - 1))
    return "".join(letters)


def _display_config_targets() -> dict[str, tuple[str, str, str]]:
    """GDI device name (lowercased) -> (manufacturer, product, friendly name).

    Failure is not fatal and returns an empty map: an identity without EDID
    falls back to friendly name and native size, and one with neither falls back
    to position. Nothing is invented to fill the gap."""
    n = _load_native()
    ctypes, u32 = n["ctypes"], n["u32"]

    path_count, mode_count = n["UINT"](), n["UINT"]()
    if u32.GetDisplayConfigBufferSizes(_QDC_ONLY_ACTIVE_PATHS,
                                       ctypes.byref(path_count),
                                       ctypes.byref(mode_count)) != 0:
        return {}
    if path_count.value == 0:
        return {}

    paths = (n["PATH_INFO"] * path_count.value)()
    modes = (n["MODE_INFO"] * max(1, mode_count.value))()
    if u32.QueryDisplayConfig(_QDC_ONLY_ACTIVE_PATHS,
                              ctypes.byref(path_count), paths,
                              ctypes.byref(mode_count), modes, None) != 0:
        return {}

    targets: dict[str, tuple[str, str, str]] = {}
    for i in range(path_count.value):
        source = n["SOURCE_DEVICE_NAME"]()
        source.header.type = _GET_SOURCE_NAME
        source.header.size = ctypes.sizeof(source)
        source.header.adapterId = paths[i].sourceInfo.adapterId
        source.header.id = paths[i].sourceInfo.id
        if u32.DisplayConfigGetDeviceInfo(ctypes.byref(source)) != 0:
            continue
        key = source.viewGdiDeviceName.lower()
        if not key or key in targets:
            continue

        target = n["TARGET_DEVICE_NAME"]()
        target.header.type = _GET_TARGET_NAME
        target.header.size = ctypes.sizeof(target)
        target.header.adapterId = paths[i].targetInfo.adapterId
        target.header.id = paths[i].targetInfo.id
        if u32.DisplayConfigGetDeviceInfo(ctypes.byref(target)) != 0:
            continue

        product = f"{target.edidProductCodeId:04X}" if target.edidProductCodeId else ""
        targets[key] = (decode_manufacturer(target.edidManufactureId),
                        product,
                        target.monitorFriendlyDeviceName or "")
    return targets


def _native_size(device_name: str) -> tuple[int, int]:
    """The largest mode the panel offers. It is a stable per-panel number, which
    is all the identity needs it for; the current mode seeds it so a driver that
    enumerates nothing still yields a size."""
    n = _load_native()
    ctypes, u32, DEVMODEW = n["ctypes"], n["u32"], n["DEVMODEW"]

    mode = DEVMODEW()
    mode.dmSize = ctypes.sizeof(DEVMODEW)
    if u32.EnumDisplaySettingsExW(device_name, _ENUM_CURRENT_SETTINGS,
                                  ctypes.byref(mode), 0):
        width, height = int(mode.dmPelsWidth), int(mode.dmPelsHeight)
    else:
        width, height = 0, 0

    best = width * height
    for index in range(4096):
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        if not u32.EnumDisplaySettingsExW(device_name, index, ctypes.byref(mode), 0):
            break
        area = int(mode.dmPelsWidth) * int(mode.dmPelsHeight)
        if area > best:
            best = area
            width, height = int(mode.dmPelsWidth), int(mode.dmPelsHeight)
    return width, height


def attached_identities() -> list[MonitorIdentity]:
    """Every display attached to the desktop, identified. Read-only: no display
    setting is touched, so this is safe to call from a probe."""
    n = _load_native()
    ctypes, u32 = n["ctypes"], n["u32"]

    handles = []
    def collect(hmonitor, hdc, rect, data):
        handles.append(hmonitor)
        return True
    proc = n["MONITOR_ENUM_PROC"](collect)
    u32.EnumDisplayMonitors(None, None, proc, 0)

    try:
        targets = _display_config_targets()
    except OSError:
        targets = {}

    identities = []
    for hmonitor in handles:
        info = n["MONITORINFOEXW"]()
        info.cbSize = ctypes.sizeof(info)
        if not u32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            continue
        device = info.szDevice
        manufacturer, product, friendly = targets.get(device.lower(), ("", "", ""))
        width, height = _native_size(device)
        identities.append(MonitorIdentity(
            manufacturer_id=manufacturer,
            product_code=product,
            # DisplayConfig's target name does not carry the EDID serial number,
            # so this stays empty by design and the identity falls back to model
            # plus native size.
            serial_number="",
            friendly_name=friendly,
            native_width=width,
            native_height=height,
            device_name=device,
            virtual_x=int(info.rcMonitor.left),
            virtual_y=int(info.rcMonitor.top),
        ))
    return identities


if __name__ == "__main__":
    for identity in attached_identities():
        print(f"{identity.device_name}  {identity.describe()}  "
              f"native {identity.native_width}x{identity.native_height}  "
              f"at ({identity.virtual_x},{identity.virtual_y})  "
              f"key {identity.stable_key or 'no-key'}")
