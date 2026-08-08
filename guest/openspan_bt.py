#!/usr/bin/env python3
"""Controller-scoped BlueZ and per-device audio operations for OpenSpan.

The original shell/bluetoothctl path remains the single-radio default.  This
helper owns operations that require an explicit controller in multi-radio mode,
plus safe address-to-PipeWire-sink volume operations used in either mode.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

import dbus


BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
AUDIO_PIN = "/opt/openspan/audio-device.txt"
MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
HCI_RE = re.compile(r"^hci[0-9]+$")
BLUEZ_SINK_RE = re.compile(r"^bluez_output\.[A-Za-z0-9_.-]+$")
BLUEZ_SINK_ADDRESS_RE = re.compile(
    r"^bluez_output\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})(?:\.|$)")
AUDIO_RUNTIME_ENV = {
    "XDG_RUNTIME_DIR": "/run/user/0",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/0/bus",
    "PULSE_SERVER": "unix:/run/user/0/pulse/native",
}


def normalize_mac(value):
    value = str(value or "").strip().upper().replace("-", ":")
    if not MAC_RE.fullmatch(value):
        raise ValueError(f"invalid Bluetooth address: {value!r}")
    return value


def normalize_controller(value):
    value = str(value or "").strip()
    if HCI_RE.fullmatch(value):
        return value
    return normalize_mac(value)


def parse_audio_pin(text):
    """Return (controller, device); old MAC-only pins remain valid."""
    parts = str(text or "").strip().upper().split("|", 1)
    if len(parts) == 2:
        return normalize_controller(parts[0]), normalize_mac(parts[1])
    if parts and parts[0]:
        return "", normalize_mac(parts[0])
    return "", ""


def format_audio_pin(controller, device):
    return f"{normalize_mac(controller)}|{normalize_mac(device)}"


def is_audio(props):
    return str(
        props.get("icon", props.get("Icon", ""))).lower().startswith("audio")


def _run_pactl(arguments, json_output=False):
    """Run pactl against OpenSpan's root PipeWire-Pulse session, never a
    caller's login session. Arguments are always an argv list (no shell)."""
    command = ["pactl"]
    if json_output:
        command.append("--format=json")
    command.extend(str(arg) for arg in arguments)
    env = os.environ.copy()
    # Do not inherit a caller-selected sink, auth cookie, or client config.
    # PULSE_SERVER below pins the one server this helper is allowed to touch.
    for key in ("PULSE_COOKIE", "PULSE_SINK", "PULSE_SOURCE",
                "PULSE_CLIENTCONFIG"):
        env.pop(key, None)
    env.update(AUDIO_RUNTIME_ENV)
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=10, env=env,
        check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "pactl failed").strip()
        raise RuntimeError(detail[-300:])
    return result.stdout or ""


def _pactl_sinks():
    try:
        rows = json.loads(_run_pactl(["list", "sinks"], json_output=True))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("pactl returned invalid sink JSON") from exc
    if not isinstance(rows, list):
        raise RuntimeError("pactl returned an invalid sink list")
    return rows


def sink_bluetooth_address(sink):
    """Return the exact Bluetooth MAC owned by a bluez_output sink.

    PipeWire exposes the address both in the sink name and, depending on its
    version, one of several exact properties. Conflicting evidence is rejected
    instead of allowing a level command to land on the wrong device.
    """
    if not isinstance(sink, dict):
        return ""
    name = str(sink.get("name") or "")
    if not BLUEZ_SINK_RE.fullmatch(name):
        return ""

    candidates = set()
    match = BLUEZ_SINK_ADDRESS_RE.match(name)
    if match:
        try:
            candidates.add(normalize_mac(match.group(1).replace("_", ":")))
        except ValueError:
            pass

    properties = sink.get("properties") or {}
    if isinstance(properties, dict):
        for key in ("api.bluez5.address", "bluez5.address",
                    "device.string", "device.serial"):
            raw = properties.get(key)
            try:
                candidates.add(normalize_mac(raw))
            except ValueError:
                pass
    return next(iter(candidates)) if len(candidates) == 1 else ""


