#!/usr/bin/env python3
"""OpenSpan BLE HID (HOGP) peripheral daemon.

iOS refuses Classic BR/EDR HID emulation from BlueZ, but it fully
supports Bluetooth Low Energy HID-over-GATT (HOGP) keyboards/mice.
This daemon publishes a GATT server (HID + Device Information +
Battery) and advertises as a keyboard, then delivers input by
notifying the Report characteristics.

Command interface (unchanged): line-oriented JSON on TCP :9955
  {"cmd":"text","text":"Hello"}
  {"cmd":"keys","mods":0,"keys":[4]}
  {"cmd":"mouse","dx":5,"dy":-3,"buttons":0,"wheel":0}
  {"cmd":"status"}
"""

import json
import socket
import threading
import time

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

BLUEZ = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"
GATT_MANAGER = "org.bluez.GattManager1"
LE_ADV_MANAGER = "org.bluez.LEAdvertisingManager1"
DBUS_OM = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP = "org.freedesktop.DBus.Properties"

GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
GATT_DESC_IFACE = "org.bluez.GattDescriptor1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"

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
    def __init__(self, bus, index):
        super().__init__(bus, index,
                         "0000180a-0000-1000-8000-00805f9b34fb", True)
        # PnP ID: vendor source=USB(2), vendor=0x1D6B, product=0x0246, ver=1
        self.add_characteristic(ReadOnlyChrc(
            bus, 0, "00002a50-0000-1000-8000-00805f9b34fb", self,
            [0x02, 0x6B, 0x1D, 0x46, 0x02, 0x01, 0x00]))
        self.add_characteristic(ReadOnlyChrc(
            bus, 1, "00002a29-0000-1000-8000-00805f9b34fb", self,
            list(b"OpenSpan")))


class BatteryService(Service):
    def __init__(self, bus, index):
        super().__init__(bus, index,
                         "0000180f-0000-1000-8000-00805f9b34fb", True)
        c = Characteristic(bus, 0, "00002a19-0000-1000-8000-00805f9b34fb",
                           ["read", "notify"], self)
        c.value = dbus.Array([dbus.Byte(100)], signature="y")
        self.add_characteristic(c)


# ---- Advertisement -----------------------------------------------------
class Advertisement(dbus.service.Object):
    def __init__(self, bus, index):
        self.path = f"/org/openspan/adv{index}"
        self.bus = bus
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
            "LocalName": dbus.String("OpenSpan Keyboard"),
            "Appearance": dbus.UInt16(0x03C1),  # HID Keyboard
            "Discoverable": dbus.Boolean(True),
            "IncludeTxPower": dbus.Boolean(True),
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
    def __init__(self):
        self.bus = dbus.SystemBus()
        self.hid = None
        self.lock = threading.Lock()
        self.adv = None
        self.adv_on = False   # broadcasting is OPT-IN -- see register()

    def configure_adapter(self):
        props = dbus.Interface(self.bus.get_object(BLUEZ, ADAPTER_PATH),
                               DBUS_PROP)
        props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "Alias", "OpenSpan Keyboard")
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
        app = Application(self.bus)
        dev = DeviceInfoService(self.bus, 0)
        bat = BatteryService(self.bus, 1)
        self.hid = HidService(self.bus, 2)
        app.add_service(dev)
        app.add_service(bat)
        app.add_service(self.hid)
        self.app = app

        gm = dbus.Interface(self.bus.get_object(BLUEZ, ADAPTER_PATH),
                            GATT_MANAGER)
        gm.RegisterApplication(app.get_path(), {},
                               reply_handler=lambda: print("gatt: app registered"),
                               error_handler=lambda e: print(f"gatt error: {e}"))

        # The LE advertisement is DELIBERATELY NOT registered here. Broadcasting
        # is OPT-IN: the app turns it on with {"cmd":"adv","on":true} when the
        # user presses Pair/Broadcast, and off again once the iPad is in.
        # Registering at boot would make this machine advertise as a Bluetooth
        # keyboard 24/7 -- and a bonded iPad would then silently reconnect on
        # its own, with nothing in the UI ever saying so. Consent, not default.
        self.adv = Advertisement(self.bus, 0)
        self.adv_on = False
        print("adv: OFF at boot -- broadcasting is opt-in (press Broadcast)")

    def _adv_mgr(self):
        return dbus.Interface(self.bus.get_object(BLUEZ, ADAPTER_PATH),
                              LE_ADV_MANAGER)

    def start_adv(self):
        """Begin advertising as a BLE keyboard. Only the app's Pair/Broadcast
        calls this. D-Bus work is marshalled onto the main loop thread."""
        if self.adv_on:
            return
        self.adv_on = True
        GLib.idle_add(self._do_start_adv)

    def _do_start_adv(self):
        def failed(e):
            self.adv_on = False
            print(f"adv error: {e}")
        self._adv_mgr().RegisterAdvertisement(
            self.adv.get_path(), {},
            reply_handler=lambda: print("adv: ON -- broadcasting as keyboard"),
            error_handler=failed)
        return False

    def stop_adv(self):
        """Stop advertising. Called the moment the iPad is in (and on cancel),
        so the machine is not left beaconing as a keyboard."""
        if not self.adv_on:
            return
        self.adv_on = False
        GLib.idle_add(self._do_stop_adv)

    def _do_stop_adv(self):
        self._adv_mgr().UnregisterAdvertisement(
            self.adv.get_path(),
            reply_handler=lambda: print("adv: OFF -- not broadcasting"),
            error_handler=lambda e: print(f"adv stop error: {e}"))
        return False

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
        srv.bind(("0.0.0.0", 9955))
        srv.listen(4)
        print("cmd: listening on :9955")
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self.handle_client, args=(conn,),
                             daemon=True).start()

    def handle_client(self, conn):
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
                        reply = self.dispatch(json.loads(line))
                    except Exception as exc:
                        reply = {"ok": False, "error": str(exc)}
                    conn.send((json.dumps(reply) + "\n").encode())
        except OSError:
            pass
        finally:
            conn.close()

    def dispatch(self, msg):
        cmd = msg.get("cmd")
        if cmd == "status":
            return {"ok": True,
                    "kbd_subscribed": bool(self.hid.kbd.notifying),
                    "mouse_subscribed": bool(self.hid.mouse.notifying),
                    "advertising": bool(self.adv_on)}
        if cmd == "adv":
            # explicit, user-driven broadcasting: on only via Pair/Broadcast
            if msg.get("on"):
                self.start_adv()
            else:
                self.stop_adv()
            return {"ok": True, "advertising": bool(self.adv_on)}
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
            self.send_keys(msg.get("mods", 0), msg.get("keys", []))
            return {"ok": True}
        if cmd == "mouse":
            self.send_mouse(msg.get("buttons", 0), msg.get("dx", 0),
                            msg.get("dy", 0), msg.get("wheel", 0))
            return {"ok": True}
        return {"ok": False, "error": f"unknown cmd {cmd!r}"}


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    app = OpenSpanBLE()
    app.configure_adapter()
    app.register()
    threading.Thread(target=app.command_server, daemon=True).start()
    print("openspan-ble: up")
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
