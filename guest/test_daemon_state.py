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


class FakeReport:
    def __init__(self, notifying=False):
        self.notifying = notifying
        self.values = []

    def notify_value(self, value):
        self.values.append(list(value))


class FakeConnection:
    def __init__(self, chunks, recv_error=None):
        self.chunks = list(chunks)
        self.recv_error = recv_error
        self.sent = []
        self.close_count = 0

    def recv(self, _size):
        if self.chunks:
            return self.chunks.pop(0)
        if self.recv_error is not None:
            raise self.recv_error
        return b""

    def send(self, value):
        self.sent.append(value)
        return len(value)

    def close(self):
        self.close_count += 1


def make_app():
    app = module.OpenSpanBLE.__new__(module.OpenSpanBLE)
    app.adapter = "hci0"
    app.adapter_address = "AA:BB:CC:00:00:01"
    app.adapter_path = "/org/bluez/hci0"
    app.command_port = 9955
    app.device_name = "OpenSpan Keyboard"
    app.hid = types.SimpleNamespace(kbd=FakeReport(), mouse=FakeReport())
    app._input_lock = threading.Lock()
    app._input_owner = None
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

# A health-check socket connects and closes every polling cycle.  It never
# sends stateful input, so its close must not emit any HID report.
ownership = make_app()
status_conn = FakeConnection([b'{"cmd":"status"}\n'])
ownership.handle_client(status_conn)
check("status-only socket close does not release input",
      ownership.hid.mouse.values == []
      and ownership.hid.kbd.values == []
      and ownership._input_owner is None
      and status_conn.close_count == 1)

# EOF and a socket error both run the same owner cleanup.  The release order is
# observable across the two report characteristics through this shared trace.
ownership = make_app()
release_trace = []
ownership.hid.mouse.notify_value = (
    lambda value: release_trace.append(("mouse", list(value))))
ownership.hid.kbd.notify_value = (
    lambda value: release_trace.append(("kbd", list(value))))
owner_conn = FakeConnection([
    b'{"cmd":"kbd","mods":2,"keys":[4]}\n'])
ownership.handle_client(owner_conn)
check("owner EOF releases mouse then keyboard exactly once",
      release_trace == [
          ("kbd", [2, 0, 4, 0, 0, 0, 0, 0]),
          ("mouse", [0, 0, 0, 0]),
          ("kbd", [0, 0, 0, 0, 0, 0, 0, 0])]
      and ownership._input_owner is None)

ownership = make_app()
error_conn = FakeConnection([
    b'{"cmd":"mouse","buttons":1,"dx":0,"dy":0,"wheel":0}\n'],
    recv_error=OSError("link lost"))
ownership.handle_client(error_conn)
check("owner socket error releases held input",
      ownership.hid.mouse.values == [[1, 0, 0, 0], [0, 0, 0, 0]]
      and ownership.hid.kbd.values == [[0, 0, 0, 0, 0, 0, 0, 0]]
      and ownership._input_owner is None)

ownership = make_app()
failing_owner = object()
ownership.dispatch(
    {"cmd": "kbd", "mods": 2, "keys": [4]}, input_token=failing_owner)
failure_trace = []


def fail_mouse_release(value):
    failure_trace.append(("mouse", list(value)))
    raise RuntimeError("mouse characteristic unavailable")


ownership.hid.mouse.notify_value = fail_mouse_release
ownership.hid.kbd.notify_value = (
    lambda value: failure_trace.append(("kbd", list(value))))
check("keyboard release is still attempted when mouse release fails",
      ownership._release_input_owner(failing_owner) is True
      and failure_trace == [
          ("mouse", [0, 0, 0, 0]),
          ("kbd", [0, 0, 0, 0, 0, 0, 0, 0])]
      and ownership._input_owner is None)

ownership = make_app()
prior_owner = object()
failed_sender = object()
ownership.dispatch(
    {"cmd": "kbd", "mods": 1, "keys": [5]}, input_token=prior_owner)