def audio_sink_map(sinks):
    """Map MAC -> unique bluez sink; ambiguous duplicate profiles are omitted."""
    mapped = {}
    ambiguous = set()
    for sink in sinks:
        address = sink_bluetooth_address(sink)
        if not address:
            continue
        old = mapped.get(address)
        if old is not None and old.get("name") != sink.get("name"):
            ambiguous.add(address)
            continue
        mapped[address] = sink
    for address in ambiguous:
        mapped.pop(address, None)
    return mapped


def sink_level(sink):
    """Return a sink's mean channel level as a clamped integer percentage."""
    volume = sink.get("volume") if isinstance(sink, dict) else None
    channels = list(volume.values()) if isinstance(volume, dict) \
        else list(volume) if isinstance(volume, list) else []
    levels = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        percent = str(channel.get("value_percent") or "").strip()
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%", percent)
        if match:
            levels.append(float(match.group(1)))
            continue
        try:
            levels.append(float(channel["value"]) * 100.0 / 65536.0)
        except (KeyError, TypeError, ValueError):
            pass
    if not levels:
        return None
    return max(0, min(100, int(round(sum(levels) / len(levels)))))


def audio_levels():
    """Return {Bluetooth MAC: 0..100} for uniquely mapped live bluez sinks."""
    result = {}
    for address, sink in audio_sink_map(_pactl_sinks()).items():
        level = sink_level(sink)
        if level is not None:
            result[address] = level
    return dict(sorted(result.items()))


def set_audio_level(address, level):
    """Set one Bluetooth sink only; never fall back to the default sink."""
    address = normalize_mac(address)
    if isinstance(level, bool) or not isinstance(level, int) \
            or not 0 <= level <= 100:
        raise ValueError("audio level must be an integer from 0 to 100")
    sink = audio_sink_map(_pactl_sinks()).get(address)
    if not sink:
        raise RuntimeError(
            f"no unique connected Bluetooth sink for {address}")
    sink_name = str(sink.get("name") or "")
    if not BLUEZ_SINK_RE.fullmatch(sink_name):
        raise RuntimeError("refusing unsafe Bluetooth sink name")
    _run_pactl(["set-sink-volume", sink_name, f"{level}%"])
    return level


def is_apple_mobile(props):
    name = " ".join((
        str(props.get("name", props.get("Name", ""))),
        str(props.get("alias", props.get("Alias", ""))),
    )).lower()
    return any(mobile in name for mobile in ("ipad", "iphone", "ipod"))


def is_wrong_target_hid(props, target=""):
    """Reject a bond whose host identity belongs to the other HID lane."""
    if target == "mac":
        return is_apple_mobile(props)
    if target == "ipad":
        return not is_apple_mobile(props)
    return False


def bt_company(hci):
    """The chip's Bluetooth SIG manufacturer name, read from the HCI itself.

    USB string descriptors do not always survive a hypervisor's USB proxy --
    a passed-through adapter can enumerate with empty product/manufacturer
    strings while remaining fully functional.  The SIG company identifier
    comes from the controller over HCI, so it survives any transport.
    """
    try:
        out = subprocess.run(
            ["hciconfig", hci, "version"],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"Manufacturer:\s*(.+?)\s*\(\d+\)", out or "")
    return match.group(1).strip() if match else ""


def usb_identity(hci):
    """Best-effort friendly hardware identity from the controller's sysfs."""
    current = os.path.realpath(f"/sys/class/bluetooth/{hci}/device")
    for _ in range(8):
        vendor_path = os.path.join(current, "idVendor")
        product_path = os.path.join(current, "idProduct")
        if os.path.isfile(vendor_path) and os.path.isfile(product_path):
            def read(name):
                try:
                    with open(os.path.join(current, name),
                              encoding="utf-8") as handle:
                        return handle.read().strip()
                except OSError:
                    return ""
            vendor_id = read("idVendor").lower()
            product_id = read("idProduct").lower()
            return {
                "vendor_id": vendor_id,
                "product_id": product_id,
                "usb_serial": read("serial"),
                "hardware": read("product") or read("manufacturer")
                or bt_company(hci)
                or (f"USB {vendor_id}:{product_id}" if vendor_id else ""),
            }
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return {
        "vendor_id": "",
        "product_id": "",
        "usb_serial": "",
        "hardware": bt_company(hci),
    }


