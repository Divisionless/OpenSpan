#!/usr/bin/env python3
"""OpenSpan HID device daemon.

Makes this machine's Bluetooth adapter present itself as a combo
keyboard + mouse to a host (iPad, phone, TV...). Requires bluetoothd
running with the input plugin disabled (-P input) so L2CAP PSMs
17/19 are free for us to bind.

Command interface: line-oriented JSON on TCP 127.0.0.1:9955
  {"cmd": "text",  "text": "Hello"}          type a string
  {"cmd": "keys",  "mods": 0, "keys": [40]}  raw HID usage codes
  {"cmd": "mouse", "dx": 5, "dy": -3, "buttons": 0, "wheel": 0}
  {"cmd": "status"}
"""

import json
import os
import socket
import threading
import time

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

P_CTRL = 17   # HID control PSM
P_INTR = 19   # HID interrupt PSM

DEVICE_NAME = "OpenSpan Keyboard"
# Major: peripheral, minor: keyboard/pointing combo
DEVICE_CLASS = 0x0005C0

KEYBOARD_MOUSE_REPORT_MAP = bytes([
    # Keyboard
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    0x85, 0x01,        #   Report ID (1)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0xE0,        #   Usage Minimum (224)
    0x29, 0xE7,        #   Usage Maximum (231)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x08,        #   Report Count (8)
    0x81, 0x02,        #   Input (Data, Variable, Absolute) ; modifiers
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x08,        #   Report Size (8)
    0x81, 0x01,        #   Input (Constant) ; reserved
    0x95, 0x05,        #   Report Count (5)
    0x75, 0x01,        #   Report Size (1)
    0x05, 0x08,        #   Usage Page (LEDs)
    0x19, 0x01,        #   Usage Minimum (1)
    0x29, 0x05,        #   Usage Maximum (5)
    0x91, 0x02,        #   Output (Data, Variable, Absolute) ; LEDs
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x03,        #   Report Size (3)
    0x91, 0x01,        #   Output (Constant)
    0x95, 0x06,        #   Report Count (6)
    0x75, 0x08,        #   Report Size (8)
    0x15, 0x00,        #   Logical Minimum (0)
    0x26, 0xFF, 0x00,  #   Logical Maximum (255)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0x00,        #   Usage Minimum (0)
    0x2A, 0xFF, 0x00,  #   Usage Maximum (255)
    0x81, 0x00,        #   Input (Data, Array) ; keys
    0xC0,              # End Collection
    # Mouse
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x02,        # Usage (Mouse)
    0xA1, 0x01,        # Collection (Application)
    0x85, 0x02,        #   Report ID (2)
    0x09, 0x01,        #   Usage (Pointer)
    0xA1, 0x00,        #   Collection (Physical)
    0x05, 0x09,        #     Usage Page (Buttons)
    0x19, 0x01,        #     Usage Minimum (1)
    0x29, 0x03,        #     Usage Maximum (3)
    0x15, 0x00,        #     Logical Minimum (0)
    0x25, 0x01,        #     Logical Maximum (1)
    0x95, 0x03,        #     Report Count (3)
    0x75, 0x01,        #     Report Size (1)
    0x81, 0x02,        #     Input (Data, Variable, Absolute)
    0x95, 0x01,        #     Report Count (1)
    0x75, 0x05,        #     Report Size (5)
    0x81, 0x03,        #     Input (Constant)
    0x05, 0x01,        #     Usage Page (Generic Desktop)
    0x09, 0x30,        #     Usage (X)
    0x09, 0x31,        #     Usage (Y)
    0x09, 0x38,        #     Usage (Wheel)
    0x15, 0x81,        #     Logical Minimum (-127)
    0x25, 0x7F,        #     Logical Maximum (127)
    0x75, 0x08,        #     Report Size (8)
    0x95, 0x03,        #     Report Count (3)
    0x81, 0x06,        #     Input (Data, Variable, Relative)
    0xC0,              #   End Collection
    0xC0,              # End Collection
])

