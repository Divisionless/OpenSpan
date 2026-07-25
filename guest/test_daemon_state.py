"""Headless unit checks for OpenSpanBLE's local state machine.

This runs on Windows without BlueZ by stubbing only the import-time dbus/GLib
surface.  It does not touch a VM, adapter, bond, or Bluetooth radio.
"""
import importlib.util
import pathlib
import sys
import threading
import time
import types


def _decorator(*_args, **_kwargs):
    def wrap(fn):
        return fn
    return wrap


dbus = types.ModuleType("dbus")
dbus_exceptions = types.ModuleType("dbus.exceptions")
dbus_exceptions.DBusException = type("DBusException", (Exception,), {})
dbus_service = types.ModuleType("dbus.service")
dbus_service.Object = type(
    "Object", (), {"__init__": lambda self, *_a, **_k: None})
dbus_service.method = _decorator
dbus_service.signal = _decorator
dbus_mainloop = types.ModuleType("dbus.mainloop")
dbus_mainloop_glib = types.ModuleType("dbus.mainloop.glib")
dbus_mainloop_glib.DBusGMainLoop = lambda *a, **k: None
dbus.exceptions = dbus_exceptions
dbus.service = dbus_service
dbus.mainloop = dbus_mainloop
dbus.Interface = lambda obj, _iface=None: obj
dbus.Array = lambda value=(), **_k: list(value)
dbus.Byte = int
dbus.Boolean = bool
dbus.UInt16 = int
dbus.UInt32 = int
dbus.ObjectPath = str
sys.modules["dbus"] = dbus
sys.modules["dbus.exceptions"] = dbus_exceptions
sys.modules["dbus.service"] = dbus_service
sys.modules["dbus.mainloop"] = dbus_mainloop
sys.modules["dbus.mainloop.glib"] = dbus_mainloop_glib


class FakeGLib:
    @staticmethod
    def idle_add(fn, *args):
        fn(*args)
        return 1

    @staticmethod
    def timeout_add_seconds(_seconds, _fn, *_args):
        return 1

    class MainLoop:
        def run(self):
            return None


gi = types.ModuleType("gi")
gi_repository = types.ModuleType("gi.repository")
gi_repository.GLib = FakeGLib
gi.repository = gi_repository
sys.modules["gi"] = gi
sys.modules["gi.repository"] = gi_repository

