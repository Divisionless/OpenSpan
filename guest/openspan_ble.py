#!/usr/bin/env python3
"""OpenSpan BLE HID (HOGP) peripheral daemon.

iOS refuses Classic BR/EDR HID emulation from BlueZ, but it fully
supports Bluetooth Low Energy HID-over-GATT (HOGP) keyboards/mice.
This daemon publishes a GATT server (HID + Device Information +
Battery) and advertises as a keyboard, then delivers input by
notifying the Report characteristics.

Command interface: line-oriented JSON on TCP :9955 by default
  {"cmd":"text","text":"Hello"}
  {"cmd":"keys","mods":0,"keys":[4]}
  {"cmd":"mouse","dx":5,"dy":-3,"buttons":0,"wheel":0}
  {"cmd":"status"}

The production fallback remains hci0:9955. A second, isolated bench instance can
be selected without changing that default:
  OPENSPAN_ADAPTER=hci1 OPENSPAN_PORT=9956 \
  OPENSPAN_DEVICE_NAME="OpenSpan Bench" ./openspan_ble.py
"""

import json
import os
import re
import socket
import threading
import time

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

BLUEZ = "org.bluez"
GATT_MANAGER = "org.bluez.GattManager1"
LE_ADV_MANAGER = "org.bluez.LEAdvertisingManager1"
DBUS_OM = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP = "org.freedesktop.DBus.Properties"

GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
GATT_DESC_IFACE = "org.bluez.GattDescriptor1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"


def _adapter_name(value):
    value = str(value).strip()
    if not re.fullmatch(r"hci[0-9]+", value):
        raise ValueError(f"invalid Bluetooth adapter {value!r}; expected hciN")
    return value


def _command_port(value):
    value = int(value)
    if not 1 <= value <= 65535:
        raise ValueError(f"invalid command port {value!r}")
    return value


def _device_name(value):
    value = str(value).strip()
    if not value:
        raise ValueError("Bluetooth device name cannot be empty")
    if len(value.encode("utf-8")) > 24:
        raise ValueError("Bluetooth device name must be at most 24 UTF-8 bytes")
    return value


def _device_identity(device_name, adapter_address):
    """Return stable, lane-specific Device Information Service values."""
    device_name = _device_name(device_name)
    adapter_address = str(adapter_address or "").strip().upper()
    is_mac = "mac" in device_name.lower()
    return {
        "product_id": 0x0247 if is_mac else 0x0246,
        "model": device_name,
        "serial": ("MAC-" if is_mac else "IPAD-")
                  + adapter_address.replace(":", ""),
    }


DEFAULT_ADAPTER = _adapter_name(os.environ.get("OPENSPAN_ADAPTER", "hci0"))
DEFAULT_COMMAND_PORT = _command_port(os.environ.get("OPENSPAN_PORT", "9955"))
DEFAULT_DEVICE_NAME = _device_name(
    os.environ.get("OPENSPAN_DEVICE_NAME", "OpenSpan Keyboard"))
# A normal iPad reconnect can take six or seven seconds to discover the HID
# characteristics and subscribe.  Recovery before that point races the healthy
# subscription and can kick off a link that has just become usable.
RESUB_SETTLE_SECONDS = 8
# Compatibility constant for existing tooling that imports this module.
ADAPTER_PATH = f"/org/bluez/{DEFAULT_ADAPTER}"

# Combined keyboard (report id 1) + mouse (report id 2) descriptor.
REPORT_MAP = bytes([
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, 0x01,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00,
    0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01,
    0x29, 0x05, 0x91, 0x02, 0x95, 0x01, 0x75, 0x03, 0x91, 0x01,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x26, 0xFF, 0x00,
    0x05, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x00, 0x81, 0x00,
    0xC0,
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x02,
    0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00,
    0x25, 0x01, 0x95, 0x03, 0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
    0xC0, 0xC0,
])

KEYMAP = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEYMAP[c] = (0, 4 + i)
    KEYMAP[c.upper()] = (2, 4 + i)
for i, c in enumerate("1234567890"):
    KEYMAP[c] = (0, 30 + i)
for c, m, u in [
    ("\n", 0, 40), ("\t", 0, 43), (" ", 0, 44),
    ("-", 0, 45), ("_", 2, 45), ("=", 0, 46), ("+", 2, 46),
    ("[", 0, 47), ("{", 2, 47), ("]", 0, 48), ("}", 2, 48),
    ("\\", 0, 49), ("|", 2, 49), (";", 0, 51), (":", 2, 51),
    ("'", 0, 52), ('"', 2, 52), ("`", 0, 53), ("~", 2, 53),
    (",", 0, 54), ("<", 2, 54), (".", 0, 55), (">", 2, 55),
    ("/", 0, 56), ("?", 2, 56),
    ("!", 2, 30), ("@", 2, 31), ("#", 2, 32), ("$", 2, 33),
    ("%", 2, 34), ("^", 2, 35), ("&", 2, 36), ("*", 2, 37),
    ("(", 2, 38), (")", 2, 39),
]:
    KEYMAP[c] = (m, u)