class Bluez:
    def __init__(self, bus=None):
        self.bus = bus or dbus.SystemBus()

    def objects(self):
        om = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
        return om.GetManagedObjects()

    def radios(self, objects=None):
        objects = objects if objects is not None else self.objects()
        rows = []
        for path, interfaces in objects.items():
            props = interfaces.get(ADAPTER)
            if not props:
                continue
            hci = str(path).rsplit("/", 1)[-1]
            rows.append({
                "path": str(path),
                "hci": hci,
                "address": normalize_mac(props.get("Address")),
                "alias": str(props.get("Alias") or props.get("Name") or ""),
                "name": str(props.get("Name") or ""),
                "powered": bool(props.get("Powered")),
                "discovering": bool(props.get("Discovering")),
                **usb_identity(hci),
            })
        return sorted(rows, key=lambda row: row["hci"])

    def radio(self, controller, objects=None):
        controller = normalize_controller(controller)
        for row in self.radios(objects):
            if controller in (row["hci"], row["address"]):
                return row
        raise RuntimeError(f"controller {controller} is not available")

    def devices(self, objects=None):
        objects = objects if objects is not None else self.objects()
        radio_by_path = {
            row["path"]: row for row in self.radios(objects)
        }
        rows = []
        for path, interfaces in objects.items():
            props = interfaces.get(DEVICE)
            if not props:
                continue
            path = str(path)
            adapter_path = path.rsplit("/dev_", 1)[0]
            radio = radio_by_path.get(adapter_path)
            if not radio:
                continue
            address = normalize_mac(props.get("Address"))
            rows.append({
                "path": path,
                "adapter_path": adapter_path,
                "hci": radio["hci"],
                "controller": radio["address"],
                "address": address,
                "name": str(props.get("Name") or address),
                "alias": str(props.get("Alias") or props.get("Name")
                             or address),
                "icon": str(props.get("Icon") or ""),
                "paired": bool(props.get("Paired")),
                "trusted": bool(props.get("Trusted")),
                "connected": bool(props.get("Connected")),
            })
        return sorted(
            rows,
            key=lambda row: (
                not row["connected"], not row["paired"],
                row["name"].lower(), row["controller"]))

    def device(self, controller, address, objects=None):
        controller = normalize_controller(controller)
        address = normalize_mac(address)
        for row in self.devices(objects):
            if row["address"] != address:
                continue
            if controller in (row["hci"], row["controller"]):
                return row
        return None

    def device_any(self, address, objects=None):
        address = normalize_mac(address)
        matches = [
            row for row in self.devices(objects)
            if row["address"] == address
        ]
        return matches[0] if matches else None

    def adapter_iface(self, radio):
        return dbus.Interface(
            self.bus.get_object(BLUEZ, radio["path"]), ADAPTER)

    def device_iface(self, device):
        return dbus.Interface(
            self.bus.get_object(BLUEZ, device["path"]), DEVICE)

    def set_property(self, path, interface, name, value):
        dbus.Interface(
            self.bus.get_object(BLUEZ, path), PROPERTIES).Set(
                interface, name, value)

    def wait_device(self, controller, address, seconds):
        deadline = time.time() + max(0, seconds)
        while True:
            row = self.device(controller, address)
            if row or time.time() >= deadline:
                return row
            time.sleep(0.5)

    def scan(self, controller, seconds=10):
        radio = self.radio(controller)
        adapter = self.adapter_iface(radio)
        started = False
        try:
            adapter.StartDiscovery()
            started = True
        except dbus.exceptions.DBusException as exc:
            if "InProgress" not in str(exc):
                raise
        try:
            time.sleep(max(1, min(int(seconds), 60)))
        finally:
            if started:
                try:
                    adapter.StopDiscovery()
                except dbus.exceptions.DBusException:
                    pass

    def connect(self, controller, address):
        """Pair-only on first use; connect on an existing bond."""
        radio = self.radio(controller)
        controller = radio["address"]
        address = normalize_mac(address)
        row = self.device(controller, address)
        discovery_started = False
        try:
            if row is None:
                try:
                    self.adapter_iface(radio).StartDiscovery()
                    discovery_started = True
                except dbus.exceptions.DBusException as exc:
                    if "InProgress" not in str(exc):
                        raise
                row = self.wait_device(controller, address, 15)
            if row is None:
                raise RuntimeError(
                    "device was not found on the assigned radio")
            if not row["paired"]:
                self.device_iface(row).Pair(timeout=55)
                self.set_property(
                    row["path"], DEVICE, "Trusted", dbus.Boolean(True))
                row = self.wait_device(controller, address, 2) or row
                if is_audio(row):
                    self.pin_audio(controller, address)
                return "PAIRED"
        finally:
            if discovery_started:
                try:
                    self.adapter_iface(radio).StopDiscovery()
                except dbus.exceptions.DBusException:
                    pass

        if row["connected"]:
            if is_audio(row):
                self.pin_audio(controller, address)
            return "CONNECTED"
        self.device_iface(row).Connect(timeout=25)
        deadline = time.time() + 6
        while time.time() < deadline:
            current = self.device(controller, address)
            if current and current["connected"]:
                if is_audio(current):
                    self.pin_audio(controller, address)
                return "CONNECTED"
            time.sleep(0.5)
        return "NO_LINK"

    def disconnect(self, controller, address):
        row = self.device(controller, address)
        if not row:
            return "NOT_FOUND"
        if row["connected"]:
            self.device_iface(row).Disconnect(timeout=20)
        return "DISCONNECTED"

    def forget(self, controller, address):
        radio = self.radio(controller)
        row = self.device(radio["address"], address)
        if not row:
            return "NOT_FOUND"
        if row["connected"]:
            try:
                self.device_iface(row).Disconnect(timeout=20)
            except dbus.exceptions.DBusException:
                pass
        self.adapter_iface(radio).RemoveDevice(
            dbus.ObjectPath(row["path"]), timeout=20)
        return "FORGOTTEN"

    def alias(self, controller, address, alias):
        row = self.device(controller, address)
        if not row:
            return "NOT_FOUND"
        self.set_property(
            row["path"], DEVICE, "Alias", dbus.String(str(alias)))
        return "RENAMED"

    def pin_audio(self, controller, address):
        text = format_audio_pin(controller, address) + "\n"
        tmp = AUDIO_PIN + ".new"
        with open(tmp, "w", encoding="ascii") as handle:
            handle.write(text)
        os.replace(tmp, AUDIO_PIN)

    def read_audio_pin(self):
        try:
            with open(AUDIO_PIN, encoding="ascii") as handle:
                return parse_audio_pin(handle.read())
        except (OSError, ValueError):
            return "", ""

    def prepare_hid(self, controller, reset=False, target=""):
        radio = self.radio(controller)
        controller = radio["address"]
        pin_controller, pin_device = self.read_audio_pin()
        if pin_device:
            audio = (
                self.device(pin_controller, pin_device)
                if pin_controller else self.device_any(pin_device)
            )
            actual_controller = audio["controller"] if audio else pin_controller
            if actual_controller == controller and audio and audio["connected"]:
                self.device_iface(audio).Disconnect(timeout=20)

        for row in list(self.devices()):
            if row["controller"] != controller or is_audio(row):
                continue
            wrong_target = is_wrong_target_hid(row, target)
            remove_for_reset = reset and row["paired"] and not row["connected"]
            if not wrong_target and not remove_for_reset:
                continue
            if row["connected"]:
                try:
                    self.device_iface(row).Disconnect(timeout=20)
                except dbus.exceptions.DBusException:
                    pass
            self.adapter_iface(radio).RemoveDevice(
                dbus.ObjectPath(row["path"]), timeout=20)
        self.set_property(
            radio["path"], ADAPTER, "Pairable", dbus.Boolean(True))
        return "READY"

    def hid_paired(self, controller, target=""):
        radio = self.radio(controller)
        return any(
            row["controller"] == radio["address"]
            and row["paired"] and not is_audio(row)
            and not is_wrong_target_hid(row, target)
            for row in self.devices())

    def forget_hid(self, controller, target=""):
        radio = self.radio(controller)
        count = 0
        for row in list(self.devices()):
            if row["controller"] != radio["address"] or is_audio(row):
                continue
            if target == "ipad" and not is_apple_mobile(row):
                continue
            if target == "mac" and is_apple_mobile(row):
                continue
            if row["connected"]:
                try:
                    self.device_iface(row).Disconnect(timeout=20)
                except dbus.exceptions.DBusException:
                    pass
            self.adapter_iface(radio).RemoveDevice(
                dbus.ObjectPath(row["path"]), timeout=20)
            count += 1
        return count

    def reconnect_audio(self):
        controller, address = self.read_audio_pin()
        row = self.device(controller, address) if controller and address \
            else self.device_any(address) if address else None
        if not row or not row["paired"] or not is_audio(row):
            return "NOT_BONDED"
        if row["connected"]:
            return "CONNECTED"
        self.device_iface(row).Connect(timeout=25)
        deadline = time.time() + 6
        while time.time() < deadline:
            current = self.device(row["controller"], address)
            if current and current["connected"]:
                return "CONNECTED"
            time.sleep(0.5)
        return "NO_LINK"