path = pathlib.Path(__file__).with_name("openspan_ble.py")
spec = importlib.util.spec_from_file_location("openspan_ble_tested", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeManager:
    def __init__(self):
        self.start_error = None
        self.stop_error = None
        self.defer_start = False
        self.pending_start = None

    def RegisterAdvertisement(
            self, _path, _options, reply_handler, error_handler):
        if self.defer_start:
            self.pending_start = (reply_handler, error_handler)
            return
        if self.start_error:
            error_handler(self.start_error)
        else:
            reply_handler()

    def UnregisterAdvertisement(self, _path, reply_handler, error_handler):
        if self.stop_error:
            error_handler(self.stop_error)
        else:
            reply_handler()

    def complete_start(self):
        reply_handler, _error_handler = self.pending_start
        self.pending_start = None
        self.defer_start = False
        reply_handler()


def make_app():
    app = module.OpenSpanBLE.__new__(module.OpenSpanBLE)
    app.adapter = "hci0"
    app.adapter_address = "58:A0:23:CD:6A:B7"
    app.adapter_path = "/org/bluez/hci0"
    app.command_port = 9955
    app.device_name = "OpenSpan Keyboard"
    app.hid = types.SimpleNamespace(
        kbd=types.SimpleNamespace(notifying=False),
        mouse=types.SimpleNamespace(notifying=False))
    app.adv = types.SimpleNamespace(get_path=lambda: "/adv")
    app.adv_on = False
    app.adv_state = "off"
    app.adv_error = ""
    app._adv_lock = threading.Lock()
    app._adv_command_lock = threading.Lock()
    app._adv_done = None
    app._adv_op = 0
    app._adv_desired = False
    app._resub_tries = {}
    app._hid_paths = set()
    app._hid_connected = set()
    return app


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


app = make_app()
manager = FakeManager()
app._adv_mgr = lambda: manager

check("initial state is confirmed off",
      app.dispatch({"cmd": "status"})["advertising"] is False)
reply = app.dispatch({"cmd": "adv", "on": True})
check("start returns confirmed success",
      reply["ok"] is True and reply["advertising"] is True
      and reply["advertising_state"] == "on")
reply = app.dispatch({"cmd": "adv", "on": False})
check("stop returns confirmed success",
      reply["ok"] is True and reply["advertising"] is False
      and reply["advertising_state"] == "off")

manager.defer_start = True
check("a start timeout is not reported as broadcasting",
      app.start_adv(timeout=0.01) is False
      and app._adv_snapshot()["advertising"] is False)
late_cleanup = {"ok": None}
cleanup_thread = threading.Thread(
    target=lambda: late_cleanup.update(ok=app.stop_adv(timeout=1.0)))
cleanup_thread.start()
deadline = time.time() + 1.0
while app._adv_desired is not False and time.time() < deadline:
    time.sleep(0.001)
manager.complete_start()
cleanup_thread.join(2)
check("cleanup-off wins over a late start callback",
      late_cleanup["ok"] is True
      and app._adv_snapshot()["advertising"] is False)

manager.start_error = "controller rejected registration"
reply = app.dispatch({"cmd": "adv", "on": True})
check("start failure is honest",
      reply["ok"] is False and reply["advertising"] is False
      and "rejected" in reply["advertising_error"])

manager.start_error = None
check("start can recover after failure",
      app.dispatch({"cmd": "adv", "on": True})["ok"] is True)
manager.stop_error = "controller refused unregister"
reply = app.dispatch({"cmd": "adv", "on": False})
check("failed stop conservatively remains on",
      reply["ok"] is False and reply["advertising"] is True
      and "refused" in reply["advertising_error"])

app._device_is_audio = lambda _path: False
app._on_props_changed(
    "org.bluez.Device1", {"Connected": 1}, [],
    path="/org/bluez/hci0/dev_IPAD")
check("dbus truthy Connected is recognized",
      "/org/bluez/hci0/dev_IPAD" in app._hid_connected)
app.hid.kbd.notifying = True
app.hid.mouse.notifying = True
app._on_props_changed(
    "org.bluez.Device1", {"Connected": 0}, [],
    path="/org/bluez/hci0/dev_IPAD")
status = app.dispatch({"cmd": "status"})
check("abrupt HID disconnect clears stale subscriptions",
      status["kbd_subscribed"] is False
      and status["mouse_subscribed"] is False
      and status["hid_connected"] is False)

check("normal HID reconnect gets an eight-second subscription grace period",
      module.RESUB_SETTLE_SECONDS == 8)

ipad_identity = module._device_identity(
    "OpenSpan Keyboard", "58:A0:23:CD:6A:B7")
mac_identity = module._device_identity(
    "OpenSpan Mac Control", "AC:A7:F1:29:9F:CB")
check("iPad and Mac lanes have distinct Bluetooth product identities",
      ipad_identity["product_id"] != mac_identity["product_id"]
      and ipad_identity["model"] != mac_identity["model"]
      and ipad_identity["serial"] != mac_identity["serial"])

race = make_app()
race_path = "/org/bluez/hci0/dev_IPAD"
race._hid_connected.add(race_path)
race._resub_tries[race_path] = 1
race.hid.kbd.notifying = True
race_start_calls = []
race_idle_calls = []
race.start_adv = lambda: race_start_calls.append(True) or True
original_idle_add = module.GLib.idle_add
module.GLib.idle_add = lambda fn, *args: race_idle_calls.append((fn, args))
race._bounce_worker(race_path)
check("queued bounce is cancelled when iPad subscribes before it starts",
      not race_start_calls and not race_idle_calls
      and race_path not in race._resub_tries)

race = make_app()
race._hid_connected.add(race_path)
race._resub_tries[race_path] = 1
race_idle_calls = []
race_stop_calls = []


def subscribe_during_advertisement():
    race.hid.kbd.notifying = True
    return True


race.start_adv = subscribe_during_advertisement
race.stop_adv = lambda: race_stop_calls.append(True) or True
race._bounce_worker(race_path)
check("bounce rechecks after advertisement confirmation",
      not race_idle_calls and race_stop_calls == [True]
      and race_path not in race._resub_tries)

race = make_app()
race._hid_connected.add(race_path)
race._resub_tries[race_path] = 1
race.hid.kbd.notifying = True
race_stop_calls = []
race.stop_adv = lambda: race_stop_calls.append(True) or True
race.bus = types.SimpleNamespace(
    get_object=lambda *_args: (_ for _ in ()).throw(
        AssertionError("healthy iPad must not be disconnected")))
race._disconnect_for_bounce(race_path)
deadline = time.time() + 1.0
while not race_stop_calls and time.time() < deadline:
    time.sleep(0.001)
check("final GLib-side guard preserves a newly healthy link",
      race_stop_calls == [True] and race_path not in race._resub_tries)
module.GLib.idle_add = original_idle_add

app._on_props_changed(
    "org.bluez.Device1", {"Connected": 1}, [],
    path="/org/bluez/hci1/dev_OTHER")
check("foreign-radio device events are ignored",
      "/org/bluez/hci1/dev_OTHER" not in app._hid_connected)

check("status identifies the default fallback lane",
      status["adapter"] == "hci0"
      and status["command_port"] == 9955
      and status["device_name"] == "OpenSpan Keyboard")

secondary = make_app()
secondary.adapter = "hci1"
secondary.adapter_path = "/org/bluez/hci1"
secondary.command_port = 9956
secondary.device_name = "OpenSpan Bench"
secondary_status = secondary.dispatch({"cmd": "status"})
check("a second bench lane has independent identity and port",
      secondary_status["adapter"] == "hci1"
      and secondary_status["command_port"] == 9956
      and secondary._owns_device_path("/org/bluez/hci1/dev_MAC")
      and not secondary._owns_device_path("/org/bluez/hci0/dev_IPAD"))

print("RESULT: ALL PASS")
