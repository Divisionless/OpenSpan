"""Headless checks for controller-scoped OpenSpan Bluetooth operations."""

import importlib.util
import pathlib
import sys
import types


dbus = types.ModuleType("dbus")
dbus.exceptions = types.SimpleNamespace(DBusException=Exception)
dbus.Boolean = bool
dbus.String = str
dbus.ObjectPath = str
dbus.Interface = lambda obj, _iface=None: obj
dbus.SystemBus = lambda: None
sys.modules["dbus"] = dbus

path = pathlib.Path(__file__).with_name("openspan_bt.py")
spec = importlib.util.spec_from_file_location("openspan_bt_tested", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


objects = {
    "/org/bluez/hci0": {
        module.ADAPTER: {
            "Address": "AA:BB:CC:00:00:01",
            "Alias": "Intel",
            "Name": "Intel",
            "Powered": True,
            "Discovering": False,
        },
    },
    "/org/bluez/hci1": {
        module.ADAPTER: {
            "Address": "AA:BB:CC:00:00:02",
            "Alias": "TP-Link",
            "Name": "TP-Link",
            "Powered": True,
            "Discovering": False,
        },
    },
    "/org/bluez/hci0/dev_60_8B_0E_05_72_82": {
        module.DEVICE: {
            "Address": "aa:bb:cc:00:00:10",
            "Name": "iPad",
            "Alias": "iPad",
            "Icon": "input-keyboard",
            "Paired": True,
            "Trusted": True,
            "Connected": True,
        },
    },
    "/org/bluez/hci0/dev_D0_11_E5_F2_7E_2A": {
        module.DEVICE: {
            "Address": "aa:bb:cc:00:00:11",
            "Name": "Managed Mac",
            "Alias": "Managed Mac",
            "Icon": "input-keyboard",
            "Paired": True,
            "Trusted": True,
            "Connected": False,
        },
    },
    "/org/bluez/hci1/dev_B3_BD_E8_69_E5_59": {
        module.DEVICE: {
            "Address": "aa:bb:cc:00:00:20",
            "Name": "Onn",
            "Alias": "Onn",
            "Icon": "audio-card",
            "Paired": True,
            "Trusted": True,
            "Connected": True,
        },
    },
    "/org/bluez/hci1/dev_60_8B_0E_05_72_82": {
        module.DEVICE: {
            "Address": "aa:bb:cc:00:00:10",
            "Name": "Apple's iPad (2)",
            "Alias": "Apple's iPad (2)",
            "Icon": "input-keyboard",
            "Paired": True,
            "Trusted": True,
            "Connected": False,
        },
    },
}

bluez = module.Bluez(bus=object())
bluez.objects = lambda: objects

radios = bluez.radios()
devices = bluez.devices()
check("two controllers are enumerated", len(radios) == 2)
check("controller identities are stable MAC addresses",
      {row["address"] for row in radios}
      == {"AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02"})
check("devices retain their owning controller",
      {(row["address"], row["controller"]) for row in devices}
      == {
          ("AA:BB:CC:00:00:10", "AA:BB:CC:00:00:01"),
          ("AA:BB:CC:00:00:10", "AA:BB:CC:00:00:02"),
          ("AA:BB:CC:00:00:20", "AA:BB:CC:00:00:02"),
          ("AA:BB:CC:00:00:11", "AA:BB:CC:00:00:01"),
      })
check("controller lookup accepts current hci name",
      bluez.radio("hci1")["address"] == "AA:BB:CC:00:00:02")
check("controller lookup accepts stable MAC",
      bluez.radio("aa-bb-cc-00-00-01")["hci"] == "hci0")
check("legacy audio pins remain readable",
      module.parse_audio_pin("AA:BB:CC:00:00:20")
      == ("", "AA:BB:CC:00:00:20"))
check("multi-radio audio pins preserve controller ownership",
      module.parse_audio_pin(
          module.format_audio_pin(
              "AA:BB:CC:00:00:02", "AA:BB:CC:00:00:20"))
      == ("AA:BB:CC:00:00:02", "AA:BB:CC:00:00:20"))
check("audio classification is icon-scoped",
      module.is_audio({"Icon": "audio-card"})
      and not module.is_audio({"Icon": "input-keyboard"}))

sink = {
    "name": "bluez_output.AA_BB_CC_00_00_20.1",
    "properties": {
        "api.bluez5.address": "AA:BB:CC:00:00:20",
        "device.bus": "bluetooth",
    },
    "volume": {
        "front-left": {"value": 32768, "value_percent": "50%"},
        "front-right": {"value": 32768, "value_percent": "50%"},
    },
}
check("a bluez sink maps to its exact Bluetooth address",
      module.sink_bluetooth_address(sink) == "AA:BB:CC:00:00:20")
check("non-Bluetooth and conflicting sinks are never addressable",
      module.sink_bluetooth_address({
          "name": "alsa_output.pci-card",
          "properties": {"api.bluez5.address": "AA:BB:CC:00:00:20"},
      }) == ""
      and module.sink_bluetooth_address({
          "name": "bluez_output.AA_BB_CC_00_00_21.1",
          "properties": {"device.string": "AA:BB:CC:00:00:20"},
      }) == "")

original_run = module.subprocess.run
run_calls = []


def fake_run(command, **kwargs):
    run_calls.append((command, kwargs))
    return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")


module.subprocess.run = fake_run
poisoned_env = {
    "PULSE_COOKIE": "/tmp/wrong-cookie",
    "PULSE_SINK": "wrong-sink",
    "PULSE_SOURCE": "wrong-source",
    "PULSE_CLIENTCONFIG": "/tmp/wrong-client.conf",
}
saved_env = {key: module.os.environ.get(key) for key in poisoned_env}
module.os.environ.update(poisoned_env)
try:
    module._run_pactl(["list", "sinks"], json_output=True)
finally:
    for key, value in saved_env.items():
        if value is None:
            module.os.environ.pop(key, None)
        else:
            module.os.environ[key] = value
check("pactl uses argv and the dedicated root PipeWire session",
      run_calls[0][0] == ["pactl", "--format=json", "list", "sinks"]
      and not run_calls[0][1].get("shell", False)
      and run_calls[0][1]["env"]["XDG_RUNTIME_DIR"] == "/run/user/0"
      and run_calls[0][1]["env"]["DBUS_SESSION_BUS_ADDRESS"]
      == "unix:path=/run/user/0/bus"
      and run_calls[0][1]["env"]["PULSE_SERVER"]
      == "unix:/run/user/0/pulse/native"
      and not any(key in run_calls[0][1]["env"] for key in poisoned_env))
module.subprocess.run = original_run

original_sinks = module._pactl_sinks
original_pactl = module._run_pactl
module._pactl_sinks = lambda: [sink]
check("audio-levels reports a clamped integer level by MAC",
      module.audio_levels() == {"AA:BB:CC:00:00:20": 50})
pactl_calls = []
module._run_pactl = lambda args, json_output=False: pactl_calls.append(
    (list(args), json_output)) or ""
check("set-audio-level targets only the mapped sink with argv, not a shell",
      module.set_audio_level("aa-bb-cc-00-00-20", 37) == 37
      and pactl_calls == [([
          "set-sink-volume", "bluez_output.AA_BB_CC_00_00_20.1", "37%"],
          False)])

pactl_calls.clear()
try:
    module.set_audio_level("AA:BB:CC:00:00:99", 37)
except RuntimeError:
    pass
else:
    raise AssertionError("accepted an address with no unique live sink")
check("a missing device is refused without falling back to another sink",
      pactl_calls == [])

duplicate = dict(sink)
duplicate["name"] = "bluez_output.AA_BB_CC_00_00_20.a2dp-sink"
module._pactl_sinks = lambda: [sink, duplicate]
check("ambiguous duplicate sinks are refused instead of guessed",
      module.audio_levels() == {})
for invalid_level in (-1, 101, 1.5, True):
    try:
        module.set_audio_level("AA:BB:CC:00:00:20", invalid_level)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted invalid audio level {invalid_level!r}")
check("set-audio-level accepts integers from 0 through 100 only", True)
module._pactl_sinks = original_sinks
module._run_pactl = original_pactl

check("Mac lane rejects Apple mobile-device bonds",
      module.is_wrong_target_hid(
          {"name": "Apple's iPad (2)", "alias": ""}, "mac")
      and module.is_wrong_target_hid(
          {"name": "", "alias": "Douglas iPhone"}, "mac")
      and not module.is_wrong_target_hid(
          {"name": "Douglas MacBook Pro", "alias": ""}, "mac")
      and not module.is_wrong_target_hid(
          {"name": "Apple's iPad (2)", "alias": ""}, "ipad")
      and module.is_wrong_target_hid(
          {"name": "Douglas MacBook Pro", "alias": ""}, "ipad"))
check("wrong iPad bond cannot make the Mac lane look paired",
      bluez.hid_paired("hci1") is True
      and bluez.hid_paired("hci1", target="mac") is False)
removed = []
disconnected = []
bluez.read_audio_pin = lambda: ("", "")
bluez.adapter_iface = lambda _radio: types.SimpleNamespace(
    RemoveDevice=lambda path, **_kwargs: removed.append(str(path)))
bluez.device_iface = lambda row: types.SimpleNamespace(
    Disconnect=lambda **_kwargs: disconnected.append(row["path"]))
bluez.set_property = lambda *_args, **_kwargs: None
check("Mac preparation removes the wrong-lane iPad bond",
      bluez.prepare_hid("hci1", target="mac") == "READY"
      and removed == ["/org/bluez/hci1/dev_60_8B_0E_05_72_82"])
removed.clear()
check("iPad unpair removes only iPad identities on its controller",
      bluez.forget_hid("hci0", target="ipad") == 1
      and removed == ["/org/bluez/hci0/dev_60_8B_0E_05_72_82"])

btready = pathlib.Path(__file__).with_name("btready.sh").read_text(
    encoding="utf-8")
check("boot helper resolves OPENSPAN_CONTROLLER from the drop-in",
      "20-radio.conf" in btready
      and "OPENSPAN_CONTROLLER=" in btready
      and "openspan_bt.py resolve --controller" in btready
      and 'export OPENSPAN_ADAPTER="$HCI"' in btready
      and 'bluetooth/$HCI/conn_min_interval' in btready
      and 'bluetooth/$HCI/conn_max_interval' in btready)
check("boot helper falls back to legacy OPENSPAN_ADAPTER drop-ins",
      'OPENSPAN_ADAPTER=' in btready)

set_hid = pathlib.Path(__file__).with_name("set-hid-radio.sh").read_text(
    encoding="utf-8")
check("radio apply tunes the resolved HID controller",
      'apply_latency "$HCI"' in set_hid
      and 'bluetooth/$hci"' in set_hid
      and '$base/conn_min_interval' in set_hid
      and '$base/conn_max_interval' in set_hid)
check("radio apply writes OPENSPAN_CONTROLLER, not OPENSPAN_ADAPTER",
      "OPENSPAN_CONTROLLER=$CTRL_UPPER" in set_hid
      and "Environment=OPENSPAN_ADAPTER=" not in set_hid)
check("radio apply checks for duplicate lanes by stable MAC",
      "OPENSPAN_CONTROLLER=$CTRL_UPPER" in set_hid)

set_target = pathlib.Path(__file__).with_name(
    "set-hid-target.sh").read_text(encoding="utf-8")
mac_service = pathlib.Path(__file__).with_name("system").joinpath(
    "openspanble-mac.service").read_text(encoding="utf-8")
check("managed Mac lane resolves a stable controller independently",
      'openspan_bt.py resolve --controller "$CTRL"' in set_target
      and "openspanble-mac.service" in set_target)
check("managed Mac drop-in stores OPENSPAN_CONTROLLER, not OPENSPAN_ADAPTER",
      "OPENSPAN_CONTROLLER=$CTRL_UPPER" in set_target
      and "Environment=OPENSPAN_ADAPTER=" not in set_target)
check("managed Mac lane has an independent name and command port",
      "OPENSPAN_PORT=9956" in mac_service
      and "OpenSpan Mac Control" in mac_service)

set_device = pathlib.Path(__file__).with_name("set-hid-device.sh").read_text(
    encoding="utf-8")
check("per-device drop-in stores OPENSPAN_CONTROLLER, not OPENSPAN_ADAPTER",
      "OPENSPAN_CONTROLLER=$CTRL_UPPER" in set_device
      and "Environment=OPENSPAN_ADAPTER=" not in set_device)
check("per-device duplicate check compares stable MACs",
      'grep -qx "Environment=OPENSPAN_CONTROLLER=$CTRL_UPPER"' in set_device)

start_lane = pathlib.Path(__file__).with_name("start-ble-lane.sh").read_text(
    encoding="utf-8")
check("lane wrapper resolves OPENSPAN_CONTROLLER to OPENSPAN_ADAPTER",
      "OPENSPAN_CONTROLLER" in start_lane
      and "openspan_bt.py resolve" in start_lane
      and "export OPENSPAN_ADAPTER" in start_lane)
check("lane wrapper falls back when OPENSPAN_CONTROLLER is unset",
      'OPENSPAN_CONTROLLER:-}' in start_lane)
check("lane wrapper runs wait-hci0 and ensure-dualmode before the daemon",
      "wait-hci0.sh" in start_lane
      and "ensure-dualmode.sh" in start_lane
      and "exec" in start_lane
      and "openspan_ble.py" in start_lane)

template = pathlib.Path(__file__).with_name("system").joinpath(
    "openspanble@.service").read_text(encoding="utf-8")
check("template service delegates to start-ble-lane.sh",
      "start-ble-lane.sh" in template
      and "ExecStartPre" not in template)