def build_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--controller", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--controller", required=True)
    scan.add_argument("--seconds", type=int, default=10)
    for name in ("connect", "disconnect", "forget"):
        item = sub.add_parser(name)
        item.add_argument("--controller", required=True)
        item.add_argument("--device", required=True)
    alias = sub.add_parser("alias")
    alias.add_argument("--controller", required=True)
    alias.add_argument("--device", required=True)
    alias.add_argument("--name", required=True)
    prep = sub.add_parser("prepare-hid")
    prep.add_argument("--controller", required=True)
    prep.add_argument("--reset", action="store_true")
    prep.add_argument("--target", default="")
    paired = sub.add_parser("hid-status")
    paired.add_argument("--controller", required=True)
    paired.add_argument("--target", default="")
    forget_hid = sub.add_parser("forget-hid")
    forget_hid.add_argument("--controller", required=True)
    forget_hid.add_argument(
        "--target", default="")
    sub.add_parser("reconnect-audio")
    sub.add_parser("audio-levels")
    set_level = sub.add_parser("set-audio-level")
    set_level.add_argument("--device", required=True)
    set_level.add_argument("--level", required=True, type=int)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "audio-levels":
        print(json.dumps(audio_levels(), sort_keys=True))
        return 0
    if args.command == "set-audio-level":
        level = set_audio_level(args.device, args.level)
        print(f"LEVEL|{normalize_mac(args.device)}|{level}")
        return 0

    bluez = Bluez()
    if args.command == "list":
        print(json.dumps({
            "radios": bluez.radios(),
            "devices": bluez.devices(),
        }, ensure_ascii=False))
    elif args.command == "resolve":
        print(bluez.radio(args.controller)["hci"])
    elif args.command == "scan":
        bluez.scan(args.controller, args.seconds)
        print("SCAN_DONE")
    elif args.command == "connect":
        print(bluez.connect(args.controller, args.device))
    elif args.command == "disconnect":
        print(bluez.disconnect(args.controller, args.device))
    elif args.command == "forget":
        print(bluez.forget(args.controller, args.device))
    elif args.command == "alias":
        print(bluez.alias(args.controller, args.device, args.name))
    elif args.command == "prepare-hid":
        print(bluez.prepare_hid(args.controller, args.reset, args.target))
    elif args.command == "hid-status":
        print("PAIRED" if bluez.hid_paired(args.controller, args.target)
              else "NOT_PAIRED")
    elif args.command == "forget-hid":
        print(f"FORGOTTEN {bluez.forget_hid(args.controller, args.target)}")
    elif args.command == "reconnect-audio":
        print(bluez.reconnect_audio())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