SDP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence><uuid value="0x1124" /></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence>
        <uuid value="0x0100" />
        <uint16 value="0x0011" />
      </sequence>
      <sequence><uuid value="0x0011" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence><uuid value="0x1002" /></sequence>
  </attribute>
  <attribute id="0x0006">
    <sequence>
      <uint16 value="0x656e" />
      <uint16 value="0x006a" />
      <uint16 value="0x0100" />
    </sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence>
        <uuid value="0x1124" />
        <uint16 value="0x0100" />
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x000d">
    <sequence>
      <sequence>
        <sequence>
          <uuid value="0x0100" />
          <uint16 value="0x0013" />
        </sequence>
        <sequence><uuid value="0x0011" /></sequence>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100">
    <text value="OpenSpan Keyboard" />
  </attribute>
  <attribute id="0x0101">
    <text value="Windows-to-iPad input bridge" />
  </attribute>
  <attribute id="0x0102">
    <text value="OpenSpan" />
  </attribute>
  <attribute id="0x0201">
    <uint16 value="0x0111" />
  </attribute>
  <attribute id="0x0202">
    <uint8 value="0xC0" />
  </attribute>
  <attribute id="0x0203">
    <uint8 value="0x00" />
  </attribute>
  <attribute id="0x0204">
    <boolean value="true" />
  </attribute>
  <attribute id="0x0205">
    <boolean value="false" />
  </attribute>
  <attribute id="0x0206">
    <sequence>
      <sequence>
        <uint8 value="0x22" />
        <text encoding="hex" value="{report_map_hex}" />
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0207">
    <sequence>
      <sequence>
        <uint16 value="0x0409" />
        <uint16 value="0x0100" />
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x020b">
    <uint16 value="0x0100" />
  </attribute>
  <attribute id="0x020c">
    <uint16 value="0x0c80" />
  </attribute>
  <attribute id="0x020d">
    <boolean value="true" />
  </attribute>
  <attribute id="0x020e">
    <boolean value="true" />
  </attribute>