class InvalidArgs(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class NotSupported(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotSupported"


# ---- GATT base classes -------------------------------------------------
class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = "/org/openspan"
        self.services = []
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            for chrc in service.characteristics:
                response[chrc.get_path()] = chrc.get_properties()
                for desc in chrc.descriptors:
                    response[desc.get_path()] = desc.get_properties()
        return response


class Service(dbus.service.Object):
    def __init__(self, bus, index, uuid, primary):
        self.path = f"/org/openspan/service{index}"
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, chrc):
        self.characteristics.append(chrc)

    def get_properties(self):
        return {GATT_SERVICE_IFACE: {
            "UUID": self.uuid,
            "Primary": self.primary,
            "Characteristics": dbus.Array(
                [c.get_path() for c in self.characteristics], signature="o"),
        }}


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = f"{service.path}/char{index}"
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.service = service
        self.descriptors = []
        self.notifying = False
        self.value = dbus.Array([], signature="y")
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_descriptor(self, desc):
        self.descriptors.append(desc)

    def get_properties(self):
        return {GATT_CHRC_IFACE: {
            "Service": self.service.get_path(),
            "UUID": self.uuid,
            "Flags": self.flags,
            "Descriptors": dbus.Array(
                [d.get_path() for d in self.descriptors], signature="o"),
        }}

    @dbus.service.method(DBUS_PROP, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_CHRC_IFACE:
            raise InvalidArgs()
        return self.get_properties()[GATT_CHRC_IFACE]

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}",
                         out_signature="ay")
    def ReadValue(self, options):
        return self.value

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        self.value = value

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self):
        self.notifying = True

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self):
        self.notifying = False

    @dbus.service.signal(DBUS_PROP, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def notify_value(self, byte_list):
        arr = dbus.Array([dbus.Byte(b) for b in byte_list], signature="y")
        self.value = arr
        if self.notifying:
            # D-Bus signals must be emitted on the main-loop thread.
            GLib.idle_add(self._emit, arr)

    def _emit(self, arr):
        self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": arr}, [])
        return False


class Descriptor(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, chrc, value=None):
        self.path = f"{chrc.path}/desc{index}"
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.chrc = chrc
        self.value = dbus.Array(value or [], signature="y")
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        return {GATT_DESC_IFACE: {
            "Characteristic": self.chrc.get_path(),
            "UUID": self.uuid,
            "Flags": self.flags,
        }}

    @dbus.service.method(GATT_DESC_IFACE, in_signature="a{sv}",
                         out_signature="ay")
    def ReadValue(self, options):
        return self.value

    @dbus.service.method(GATT_DESC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        raise NotSupported()


# ---- Concrete characteristics -----------------------------------------
class ReadOnlyChrc(Characteristic):
    def __init__(self, bus, index, uuid, service, value, flags=None):
        super().__init__(bus, index, uuid, flags or ["read"], service)
        self.value = dbus.Array([dbus.Byte(b) for b in value], signature="y")


class ReportChrc(Characteristic):
    """HID Report characteristic (0x2A4D) with a Report Reference desc.

    Input reports REQUIRE an encrypted link (encrypt-read) so iOS is
    forced to bond — iOS will not activate a BLE keyboard for text
    input until the device is properly bonded.
    """
    def __init__(self, bus, index, service, report_id, report_type,
                 notify=True):
        flags = ["encrypt-read", "notify"] if notify else \
            ["encrypt-read", "encrypt-write", "write-without-response"]
        super().__init__(bus, index, "00002a4d-0000-1000-8000-00805f9b34fb",
                         flags, service)
        self.add_descriptor(Descriptor(
            bus, 0, "00002908-0000-1000-8000-00805f9b34fb", ["read"],
            self, [dbus.Byte(report_id), dbus.Byte(report_type)]))


# ---- HID service assembly ---------------------------------------------
class HidService(Service):
    def __init__(self, bus, index):
        super().__init__(bus, index,
                         "00001812-0000-1000-8000-00805f9b34fb", True)
        # HID Information: bcdHID=0x0111, country=0, flags=0x03
        self.add_characteristic(ReadOnlyChrc(
            bus, 0, "00002a4a-0000-1000-8000-00805f9b34fb", self,
            [0x11, 0x01, 0x00, 0x03], flags=["read"]))
        # Report Map — require encryption so iOS bonds before reading it
        self.add_characteristic(ReadOnlyChrc(
            bus, 1, "00002a4b-0000-1000-8000-00805f9b34fb", self,
            list(REPORT_MAP), flags=["encrypt-read"]))
        # HID Control Point
        cp = Characteristic(bus, 2, "00002a4c-0000-1000-8000-00805f9b34fb",
                            ["write-without-response"], self)
        self.add_characteristic(cp)
        # Protocol Mode (report protocol = 1)
        pm = Characteristic(bus, 3, "00002a4e-0000-1000-8000-00805f9b34fb",
                            ["read", "write-without-response"], self)
        pm.value = dbus.Array([dbus.Byte(0x01)], signature="y")
        self.add_characteristic(pm)
        # Keyboard input report (id 1, type input=1)
        self.kbd = ReportChrc(bus, 4, self, 0x01, 0x01, notify=True)
        self.add_characteristic(self.kbd)
        # Keyboard output report (id 1, type output=2) for LEDs
        self.kbd_out = ReportChrc(bus, 5, self, 0x01, 0x02, notify=False)
        self.add_characteristic(self.kbd_out)
        # Mouse input report (id 2, type input=1)
        self.mouse = ReportChrc(bus, 6, self, 0x02, 0x01, notify=True)
        self.add_characteristic(self.mouse)
        # Boot keyboard input (0x2A22) - some hosts probe for it
        self.boot_kbd = Characteristic(
            bus, 7, "00002a22-0000-1000-8000-00805f9b34fb",
            ["encrypt-read", "notify"], self)
        self.add_characteristic(self.boot_kbd)


class DeviceInfoService(Service):
    def __init__(self, bus, index, device_name, adapter_address):
        super().__init__(bus, index,
                         "0000180a-0000-1000-8000-00805f9b34fb", True)
        identity = _device_identity(device_name, adapter_address)
        product = identity["product_id"]
        # PnP ID: vendor source=USB(2), vendor=0x1D6B, lane-specific product,
        # version 1. Model and serial keep two OpenSpan radios distinct in the
        # host's Bluetooth cache even though both expose the same HID reports.
        self.add_characteristic(ReadOnlyChrc(
            bus, 0, "00002a50-0000-1000-8000-00805f9b34fb", self,
            [0x02, 0x6B, 0x1D, product & 0xff, product >> 8, 0x01, 0x00]))
        self.add_characteristic(ReadOnlyChrc(
            bus, 1, "00002a29-0000-1000-8000-00805f9b34fb", self,
            list(b"OpenSpan")))
        self.add_characteristic(ReadOnlyChrc(
            bus, 2, "00002a24-0000-1000-8000-00805f9b34fb", self,
            list(identity["model"].encode("utf-8"))))
        self.add_characteristic(ReadOnlyChrc(
            bus, 3, "00002a25-0000-1000-8000-00805f9b34fb", self,
            list(identity["serial"].encode("ascii"))))


class BatteryService(Service):
    def __init__(self, bus, index):
        super().__init__(bus, index,
                         "0000180f-0000-1000-8000-00805f9b34fb", True)
        c = Characteristic(bus, 0, "00002a19-0000-1000-8000-00805f9b34fb",
                           ["read", "notify"], self)
        c.value = dbus.Array([dbus.Byte(100)], signature="y")
        self.add_characteristic(c)


class LayoutSaltService(Service):
    """Throwaway service whose UUID and characteristic COUNT change on every
    (re)registration, so the local GATT layout + Database Hash (0x2B2A) differ.
    Added to the application FIRST so its handles shift the HID Report handles
    too. Re-registering it WHILE the iPad is connected makes BlueZ flag that
    central change-unaware (Robust Caching) + indicate Service Changed, forcing
    the iPad to re-discover and re-subscribe -> StartNotify fires -> input flows
    again after a cold-boot reconnect. iOS ignores the extra unknown service."""
    def __init__(self, bus, index, gen):
        g = gen & 0xffff
        super().__init__(bus, index,
                         f"6f70656e-7370-616e-{g:04x}-000000000001", True)
        for i in range(1 + (gen % 4)):
            self.add_characteristic(ReadOnlyChrc(
                bus, i, f"6f70656e-7370-616e-{g:04x}-0000000000{i:02x}",
                self, [gen & 0xff], flags=["read"]))


# ---- Advertisement -----------------------------------------------------
class Advertisement(dbus.service.Object):
    def __init__(self, bus, index, local_name=DEFAULT_DEVICE_NAME):
        self.path = f"/org/openspan/adv{index}"
        self.bus = bus
        self.local_name = _device_name(local_name)
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgs()
        return {
            "Type": "peripheral",
            "ServiceUUIDs": dbus.Array(
                ["00001812-0000-1000-8000-00805f9b34fb"], signature="s"),
            "LocalName": dbus.String(self.local_name),
            "Appearance": dbus.UInt16(0x03C1),  # HID Keyboard
            "Discoverable": dbus.Boolean(True),
            "IncludeTxPower": dbus.Boolean(True),
            # Advertise FAST (ms) so the iPad discovers us quickly instead of
            # buried behind neighbours' beacons. 20-152ms is the "fast connect"
            # band. If a controller rejects these, RegisterAdvertisement fails
            # and the app's honest Broadcast surfaces it -- then drop these two.
            "MinInterval": dbus.UInt32(100),
            "MaxInterval": dbus.UInt32(152),
        }

    @dbus.service.method(LE_ADVERTISEMENT_IFACE)
    def Release(self):
        print("advertisement released")


# ---- Pairing agent -----------------------------------------------------
class Agent(dbus.service.Object):
    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os",
                         out_signature="")
    def AuthorizeService(self, device, uuid):
        print(f"agent: authorize service {uuid}")

    @dbus.service.method("org.bluez.Agent1", in_signature="o",
                         out_signature="u")
    def RequestPasskey(self, device):
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="o",
                         out_signature="s")
    def RequestPinCode(self, device):
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="ou",
                         out_signature="")
    def RequestConfirmation(self, device, passkey):
        print(f"agent: confirm passkey {passkey:06d}")

    @dbus.service.method("org.bluez.Agent1", in_signature="o",
                         out_signature="")
    def RequestAuthorization(self, device):
        print(f"agent: authorize {device}")

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq",
                         out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print(f"agent: display passkey {passkey:06d}")

    @dbus.service.method("org.bluez.Agent1", in_signature="os",
                         out_signature="")
    def DisplayPinCode(self, device, pincode):
        print(f"agent: display pin {pincode}")

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        pass


