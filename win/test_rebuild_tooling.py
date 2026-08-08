"""Offline contract checks for the fresh-VM rebuild tooling."""

import pathlib


ROOT = pathlib.Path(__file__).parents[1]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


provision = read("guest/provision.sh")
verify = read("guest/verify-provision.sh")
cold = read("cold-test.ps1")

# Join shell continuations so command-level assertions are independent of
# formatting changes.
provision_commands = provision.replace("\\\n", " ")
runtime_block = provision_commands.split("for s in ", 1)[1].split("; do", 1)[0]

check("provision installs the Bluetooth D-Bus recovery preflight",
      "bt-preflight.sh" in runtime_block
      and 'install -m 755 "$HERE/$s" "$OPT/$s"' in provision)
check("provision installs the generic per-device unit template",
      'system/openspanble@.service' in provision
      and '"$SD/openspanble@.service"' in provision)
check("provision refuses to destroy a fixed lane that is in use",
      "legacy_in_use=0" in provision
      and "refusing an in-place migration" in provision
      and '"$SD/openspanble.service.d/20-radio.conf"' in provision
      and "all) refuse_legacy_in_place; stage1_base" in provision_commands)

check("visible legacy radio controls retain disabled compatibility tooling",
      all(item in runtime_block for item in (
          "set-hid-radio.sh", "set-hid-target.sh"))
      and 'system/openspanble.service"' in provision
      and 'system/openspanble-mac.service"' in provision)

enable_lines = [line.strip() for line in provision_commands.splitlines()
                if line.strip().startswith("systemctl enable ")]
check("provision never enables a fixed openspanble service",
      enable_lines
      and all("openspanble.service" not in line
              and "openspanble-mac.service" not in line
              for line in enable_lines))

check("verifier requires the template and current lane helpers",
      all(item in verify for item in (
          '"$SD/openspanble@.service"',
          "/opt/openspan/set-hid-device.sh",
          "/opt/openspan/bt-preflight.sh")))
check("verifier keeps compatibility units dormant but complete",
      "dormant fixed-lane compatibility" in verify
      and "/opt/openspan/set-hid-radio.sh" in verify
      and '"$SD/openspanble.service"' in verify
      and '"$SD/openspanble-mac.service"' in verify
      and '"$SD/openspanble.service.d/10-wait.conf"' in verify
      and '"$SD/openspanble.service.d/override.conf"' in verify)
check("verifier audits every configured template instance",
      "openspanble@*.service.d/20-device.conf" in verify
      and "OPENSPAN_ADAPTER" in verify
      and "OPENSPAN_PORT" in verify
      and "OPENSPAN_DEVICE_NAME" in verify)
check("verifier accepts a fresh clone before lanes are configured",
      "no lanes configured yet" in verify)
check("radio reporting enumerates N controllers instead of assuming hci0",
      "/sys/class/bluetooth/hci*" in verify
      and "hciconfig hci0" not in verify)

check("cold test refuses to stage an obsolete/incomplete guest tree",
      all(item in cold for item in (
          '"bt-preflight.sh"',
          '"set-hid-device.sh"',
          '"set-hid-radio.sh"',
          '"set-hid-target.sh"',
          '"system\\openspanble@.service"')))
check("cold test cannot stop for an interactive SSH prompt",
      cold.count('"BatchMode=yes"') == 2
      and cold.count('"ConnectTimeout=8"') == 2)
check("cold-test handoff covers each hardware-dependent lane check",
      "one controller per HID device, plus the audio/scan controller" in cold
      and "TCP NAT forward for every configured device port" in cold
      and "openspanble@ID" in cold)

print("RESULT: ALL PASS")