</record>
"""

# ASCII -> (modifier, HID usage). Mod 0x02 = left shift.
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


class Agent(dbus.service.Object):
    """NoInputNoOutput pairing agent — accepts everything."""

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        print(f"agent: authorizing {device}")

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        print(f"agent: authorizing service {uuid} for {device}")

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print(f"agent: confirming passkey {passkey:06d} for {device}")

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        print(f"agent: passkey requested by {device} -> 0")
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        print(f"agent: PIN requested by {device} -> 0000")
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print(f"agent: display passkey {passkey:06d} ({entered} entered)")

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print(f"agent: display PIN {pincode}")

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        print("agent: cancelled")


class HidDevice:
    def __init__(self):
        self.ctrl_sock = None
        self.intr_sock = None
        self.ctrl_conn = None
        self.intr_conn = None
        self.host = None
        self.protocol = 1  # 0 = boot, 1 = report
        self.lock = threading.Lock()

    # --- BlueZ setup -------------------------------------------------
    def setup_bluez(self):
        bus = dbus.SystemBus()
        adapter_path = "/org/bluez/hci0"

        props = dbus.Interface(
            bus.get_object("org.bluez", adapter_path),
            "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "Alias", DEVICE_NAME)
        props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
        props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(0))
        props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))

        agent = Agent(bus, "/openspan/agent")
        am = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                            "org.bluez.AgentManager1")
        am.RegisterAgent("/openspan/agent", "NoInputNoOutput")
        am.RequestDefaultAgent("/openspan/agent")

        record = SDP_TEMPLATE.replace(
            "{report_map_hex}", KEYBOARD_MOUSE_REPORT_MAP.hex())
        pm = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                            "org.bluez.ProfileManager1")
        pm.RegisterProfile("/openspan/profile",
                           "00001124-0000-1000-8000-00805f9b34fb",
                           {"Role": "server",
                            "RequireAuthentication": dbus.Boolean(False),
                            "RequireAuthorization": dbus.Boolean(False),
                            "AutoConnect": dbus.Boolean(True),
                            "ServiceRecord": record})
        print("bluez: adapter configured, profile registered, discoverable")

    # --- L2CAP listeners ---------------------------------------------
    def listen(self):
        self.ctrl_sock = socket.socket(socket.AF_BLUETOOTH,
                                       socket.SOCK_SEQPACKET,
                                       socket.BTPROTO_L2CAP)
        self.ctrl_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ctrl_sock.bind((socket.BDADDR_ANY, P_CTRL))
        self.ctrl_sock.listen(1)

        self.intr_sock = socket.socket(socket.AF_BLUETOOTH,
                                       socket.SOCK_SEQPACKET,
                                       socket.BTPROTO_L2CAP)
        self.intr_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.intr_sock.bind((socket.BDADDR_ANY, P_INTR))
        self.intr_sock.listen(1)
        print(f"l2cap: listening on PSM {P_CTRL}/{P_INTR}")

        while True:
            ctrl, ctrl_addr = self.ctrl_sock.accept()
            print(f"l2cap: control channel from {ctrl_addr[0]}")
            intr, intr_addr = self.intr_sock.accept()
            print(f"l2cap: interrupt channel from {intr_addr[0]}")
            with self.lock:
                self.ctrl_conn, self.intr_conn = ctrl, intr
                self.host = intr_addr[0]
            threading.Thread(target=self.drain_control, args=(ctrl,),
                             daemon=True).start()
            threading.Thread(target=self.drain_interrupt, args=(intr,),
                             daemon=True).start()

    def drain_interrupt(self, conn):
        # CRITICAL: the interrupt channel MUST be read continuously.
        # If we never recv() it, the host's frames pile up in the kernel
        # socket buffer and back-pressure hci_rx_work, wedging the whole
        # controller (every HCI command then times out with -110).
        try:
            while True:
                data = conn.recv(64)
                if not data:
                    break
                # Host->device output reports (rare for us); log and drop.
                print(f"intr: <- {data.hex()}")
        except OSError:
            pass
        print("l2cap: interrupt channel closed")

    def drain_control(self, conn):
        # Host-side HID housekeeping: GET_/SET_PROTOCOL, GET_/SET_REPORT.
        try:
            while True:
                data = conn.recv(64)
                if not data:
                    break
                hdr = data[0]
                msg_type = hdr >> 4
                print(f"ctrl: <- {data.hex()} (type {msg_type})")
                if msg_type == 4:    # GET_REPORT -> DATA, empty-ish report
                    rid = data[1] if len(data) > 1 else 0x01
                    if rid == 0x02:
                        conn.send(bytes([0xA1, 0x02, 0, 0, 0, 0]))
                    else:
                        conn.send(bytes([0xA1, 0x01, 0, 0, 0, 0, 0, 0, 0, 0]))
                elif msg_type == 5:  # SET_REPORT (e.g. LED state) -> ack
                    conn.send(bytes([0x00]))
                elif msg_type == 6:  # GET_PROTOCOL -> DATA, 1 byte
                    conn.send(bytes([0xA0, self.protocol]))
                elif msg_type == 7:  # SET_PROTOCOL -> record + ack
                    self.protocol = hdr & 0x01
                    print(f"ctrl: protocol set to "
                          f"{'report' if self.protocol else 'boot'}")
                    conn.send(bytes([0x00]))
                elif msg_type == 1 and (hdr & 0x0F) == 5:
                    print("ctrl: HID_VIRTUAL_CABLE_UNPLUG")
                    break
        except OSError:
            pass
        with self.lock:
            if self.ctrl_conn is conn:
                self.ctrl_conn = self.intr_conn = self.host = None
        print("l2cap: control channel closed")

    # --- report senders ----------------------------------------------
    def send_report(self, payload):
        with self.lock:
            conn = self.intr_conn
        if conn is None:
            raise RuntimeError("no host connected")
        conn.send(bytes([0xA1]) + payload)

    def send_keys(self, mods, keys):
        keys = (list(keys) + [0] * 6)[:6]
        if self.protocol == 0:  # boot protocol: no report ID
            self.send_report(bytes([mods, 0x00] + keys))
        else:
            self.send_report(bytes([0x01, mods, 0x00] + keys))

    def send_mouse(self, buttons, dx, dy, wheel):
        clamp = lambda v: max(-127, min(127, int(v))) & 0xFF
        if self.protocol == 0:  # boot protocol: 3-byte mouse
            self.send_report(bytes([buttons & 0x07, clamp(dx), clamp(dy)]))
        else:
            self.send_report(bytes([0x02, buttons & 0x07,
                                    clamp(dx), clamp(dy), clamp(wheel)]))

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

    # --- command server ----------------------------------------------
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

    def connect_out(self, addr):
        """Open HID channels to a paired host (we are the reconnect
        initiator, per SDP attribute 0x0205)."""
        ctrl = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                             socket.BTPROTO_L2CAP)
        ctrl.connect((addr, P_CTRL))
        intr = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                             socket.BTPROTO_L2CAP)
        intr.connect((addr, P_INTR))
        with self.lock:
            self.ctrl_conn, self.intr_conn = ctrl, intr
            self.host = addr
        threading.Thread(target=self.drain_control, args=(ctrl,),
                         daemon=True).start()
        threading.Thread(target=self.drain_interrupt, args=(intr,),
                         daemon=True).start()
        print(f"l2cap: outbound HID connection to {addr} established")

    def dispatch(self, msg):
        cmd = msg.get("cmd")
        if cmd == "connect":
            self.connect_out(msg["addr"])
            return {"ok": True, "host": msg["addr"]}
        if cmd == "status":
            with self.lock:
                return {"ok": True, "connected": self.host is not None,
                        "host": self.host}
        if cmd == "text":
            self.type_text(msg["text"])
            return {"ok": True}
        if cmd == "keys":
            self.send_keys(msg.get("mods", 0), msg.get("keys", []))
            self.send_keys(0, [])
            return {"ok": True}
        if cmd == "mouse":
            self.send_mouse(msg.get("buttons", 0), msg.get("dx", 0),
                            msg.get("dy", 0), msg.get("wheel", 0))
            return {"ok": True}
        return {"ok": False, "error": f"unknown cmd {cmd!r}"}


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    dev = HidDevice()
    dev.setup_bluez()
    threading.Thread(target=dev.listen, daemon=True).start()
    threading.Thread(target=dev.command_server, daemon=True).start()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
