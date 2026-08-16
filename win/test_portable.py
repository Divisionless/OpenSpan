"""Portable install (v3.120): what ships, what a bridge-less node says.

Three claims, each of which fails silently if it is wrong:

  1. THE PORTABLE FOLDER CARRIES NOTHING ABOUT THIS MACHINE. A leak here is a
     private SSH key or a clipboard token on somebody else's laptop, and
     nothing about the copy would look wrong. So the exclusion list is named
     file by file below and the script is actually RUN against this very repo
     -- which really does contain id_openspan, openspan_config.json and a
     clipboard token -- and the output folder is searched for them.

  2. A MACHINE WITH NO BRIDGE SAYS SO, ONCE, AND STOPS. The failure mode being
     closed is "◐ Booting… (~90s)" forever on a laptop that has no VM to boot,
     with a VBoxManage process spawned every three seconds to re-learn it.

  3. A FIRST RUN WITH NO CONFIG COMES UP CLEAN, on this PC's monitors, and says
     what it found.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openspan as A  # noqa: E402
from openspan_targets import layout_surfaces, normalize_config  # noqa: E402

failures = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)
        if detail:
            print("      " + detail)


HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPT = REPO / "install" / "make-portable.ps1"
APP_SRC = (HERE / "openspan.py").read_text(encoding="utf-8")

# THE LIST. Every one of these is a secret, a machine fact, or state the new
# node must mint for itself. Written out here rather than read from the script,
# so the test cannot agree with the script by construction.
MUST_NOT_SHIP = [
    "openspan_config.json",     # this desk, this machine's monitor identities
    "openspan_settings.json",   # holds the clipboard relay token
    "bt_prefs.json",            # which Bluetooth devices this machine knows
    "module_settings.json",     # what the modules observed on this machine
    "node.json",                # this node's PRIVATE KEY
    "peers.json",               # the shared secret of every pairing
    "status.json",
    "portal.log",
    "audio_send.log",
    "boot.log",
    "openspan_frozen.log",
    "mode.txt",
    "audio_balance.txt",
    "audio_gain.txt",
    "id_openspan",
    "id_openspan.pub",
    "id_laptop",
    "id_laptop.pub",
]
MUST_NOT_SHIP_PATTERNS = ["*.prev", "*.log", "*.lnk", "*.key", "*.pem", "*.raw"]
MUST_NOT_SHIP_DIRS = ["vm", "profiles", "build", "dist", "__pycache__", ".git"]


# =========================================================================
# 1. THE SCRIPT EXISTS, IS ASCII, AND IS AN ALLOWLIST.
# =========================================================================
check("install\\make-portable.ps1 exists", SCRIPT.is_file())
raw = SCRIPT.read_bytes() if SCRIPT.is_file() else b""
check("make-portable.ps1 is ASCII-only (PowerShell 5.1 reads it as ANSI)",
      all(byte < 128 for byte in raw),
      str([raw[max(0, i - 20):i + 5] for i, b in enumerate(raw)
           if b >= 128][:1]))
text = raw.decode("ascii", "replace")
for name in MUST_NOT_SHIP:
    check(f"the script names '{name}' as excluded", f'"{name}"' in text)
for pattern in MUST_NOT_SHIP_PATTERNS:
    check(f"the script excludes the PATTERN '{pattern}'", f'"{pattern}"' in text)
for folder in MUST_NOT_SHIP_DIRS:
    check(f"the script excludes the directory '{folder}'", f'"{folder}"' in text)
check("assembly is an ALLOWLIST -- nothing is copied wholesale and pruned",
      "Add-Item-Copy" in text
      and not re.search(r"Copy-Item[^\n]*-Recurse[^\n]*\$repo", text))
check("the manifest prints sizes and SHA-256 prefixes",
      "Get-FileHash" in text and "SHA256" in text and "Substring(0, 12)" in text)
check("-Zip produces the archive beside the folder",
      "Compress-Archive" in text and "$Out.zip" in text)
check("no '&&' anywhere -- PowerShell 5.1 cannot parse it", "&&" not in text)
check("bake-in.ps1 and swap-build.ps1 ship with the exe",
      '"bake-in.ps1"' in text and '"swap-build.ps1"' in text)
check("brand\\ ships (the icons)", 'Join-Path $repo "brand"' in text)
check("README-PORTABLE.md ships", "README-PORTABLE.md" in text)
check("README-PORTABLE.md exists to be shipped",
      (REPO / "install" / "README-PORTABLE.md").is_file())


# =========================================================================
# 2. RUN IT. The strongest available test: this repo really does contain the
#    secrets, so an assembled folder is checked for them by name.
# =========================================================================
def matches_excluded(name):
    if name in MUST_NOT_SHIP:
        return True
    for pattern in MUST_NOT_SHIP_PATTERNS:
        if pathlib.PurePath(name).match(pattern):
            return True
    return False


powershell = shutil.which("powershell") or shutil.which("pwsh")
if not powershell or not SCRIPT.is_file():
    check("PowerShell is available to run the assembly", False,
          "cannot verify the produced folder without a shell")
else:
    with tempfile.TemporaryDirectory() as tmp:
        # A stand-in exe. The real one is ~70 MB and building it is not this
        # test's job; what is being tested is the ASSEMBLY, not the compile.
        fake = os.path.join(tmp, "EsotericOS.exe")
        pathlib.Path(fake).write_bytes(b"MZ not a real build\n")
        out = os.path.join(tmp, "portable")
        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(SCRIPT), "-Exe", fake, "-Out", out],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        check("make-portable.ps1 runs cleanly", result.returncode == 0,
              (result.stderr or result.stdout or "")[-600:])
        produced = []
        for base, dirs, files in os.walk(out):
            for name in files:
                produced.append(os.path.relpath(os.path.join(base, name), out))
        check("it produced a folder with the exe in it",
              "EsotericOS.exe" in produced, str(sorted(produced)))
        leaked = sorted(p for p in produced
                        if matches_excluded(os.path.basename(p)))
        check("NOTHING machine-specific was assembled", not leaked, str(leaked))
        for folder in MUST_NOT_SHIP_DIRS:
            check(f"no {folder}\\ in the portable folder",
                  not os.path.isdir(os.path.join(out, folder)))
        # The proof that the exclusion actually had something to exclude: if
        # the repo had none of these, the test above would pass vacuously.
        present = [n for n in MUST_NOT_SHIP if (REPO / n).exists()]
        check("...and this repo really does hold files on the list, so that "
              "was not a vacuous pass", len(present) >= 3, str(present))
        blob = b""
        for rel in produced:
            try:
                blob += pathlib.Path(out, rel).read_bytes()
            except OSError:
                pass
        token = ""
        try:
            token = str(json.loads(
                (REPO / "openspan_settings.json").read_text(encoding="utf-8")
            ).get("clip_token", "") or "")
        except (OSError, ValueError):
            token = ""
        if token:
            check("the clipboard relay TOKEN is not in any shipped byte",
                  token.encode("utf-8") not in blob)
        else:
            check("the clipboard relay token check ran", True)
        check("the manifest was printed with hashes",
              "manifest" in result.stdout and "excluded by design"
              in result.stdout)
        check("the run reported skipping machine-specific files it found",
              "NOT shipped" in result.stdout, result.stdout[-400:])


# =========================================================================
# 3. VBoxManage IS PROBED, NEVER ASSUMED.
# =========================================================================
check("find_vboxmanage exists and returns a string or ''",
      isinstance(A.find_vboxmanage(), str))
src_probe = ast.get_source_segment(APP_SRC, next(
    n for n in ast.walk(ast.parse(APP_SRC))
    if isinstance(n, ast.FunctionDef) and n.name == "find_vboxmanage"))
check("it consults PATH, not only a fixed folder", "shutil.which" in src_probe)
check("it consults VirtualBox's own install variable",
      "VBOX_MSI_INSTALL_PATH" in src_probe)
check("every candidate is checked against the filesystem",
      "os.path.isfile" in src_probe)
check("no bare hardcoded VBoxManage path survives outside the probe",
      APP_SRC.count(r"C:\Program Files\Oracle\VirtualBox") == 0,
      "the old constant is gone")

with tempfile.TemporaryDirectory() as tmp:
    fake_vbox = os.path.join(tmp, "VBoxManage.exe")
    pathlib.Path(fake_vbox).write_bytes(b"x")
    os.environ["ESOTERICOS_VBOXMANAGE"] = fake_vbox
    check("an explicit override wins", A.find_vboxmanage() == fake_vbox)
    os.environ["ESOTERICOS_VBOXMANAGE"] = os.path.join(tmp, "nope.exe")
    check("an override pointing at nothing is ignored, not returned",
          A.find_vboxmanage() != os.path.join(tmp, "nope.exe"))
    del os.environ["ESOTERICOS_VBOXMANAGE"]


# =========================================================================
# 4. LAN-ONLY MODE. Faked so the answer does not depend on this machine.
# =========================================================================
real_vbox, real_state = A.VBOX, dict(A._BRIDGE_STATE)
real_runner = A.vbox
calls = []


def fake_runner(*args, **kw):
    calls.append(args)

    class R:
        returncode = 0
        stdout = fake_runner.vms
        stderr = ""
    return R()


fake_runner.vms = ""
try:
    # -- fake 1: VBoxManage is unfindable ------------------------------
    A.VBOX = ""
    A.vbox = fake_runner
    A._BRIDGE_STATE.update({"checked": False, "vbox": "", "vm": False})
    check("no VBoxManage: there is no bridge here", not A.bridge_available())
    reason = A.bridge_absence_reason()
    check("...and the reason names VirtualBox, not a fault",
          "VirtualBox is not installed" in reason
          and "error" not in reason.lower() and "fail" not in reason.lower())
    check("...and it says LAN lanes still work",
          "LAN lanes do not" in reason)
    calls.clear()
    check("vm_running() is False WITHOUT running anything",
          A.vm_running() is False and calls == [])

    # -- fake 2: VirtualBox present, the VM is not registered -----------
    A.VBOX = r"X:\fake\VBoxManage.exe"
    fake_runner.vms = '"SomeOtherVM" {0000}\n'
    A._BRIDGE_STATE.update({"checked": False})
    check("VirtualBox but no guest VM: still no bridge", not A.bridge_available())
    check("...and the reason names the missing VM",
          A.VM in A.bridge_absence_reason()
          and "not registered" in A.bridge_absence_reason())

    # -- fake 3: both present -------------------------------------------
    fake_runner.vms = f'"{A.VM}" ' + "{1111}\n"
    A._BRIDGE_STATE.update({"checked": False})
    check("VirtualBox + the guest VM: there IS a bridge", A.bridge_available())
    check("...and nothing is said about an absent one",
          A.bridge_absence_reason() == "")
    calls.clear()
    A.bridge_available()
    A.bridge_available()
    check("the answer is cached -- no VBoxManage per tick", calls == [])
finally:
    A.VBOX, A.vbox = real_vbox, real_runner
    A._BRIDGE_STATE.clear()
    A._BRIDGE_STATE.update(real_state)


# =========================================================================
# 5. status.json says vm="none", and every existing key is untouched.
# =========================================================================
KEYS = {"written", "pid", "version", "vm", "daemon", "portal", "audio",
        "devices", "broadcasting", "line", "ready", "peers"}
lan = A.status_document(running=False, ready_state="lan-only",
                        ready_text=A.LAN_ONLY_BANNER, portal_on=False,
                        audio_on=False, devices_live=0, devices_total=1,
                        advertising=False, line="", daemon_up=False,
                        bridge=False, peers_seen=2, peers_paired=1)
check("a LAN-only node reports vm='none'", lan["vm"] == "none")
check("'none' is NOT 'down' -- down means a bridge that is stopped",
      A.status_document(running=False, ready_state="stopped", ready_text="",
                        portal_on=False, audio_on=False, devices_live=0,
                        devices_total=0, advertising=False, line="",
                        daemon_up=False)["vm"] == "down")
check("the peers count is exported", lan["peers"] == {"seen": 2, "paired": 1})
check("every existing key survives, with its existing type",
      set(lan) == KEYS and isinstance(lan["devices"], dict)
      and isinstance(lan["daemon"], bool) and isinstance(lan["line"], str))
check("peers defaults to zeros, so no existing caller has to change",
      A.status_document(running=True, ready_state="ready", ready_text="",
                        portal_on=True, audio_on=True, devices_live=1,
                        devices_total=1, advertising=False, line="",
                        daemon_up=True)["peers"] == {"seen": 0, "paired": 0})
check("bridge defaults to True, so no existing caller changes meaning",
      A.status_document(running=True, ready_state="ready", ready_text="",
                        portal_on=False, audio_on=False, devices_live=0,
                        devices_total=0, advertising=False, line="",
                        daemon_up=True)["vm"] == "up")


# =========================================================================
# 6. THE BANNER, AND THE CONTROLS THAT GO QUIET.
# =========================================================================
check("the LAN-only banner says what this node IS, not what failed",
      "LAN node" in A.LAN_ONLY_BANNER and "no bridge here" in A.LAN_ONLY_BANNER
      and "Booting" not in A.LAN_ONLY_BANNER)
check("the control notice names both halves of the truth",
      "Bluetooth lanes need VirtualBox" in A.BRIDGE_ABSENT_TEXT
      and "LAN lanes do not" in A.BRIDGE_ABSENT_TEXT)

TREE = ast.parse(APP_SRC)
FUNCS = {n.name: n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}
apply_poll = ast.get_source_segment(APP_SRC, FUNCS["_apply_poll"])
check("the banner branch tests bridge_available() FIRST",
      apply_poll.index("bridge_available()")
      < apply_poll.index("elif not running"))
check("the banner state is a real state with its own console line",
      '"lan-only"' in apply_poll and "bridge_absence_reason()" in apply_poll)
check("status.json is written with the bridge fact and the peer counts",
      "bridge=bridge_available()" in apply_poll
      and "self._peer_counts()" in apply_poll)

notice = ast.get_source_segment(APP_SRC, FUNCS["_bridge_notice"])
check("the notice is secondary text, with no error colour",
      "fg=MUTED" in notice and "DANGER" not in notice)
check("the notice appears on all three bridge surfaces -- the Bluetooth pane, "
      "System control, and the Bluetooth radio section",
      APP_SRC.count("self._bridge_notice(") >= 3)
check("the VM buttons are disabled rather than removed",
      '_b.state(["disabled"])' in APP_SRC)

poll = ast.get_source_segment(APP_SRC, FUNCS["_poll"])
check("nothing polls the VM on a LAN-only node",
      "if bridge_available():" in poll)
init = ast.get_source_segment(APP_SRC, FUNCS["__init__"])
check("the VM start, the audio sender and the guest sync are all gated",
      init.count("if bridge_available():") >= 1
      and "self._start_lan_node()" in init)
vm_run = ast.get_source_segment(APP_SRC, FUNCS["vm_running"])
check("vm_running short-circuits before spawning anything",
      "if not bridge_available():" in vm_run
      and vm_run.index("bridge_available") < vm_run.index("vbox("))


# =========================================================================
# 7. FIRST RUN: no config, this PC's monitors, and a Console that says so.
# =========================================================================
live = [{"name": r"\\.\DISPLAY1", "x": 0, "y": 0, "w": 2560, "h": 1440,
         "primary": True},
        {"name": r"\\.\DISPLAY2", "x": 2560, "y": 0, "w": 1920, "h": 1080,
         "primary": False}]
fresh = normalize_config({}, live)
check("no config: the desk is exactly this PC's monitors",
      [m["name"] for m in fresh["monitors"]] == [m["name"] for m in live])
check("no config: NO devices are invented", fresh["devices"] == [])
check("no config: the surfaces are the local screens and nothing else",
      len(layout_surfaces(fresh)) == 2
      and all(s["kind"] == "local" for s in layout_surfaces(fresh)))
check("no config: portals/links are derived, not missing",
      "portals" in fresh and "links" in fresh)
check("no config: the version is stamped", fresh.get("version") == 3)
check("an empty FILE is the same as no file",
      normalize_config({}, live)["monitors"]
      == normalize_config(None, live)["monitors"])

check("FIRST_RUN is decided at import, before anything can create a config",
      APP_SRC.index("FIRST_RUN = not os.path.isfile(CONFIG)")
      < APP_SRC.index("class App"))
first = ast.get_source_segment(APP_SRC, FUNCS["_report_first_run"])
check("the first-run report is gated on FIRST_RUN", "if not FIRST_RUN:" in first)
check("it names the monitors it found", "monitor(s)" in first)
check("it says there are no devices yet, and does not invent one",
      "No devices configured yet" in first)
check("it says LAN pairing is available", "pair another" in first.lower())
check("it says whether a bridge is present, in the same breath",
      "bridge_absence_reason()" in first and "Bluetooth bridge present" in first)
check("first-run reporting runs from __init__", "self._report_first_run()" in init)

check("node.json and peers.json live on ROOT, beside the exe",
      A.NODE_FILE == os.path.join(A.ROOT, "node.json")
      and A.PEERS_FILE == os.path.join(A.ROOT, "peers.json"))
ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
for name in ("node.json", "peers.json"):
    check(f".gitignore covers {name}", re.search(rf"^{name}$", ignore,
                                                 re.M) is not None)


# =========================================================================
# 8. THE INSTALL PATH ADDS A PROGRAM RULE, AND SAYS SO.
# =========================================================================
bake = (REPO / "bake-in.ps1").read_text(encoding="utf-8")
check("bake-in.ps1 adds a firewall rule for the PROGRAM",
      'program="$exe"' in bake and "dir=in" in bake and "dir=out" in bake)
check("...scoped to private networks", "profile=private" in bake)
check("...and NOT for a port", "localport" not in bake)
check("...idempotently", "already present" in bake)
check("...and -Undo removes it again", "delete rule" in bake)
check("bake-in.ps1 explains why a port rule would be wrong",
      "every launch" in bake)
check("bake-in.ps1 is ASCII-only",
      all(byte < 128 for byte in (REPO / "bake-in.ps1").read_bytes()))


if failures:
    print(f"\nRESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("\nRESULT: ALL PORTABLE INSTALL TESTS PASSED")
