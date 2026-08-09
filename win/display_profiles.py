"""Capture, match, plan, and provisionally apply display arrangements.

The model and planning code are platform-independent.  Reading the live desk is
explicit and read-only.  Writing is possible only through ``apply_profile`` and
an injected applier; the Windows implementation is constructed lazily by
``make_real_applier``.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from monitor_identity import (
    MonitorIdentity,
    MonitorMatch,
    assign,
    attached_identities,
    score,
    topology_changed,
)


class DisplayOrientation(str, enum.Enum):
    LANDSCAPE = "Landscape"
    PORTRAIT = "Portrait"
    LANDSCAPE_FLIPPED = "LandscapeFlipped"
    PORTRAIT_FLIPPED = "PortraitFlipped"


@dataclasses.dataclass(frozen=True, slots=True)
class DisplayProfileEntry:
    identity: MonitorIdentity
    x: int
    y: int
    width: int
    height: int
    refresh_hz: int
    orientation: DisplayOrientation = DisplayOrientation.LANDSCAPE
    is_primary: bool = False

    @property
    def stable_key(self) -> str | None:
        return self.identity.stable_key


@dataclasses.dataclass(frozen=True, slots=True)
class DisplayProfile:
    name: str
    entries: tuple[DisplayProfileEntry, ...]

    def __init__(self, name: str, entries: Iterable[DisplayProfileEntry] | None):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "entries", tuple(entries or ()))

    @property
    def is_usable(self) -> bool:
        return bool(self.entries)

    @property
    def monitor_keys(self) -> tuple[str | None, ...]:
        """The durable monitor-set keys; GDI device names are never keys."""
        return tuple(entry.stable_key for entry in self.entries)


@dataclasses.dataclass(frozen=True, slots=True)
class AttachedDisplay:
    identity: MonitorIdentity
    x: int
    y: int
    width: int
    height: int
    refresh_hz: int
    orientation: DisplayOrientation = DisplayOrientation.LANDSCAPE
    is_primary: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class DisplayModeChange:
    device_name: str
    x: int
    y: int
    width: int
    height: int
    refresh_hz: int
    orientation: DisplayOrientation
    is_primary: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ApplyOutcome:
    success: bool
    failures: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return "Applied." if self.success else " ".join(self.failures)


@dataclasses.dataclass(frozen=True, slots=True)
class ApplyProfileResult:
    status: str
    plan: tuple[DisplayModeChange, ...] = ()
    apply_outcome: ApplyOutcome | None = None
    revert_outcome: ApplyOutcome | None = None

    @property
    def kept(self) -> bool:
        return self.status == "kept"

    @property
    def reverted(self) -> bool:
        return self.status in {"reverted", "apply-failed-reverted"}


MINIMUM_MATCH = MonitorMatch.NAME


def _identities(attached: Sequence[MonitorIdentity | AttachedDisplay]) -> list[MonitorIdentity]:
    return [item.identity if isinstance(item, AttachedDisplay) else item
            for item in attached]


def _assignment(profile: DisplayProfile,
                attached: Sequence[MonitorIdentity | AttachedDisplay]) -> dict[int, int]:
    return assign([entry.identity for entry in profile.entries],
                  _identities(attached), MINIMUM_MATCH)


def matches(profile: DisplayProfile,
            attached: Sequence[MonitorIdentity | AttachedDisplay]) -> bool:
    """Whether the profile describes exactly the attached monitor set."""
    identities = _identities(attached)
    remembered = [entry.identity for entry in profile.entries]
    if not profile.is_usable or len(remembered) != len(identities):
        return False
    remembered_keys = [identity.stable_key for identity in remembered]
    attached_keys = [identity.stable_key for identity in identities]
    if (any(key is None for key in remembered_keys + attached_keys)
            or Counter(remembered_keys) != Counter(attached_keys)):
        return False
    if topology_changed(remembered, identities):
        return False
    pairing = assign(remembered, identities, MINIMUM_MATCH)
    return (len(pairing) == len(remembered)
            and all(score(remembered[index], identities[other]) >= MINIMUM_MATCH
                    for index, other in pairing.items()))


def choose_applicable(profiles: Sequence[DisplayProfile],
                      attached: Sequence[MonitorIdentity | AttachedDisplay]
                      ) -> DisplayProfile | None:
    """Return the sole matching profile; ambiguity is never guessed through."""
    found = [profile for profile in profiles if matches(profile, attached)]
    return found[0] if len(found) == 1 else None


def _choose_primary_index(entries: Sequence[DisplayProfileEntry]) -> int:
    flagged = [index for index, entry in enumerate(entries) if entry.is_primary]
    if len(flagged) == 1:
        return flagged[0]
    for index, entry in enumerate(entries):
        if entry.x == 0 and entry.y == 0:
            return index
    return 0


def _same_mode(change: DisplayModeChange, current: AttachedDisplay) -> bool:
    return (change.device_name.casefold() == current.identity.device_name.casefold()
            and change.x == current.x and change.y == current.y
            and change.width == current.width and change.height == current.height
            and (change.refresh_hz <= 0 or change.refresh_hz == current.refresh_hz)
            and change.orientation is current.orientation
            and change.is_primary == current.is_primary)


def _build_plan(profile: DisplayProfile,
                attached: Sequence[MonitorIdentity | AttachedDisplay], *,
                omit_unchanged: bool) -> tuple[DisplayModeChange, ...]:
    if not matches(profile, attached):
        return ()

    pairing = _assignment(profile, attached)
    identities = _identities(attached)
    primary_index = _choose_primary_index(profile.entries)
    primary = profile.entries[primary_index]
    offset_x, offset_y = -primary.x, -primary.y
    order = [primary_index]
    order.extend(index for index in range(len(profile.entries))
                 if index != primary_index)

    plan = tuple(DisplayModeChange(
        device_name=identities[pairing[index]].device_name,
        x=profile.entries[index].x + offset_x,
        y=profile.entries[index].y + offset_y,
        width=profile.entries[index].width,
        height=profile.entries[index].height,
        refresh_hz=profile.entries[index].refresh_hz,
        orientation=profile.entries[index].orientation,
        is_primary=index == primary_index,
    ) for index in order)

    if (omit_unchanged and all(isinstance(item, AttachedDisplay) for item in attached)
            and all(_same_mode(change, attached[pairing[index]])
                    for change, index in zip(plan, order, strict=True))):
        return ()
    return plan


def build_plan(profile: DisplayProfile,
               attached: Sequence[MonitorIdentity | AttachedDisplay]
               ) -> tuple[DisplayModeChange, ...]:
    """Build a deterministic, primary-first plan, or no plan if none is needed."""
    return _build_plan(profile, attached, omit_unchanged=True)


def name_equals(one: str, other: str) -> bool:
    return one.strip().casefold() == other.strip().casefold()


def upsert(existing: Sequence[DisplayProfile],
           profile: DisplayProfile) -> list[DisplayProfile]:
    result: list[DisplayProfile] = []
    replaced = False
    for candidate in existing:
        if name_equals(candidate.name, profile.name):
            if not replaced:
                result.append(profile)
                replaced = True
        else:
            result.append(candidate)
    if not replaced:
        result.append(profile)
    return result


def find(profiles: Sequence[DisplayProfile], name: str | None) -> DisplayProfile | None:
    if name is None or not name.strip():
        return None
    return next((profile for profile in profiles if name_equals(profile.name, name)), None)


def _identity_dict(identity: MonitorIdentity) -> dict[str, Any]:
    return {
        "ManufacturerId": identity.manufacturer_id,
        "ProductCode": identity.product_code,
        "SerialNumber": identity.serial_number,
        "FriendlyName": identity.friendly_name,
        "NativeWidth": identity.native_width,
        "NativeHeight": identity.native_height,
        "DeviceName": identity.device_name,
        "VirtualX": identity.virtual_x,
        "VirtualY": identity.virtual_y,
    }


def write_profiles(profiles: Sequence[DisplayProfile]) -> str:
    payload = [{
        "Name": profile.name,
        "Entries": [{
            "Identity": _identity_dict(entry.identity),
            "X": entry.x,
            "Y": entry.y,
            "Width": entry.width,
            "Height": entry.height,
            "RefreshHz": entry.refresh_hz,
            "Orientation": entry.orientation.value,
            "IsPrimary": entry.is_primary,
        } for entry in profile.entries],
    } for profile in profiles]
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _field(value: dict[str, Any], name: str, default: Any = None) -> Any:
    wanted = name.casefold()
    return next((item for key, item in value.items() if key.casefold() == wanted), default)


def _read_identity(value: Any) -> MonitorIdentity:
    if not isinstance(value, dict):
        raise ValueError("Identity must be an object")
    return MonitorIdentity(
        manufacturer_id=str(_field(value, "ManufacturerId", "") or ""),
        product_code=str(_field(value, "ProductCode", "") or ""),
        serial_number=str(_field(value, "SerialNumber", "") or ""),
        friendly_name=str(_field(value, "FriendlyName", "") or ""),
        native_width=int(_field(value, "NativeWidth", 0)),
        native_height=int(_field(value, "NativeHeight", 0)),
        device_name=str(_field(value, "DeviceName", "") or ""),
        virtual_x=int(_field(value, "VirtualX", 0)),
        virtual_y=int(_field(value, "VirtualY", 0)),
    )


def _read_entry(value: Any) -> DisplayProfileEntry:
    if not isinstance(value, dict):
        raise ValueError("entry must be an object")
    return DisplayProfileEntry(
        identity=_read_identity(_field(value, "Identity")),
        x=int(_field(value, "X", 0)),
        y=int(_field(value, "Y", 0)),
        width=int(_field(value, "Width", 0)),
        height=int(_field(value, "Height", 0)),
        refresh_hz=int(_field(value, "RefreshHz", 0)),
        orientation=DisplayOrientation(_field(value, "Orientation", "Landscape")),
        is_primary=bool(_field(value, "IsPrimary", False)),
    )


def read_profiles(text: str | None) -> tuple[list[DisplayProfile], list[str]]:
    if text is None or not text.strip():
        return [], []
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return [], [f"Saved display profiles are not valid JSON ({exc}); none are available."]
    if not isinstance(payload, list):
        return [], ["Saved display profiles must be a JSON array; none are available."]

    profiles: list[DisplayProfile] = []
    problems: list[str] = []
    for position, value in enumerate(payload, 1):
        if not isinstance(value, dict):
            problems.append(f"Profile {position} could not be read; skipped.")
            continue
        name = _field(value, "Name", "")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"Profile {position} has no name; skipped.")
            continue
        values = _field(value, "Entries")
        if not isinstance(values, list) or not values:
            problems.append(f"Profile '{name}' lists no monitors; skipped.")
            continue
        try:
            entries = tuple(_read_entry(item) for item in values)
        except (TypeError, ValueError, KeyError) as exc:
            problems.append(f"Profile {position} could not be read ({exc}); skipped.")
            continue
        profiles.append(DisplayProfile(name, entries))
    return profiles, problems


def _orientation(value: int) -> DisplayOrientation:
    return {
        1: DisplayOrientation.PORTRAIT,
        2: DisplayOrientation.LANDSCAPE_FLIPPED,
        3: DisplayOrientation.PORTRAIT_FLIPPED,
    }.get(value, DisplayOrientation.LANDSCAPE)


def attached_displays() -> list[AttachedDisplay]:
    """Read the current modes of every attached display.  This is read-only."""
    identities = attached_identities()
    if not identities:
        return []

    # Reuse monitor_identity's private, lazily-built read surface so the two
    # modules cannot silently disagree about the DEVMODE layout.
    import ctypes
    import monitor_identity as identity_module

    native = identity_module._load_native()
    user32, mode_type = native["u32"], native["DEVMODEW"]
    result: list[AttachedDisplay] = []
    primary_claimed = False
    for identity in identities:
        mode = mode_type()
        mode.dmSize = ctypes.sizeof(mode_type)
        if not user32.EnumDisplaySettingsExW(
                identity.device_name, 0xFFFFFFFF, ctypes.byref(mode), 0):
            width, height, refresh, orientation = (
                identity.native_width, identity.native_height, 0,
                DisplayOrientation.LANDSCAPE)
        else:
            width, height = int(mode.dmPelsWidth), int(mode.dmPelsHeight)
            refresh = int(mode.dmDisplayFrequency)
            orientation = _orientation(int(mode.dmDisplayOrientation))
        is_primary = (not primary_claimed and identity.virtual_x == 0
                      and identity.virtual_y == 0)
        primary_claimed = primary_claimed or is_primary
        result.append(AttachedDisplay(
            identity, identity.virtual_x, identity.virtual_y, width, height,
            refresh, orientation, is_primary))
    if result and not primary_claimed:
        result[0] = dataclasses.replace(result[0], is_primary=True)
    return result


def capture_profile(name: str,
                    current: Sequence[AttachedDisplay] | None = None) -> DisplayProfile:
    """Capture a named profile.  Reading only; no applier is constructed."""
    displays = list(attached_displays() if current is None else current)
    return DisplayProfile(name, (DisplayProfileEntry(
        display.identity, display.x, display.y, display.width, display.height,
        display.refresh_hz, display.orientation, display.is_primary)
        for display in displays))


def _as_outcome(value: ApplyOutcome | bool | None) -> ApplyOutcome:
    if isinstance(value, ApplyOutcome):
        return value
    if value is False:
        return ApplyOutcome(False, ("The applier reported failure.",))
    return ApplyOutcome(True)


def _confirmed(confirmation: Any, timeout: float) -> bool:
    seconds = max(0.0, float(timeout))
    if confirmation is None:
        return threading.Event().wait(seconds)
    if isinstance(confirmation, bool):
        return confirmation
    waiter = getattr(confirmation, "wait", None)
    if callable(waiter):
        return bool(waiter(seconds))
    if callable(confirmation):
        return bool(confirmation(seconds))
    raise TypeError("confirmation must be a bool, callable, event, or None")


def apply_profile(profile: DisplayProfile, applier: Callable[[Sequence[DisplayModeChange]],
                  ApplyOutcome | bool | None] | None = None, *,
                  current: Sequence[AttachedDisplay] | None = None,
                  confirmation: Any = None, timeout: float = 15.0) -> ApplyProfileResult:
    """Apply provisionally, retaining the change only after confirmation.

    Silence, an apply failure, or an exception all cause a best-effort restore
    of the arrangement captured immediately before the first apply.
    """
    displays = list(attached_displays() if current is None else current)
    if not matches(profile, displays):
        return ApplyProfileResult("not-applicable")
    plan = build_plan(profile, displays)
    if not plan:
        return ApplyProfileResult("unchanged")

    previous = capture_profile("previous arrangement", displays)
    revert_plan = _build_plan(previous, displays, omit_unchanged=False)
    perform = make_real_applier() if applier is None else applier
    try:
        outcome = _as_outcome(perform(plan))
    except Exception as exc:  # an attempted mode change must still be unwound
        outcome = ApplyOutcome(False, (f"Applying raised {type(exc).__name__}: {exc}",))

    try:
        confirmed = outcome.success and _confirmed(confirmation, timeout)
    except Exception:
        confirmed = False
    if confirmed:
        return ApplyProfileResult("kept", plan, outcome)

    try:
        reverted = _as_outcome(perform(revert_plan))
    except Exception as exc:
        reverted = ApplyOutcome(False, (f"Reverting raised {type(exc).__name__}: {exc}",))
    if reverted.success:
        status = "reverted" if outcome.success else "apply-failed-reverted"
    else:
        status = "revert-failed"
    return ApplyProfileResult(status, plan, outcome, reverted)


def make_real_applier() -> Callable[[Sequence[DisplayModeChange]], ApplyOutcome]:
    """Construct the Windows plan applier.  Construction itself changes nothing."""
    import ctypes
    import ctypes.wintypes as wt
    import monitor_identity as identity_module

    native = identity_module._load_native()
    user32, mode_type = native["u32"], native["DEVMODEW"]
    change_settings = getattr(user32, "ChangeDisplaySettingsExW")
    change_settings.restype = ctypes.c_long
    change_settings.argtypes = [wt.LPCWSTR, ctypes.c_void_p, wt.HWND,
                                wt.DWORD, ctypes.c_void_p]

    fields = 0x00000020 | 0x00000080 | 0x00080000 | 0x00100000
    orientation_values = {
        DisplayOrientation.LANDSCAPE: 0,
        DisplayOrientation.PORTRAIT: 1,
        DisplayOrientation.LANDSCAPE_FLIPPED: 2,
        DisplayOrientation.PORTRAIT_FLIPPED: 3,
    }

    def perform(plan: Sequence[DisplayModeChange]) -> ApplyOutcome:
        if not plan:
            return ApplyOutcome(False, ("There was nothing to apply.",))
        failures: list[str] = []
        for change in plan:
            mode = mode_type()
            mode.dmSize = ctypes.sizeof(mode_type)
            if not user32.EnumDisplaySettingsExW(
                    change.device_name, 0xFFFFFFFF, ctypes.byref(mode), 0):
                failures.append(f"{change.device_name}: the display could not be read.")
                continue
            mode.dmPositionX, mode.dmPositionY = change.x, change.y
            mode.dmPelsWidth, mode.dmPelsHeight = max(0, change.width), max(0, change.height)
            mode.dmDisplayOrientation = orientation_values[change.orientation]
            mode.dmFields = fields
            if change.refresh_hz > 0:
                mode.dmDisplayFrequency = change.refresh_hz
                mode.dmFields |= 0x00400000
            flags = 0x00000001 | 0x10000000
            if change.is_primary:
                flags |= 0x00000010
            code = change_settings(change.device_name,
                                   ctypes.cast(ctypes.byref(mode), ctypes.c_void_p),
                                   None, flags, None)
            if code != 0:
                failures.append(f"{change.device_name}: driver result {code}.")

        commit = change_settings(None, None, None, 0, None)
        if commit != 0:
            failures.append(f"Committing the arrangement failed: driver result {commit}.")
        return ApplyOutcome(not failures, tuple(failures))

    return perform


def _probe() -> None:
    print("READ-ONLY PROBE: no display changes will be applied.")
    current = attached_displays()
    profile = capture_profile("Live desk", current)
    print(f"Captured profile: {profile.name} ({len(profile.entries)} display(s))")
    for entry in profile.entries:
        print(f"  {entry.identity.device_name}  key {entry.stable_key or 'no-key'}  "
              f"at ({entry.x},{entry.y})  {entry.width}x{entry.height}@{entry.refresh_hz}Hz  "
              f"{entry.orientation.value}{'  primary' if entry.is_primary else ''}")
    plan = build_plan(profile, current)
    print(f"Matches live topology: {'yes' if matches(profile, current) else 'no'}")
    if not plan:
        print("Plan: already matches; would set nothing.")
    else:
        print("Plan:")
        for change in plan:
            print(f"  would set {change.device_name} to "
                  f"({change.x},{change.y}) {change.width}x{change.height}@"
                  f"{change.refresh_hz}Hz {change.orientation.value}"
                  f"{' primary' if change.is_primary else ''}")
    print("READ-ONLY PROBE COMPLETE: applied nothing.")


if __name__ == "__main__":
    _probe()
