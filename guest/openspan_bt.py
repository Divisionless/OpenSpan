#!/usr/bin/env python3
"""Controller-scoped BlueZ operations for OpenSpan multi-radio mode.

The original shell/bluetoothctl path remains the single-radio default.  This
helper is used only after the user opts into multi-radio mode, where every
operation must name a controller explicitly so one device cannot disturb a
different radio.
"""

import argparse
import json
import os
import re
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
            return {
                "vendor_id": read("idVendor").lower(),
                "product_id": read("idProduct").lower(),
                "usb_serial": read("serial"),
                "hardware": read("product") or read("manufacturer"),
            }
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return {
        "vendor_id": "",
        "product_id": "",
        "usb_serial": "",
        "hardware": "",
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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
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