# ---- Daemon glue -------------------------------------------------------
class OpenSpanBLE:
    def __init__(self, adapter=DEFAULT_ADAPTER,
                 command_port=DEFAULT_COMMAND_PORT,
                 device_name=DEFAULT_DEVICE_NAME):
        self.adapter = _adapter_name(adapter)
        self.adapter_path = f"/org/bluez/{self.adapter}"
        self.command_port = _command_port(command_port)
        self.device_name = _device_name(device_name)
        self.bus = dbus.SystemBus()
        self.adapter_address = ""
        self.hid = None
        # The Windows portal keeps one TCP connection per input lane.  Track
        # the most recent connection that sent stateful HID input so losing
        # that connection cannot strand a key, modifier, or mouse button on
        # the remote device.  Ownership changes and reports share one lock:
        # an old connection's cleanup can therefore never clear input sent by
        # a newer connection.
        self._input_lock = threading.Lock()
        self._input_owner = None
        self.adv = None
        self.adv_on = False   # broadcasting is OPT-IN -- see register()
        # Advertisement state must reflect BlueZ's callback, not merely that a
        # request was queued.  The TCP command server runs on worker threads,
        # while all D-Bus work belongs on GLib's main-loop thread.
        self.adv_state = "off"       # off | starting | on | stopping
        self.adv_error = ""
        self._adv_lock = threading.Lock()
        self._adv_command_lock = threading.Lock()
        self._adv_done = None
        self._adv_op = 0
        self._adv_desired = False
        self._gen = int(time.time()) & 0xffff  # GATT layout salt; differs each boot
        self._exported = []     # every exported GATT dbus obj, for clean teardown
        self._resub_tries = {}  # iPad device path -> re-subscribe nudge count (cap 2)
        self._hid_paths = set()       # known non-audio Device1 paths
        self._hid_connected = set()   # currently connected HID hosts

    def configure_adapter(self):
        props = dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path),
                               DBUS_PROP)
        self.adapter_address = str(
            props.Get("org.bluez.Adapter1", "Address")).upper()
        props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "Alias", self.device_name)
        # BR/EDR discoverable OFF. A dual-mode adapter that is Classic-
        # discoverable shows a SECOND "OpenSpan Keyboard" decoy on the iPad that
        # cannot pair. The iPad finds the REAL keyboard via the LE advertisement
        # registered below, so Classic discoverability is pure downside. BR/EDR
        # stays enabled (needed for A2DP audio) -- just not discoverable.
        props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))
        props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(0))

        agent = Agent(self.bus, "/org/openspan/agent")  # noqa: F841
        am = dbus.Interface(self.bus.get_object(BLUEZ, "/org/bluez"),
                            "org.bluez.AgentManager1")
        am.RegisterAgent("/org/openspan/agent", "NoInputNoOutput")
        am.RequestDefaultAgent("/org/openspan/agent")
        self._agent = agent

    def register(self):
        self._build_and_register()
        # LE advertisement stays OPT-IN (unchanged): the app turns it on via
        # {"cmd":"adv","on":true} on Pair/Broadcast and off once the iPad is in.
        # Consent, not default -- no 24/7 keyboard beacon.
        self.adv = Advertisement(self.bus, 0, self.device_name)
        self.adv_on = False
        print("adv: OFF at boot -- broadcasting is opt-in (press Broadcast)")
        # Watch for the bonded iPad reconnecting: a FRESH daemon (post cold
        # boot) has no CCC state, and a GATT-caching iPad won't re-subscribe on
        # its own, so input silently dies. _check_resub forces the re-subscribe.
        self.bus.add_signal_receiver(
            self._on_props_changed, dbus_interface=DBUS_PROP,
            signal_name="PropertiesChanged", arg0="org.bluez.Device1",
            path_keyword="path")

    def _build_and_register(self):
        app = Application(self.bus)
        salt = LayoutSaltService(self.bus, 3, self._gen)  # FIRST -> shifts HID handles
        dev = DeviceInfoService(
            self.bus, 0, self.device_name, self.adapter_address)
        bat = BatteryService(self.bus, 1)
        self.hid = HidService(self.bus, 2)
        for s in (salt, dev, bat, self.hid):
            app.add_service(s)
        self.app = app
        # track every exported dbus object so _reregister tears them all down
        self._exported = [app]
        for s in (salt, dev, bat, self.hid):
            self._exported.append(s)
            for c in s.characteristics:
                self._exported.append(c)
                self._exported.extend(c.descriptors)
        gm = dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path),
                            GATT_MANAGER)
        gm.RegisterApplication(
            app.get_path(), {},
            reply_handler=lambda: print(f"gatt: app registered (gen={self._gen})"),
            error_handler=lambda e: print(f"gatt error: {e}"))

    def _reregister(self):
        """Tear down + rebuild the GATT app with a new layout, WHILE the iPad is
        connected, so BlueZ flags it change-unaware (Robust Caching)."""
        try:
            dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path),
                           GATT_MANAGER).UnregisterApplication(
                               self.app.get_path())
        except Exception as e:  # noqa: BLE001
            print(f"gatt: unregister {e}")
        for obj in self._exported:
            try:
                obj.remove_from_connection()
            except Exception:  # noqa: BLE001
                pass
        self._exported = []
        self._gen = (self._gen + 1) & 0xffff   # new layout => new hash + handles
        self._build_and_register()
        print(f"gatt: re-registered (gen={self._gen}) to invalidate iPad cache")
        return False

    def _on_props_changed(self, interface, changed, invalidated, path=None):
        if interface != "org.bluez.Device1" or path is None \
                or "Connected" not in changed \
                or not self._owns_device_path(path):
            return
        # dbus.Boolean(True) is truthy but is not guaranteed to be the singleton
        # `True`, so an identity comparison silently misses real connections.
        connected = bool(changed.get("Connected"))
        is_audio = self._device_is_audio(path)
        if connected:
            if is_audio is True:
                return
            if is_audio is False:
                self._hid_paths.add(path)
                self._hid_connected.add(path)
            # Classification can briefly be unavailable on a brand-new Device1;
            # _check_resub retries the property read after the object settles.
            GLib.timeout_add_seconds(
                RESUB_SETTLE_SECONDS, self._check_resub, path)
            return
        if path in self._hid_paths or is_audio is False:
            self._mark_hid_disconnected(path)

    def _device_is_audio(self, path):
        """Return True/False for a Device1 audio classification, else None."""
        try:
            icon = str(dbus.Interface(
                self.bus.get_object(BLUEZ, path), DBUS_PROP).Get(
                    "org.bluez.Device1", "Icon"))
        except Exception:  # noqa: BLE001
            return None
        return icon.startswith("audio")

    def _owns_device_path(self, path):
        """True only for Device1 objects belonging to this daemon's radio."""
        return str(path).startswith(self.adapter_path + "/dev_")

    def _mark_hid_disconnected(self, path):
        """Clear subscription truth when BlueZ drops a HID Device1 abruptly.

        BlueZ normally calls StopNotify, but abrupt radio/link loss can omit it.
        Leaving these flags true makes Windows render a dead iPad as connected.
        """
        self._hid_paths.add(path)
        self._hid_connected.discard(path)
        # DO NOT clear the re-subscribe counter here. The bounce below IS a
        # disconnect, so clearing it let the "give up after 2" cap reset itself
        # every cycle: try 1 -> bounce -> disconnect -> counter cleared ->
        # try 1 again, forever. That loop re-registered the GATT database tens
        # of thousands of times and tore down BOTH devices' links. The counter
        # is cleared only when the device actually subscribes (_check_resub).
        if not self.hid:
            return
        was_subscribed = bool(self.hid.kbd.notifying
                              or self.hid.mouse.notifying)
        self.hid.kbd.notifying = False
        self.hid.mouse.notifying = False
        if was_subscribed:
            print(f"gatt: {path} disconnected -- subscription state cleared")

    def _check_resub(self, path):
        if not self.hid or not self._owns_device_path(path):
            return False
        # NEVER bounce an audio device (the earbuds share this one radio);
        # only ever nudge the HID host (the iPad).
        is_audio = self._device_is_audio(path)
        if is_audio is None:
            return False   # can't classify -> do not risk bouncing the wrong dev
        if is_audio:
            return False
        self._hid_paths.add(path)
        self._hid_connected.add(path)
        if self.hid.kbd.notifying:             # already delivering -> done
            self._resub_tries.pop(path, None)
            return False
        tries = self._resub_tries.get(path, 0)
        if tries >= 2:
            print(f"gatt: {path} still unsubscribed after {tries}; giving up")
            return False
        self._resub_tries[path] = tries + 1
        print(f"gatt: {path} connected but not subscribed "
              f"(try {tries + 1}) -> cache-invalidate + bounce")
        self._reregister()
        GLib.timeout_add_seconds(2, self._bounce, path)
        return False

    def _bounce(self, path):
        # This callback runs on GLib's thread.  Advertisement confirmation waits
        # on a worker, then marshals only the actual Disconnect back to GLib.
        threading.Thread(target=self._bounce_worker, args=(path,),
                         daemon=True).start()
        return False

    def _bounce_worker(self, path):
        # _check_resub schedules this two seconds ahead.  The iPad may finish
        # subscribing in that window, so the scheduled recovery is no longer
        # authoritative.  Never disturb a link that became healthy.
        if path not in self._hid_connected:
            print(f"gatt: bounce cancelled -- {path} is no longer connected")
            return
        if self.hid and self.hid.kbd.notifying:
            self._resub_tries.pop(path, None)
            print(f"gatt: bounce skipped -- {path} subscribed in time")
            return
        if not self.start_adv():
            print("gatt: bounce cancelled -- advertisement did not start")
            return
        # Advertisement confirmation itself is asynchronous.  Recheck before
        # handing the destructive step back to GLib.
        if self.hid and self.hid.kbd.notifying:
            self._resub_tries.pop(path, None)
            print(f"gatt: bounce skipped -- {path} subscribed while "
                  "advertisement was starting")
            self.stop_adv()
            return
        GLib.idle_add(self._disconnect_for_bounce, path)

    def _disconnect_for_bounce(self, path):
        # Final race guard on GLib's thread.  Do not synchronously stop the
        # advertisement here (its BlueZ callback also needs GLib); use a worker.
        if self.hid and self.hid.kbd.notifying:
            self._resub_tries.pop(path, None)
            print(f"gatt: bounce skipped -- {path} is now subscribed")
            threading.Thread(target=self.stop_adv, daemon=True).start()
            return False
        if path not in self._hid_connected:
            print(f"gatt: bounce cancelled -- {path} disconnected first")
            return False
        try:
            dbus.Interface(self.bus.get_object(BLUEZ, path),
                           "org.bluez.Device1").Disconnect()
            print(f"gatt: bounced {path} -- iPad should re-discover + re-subscribe")
        except Exception as e:  # noqa: BLE001
            print(f"gatt: bounce {e}")
        return False

    def _adv_mgr(self):
        return dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path),
                              LE_ADV_MANAGER)

    def _adv_snapshot(self):
        with self._adv_lock:
            return {
                "advertising": bool(self.adv_on),
                "advertising_state": self.adv_state,
                "advertising_error": self.adv_error,
            }

    def _set_adv(self, enabled, timeout=6.0):
        """Set advertising and wait for BlueZ's actual completion callback.

        A request queued onto GLib is not success.  The old code set adv_on=True
        before RegisterAdvertisement completed, so Windows could show a green
        broadcast while BlueZ rejected the operation a moment later.
        """
        deadline = time.monotonic() + timeout
        with self._adv_lock:
            # Even if another BlueZ request is still pending, remember the
            # newest intent. A late start callback must not resurrect a beacon
            # after Windows has already issued cleanup-off.
            self._adv_desired = bool(enabled)
        with self._adv_command_lock:
            # If an earlier request timed out at the TCP layer, wait for its
            # callback rather than issuing a conflicting duplicate operation.
            while True:
                with self._adv_lock:
                    state = self.adv_state
                    current = bool(self.adv_on)
                    active_done = self._adv_done
                if state not in ("starting", "stopping"):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or active_done is None \
                        or not active_done.wait(remaining):
                    return False
            if current == bool(enabled):
                return True

            done = threading.Event()
            with self._adv_lock:
                self._adv_op += 1
                op = self._adv_op
                previous = bool(self.adv_on)
                self.adv_state = "starting" if enabled else "stopping"
                self.adv_error = ""
                self._adv_done = done
            GLib.idle_add(self._do_set_adv, bool(enabled), op, previous, done)
            remaining = max(0.0, deadline - time.monotonic())
            if not done.wait(remaining):
                with self._adv_lock:
                    if op == self._adv_op:
                        self.adv_error = (
                            "BlueZ advertisement start timed out"
                            if enabled else
                            "BlueZ advertisement stop timed out")
                return False
            with self._adv_lock:
                return self.adv_state == ("on" if enabled else "off") \
                    and bool(self.adv_on) == bool(enabled)

    def _finish_adv(self, enabled, op, previous, done, error=None):
        follow_up = None
        with self._adv_lock:
            if op == self._adv_op:
                if error is None:
                    self.adv_on = bool(enabled)
                    self.adv_state = "on" if enabled else "off"
                    self.adv_error = ""
                    if self._adv_desired != bool(enabled):
                        follow_up = self._adv_desired
                else:
                    # Preserve the last confirmed state.  If stop failed, it is
                    # safer to keep reporting ON than to claim the beacon ended.
                    self.adv_on = bool(previous)
                    self.adv_state = "on" if previous else "off"
                    self.adv_error = str(error)
        done.set()
        if error is None:
            print("adv: ON -- broadcasting as keyboard" if enabled
                  else "adv: OFF -- not broadcasting")
        else:
            print(f"adv {'start' if enabled else 'stop'} error: {error}")
        if follow_up is not None:
            threading.Thread(target=self._set_adv, args=(follow_up,),
                             daemon=True).start()

    def _do_set_adv(self, enabled, op, previous, done):
        try:
            if enabled:
                self._adv_mgr().RegisterAdvertisement(
                    self.adv.get_path(), {},
                    reply_handler=lambda: self._finish_adv(
                        True, op, previous, done),
                    error_handler=lambda e: self._finish_adv(
                        True, op, previous, done, e))
            else:
                self._adv_mgr().UnregisterAdvertisement(
                    self.adv.get_path(),
                    reply_handler=lambda: self._finish_adv(
                        False, op, previous, done),
                    error_handler=lambda e: self._finish_adv(
                        False, op, previous, done, e))
        except Exception as e:  # noqa: BLE001
            self._finish_adv(enabled, op, previous, done, e)
        return False

    def start_adv(self, timeout=6.0):
        """Confirmably begin advertising as a BLE keyboard."""
        return self._set_adv(True, timeout)

    def stop_adv(self, timeout=6.0):
        """Confirmably stop advertising as a BLE keyboard."""
        return self._set_adv(False, timeout)

    # ---- input helpers ----
    def send_keys(self, mods, keys):
        keys = (list(keys) + [0] * 6)[:6]
        self.hid.kbd.notify_value([mods, 0x00] + keys)

    def send_mouse(self, buttons, dx, dy, wheel):
        clamp = lambda v: max(-127, min(127, int(v))) & 0xFF
        self.hid.mouse.notify_value(
            [buttons & 0x07, clamp(dx), clamp(dy), clamp(wheel)])

    def type_text(self, text, delay=0.012):
        for ch in text:
            hit = KEYMAP.get(ch)
            if hit is None:
                continue
            mods, usage = hit
            self.send_keys(mods, [usage])
            time.sleep(delay)
            self.send_keys(0, [])
            time.sleep(delay)

    # ---- command server ----
    def command_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.command_port))
        srv.listen(4)
        print(f"cmd: {self.adapter} listening on :{self.command_port}")
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self.handle_client, args=(conn,),
                             daemon=True).start()

    def handle_client(self, conn):
        input_token = object()
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        reply = self.dispatch(
                            json.loads(line), input_token=input_token)
                    except Exception as exc:
                        reply = {"ok": False, "error": str(exc)}
                    conn.send((json.dumps(reply) + "\n").encode())
        except OSError:
            pass
        finally:
            self._release_input_owner(input_token)
            conn.close()

    def _send_owned_input(self, input_token, send):
        """Serialize a stateful HID report and make its socket the owner."""
        with self._input_lock:
            send()
            # A failed report must not supersede the connection whose prior
            # state may still be held on the remote device.
            if input_token is not None:
                self._input_owner = input_token

    def _release_input_owner(self, input_token):
        """Release all held input iff ``input_token`` still owns the lane."""
        with self._input_lock:
            if self._input_owner is not input_token:
                return False

            # Mouse first: ending a drag before releasing keyboard modifiers
            # avoids turning a stuck drag into a modified click/gesture.  Keep
            # the reports independent so one unavailable characteristic does
            # not prevent the other from being cleared.
            release_failed = False
            try:
                self.send_mouse(0, 0, 0, 0)
            except Exception as exc:  # noqa: BLE001
                release_failed = True
                print(f"input: mouse release after socket close failed: {exc}")
            try:
                self.send_keys(0, [])
            except Exception as exc:  # noqa: BLE001
                release_failed = True
                print(f"input: keyboard release after socket close failed: {exc}")
            self._input_owner = None
            if release_failed:
                print("input: owner socket closed -- neutral release attempted")
            else:
                print("input: owner socket closed -- released mouse and keyboard")
            return True

    def dispatch(self, msg, input_token=None):
        cmd = msg.get("cmd")
        if cmd == "status":
            return {"ok": True,
                    "adapter": self.adapter,
                    "adapter_path": self.adapter_path,
                    "adapter_address": self.adapter_address,
                    "device_name": self.device_name,
                    "command_port": self.command_port,
                    "kbd_subscribed": bool(self.hid.kbd.notifying),
                    "mouse_subscribed": bool(self.hid.mouse.notifying),
                    "hid_connected": bool(self._hid_connected),
                    **self._adv_snapshot()}
        if cmd == "adv":
            # explicit, user-driven broadcasting: on only via Pair/Broadcast
            if msg.get("on"):
                ok = self.start_adv()
            else:
                ok = self.stop_adv()
            return {"ok": bool(ok), **self._adv_snapshot()}
        if cmd == "disconnect":
            # disconnect the connected HID host (the iPad); NEVER an audio
            # device (the earbuds share this radio).
            n = 0
            try:
                om = dbus.Interface(self.bus.get_object(BLUEZ, "/"), DBUS_OM)
                for p, ifaces in om.GetManagedObjects().items():
                    if not self._owns_device_path(p):
                        continue
                    d = ifaces.get("org.bluez.Device1")
                    if not d or not d.get("Connected"):
                        continue
                    if str(d.get("Icon", "")).startswith("audio"):
                        continue
                    dbus.Interface(self.bus.get_object(BLUEZ, p),
                                   "org.bluez.Device1").Disconnect()
                    self._hid_paths.add(p)
                    self._mark_hid_disconnected(p)
                    n += 1
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}
            return {"ok": True, "disconnected": n}
        if cmd == "text":
            threading.Thread(target=self.type_text, args=(msg["text"],),
                             daemon=True).start()
            return {"ok": True}
        if cmd == "keys":
            m, k = msg.get("mods", 0), msg.get("keys", [])
            def _tap():
                self.send_keys(m, k); time.sleep(0.01); self.send_keys(0, [])
            threading.Thread(target=_tap, daemon=True).start()
            return {"ok": True}
        if cmd == "kbd":
            # Stateful: set the exact modifier + held-keys report, no
            # auto-release (for live keyboard passthrough).
            self._send_owned_input(
                input_token,
                lambda: self.send_keys(
                    msg.get("mods", 0), msg.get("keys", [])))
            return {"ok": True}
        if cmd == "mouse":
            self._send_owned_input(
                input_token,
                lambda: self.send_mouse(
                    msg.get("buttons", 0), msg.get("dx", 0),
                    msg.get("dy", 0), msg.get("wheel", 0)))
            return {"ok": True}
        return {"ok": False, "error": f"unknown cmd {cmd!r}"}


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    app = OpenSpanBLE()
    app.configure_adapter()
    app.register()
    threading.Thread(target=app.command_server, daemon=True).start()
    print(f"openspan-ble: up on {app.adapter} as {app.device_name!r} "
          f"(command port {app.command_port})")
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