ownership.hid.mouse.notify_value = fail_mouse_release
try:
    ownership.dispatch(
        {"cmd": "mouse", "buttons": 1}, input_token=failed_sender)
except RuntimeError:
    pass
check("failed input report does not supersede the prior owner",
      ownership._input_owner is prior_owner)

# A newer input socket supersedes the old token atomically.  Closing the stale
# socket then has no authority to clear the newer socket's input.
ownership = make_app()
old_owner = object()
new_owner = object()
ownership.dispatch(
    {"cmd": "kbd", "mods": 1, "keys": [5]}, input_token=old_owner)
ownership.dispatch(
    {"cmd": "mouse", "buttons": 1}, input_token=new_owner)
before_stale_close = (
    list(ownership.hid.mouse.values), list(ownership.hid.kbd.values))
check("stale owner close cannot release newer owner",
      ownership._release_input_owner(old_owner) is False
      and before_stale_close == (
          ownership.hid.mouse.values, ownership.hid.kbd.values)
      and ownership._input_owner is new_owner)

# The release reports are one serialized transition.  A new owner that arrives
# during cleanup waits, then its report becomes the final state.
serialized = make_app()
closing_owner = object()
arriving_owner = object()
serialized.dispatch(
    {"cmd": "kbd", "mods": 1, "keys": [5]}, input_token=closing_owner)
serialized_trace = []
mouse_release_started = threading.Event()
allow_mouse_release = threading.Event()
new_send_started = threading.Event()


def blocking_mouse_release(value):
    serialized_trace.append(("mouse", list(value)))
    mouse_release_started.set()
    allow_mouse_release.wait(1.0)


serialized.hid.mouse.notify_value = blocking_mouse_release
serialized.hid.kbd.notify_value = (
    lambda value: serialized_trace.append(("kbd", list(value))))
release_thread = threading.Thread(
    target=serialized._release_input_owner, args=(closing_owner,))
release_thread.start()
check("owner cleanup reached its serialized mouse release",
      mouse_release_started.wait(1.0))


def send_from_new_owner():
    new_send_started.set()
    serialized.dispatch(
        {"cmd": "kbd", "mods": 2, "keys": [4]},
        input_token=arriving_owner)


new_send_thread = threading.Thread(target=send_from_new_owner)
new_send_thread.start()
check("new owner waits until old owner cleanup is complete",
      new_send_started.wait(1.0)
      and new_send_thread.is_alive()
      and serialized._input_owner is closing_owner)
allow_mouse_release.set()
release_thread.join(1.0)
new_send_thread.join(1.0)
check("new owner input is the final serialized state",
      not release_thread.is_alive()
      and not new_send_thread.is_alive()
      and serialized_trace == [
          ("mouse", [0, 0, 0, 0]),
          ("kbd", [0, 0, 0, 0, 0, 0, 0, 0]),
          ("kbd", [2, 0, 4, 0, 0, 0, 0, 0])]
      and serialized._input_owner is arriving_owner)

# Non-input commands carry a connection token too, but cannot take ownership.
observer = object()
ownership.dispatch({"cmd": "status"}, input_token=observer)
check("non-input socket cannot steal ownership",
      ownership._input_owner is new_owner
      and ownership._release_input_owner(observer) is False)

# notify_value records the neutral state even while BlueZ reports notification
# delivery down; cleanup does not skip one report based on the other.
ownership.hid.kbd.notifying = False
ownership.hid.mouse.notifying = False
check("owner release records neutral state while notifications are down",
      ownership._release_input_owner(new_owner) is True
      and ownership.hid.mouse.values[-1] == [0, 0, 0, 0]
      and ownership.hid.kbd.values[-1] == [0, 0, 0, 0, 0, 0, 0, 0])

check("normal HID reconnect gets an eight-second subscription grace period",
      module.RESUB_SETTLE_SECONDS == 8)

ipad_identity = module._device_identity(
    "OpenSpan Keyboard", "AA:BB:CC:00:00:01")
mac_identity = module._device_identity(
    "OpenSpan Mac Control", "AA:BB:CC:00:00:02")
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
