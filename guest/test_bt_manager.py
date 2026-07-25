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
check("boot helper follows the persisted multi-radio HID controller",
      "20-radio.conf" in btready
      and 'export OPENSPAN_ADAPTER="$HCI"' in btready
      and 'bluetooth/$HCI/conn_min_interval' in btready
      and 'bluetooth/$HCI/conn_max_interval' in btready)

set_hid = pathlib.Path(__file__).with_name("set-hid-radio.sh").read_text(
    encoding="utf-8")
check("radio apply tunes the resolved HID controller",
      'apply_latency "$HCI"' in set_hid
      and 'bluetooth/$hci"' in set_hid
      and '$base/conn_min_interval' in set_hid
      and '$base/conn_max_interval' in set_hid)

set_target = pathlib.Path(__file__).with_name(
    "set-hid-target.sh").read_text(encoding="utf-8")
mac_service = pathlib.Path(__file__).with_name("system").joinpath(
    "openspanble-mac.service").read_text(encoding="utf-8")
check("managed Mac lane resolves a stable controller independently",
      'openspan_bt.py resolve --controller "$CTRL"' in set_target
      and "openspanble-mac.service" in set_target)
check("managed Mac lane has an independent name and command port",
      "OPENSPAN_PORT=9956" in mac_service
      and "OpenSpan Mac Control" in mac_service)
