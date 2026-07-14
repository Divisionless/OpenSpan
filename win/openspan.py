#!/usr/bin/env python3
"""OpenSpan — one dark-mode window that runs the whole PC → iPad bridge.

Everything in one place: the live screen arrangement (drag the iPad to
where it sits), start/stop the bridge VM and input portal, broadcast for
pairing, hand off the Bluetooth radio, and edit the keymap.

Pure standard library (tkinter + ctypes). No dependencies.
"""

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# reuse the monitor enumeration + presets from the setup module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openspan_setup import enum_monitors, IPAD_PRESETS  # noqa: E402

# Frozen (OpenSpan.exe) or plain-Python: either way the data files live in
# a fixed layout — ROOT holds the configs/keys, ROOT\win the scripts. In the
# frozen dist folder the exe sits AT ROOT and __file__ points inside the
# bundle, so anchor on the executable there.
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
    HERE = os.path.join(ROOT, "win")
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.abspath(os.path.join(HERE, ".."))
VBOX = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VM = "OpenSpan"
DAEMON = ("127.0.0.1", 9955)
KEY = os.path.join(ROOT, "id_openspan")
KEYMAP = os.path.join(ROOT, "openspan_keymap.json")
CONFIG = os.path.join(ROOT, "openspan_config.json")
LOG = os.path.join(ROOT, "portal.log")
AUDIO_SEND = os.path.join(HERE, "win_audio_send.py")
AUDIO_LOG = os.path.join(ROOT, "audio_send.log")
BT_PREFS = os.path.join(ROOT, "bt_prefs.json")
ICON = os.path.join(ROOT, "openspan.ico")

PYW = sys.executable
if PYW.lower().endswith("python.exe"):
    _c = PYW[:-len("python.exe")] + "pythonw.exe"
    if os.path.exists(_c):
        PYW = _c
# the portal and audio sender are separate PROCESSES: scripts under plain
# Python, role flags of the same exe when frozen (see openspan_launcher.py)
if getattr(sys, "frozen", False):
    PORTAL_CMD = [sys.executable, "--portal"]
    AUDIO_CMD = [sys.executable, "--audio"]
else:
    PORTAL_CMD = [PYW, os.path.join(HERE, "openspan_portal.py")]
    AUDIO_CMD = [PYW, AUDIO_SEND]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---- dark theme palette ----
BG = "#14161c"
PANEL = "#1d212b"
CARD = "#232936"
FG = "#dfe4ee"
MUTED = "#8b93a7"
ACCENT = "#3fdc8a"
ACCENT_DIM = "#1f6f43"
MON_FILL = "#26324c"
MON_LINE = "#4a6ea8"
IPAD_FILL = "#1f6f43"
IPAD_LINE = "#3fdc8a"
PORTAL = "#ffd43b"
DANGER = "#e06c68"
SCRIM = "#0a0b0e"   # near-black overlay behind an in-frame modal
BORDER = "#39435a"  # card edge for the in-frame modal


# ---- themed dialogs ---------------------------------------------------------
# The native tk messagebox renders in the OS (light) theme, which clashes badly
# with the dark app. These are drop-in dark replacements that live INSIDE the
# app's look: dark background, themed buttons, a dark title bar, modal, centered
# over the parent. dark_confirm mirrors messagebox.askyesno (returns bool);
# dark_alert mirrors a single-button showwarning/showinfo.
def _paint_dark_titlebar(win):
    """Paint a window's Windows title bar dark (DWM immersive dark mode)."""
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # 20 = Win11/20H1+, 19 = older Win10
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:  # noqa: BLE001
        pass


def _dialog(parent, title, message, buttons):
    """Show a dark modal dialog INSIDE the app window — an in-frame overlay, not
    a separate OS window — and block until a button is chosen. `buttons` is a
    list of (text, value, style), primary first; returns the chosen value (the
    last button's value on Escape or a click outside the card). Enter = first
    button. Keeps a synchronous return so callers stay unchanged."""
    top = parent.winfo_toplevel()
    # re-entrancy guard: a second trigger while a modal is open (e.g. the title
    # X hit twice) must not stack a second overlay
    if getattr(top, "_os_modal_open", False):
        return buttons[-1][1]
    top._os_modal_open = True

    result = {"v": buttons[-1][1]}
    done_var = tk.StringVar(master=top, value="")

    def done(v):
        if done_var.get():
            return  # first click wins; ignore the rest
        result["v"] = v
        done_var.set("1")

    # full-window scrim: dims the app and swallows clicks (modal within frame).
    # Overhang every edge by 20px (relwidth=1 + width=40, offset -20) so no
    # sliver of the app can peek past it regardless of borders/geometry.
    scrim = tk.Frame(top, bg=SCRIM)
    scrim.place(x=-20, y=-20, relwidth=1, relheight=1, width=40, height=40)
    scrim.lift()
    scrim.bind("<Button-1>", lambda e: done(buttons[-1][1]))

    # centered card (same interior look as the app: BG panel, themed buttons)
    card = tk.Frame(scrim, bg=BG, highlightbackground=BORDER,
                    highlightthickness=1)
    card.place(relx=0.5, rely=0.44, anchor="center")
    card.bind("<Button-1>", lambda e: "break")  # a card click is not a cancel
    inner = tk.Frame(card, bg=BG)
    inner.pack(padx=26, pady=22)
    tk.Label(inner, text=title, bg=BG, fg=FG, justify="left",
             font=("Segoe UI Semibold", 13)).pack(anchor="w")
    if message:
        try:  # wrap to the window so a small/compact window still fits
            wl = max(240, min(400, top.winfo_width() - 80))
        except tk.TclError:
            wl = 360
        tk.Label(inner, text=message, bg=BG, fg=MUTED, justify="left",
                 wraplength=wl, font=("Segoe UI", 10)).pack(anchor="w",
                                                            pady=(10, 0))
    bar = tk.Frame(inner, bg=BG)
    bar.pack(anchor="e", pady=(20, 0))
    focus_btn = None
    for i, (text, value, style) in enumerate(buttons):
        b = ttk.Button(bar, text=text, style=style,
                       command=lambda v=value: done(v))
        b.pack(side="left", padx=(0, 8) if i < len(buttons) - 1 else 0)
        if i == 0:
            focus_btn = b

    prev_ret, prev_esc = top.bind("<Return>"), top.bind("<Escape>")
    top.bind("<Return>", lambda e: done(buttons[0][1]))
    top.bind("<Escape>", lambda e: done(buttons[-1][1]))
    try:
        scrim.grab_set()  # keyboard modality; the scrim already blocks the mouse
    except tk.TclError:
        pass
    if focus_btn is not None:
        focus_btn.focus_set()
    try:
        top.wait_variable(done_var)
    finally:
        try:
            scrim.grab_release()
        except tk.TclError:
            pass
        top.unbind("<Return>")
        top.unbind("<Escape>")
        if prev_ret:
            top.bind("<Return>", prev_ret)
        if prev_esc:
            top.bind("<Escape>", prev_esc)
        scrim.destroy()
        top._os_modal_open = False
    return result["v"]


def dark_confirm(parent, title, message, yes="Yes", no="No"):
    """Dark drop-in for messagebox.askyesno — returns True on yes, else False."""
    return _dialog(parent, title, message,
                   [(yes, True, "Accent.TButton"), (no, False, "TButton")])


def dark_alert(parent, title, message, ok="OK"):
    """Dark drop-in for messagebox.showwarning/showinfo — single OK button."""
    _dialog(parent, title, message, [(ok, True, "Accent.TButton")])


# ---- console log sink -------------------------------------------------------
# Every command the app runs (VBoxManage + ssh into the VM) is mirrored to the
# right-hand console panel. The App installs the sink once its console exists;
# until then _emit is a no-op. Routine health polls pass quiet=True so the 3s
# tick never floods the console -- only meaningful commands show.
_LOG_SINK = None


def set_log_sink(fn):
    global _LOG_SINK
    _LOG_SINK = fn


def _emit(kind, text):
    fn = _LOG_SINK
    if fn and text:
        try:
            fn(kind, str(text))
        except Exception:  # noqa: BLE001
            pass


def vbox(*args, quiet=False):
    if not quiet:
        _emit("cmd", "VBoxManage " + " ".join(str(a) for a in args))
    try:
        r = subprocess.run([VBOX, *args], capture_output=True, text=True,
                           timeout=30, creationflags=NO_WINDOW)
        if not quiet:
            out = (r.stderr or r.stdout or "").strip()
            _emit("err" if r.returncode else "ok", out[:240] or "ok")
        return r
    except Exception as e:  # noqa: BLE001
        if not quiet:
            _emit("err", str(e)[:240])

        class R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return R()


def ensure_ssh_key():
    """Generate the host<->VM SSH key on first run if it's missing, so a
    fresh clone can reach its own bridge (the private key is gitignored and
    must never ship). ed25519, no passphrase -- this is an unattended
    loopback to a local VM. Returns True if a key exists/was made.

    The PUBLIC half (id_openspan.pub) still has to land in the VM's
    /root/.ssh/authorized_keys; that's the VM provisioner's job
    (guest/install-authorized-key.sh) -- this only guarantees the host has
    a key to offer. Never regenerates an existing key."""
    if os.path.exists(KEY):
        return True
    try:
        r = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", KEY,
             "-C", "openspan-host"],
            capture_output=True, text=True, timeout=30,
            creationflags=NO_WINDOW)
        if r.returncode == 0 and os.path.exists(KEY):
            _emit("event", "generated a new bridge SSH key "
                           f"({os.path.basename(KEY)}). Install its .pub in "
                           "the VM (the provisioner does this).")
            return True
        _emit("err", "couldn't generate the SSH key: "
                     + (r.stderr or "ssh-keygen failed")[:180])
    except FileNotFoundError:
        _emit("err", "ssh-keygen not found — install the Windows OpenSSH "
                     "client, or drop an id_openspan key in the app folder.")
    except Exception as e:  # noqa: BLE001
        _emit("err", f"SSH key generation failed: {e}")
    return False


def ssh_guest(cmd, timeout=20, quiet=False, show_result=True):
    if not quiet:
        _emit("cmd", "ssh: " + " ".join(cmd.split())[:240])
    try:
        r = subprocess.run(
            ["ssh", "-p", "2222", "-i", KEY,
             "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ConnectTimeout=6", "root@127.0.0.1", cmd],
            capture_output=True, text=True, timeout=timeout,
            creationflags=NO_WINDOW)
        if not quiet and show_result:
            out = (r.stdout or r.stderr or "").strip()
            if out:
                _emit("err" if r.returncode else "ok", out[:240])
        return r
    except Exception as e:  # noqa: BLE001
        if not quiet:
            _emit("err", str(e)[:240])

        class R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return R()


def vm_running():
    return f'"{VM}"' in (vbox("list", "runningvms", quiet=True).stdout or "")


def load_bt_prefs():
    """Local, persistent Bluetooth prefs: custom names (survive re-pairing) and
    a blacklist of devices that never show in scans. Keyed by MAC."""
    try:
        with open(BT_PREFS) as f:
            d = json.load(f)
        return {"renames": dict(d.get("renames", {})),
                "blacklist": set(d.get("blacklist", []))}
    except (OSError, ValueError):
        return {"renames": {}, "blacklist": set()}


def save_bt_prefs(prefs):
    try:
        with open(BT_PREFS, "w") as f:
            json.dump({"renames": prefs["renames"],
                       "blacklist": sorted(prefs["blacklist"])}, f, indent=2)
    except OSError:
        pass


def start_vm_clean():
    """Start the VM headless with a guaranteed COLD boot. VirtualBox saves the
    VM state on host shutdown; resuming a saved state skips the kernel cmdline
    (USB autosuspend off) and can leave the passed-through Bluetooth radio
    wedged. Discarding any saved state first forces a clean boot + a clean
    radio re-enumeration on xHCI."""
    _emit("event", "starting the bridge VM (clean cold boot)…")
    info = vbox("showvminfo", VM, "--machinereadable", quiet=True).stdout or ""
    if 'VMState="saved"' in info:
        vbox("discardstate", VM)
    vbox("startvm", VM, "--type", "headless")


def daemon_status():
    try:
        s = socket.create_connection(DAEMON, 2)
        s.sendall(b'{"cmd":"status"}\n')
        s.settimeout(2)
        # read to the newline the daemon terminates replies with -- a single
        # recv can legally return a partial JSON line
        data = b""
        while b"\n" not in data and len(data) < 4096:
            chunk = s.recv(512)
            if not chunk:
                break
            data += chunk
        s.close()
        return json.loads(data.split(b"\n", 1)[0].decode())
    except Exception:  # noqa: BLE001
        return None


def daemon_cmd(obj):
    """Send one command to the daemon and return its reply (or None)."""
    try:
        s = socket.create_connection(DAEMON, 2)
        s.sendall((json.dumps(obj) + "\n").encode())
        s.settimeout(2)
        data = b""
        while b"\n" not in data and len(data) < 4096:
            chunk = s.recv(512)
            if not chunk:
                break
            data += chunk
        s.close()
        return json.loads(data.split(b"\n", 1)[0].decode())
    except Exception:  # noqa: BLE001
        return None


def set_advertising(on):
    """Broadcasting is OPT-IN. The daemon no longer advertises at boot, so this
    is the ONLY thing that makes the machine visible as a Bluetooth keyboard --
    and it is called only from Pair/Broadcast. It is switched back off the
    moment the iPad is in, so the PC is never left beaconing and a bonded iPad
    cannot silently reconnect on its own."""
    r = daemon_cmd({"cmd": "adv", "on": bool(on)})
    return bool(r and r.get("ok"))


_ELEVATED = None


def is_elevated():
    """True if OpenSpan is running with administrator rights.

    THIS MATTERS FAR MORE THAN IT LOOKS. Windows UIPI: a NON-elevated process's
    low-level input hooks receive NOTHING while an ELEVATED window has focus.
    So if you run anything as admin (an admin terminal, say), the portal goes
    silently deaf the instant that window is focused -- the mouse just stops
    crossing the border. No error, no exception, nothing in any log, and the
    hooks still report as successfully installed. It cost days to find.

    Rule: OpenSpan must run at least as elevated as the apps you use."""
    global _ELEVATED
    if _ELEVATED is None:
        try:
            import ctypes
            _ELEVATED = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            _ELEVATED = False
    return _ELEVATED


# ---- clipboard relay (two-way clipboard with the iPad) ---------------------
# See CLIPBOARD_DESIGN.md. The iPad's Shortcuts app calls these endpoints
# (triggered by FKA key combos the portal sends through the HID keyboard):
#   GET  /clip  -> the Windows clipboard text        ("Paste from PC")
#   POST /clip  -> sets the Windows clipboard        ("Copy to PC")
# Token-gated: the clipboard carries passwords, and any LAN device can
# reach the port. Text only (CF_UNICODETEXT), UTF-8 on the wire.
SETTINGS = os.path.join(ROOT, "openspan_settings.json")
CLIP_MAX = 10 * 1024 * 1024  # 10 MB cap on inbound clipboard payloads
BAL_FILE = os.path.join(ROOT, "audio_balance.txt")  # -1 (L) .. +1 (R); the
#   audio sender polls this every 150ms and applies it as channel gains


# Volume for the compact slider is handled by App._volume_thread — ALL Core
# Audio COM lives on that one dedicated thread (the sender's proven pattern).
# COM on the Tk thread froze the whole UI for the RPC duration whenever
# AudioSrv was busy or the endpoint needed re-acquiring.


def clipboard_config():
    """Token + port for the relay, persisted in openspan_settings.json
    (token is generated once; the iPad shortcuts carry it in a header).
    NEVER destroys the user's settings: an unparseable file is backed up to
    .bad instead of being silently rewritten, and the write is atomic
    (tmp + os.replace) so a crash can't manufacture a corrupt file."""
    cfg = {}
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        try:
            os.replace(SETTINGS, SETTINGS + ".bad")
            _emit("err", "openspan_settings.json was unreadable — moved to "
                         ".bad and rebuilt (check your custom settings).")
        except OSError:
            pass
    changed = False
    if not cfg.get("clipboard_token"):
        import uuid
        cfg["clipboard_token"] = uuid.uuid4().hex
        changed = True
    if not cfg.get("clipboard_port"):
        cfg["clipboard_port"] = 9966
        changed = True
    if changed:
        try:
            tmp = SETTINGS + ".new"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, SETTINGS)
        except OSError:
            pass
    return cfg["clipboard_token"], int(cfg["clipboard_port"])


def lan_ip():
    """This PC's LAN address (no packets are sent by a UDP connect)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET: never actually routed to
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def get_clipboard_text():
    """Read Unicode text from the Windows clipboard (stdlib ctypes)."""
    import ctypes
    CF_UNICODETEXT = 13
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    # HANDLE-returning calls MUST be prototyped: ctypes defaults restype to
    # 32-bit int, silently truncating 64-bit clipboard handles -> GlobalLock
    # on the mangled handle returns NULL and reads come back empty
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    u.GetClipboardData.restype = ctypes.c_void_p
    u.GetClipboardData.argtypes = [ctypes.c_uint]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    for _ in range(5):  # clipboard is a contended global -- retry briefly
        if u.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return ""
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.c_wchar_p(p).value or ""
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def set_clipboard_text(text):
    """Put Unicode text on the Windows clipboard (stdlib ctypes)."""
    import ctypes
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k.GlobalFree.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.restype = ctypes.c_void_p
    u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    for _ in range(5):
        if u.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            return False
        ctypes.memmove(p, buf, size)
        k.GlobalUnlock(h)
        if not u.SetClipboardData(CF_UNICODETEXT, h):
            k.GlobalFree(h)  # ownership passes only on SUCCESS
            return False
        return True
    finally:
        u.CloseClipboard()


class ClipboardServer:
    """LAN HTTP relay for the iPad clipboard shortcuts. Daemon-threaded;
    dies with the app. bind_host is 0.0.0.0 in production (the iPad must
    reach it) and 127.0.0.1 in test harnesses."""

    def __init__(self, token, port, bind_host="0.0.0.0"):
        self.token = token
        self.port = port
        self.bind_host = bind_host
        self.httpd = None

    def start(self):
        import http.server
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # a stalled/slowloris connection must never pin a thread forever
            # (handle_one_request treats a socket timeout as close)
            timeout = 30

            def log_message(self, *a):  # no stderr chatter
                pass

            def _plain(self, code, body=b""):
                if code != 200:
                    # error paths may leave request bytes unread; never let
                    # keep-alive parse leftovers as a pipelined request
                    self.close_connection = True
                self.send_response(code)
                if body:
                    self.send_header("Content-Type",
                                     "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _authed(self):
                # constant-time compare over BYTES: the str form raises
                # TypeError on non-ASCII header bytes (latin-1 decoded)
                import hmac
                tok = self.headers.get("X-OpenSpan-Token", "")
                if hmac.compare_digest(tok.encode("utf-8", "replace"),
                                       outer.token.encode("utf-8")):
                    return True
                # visible on the PC, and an explanatory body on the iPad
                # (Shortcuts copies the response body even on a 403 — an
                # empty one would silently blank the iPad clipboard)
                _emit("err", "clipboard request REJECTED (bad token) from "
                             f"{self.client_address[0]}")
                self._plain(403, b"OpenSpan relay: bad or missing token "
                                 b"(check the shortcut's header)")
                return False

            def do_GET(self):
                try:
                    self._get()
                except Exception as e:  # noqa: BLE001 -- a handler crash
                    #  under pythonw is an invisible dead connection
                    try:
                        self._plain(500)
                    except Exception:  # noqa: BLE001
                        pass
                    _emit("err", f"clipboard GET failed: {e}")

            def do_POST(self):
                try:
                    self._post()
                except Exception as e:  # noqa: BLE001
                    try:
                        self._plain(500)
                    except Exception:  # noqa: BLE001
                        pass
                    _emit("err", f"clipboard POST failed: {e}")

            def _get(self):
                if self.path != "/clip":
                    self._plain(404)
                    return
                if not self._authed():
                    return
                # errors="replace": a lone UTF-16 surrogate on the clipboard
                # must not abort the request
                data = get_clipboard_text().encode("utf-8", "replace")
                self._plain(200, data)
                _emit("event",
                      f"clipboard served to the iPad ({len(data)} bytes)")

            def _post(self):
                if self.path != "/clip":
                    self._plain(404)
                    return
                if not self._authed():
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if n <= 0 or n > CLIP_MAX:
                    self._plain(413 if n > CLIP_MAX else 400)
                    return
                body = self.rfile.read(n).decode("utf-8", "replace")
                # Shortcuts' JSON body arrives as {"text": ...}; a raw
                # text body works too
                if "json" in (self.headers.get("Content-Type") or "").lower():
                    try:
                        body = json.loads(body).get("text")
                    except (ValueError, AttributeError):
                        pass  # not valid JSON -> treat as raw text
                    if not isinstance(body, str):
                        # missing/null/non-string "text": reject rather
                        # than silently blanking the Windows clipboard
                        self._plain(400, b"OpenSpan relay: JSON body needs "
                                         b'a string "text" field')
                        return
                if set_clipboard_text(body):
                    self._plain(200, b"ok")
                    _emit("event", "clipboard received from the iPad "
                                   f"({len(body)} chars)")
                else:
                    self._plain(500)
                    _emit("err", "clipboard write failed (clipboard busy?)")

        try:
            self.httpd = http.server.ThreadingHTTPServer(
                (self.bind_host, self.port), Handler)
        except OSError as e:
            _emit("err", f"clipboard relay couldn't bind :{self.port} ({e})")
            return False
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        return True

    def stop(self):
        """Close the listener so the clipboard is not remotely reachable
        during app teardown."""
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
                self.httpd = None
        except Exception:  # noqa: BLE001
            pass


# ---- radio-ownership mode (Windows vs Station), switched via reboot ----
MODE_FILE = os.path.join(ROOT, "mode.txt")
BOOT_TASK = "OpenSpanBoot"
INSTALL_TASK = os.path.join(ROOT, "install-boot-task.ps1")


def current_mode():
    try:
        with open(MODE_FILE) as f:
            m = f.read().strip().lower()
        return "station" if m == "station" else "windows"
    except OSError:
        return "windows"


def set_mode(mode):
    with open(MODE_FILE, "w") as f:
        f.write(mode + "\n")


def boot_task_exists():
    r = subprocess.run(["schtasks", "/Query", "/TN", BOOT_TASK],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def ensure_boot_task():
    """Install the SYSTEM startup task (one-time, elevates via UAC)."""
    if boot_task_exists():
        return True
    # elevate PowerShell to run the installer, wait for it
    ps = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
          f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"
          f"'{INSTALL_TASK}'")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=NO_WINDOW)
    return boot_task_exists()


# The tray window class and its WNDPROC thunk are registered ONCE per process
# and are IMMORTAL: unregistering (or letting the thunk be garbage-collected
# after a failed init) leaves lpfnWndProc pointing at freed memory, and the
# next tray attempt crashes natively (0xC0000005) inside CreateWindowExW.
_TRAY = {"registered": False, "proc": None, "cls": None, "nid_cls": None,
         "active": None, "taskbar_created": 0}


class TrayIcon:
    """Minimal Windows system-tray icon — pure ctypes, no dependencies.
    A hidden (real, NOT message-only) window receives the callbacks; it
    lives on the Tk thread, so Tk's mainloop pumps its messages. Left-click
    (or double-click) the icon -> on_restore(). A real window is required
    because explorer.exe restarts wipe all tray icons and announce it with
    the broadcast "TaskbarCreated" — which message-only windows never
    receive; on that message the icon is re-added automatically."""
    _WM_TRAY = 0x8001  # WM_APP + 1

    def __init__(self, tip, icon_path, on_restore):
        import ctypes
        import ctypes.wintypes as wt
        self._ct = ctypes
        self.on_restore = on_restore
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        sh = ctypes.windll.shell32
        self._u32, self._sh = u32, sh
        self._register_once(ctypes, wt, u32, k32)

        hinst = k32.GetModuleHandleW(None)
        self.hwnd = u32.CreateWindowExW(
            0, "OpenSpanTrayWnd", "OpenSpanTray", 0, 0, 0, 0, 0,
            None, None, hinst, None)  # top-level, never shown
        if not self.hwnd:
            raise OSError("tray: CreateWindowExW failed")

        hicon = u32.LoadImageW(None, icon_path, 1, 16, 16,
                               0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if not hicon:
            hicon = u32.LoadIconW(None, 32512)  # IDI_APPLICATION fallback

        NID = _TRAY["nid_cls"]
        self._nid = NID()
        self._nid.cbSize = ctypes.sizeof(NID)
        self._nid.hWnd = self.hwnd
        self._nid.uID = 1
        self._nid.uFlags = 0x07  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = self._WM_TRAY
        self._nid.hIcon = hicon
        self._nid.szTip = tip[:127]
        _TRAY["active"] = self  # before NIM_ADD: callbacks may fire at once
        if not sh.Shell_NotifyIconW(0, ctypes.byref(self._nid)):  # NIM_ADD
            _TRAY["active"] = None
            u32.DestroyWindow(self.hwnd)  # class/thunk stay: immortal
            raise OSError("tray: Shell_NotifyIconW failed")

    @staticmethod
    def _register_once(ctypes, wt, u32, k32):
        if _TRAY["registered"]:
            return
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, ctypes.c_uint,
                                     wt.WPARAM, wt.LPARAM)
        # 64-bit-correct prototypes (defaults truncate handles to 32-bit)
        u32.DefWindowProcW.restype = ctypes.c_ssize_t
        u32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM,
                                       wt.LPARAM]
        u32.CreateWindowExW.restype = wt.HWND
        u32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        u32.DestroyWindow.argtypes = [wt.HWND]
        u32.LoadImageW.restype = ctypes.c_void_p
        u32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, ctypes.c_uint,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        u32.RegisterWindowMessageW.restype = ctypes.c_uint
        u32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        _TRAY["taskbar_created"] = u32.RegisterWindowMessageW("TaskbarCreated")

        def proc(hwnd, msg, w, l):
            # This is a Win32 callback: an exception must NEVER escape it. In a
            # windowed frozen build ctypes would try to print the traceback to
            # a None stderr and hard-crash the process (0xc000041d in
            # _ctypes.pyd). Swallow everything; fall back to DefWindowProc.
            try:
                t = _TRAY["active"]
                if t is not None:
                    if msg == TrayIcon._WM_TRAY and l in (0x0202, 0x0203):
                        # WM_LBUTTONUP / WM_LBUTTONDBLCLK on the icon
                        try:
                            t.on_restore()
                        except Exception:  # noqa: BLE001
                            pass
                        return 0
                    if msg == _TRAY["taskbar_created"]:
                        # explorer restarted and forgot every tray icon: re-add
                        try:
                            t._sh.Shell_NotifyIconW(0, ctypes.byref(t._nid))
                        except Exception:  # noqa: BLE001
                            pass
                        return 0
            except BaseException:  # noqa: BLE001 -- a callback must not raise
                pass
            try:
                return u32.DefWindowProcW(hwnd, msg, w, l)
            except BaseException:  # noqa: BLE001
                return 0
        _TRAY["proc"] = WNDPROC(proc)  # immortal: keeps the thunk alive

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", ctypes.c_void_p),
                        ("hbrBackground", ctypes.c_void_p),
                        ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]
        _TRAY["cls"] = WNDCLASSW(0, _TRAY["proc"], 0, 0,
                                 k32.GetModuleHandleW(None), None, None,
                                 None, None, "OpenSpanTrayWnd")
        if not u32.RegisterClassW(ctypes.byref(_TRAY["cls"])):
            _TRAY["proc"] = None
            _TRAY["cls"] = None
            raise OSError("tray: RegisterClassW failed")

        class NOTIFYICONDATAW(ctypes.Structure):  # V2 layout
            _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND),
                        ("uID", ctypes.c_uint), ("uFlags", ctypes.c_uint),
                        ("uCallbackMessage", ctypes.c_uint),
                        ("hIcon", wt.HICON), ("szTip", ctypes.c_wchar * 128),
                        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                        ("szInfo", ctypes.c_wchar * 256),
                        ("uVersion", ctypes.c_uint),
                        ("szInfoTitle", ctypes.c_wchar * 64),
                        ("dwInfoFlags", wt.DWORD)]
        ctypes.windll.shell32.Shell_NotifyIconW.restype = wt.BOOL
        ctypes.windll.shell32.Shell_NotifyIconW.argtypes = [
            wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        _TRAY["nid_cls"] = NOTIFYICONDATAW
        _TRAY["registered"] = True

    def ensure(self):
        """True if the icon is (still) in the tray; re-adds it if the shell
        lost it. Polled while the window is hidden so the app can never be
        stranded icon-less."""
        try:
            if self._sh.Shell_NotifyIconW(1, self._ct.byref(self._nid)):
                return True  # NIM_MODIFY succeeded -> icon exists
            return bool(
                self._sh.Shell_NotifyIconW(0, self._ct.byref(self._nid)))
        except Exception:  # noqa: BLE001
            return False

    def destroy(self):
        if _TRAY["active"] is self:
            _TRAY["active"] = None
        try:
            self._sh.Shell_NotifyIconW(2, self._ct.byref(self._nid))
        except Exception:  # noqa: BLE001
            pass
        try:
            self._u32.DestroyWindow(self.hwnd)
            # the window class + WNDPROC thunk are deliberately NOT
            # unregistered -- see the _TRAY comment above
        except Exception:  # noqa: BLE001
            pass


class ArrangeCanvas(tk.Canvas):
    """Always-visible screen arrangement; drag the iPad, it snaps + saves."""

    def __init__(self, master, on_change=None, **kw):
        super().__init__(master, bg=PANEL, highlightthickness=0, **kw)
        self.on_change = on_change
        self.monitors = enum_monitors()
        self.ipad = self._default_ipad()
        self._load()
        self.dragging = False
        self.drag_off = (0, 0)
        self._world_bounds()
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)

    def _default_ipad(self):
        p = next((m for m in self.monitors if m["primary"]), self.monitors[0])
        return {"x": p["x"] + p["w"], "y": p["y"], "w": 1080, "h": 810,
                "res_w": 1080, "res_h": 810}

    def _load(self):
        try:
            with open(CONFIG) as f:
                cfg = json.load(f)
            if "ipad" in cfg:
                self.ipad.update(cfg["ipad"])
        except (OSError, ValueError):
            pass

    def set_ipad_size(self, w, h):
        self.ipad.update(w=w, h=h, res_w=w, res_h=h)
        self.redraw()

    def rotate(self):
        i = self.ipad
        i["w"], i["h"] = i["h"], i["w"]
        i["res_w"], i["res_h"] = i["res_h"], i["res_w"]
        self.redraw()
        self.save()

    def _world_bounds(self):
        xs = [m["x"] for m in self.monitors] + \
             [m["x"] + m["w"] for m in self.monitors]
        ys = [m["y"] for m in self.monitors] + \
             [m["y"] + m["h"] for m in self.monitors]
        mw = max(m["w"] for m in self.monitors)
        mh = max(m["h"] for m in self.monitors)
        self.wx0, self.wx1 = min(xs) - mw, max(xs) + mw
        self.wy0, self.wy1 = min(ys) - mh, max(ys) + mh

    def _scale(self):
        cw, ch = max(self.winfo_width(), 100), max(self.winfo_height(), 100)
        s = min(cw / (self.wx1 - self.wx0),
                ch / (self.wy1 - self.wy0)) * 0.90
        ox = (cw - (self.wx1 - self.wx0) * s) / 2
        oy = (ch - (self.wy1 - self.wy0) * s) / 2
        return s, ox, oy

    def w2c(self, x, y):
        s, ox, oy = self._scale()
        return (x - self.wx0) * s + ox, (y - self.wy0) * s + oy

    def c2w(self, cx, cy):
        s, ox, oy = self._scale()
        return (cx - ox) / s + self.wx0, (cy - oy) / s + self.wy0

    def redraw(self):
        self.delete("all")
        for m in self.monitors:
            x0, y0 = self.w2c(m["x"], m["y"])
            x1, y1 = self.w2c(m["x"] + m["w"], m["y"] + m["h"])
            self.create_rectangle(x0, y0, x1, y1, fill=MON_FILL,
                                  outline=MON_LINE, width=2)
            tag = "PRIMARY\n" if m["primary"] else ""
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                             text=f"{tag}{m['w']}x{m['h']}", fill="#c9d4ec",
                             justify="center", font=("Segoe UI", 9, "bold"))
        ix0, iy0 = self.w2c(self.ipad["x"], self.ipad["y"])
        ix1, iy1 = self.w2c(self.ipad["x"] + self.ipad["w"],
                            self.ipad["y"] + self.ipad["h"])
        self.create_rectangle(ix0, iy0, ix1, iy1, fill=IPAD_FILL,
                              outline=IPAD_LINE, width=3)
        self.create_text((ix0 + ix1) / 2, (iy0 + iy1) / 2,
                         text=f"iPad\n{self.ipad['w']}x{self.ipad['h']}",
                         fill="#d6ffe9", justify="center",
                         font=("Segoe UI", 9, "bold"))
        for edge, m, lo, hi in self._portals():
            if edge in ("ipad-left", "ipad-right"):
                wx = self.ipad["x"] if edge == "ipad-left" \
                    else self.ipad["x"] + self.ipad["w"]
                a, b = self.w2c(wx, lo), self.w2c(wx, hi)
            else:
                wy = self.ipad["y"] if edge == "ipad-top" \
                    else self.ipad["y"] + self.ipad["h"]
                a, b = self.w2c(lo, wy), self.w2c(hi, wy)
            self.create_line(a[0], a[1], b[0], b[1], fill=PORTAL, width=5)

    def _portals(self):
        ip, out = self.ipad, []
        for m in self.monitors:
            if abs(ip["x"] - (m["x"] + m["w"])) <= 2:
                lo, hi = max(ip["y"], m["y"]), min(ip["y"] + ip["h"],
                                                   m["y"] + m["h"])
                if hi - lo > 20:
                    out.append(("ipad-left", m, lo, hi))
            if abs((ip["x"] + ip["w"]) - m["x"]) <= 2:
                lo, hi = max(ip["y"], m["y"]), min(ip["y"] + ip["h"],
                                                   m["y"] + m["h"])
                if hi - lo > 20:
                    out.append(("ipad-right", m, lo, hi))
            if abs(ip["y"] - (m["y"] + m["h"])) <= 2:
                lo, hi = max(ip["x"], m["x"]), min(ip["x"] + ip["w"],
                                                   m["x"] + m["w"])
                if hi - lo > 20:
                    out.append(("ipad-top", m, lo, hi))
            if abs((ip["y"] + ip["h"]) - m["y"]) <= 2:
                lo, hi = max(ip["x"], m["x"]), min(ip["x"] + ip["w"],
                                                   m["x"] + m["w"])
                if hi - lo > 20:
                    out.append(("ipad-bottom", m, lo, hi))
        return out

    def _press(self, e):
        wx, wy = self.c2w(e.x, e.y)
        if (self.ipad["x"] <= wx <= self.ipad["x"] + self.ipad["w"] and
                self.ipad["y"] <= wy <= self.ipad["y"] + self.ipad["h"]):
            self.dragging = True
            self.drag_off = (wx - self.ipad["x"], wy - self.ipad["y"])

    def _drag(self, e):
        if not self.dragging:
            return
        wx, wy = self.c2w(e.x, e.y)
        self.ipad["x"] = int(wx - self.drag_off[0])
        self.ipad["y"] = int(wy - self.drag_off[1])
        self.redraw()

    def _release(self, e):
        if not self.dragging:
            return
        self.dragging = False
        self._snap()
        self.redraw()
        self.save()

    def _snap(self):
        ip = self.ipad
        TH = max(m["w"] for m in self.monitors) * 0.25
        best = None
        for m in self.monitors:
            for cx, cy, axis in [
                    (m["x"] + m["w"], None, "x"), (m["x"] - ip["w"], None, "x"),
                    (None, m["y"] + m["h"], "y"), (None, m["y"] - ip["h"], "y")]:
                if axis == "x" and abs(ip["x"] - cx) < TH and \
                        (best is None or abs(ip["x"] - cx) < best[0]):
                    ny = max(m["y"] - ip["h"] + 40,
                             min(ip["y"], m["y"] + m["h"] - 40))
                    best = (abs(ip["x"] - cx), cx, ny)
                elif axis == "y" and abs(ip["y"] - cy) < TH and \
                        (best is None or abs(ip["y"] - cy) < best[0]):
                    nx = max(m["x"] - ip["w"] + 40,
                             min(ip["x"], m["x"] + m["w"] - 40))
                    best = (abs(ip["y"] - cy), nx, cy)
        if best:
            self.ipad["x"], self.ipad["y"] = int(best[1]), int(best[2])

    def save(self):
        cfg = {"monitors": self.monitors, "ipad": self.ipad,
               "portals": [{"edge": e, "monitor": m["name"], "lo": lo,
                            "hi": hi} for (e, m, lo, hi) in self._portals()]}
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
        if self.on_change:
            self.on_change(bool(self._portals()))


class BtPanel(tk.Frame):
    """Bluetooth & headphones, embedded in the main window (a notebook tab).
    Right-click any device for its actions. Custom names and a blacklist are
    saved locally (bt_prefs.json), so a rename survives re-pairing and
    blacklisted devices never appear in scans."""

    def __init__(self, master, app=None):
        super().__init__(master, bg=BG)
        self.app = app
        self._refreshing = False
        self._refresh_pending = False  # trailing rerun, never a swallow
        self._conn_busy = False  # one connect-retry loop at a time
        self._connected = set()
        self._connected_names = []  # display names, for the compact view
        self._seen = {}  # mac -> (name, icon): every device seen this session,
        #                  kept in the list even after BlueZ purges an un-bonded
        #                  device, so a failed Connect never drops it from view.
        self.prefs = load_bt_prefs()
        self.show_blk = tk.BooleanVar(value=False)

        tk.Label(self, text="Put headphones in pairing mode, Scan, then "
                            "RIGHT-CLICK a device: Connect, Rename, Blacklist, "
                            "Forget. Renames + blacklist are saved and survive "
                            "re-pairing.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=500,
                 justify="left").pack(anchor="w", padx=12, pady=(10, 0))
        self.info = tk.StringVar(value="")
        tk.Label(self, textvariable=self.info, bg=BG, fg=ACCENT,
                 font=("Consolas", 9), anchor="w").pack(fill="x", padx=12,
                                                        pady=(4, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        self.tree = ttk.Treeview(body, columns=("name", "status", "type",
                                                "addr"),
                                 show="headings", selectmode="browse",
                                 height=8)
        self.tree.heading("name", text="Device")
        self.tree.heading("status", text="Status")
        self.tree.heading("type", text="Type")
        self.tree.heading("addr", text="Address")
        self.tree.column("name", width=190, anchor="w")
        self.tree.column("status", width=120, anchor="w")
        self.tree.column("type", width=85, anchor="w")
        self.tree.column("addr", width=130, anchor="w")
        self.tree.tag_configure("connected", foreground=ACCENT)
        self.tree.tag_configure("paired", foreground=FG)
        self.tree.tag_configure("available", foreground=MUTED)
        self.tree.tag_configure("blacklisted", foreground=DANGER)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.connect())
        self.tree.bind("<Button-3>", self._popup)

        self.menu = tk.Menu(self, tearoff=0, bg=CARD, fg=FG,
                            activebackground=ACCENT_DIM,
                            activeforeground="#eafff3", bd=0)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 4))
        self.btn_scan = ttk.Button(bar, text="🔍 Scan", command=self.scan)
        self.btn_scan.pack(side="left")
        ttk.Button(bar, text="↻ Refresh", command=self.refresh).pack(
            side="left", padx=6)
        ttk.Checkbutton(bar, text="Show blacklisted", variable=self.show_blk,
                        command=self.refresh).pack(side="left", padx=8)
        ttk.Button(bar, text="⟳ Restart audio",
                   command=self._restart_all).pack(side="right")

        self.out = tk.Text(self, bg="#0e1015", fg="#b7c0d4", height=5, bd=0,
                           font=("Consolas", 9), wrap="word",
                           insertbackground=FG)
        self.out.pack(fill="both", expand=False, padx=12, pady=(4, 10))
        self._log("Ready. Right-click a device for its actions.")
        self.refresh()

    def _log(self, msg):
        # callable from worker threads: queue the Text mutation to the UI
        # thread via App.ui() (workers must never call into Tk, even after())
        def put():
            self.out.insert("end", msg.rstrip() + "\n")
            self.out.see("end")
        if self.app:
            self.app.ui(put)
        else:
            put()
        _emit("bt", msg)  # mirror into the main console too

    def _reachable(self):
        return ssh_guest("echo ok", timeout=6, quiet=True).stdout.strip() == "ok"

    def _sel_mac(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _popup(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        mac = row
        m = self.menu
        m.delete(0, "end")
        if mac in self.prefs["blacklist"]:
            m.add_command(label="Un-blacklist (show again)",
                          command=self.unblacklist)
        else:
            if mac in self._connected:
                m.add_command(label="Disconnect", command=self.disconnect)
            else:
                m.add_command(label="🎧  Connect", command=self.connect)
            m.add_command(label="Rename…", command=self.rename)
            m.add_separator()
            m.add_command(label="Blacklist (hide from scans)",
                          command=self.blacklist)
            m.add_command(label="Forget (unpair)", command=self.forget)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _restart_all(self):
        if not dark_confirm(
                self, "Restart audio?",
                "Restarts just the audio pipeline (~15s). Your iPad keyboard "
                "is NOT affected.\n\nRestart audio now?"):
            return
        self._log("restarting the audio pipeline (keyboard untouched)…")
        if self.app:
            self.app.restart_everything(log=self._log)

    def refresh(self, quiet=False):
        if self._refreshing:
            # never swallow a refresh: an in-flight pass may carry a
            # PRE-link snapshot; queue one trailing rerun instead
            self._refresh_pending = True
            return
        self._refreshing = True

        def ui(fn):
            if self.app:
                self.app.ui(fn)  # queue -> UI thread; safe from any thread

        def work():
            # network I/O only in this thread; every widget mutation is
            # marshaled to the UI thread via after()
            try:
                if not self._reachable():
                    if vm_running():
                        msg = ("VM is starting up (~90s)… refreshes "
                               "automatically when ready.")
                    else:
                        msg = ("VM isn't running — Start VM on the iPad "
                               "Bridge tab.")

                    def apply_unreachable():
                        self.info.set(msg)
                        self._connected_names = []  # VM down = nothing linked
                        self.after(5000, self.refresh)  # retry until reachable
                    ui(apply_unreachable)
                    return
                r = ssh_guest("bash /opt/openspan/bt-list.sh", timeout=25,
                              quiet=quiet, show_result=False)
                rows = []
                for line in (r.stdout or "").splitlines():
                    p = line.split("|")
                    if len(p) >= 4 and re.match(
                            r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$", p[0]):
                        rows.append((p[0], p[1], p[2] == "1", p[3] == "1",
                                     p[4] if len(p) > 4 else ""))
                # Remember everything BlueZ currently knows, then add back any
                # device we've seen this session that BlueZ has since purged
                # (an un-bonded device is dropped the moment discovery stops).
                # Those come back as plain "available" so a failed Connect never
                # makes the buds vanish from the list — you can just try again.
                live = set()
                for mac, name, paired, conn, icon in rows:
                    live.add(mac)
                    self._seen[mac] = (name, icon)
                for mac, (name, icon) in self._seen.items():
                    if mac not in live:
                        rows.append((mac, name, False, False, icon))
                rows.sort(key=lambda x: (not x[3], not x[2], x[1].lower()))
                ui(lambda: self._apply_rows(rows))
            finally:
                self._refreshing = False
                if self._refresh_pending:
                    self._refresh_pending = False
                    self.refresh(quiet=True)  # the queued trailing rerun
        threading.Thread(target=work, daemon=True).start()

    def _apply_rows(self, rows):
        """Rebuild the device list. UI thread only."""
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._connected = set()
        names = []
        nconn = nhidden = 0
        show_blk = self.show_blk.get()
        for mac, name, paired, conn, icon in rows:
            blk = mac in self.prefs["blacklist"]
            if blk and not show_blk:
                nhidden += 1
                continue
            nm = self.prefs["renames"].get(mac, name)
            typ = ("🎧 audio" if "audio" in (icon or "")
                   else (icon or "device"))
            if blk:
                status, tag = "⛔ Blacklisted", "blacklisted"
            elif conn:
                status, tag = "● Connected", "connected"
                nconn += 1
                self._connected.add(mac)
                names.append(nm)
            elif paired:
                status, tag = "○ Paired (idle)", "paired"
            else:
                status, tag = "Available", "available"
            self.tree.insert("", "end", iid=mac,
                             values=(nm, status, typ, mac), tags=(tag,))
        for k in keep:
            if self.tree.exists(k):
                self.tree.selection_set(k)
        self._connected_names = names
        extra = f" · {nhidden} blacklisted hidden" if nhidden else ""
        self.info.set(f"{nconn} connected{extra}  —  right-click a "
                      f"device for actions")

    def _retry_lock(self, what):
        """True (and logs) when the connect-retry loop is running: BT
        actions during it would race the guest script — bt-connect.sh's
        cleanup kills a live Scan, and a Forget would be silently undone
        by the next attempt re-pairing the device."""
        if self._conn_busy:
            self._log(f"{what} is locked while connect attempts run — "
                      "give it a few seconds.")
            return True
        return False

    def scan(self):
        if self._retry_lock("Scan"):
            return
        self.btn_scan.state(["disabled"])
        self._log("scanning 10s — make sure headphones are blinking…")
        def work():
            if self.app:
                self.app._manual_bt_begin()
            try:
                ssh_guest("source /opt/openspan/env.sh; "
                          "bluetoothctl --timeout 10 scan on", timeout=18,
                          show_result=False)
            finally:
                if self.app:
                    self.app._manual_bt_end()
            self._log("scan done.")
            self.refresh()
            if self.app:
                self.app.ui(lambda: self.btn_scan.state(["!disabled"]))
        threading.Thread(target=work, daemon=True).start()

    def connect(self):
        mac = self._sel_mac()
        if not mac or mac in self.prefs["blacklist"]:
            return
        if self._conn_busy:
            self._log("already trying to connect — hold on…")
            return
        self._conn_busy = True
        self._log(f"connecting {mac} — up to 5 attempts over ~30s…")

        def work():
            # keep trying: earbuds waking from the case routinely miss the
            # first page. Stop the moment a real link exists; a first-time
            # pairing pass ("paired ✓") rolls straight into the connect on
            # the next attempt (with a FRESH time budget — a slow pairing
            # must not eat the connect's 30s). A failed pairing stops the
            # loop outright: retrying it would fire another 30s scan volley
            # per attempt.
            if self.app:
                self.app._manual_bt_begin()
            try:
                t0 = time.time()
                for attempt in range(1, 6):
                    # first attempt may hit the (long) pairing branch; the
                    # bonded fast path needs only a few seconds — a shorter
                    # timeout keeps one wedged ssh from holding the loop
                    r = ssh_guest(f"bash /opt/openspan/bt-connect.sh {mac}",
                                  timeout=70 if attempt == 1 else 20,
                                  show_result=False)
                    out = (r.stdout or r.stderr or "").strip()
                    self._log(f"[{attempt}/5] {out[-260:]}")
                    if "CONNECTED" in out:
                        break
                    if "pairing didn't take" in out:
                        self._log("pairing needs the buds BLINKING — put "
                                  "them in pairing mode and click Connect "
                                  "again (one scan per click).")
                        break
                    if "paired ✓" in out:
                        t0 = time.time()  # fresh budget for the connect
                    if attempt >= 5 or time.time() - t0 > 30:
                        self._log("no luck after retries — wake the buds "
                                  "(pop them back in pairing mode) and "
                                  "Connect again.")
                        break
                    threading.Event().wait(2.5)
            finally:
                self._conn_busy = False
                if self.app:
                    self.app._manual_bt_end()
            self.refresh()
        threading.Thread(target=work, daemon=True).start()

    def disconnect(self):
        mac = self._sel_mac()
        if not mac or self._retry_lock("Disconnect"):
            return
        self._log(f"disconnecting {mac}…")
        def work():
            if self.app:
                self.app._manual_bt_begin()
            try:
                ssh_guest(f"bluetoothctl disconnect {mac}", timeout=15)
            finally:
                if self.app:
                    self.app._manual_bt_end()
            self._log("disconnected.")
            self.refresh()
        threading.Thread(target=work, daemon=True).start()

    def forget(self):
        mac = self._sel_mac()
        if not mac or self._retry_lock("Forget"):
            return
        self._log(f"forgetting {mac}…")
        self._seen.pop(mac, None)  # don't let it reappear as "available"
        def work():
            if self.app:
                self.app._manual_bt_begin()
            try:
                ssh_guest(f"bluetoothctl disconnect {mac} >/dev/null 2>&1; "
                          f"bluetoothctl remove {mac}", timeout=15)
            finally:
                if self.app:
                    self.app._manual_bt_end()
            self.refresh()
        threading.Thread(target=work, daemon=True).start()

    def rename(self):
        """Inline rename: drop an Entry right on top of the Device cell."""
        mac = self._sel_mac()
        if not mac or not self.tree.exists(mac):
            return
        self.tree.see(mac)
        self.tree.update_idletasks()
        bbox = self.tree.bbox(mac, "name")
        if not bbox:
            return
        x, y, w, h = bbox
        cur = self.prefs["renames"].get(mac, "")
        if not cur:
            vals = self.tree.item(mac, "values")
            cur = vals[0] if vals else ""
        ed = tk.Entry(self.tree, bg=CARD, fg=FG, insertbackground=FG,
                      relief="flat", font=("Segoe UI", 10))
        ed.insert(0, cur)
        ed.select_range(0, "end")
        ed.place(x=x, y=y, width=w, height=h)
        ed.focus_set()
        done = {"v": False}

        def commit(_=None):
            if done["v"]:
                return
            done["v"] = True
            # strip chars the remote shell would interpret inside the
            # double-quoted busctl argument (`, $, \, ")
            new = re.sub(r'[`$\\"]', "", ed.get()).strip()
            ed.destroy()
            if new:
                self.prefs["renames"][mac] = new
            else:
                self.prefs["renames"].pop(mac, None)
            save_bt_prefs(self.prefs)
            self._log(f"renamed {mac} → “{new or '(default)'}”")
            path = "/org/bluez/hci0/dev_" + mac.replace(":", "_")
            threading.Thread(target=lambda: ssh_guest(
                "busctl --system set-property org.bluez " + path +
                ' org.bluez.Device1 Alias s "' + new + '"', timeout=10,
                quiet=True), daemon=True).start()
            self.refresh()

        def cancel(_=None):
            if not done["v"]:
                done["v"] = True
                ed.destroy()

        ed.bind("<Return>", commit)
        ed.bind("<KP_Enter>", commit)
        ed.bind("<Escape>", cancel)
        ed.bind("<FocusOut>", commit)

    def blacklist(self):
        mac = self._sel_mac()
        if not mac or self._retry_lock("Blacklist"):
            return
        self.prefs["blacklist"].add(mac)
        save_bt_prefs(self.prefs)
        self._log(f"blacklisted {mac} — it won't show in scans.")
        self.refresh()

    def unblacklist(self):
        mac = self._sel_mac()
        if not mac:
            return
        self.prefs["blacklist"].discard(mac)
        save_bt_prefs(self.prefs)
        self._log(f"un-blacklisted {mac}.")
        self.refresh()


class App:
    def __init__(self, root):
        self.root = root
        root.title("OpenSpan")
        root.geometry("1120x860")   # lean: console collapsed; grows when opened
        root.minsize(940, 600)
        root.configure(bg=BG)
        try:
            root.iconbitmap(ICON)
        except Exception:  # noqa: BLE001
            pass
        self.portal_proc = None
        self.audio_proc = None
        self._tray = None
        self._audio_logf = None
        self._portal_logf = None
        self._audio_lock = threading.Lock()  # serialize sender (re)launch so
        #                     overlapping _poll ticks never spawn two senders
        # ui() queue + its UI-thread pump MUST exist before any worker thread
        # can be spawned (console sink, BtPanel refresh, boot thread, _tick)
        self._uiq = queue.Queue()
        self._closing = False
        self._auto_conn_busy = False   # one auto-reconnect worker at a time
        self._auto_conn_last = 0.0     # last firing (cooldown anchor)
        self._auto_conn_cooldown = 90.0  # min seconds between auto firings
        self._auto_conn_fails = 0      # 3 failed rounds -> pause for session
        self._manual_bt_ops = 0        # in-flight manual BT actions
        self._bt_ops_lock = threading.Lock()
        self._pair_inflight = False    # Broadcast pressed, iPad not yet in
        self._broadcast_started = 0.0
        root.after(50, self._drain_ui)
        self._theme()

        # The whole UI lives inside self._full. The command console collapses to
        # keep the default window lean; it re-opens via the header toggle.
        self._console_open = False
        self._was_zoomed = False   # for the un-maximize width re-sync
        self._vol_ok = None      # None = probing; set by _volume_thread
        self._vol_now = None     # last read master volume 0..1
        self._vol_target = None  # slider-requested volume (thread applies)
        threading.Thread(target=self._volume_thread, daemon=True).start()
        full = tk.Frame(root, bg=BG)
        full.pack(fill="both", expand=True)
        self._full = full

        # ---- persistent console (right side, spans BOTH tabs) --------------
        # Packed first with side="right" so it owns a full-height strip; the
        # header/status/notebook then fill the remaining left cavity. Shows
        # every command the app runs and a big readiness banner up top.
        consf = tk.Frame(full, bg=PANEL, width=390)
        consf.pack_propagate(False)
        self._consf = consf   # collapsed by default; opened via the header toggle
        self._ready_state = None
        self._ipad_conn = None
        self.ready_lbl = tk.Label(consf, text="◌  Starting…", bg=PANEL,
                                  fg=MUTED, font=("Segoe UI Semibold", 13),
                                  anchor="w", padx=12, pady=12)
        self.ready_lbl.pack(fill="x")
        chead = tk.Frame(consf, bg=PANEL)
        chead.pack(fill="x", padx=10)
        tk.Label(chead, text="Console — every command the app runs", bg=PANEL,
                 fg=MUTED, font=("Segoe UI", 9, "bold")).pack(
            side="left", pady=(0, 4))
        ttk.Button(chead, text="Clear", width=6,
                   command=self._console_clear).pack(side="right")
        cwrap = tk.Frame(consf, bg=PANEL)
        cwrap.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.console = tk.Text(cwrap, bg="#0b0d12", fg="#b7c0d4", bd=0,
                               font=("Consolas", 9), wrap="word",
                               state="disabled", insertbackground=FG)
        csb = ttk.Scrollbar(cwrap, command=self.console.yview)
        csb.pack(side="right", fill="y")
        self.console.config(yscrollcommand=csb.set)
        self.console.pack(side="left", fill="both", expand=True)
        self.console.tag_config("ts", foreground="#5b6172")
        self.console.tag_config("cmd", foreground="#6cc6ff")
        self.console.tag_config("ok", foreground=ACCENT)
        self.console.tag_config("err", foreground=DANGER)
        self.console.tag_config("bt", foreground=PORTAL)
        self.console.tag_config("event", foreground=FG)
        self.console.tag_config("info", foreground=MUTED)
        set_log_sink(self._log_sink)
        self.log("event", "OpenSpan started.")

        head = tk.Frame(full, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 4))
        self._cons_anchor = head   # the console packs before this when opened
        tk.Label(head, text="OpenSpan", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 18)).pack(side="left")
        tk.Label(head, text="PC → iPad bridge", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left", padx=(10, 0),
                                             pady=(8, 0))
        ttk.Button(head, text="⤓  Send to Tray", command=self._to_tray).pack(
            side="right")
        self._cons_btn = ttk.Button(head, text="▸  Console",
                                    command=self._toggle_console)
        self._cons_btn.pack(side="right", padx=(0, 8))

        self.status = tk.StringVar(value="Checking…")
        tk.Label(full, textvariable=self.status, bg=BG, fg=ACCENT,
                 font=("Consolas", 10), anchor="w").pack(
            fill="x", padx=16, pady=(0, 6))

        # both panels side by side in one window (no tabs): iPad Bridge on
        # the left, Bluetooth & Headphones on the right, console far right
        main = tk.Frame(full, bg=BG)
        main.pack(fill="both", expand=True, padx=10, pady=4)
        bridge_col = tk.Frame(main, bg=BG)
        bridge_col.pack(side="left", fill="both", expand=True)
        tk.Label(bridge_col, text="iPad Bridge", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w",
                                                      padx=16, pady=(0, 2))
        bridge = tk.Frame(bridge_col, bg=BG)
        bridge.pack(fill="both", expand=True)
        tk.Frame(main, bg="#2d3444", width=1).pack(side="left", fill="y",
                                                   pady=6)
        bt_col = tk.Frame(main, bg=BG)
        bt_col.pack(side="left", fill="both", expand=True)
        tk.Label(bt_col, text="Bluetooth & Headphones", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w",
                                                      padx=12, pady=(0, 2))
        self._build_audio_panel(bt_col)
        self.bt_panel = BtPanel(bt_col, app=self)
        self.bt_panel.pack(fill="both", expand=True)

        # arrangement — always visible (Bridge tab)
        arr_wrap = tk.Frame(bridge, bg=CARD, bd=0)
        arr_wrap.pack(fill="both", expand=True, padx=8, pady=6)
        tk.Label(arr_wrap, text="Drag the iPad to the screen edge it sits "
                                "next to — the yellow line is the portal.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=8, pady=(6, 0))
        self.canvas = ArrangeCanvas(arr_wrap, on_change=self._portal_changed,
                                    height=230)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        row = tk.Frame(arr_wrap, bg=CARD)
        row.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(row, text="iPad:", bg=CARD, fg=MUTED).pack(side="left")
        self.model = tk.StringVar(value=list(IPAD_PRESETS)[0])
        cb = ttk.Combobox(row, textvariable=self.model, width=22,
                          values=list(IPAD_PRESETS), state="readonly")
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", self._pick_model)
        ttk.Button(row, text="Rotate",
                   command=self.canvas.rotate).pack(side="left")

        # controls (Bridge tab)
        ctl = tk.Frame(bridge, bg=BG)
        ctl.pack(fill="x", padx=16, pady=(2, 4))
        self.vm_btn = ttk.Button(ctl, text="Start VM", command=self.toggle_vm)
        self.vm_btn.grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        self.portal_btn = ttk.Button(ctl, text="Start portal",
                                     command=self.toggle_portal)
        self.portal_btn.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self.pair_btn = ttk.Button(ctl, text="📡  Pair / Broadcast",
                                   command=self.pair)
        self.pair_btn.grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        self.broadcasting = False

        ttk.Button(ctl, text="Edit keymap",
                   command=lambda: os.startfile(KEYMAP)).grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=3, pady=3)
        for c in range(3):
            ctl.columnconfigure(c, weight=1)

        # ---- System control: every backend action, nothing hidden ----
        sysf = ttk.LabelFrame(bridge, text="System control", padding=8)
        sysf.pack(fill="x", padx=16, pady=(6, 2))
        self.sys_status = tk.StringVar(value="…")
        tk.Label(sysf, textvariable=self.sys_status, bg=BG, fg=MUTED,
                 font=("Consolas", 8), anchor="w", justify="left").pack(
            fill="x", pady=(0, 4))
        sg = tk.Frame(sysf, bg=BG)
        sg.pack(fill="x")
        sysbtns = [("Stop VM", self.stop_vm),
                   ("Cold-restart VM", self.cold_restart_vm),
                   ("Restart keyboard", self.restart_keyboard),
                   ("Restart audio", self.restart_audio_btn),
                   ("⏻ Shut down everything", self.shutdown_all)]
        for i, (label, fn) in enumerate(sysbtns):
            ttk.Button(sg, text=label, command=fn).grid(
                row=i // 3, column=i % 3, sticky="ew", padx=3, pady=3)
        for c in range(3):
            sg.columnconfigure(c, weight=1)

        # ---- Radio ownership mode (switched via a clean reboot) ----
        mode = ttk.LabelFrame(bridge, text="Bluetooth radio", padding=8)
        mode.pack(fill="x", padx=16, pady=(6, 2))
        self.mode_lbl = tk.Label(mode, bg=BG, fg=FG, font=("Segoe UI", 10),
                                 anchor="w")
        self.mode_lbl.pack(fill="x")
        tk.Label(mode, bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w",
                 wraplength=480, justify="left",
                 text="Station = the app owns the radio (iPad bridge + "
                      "command station, near-bare-metal). Windows = native "
                      "Bluetooth + audio. Switching cleanly reboots the PC."
                 ).pack(fill="x", pady=(2, 6))
        mrow = tk.Frame(mode, bg=BG)
        mrow.pack(fill="x")
        self.to_station = ttk.Button(
            mrow, text="Switch to Station  (restart)",
            command=lambda: self.switch_mode("station"))
        self.to_station.pack(side="left", expand=True, fill="x", padx=2)
        self.to_windows = ttk.Button(
            mrow, text="Switch to Windows  (restart)",
            command=lambda: self.switch_mode("windows"))
        self.to_windows.pack(side="left", expand=True, fill="x", padx=2)
        self._refresh_mode_buttons()

        tk.Label(full, text="open source · MIT · nothing phones home",
                 bg=BG, fg="#5b6172", font=("Segoe UI", 8)).pack(
            side="bottom", pady=6)

        # clipboard relay for the iPad shortcuts (CLIPBOARD_DESIGN.md);
        # fail-soft: without it everything else works, and Ctrl+Alt+V
        # typing paste is unaffected
        try:
            tok, cport = clipboard_config()
            self.clip_server = ClipboardServer(tok, cport)
            if self.clip_server.start():
                _emit("event", "clipboard relay ready on "
                               f"http://{lan_ip()}:{cport}/clip "
                               "(token in openspan_settings.json)")
        except Exception:  # noqa: BLE001
            self.clip_server = None

        # only the app owns the radio in Station mode; never grab it in
        # Windows mode
        if current_mode() == "station" and not vm_running():
            threading.Thread(target=start_vm_clean, daemon=True).start()
        # keep the Windows->VM audio sender running whenever the app is open,
        # so connecting headphones is all it takes -- nothing else to launch
        self._ensure_audio()
        # push app-bundled guest scripts to the VM so a fix in the app also
        # updates the VM-side connection logic (no manual deploy, no reliance)
        self._sync_guest_scripts()
        self.root.after(120, self._dark_titlebar)
        # re-sync the window width to the console state when un-maximized (a
        # width change requested while zoomed is deferred, not lost)
        self.root.bind("<Configure>", self._on_configure)
        self._tick()

    # ---- Audio & status panel (always visible) + console toggle ----------
    def _build_audio_panel(self, parent):
        """Audio + at-a-glance status, always on screen — this is what the tray
        restores to and what the console-collapsed window shows. Readiness line,
        VM / iPad / audio / portal dots, the connected headphones, a volume
        slider (drives the Windows master volume, the same dial the sender's
        GAIN mirror follows), and an L/R balance slider (written to
        audio_balance.txt, applied per-channel inside the sender)."""
        p = ttk.LabelFrame(parent, text="Audio & status", padding=8)
        p.pack(fill="x", padx=12, pady=(0, 6))

        self.c_ready = tk.Label(p, text="◌  Starting…", bg=BG, fg=MUTED,
                                font=("Segoe UI Semibold", 11), anchor="w")
        self.c_ready.pack(fill="x")

        dots = tk.Frame(p, bg=BG)
        dots.pack(fill="x", pady=(4, 0))
        self.c_stat = {}
        for key, label in [("vm", "VM"), ("ipad", "iPad"),
                           ("audio", "Audio"), ("portal", "Portal")]:
            cell = tk.Frame(dots, bg=BG)
            cell.pack(side="left", padx=(0, 12))
            d = tk.Label(cell, text="●", bg=BG, fg=MUTED,
                         font=("Segoe UI", 11))
            d.pack(side="left")
            tk.Label(cell, text=label, bg=BG, fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="left", padx=(4, 0))
            self.c_stat[key] = d

        self.c_buds = tk.Label(p, text="🎧  —", bg=BG, fg=MUTED,
                               font=("Segoe UI", 10), anchor="w")
        self.c_buds.pack(fill="x", pady=(8, 0))

        self._vol_drag = False
        self._vol_syncing = False
        vr = tk.Frame(p, bg=BG)
        vr.pack(fill="x", pady=(8, 0))
        tk.Label(vr, text="Volume", bg=BG, fg=MUTED, width=9, anchor="w",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.c_vol_var = tk.DoubleVar(value=50.0)
        self.c_vol = ttk.Scale(vr, from_=0, to=100, variable=self.c_vol_var,
                               command=self._vol_changed)
        self.c_vol.pack(side="left", fill="x", expand=True)
        self.c_vol.bind("<ButtonPress-1>",
                        lambda e: setattr(self, "_vol_drag", True))
        self.c_vol.bind("<ButtonRelease-1>",
                        lambda e: setattr(self, "_vol_drag", False))
        # disabled later by _apply_poll if _volume_thread reports no pycaw

        br = tk.Frame(p, bg=BG)
        br.pack(fill="x", pady=(6, 0))
        tk.Label(br, text="L ↔ R", bg=BG, fg=MUTED, width=9, anchor="w",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.c_bal_var = tk.DoubleVar(value=self._load_balance() * 100)
        self.c_bal = ttk.Scale(br, from_=-100, to=100, variable=self.c_bal_var,
                               command=self._bal_changed)
        self.c_bal.pack(side="left", fill="x", expand=True)
        self.c_bal.bind("<Double-1>", self._bal_center)
        tk.Label(p, text="double-click balance to center", bg=BG,
                 fg="#5b6172", font=("Segoe UI", 8), anchor="w").pack(
            fill="x", pady=(2, 0))

    def _toggle_console(self):
        """Show/hide the command console on the right. Collapsed by default so
        the window opens lean; the window width grows/shrinks to match."""
        if self._console_open:
            self._consf.pack_forget()
            self._console_open = False
            self._cons_btn.config(text="▸  Console")
            self._set_win_width(1120)
        else:
            self._consf.pack(side="right", fill="y", before=self._cons_anchor)
            self._console_open = True
            self._cons_btn.config(text="◂  Console")
            self._set_win_width(1520)

    def _set_win_width(self, w):
        """Resize the window to width w, keeping height and position. No-op
        while maximized (a zoomed window ignores geometry())."""
        try:
            if self.root.state() == "zoomed":
                return
            m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", self.root.geometry())
            if m:
                _, h, x, y = m.groups()
                self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass

    def _on_configure(self, e):
        """Reconcile the window width with the console state when the window
        leaves the maximized state. A width change requested while zoomed is a
        no-op (Tk ignores geometry() when maximized), so re-apply it the moment
        the window un-maximizes — otherwise it restores to the pre-maximize
        width, which may not match the current console state."""
        if e.widget is not self.root:
            return  # ignore child-widget configure events
        z = (self.root.state() == "zoomed")
        if z == self._was_zoomed:
            return  # no zoomed<->normal transition; nothing to reconcile
        self._was_zoomed = z
        if not z:  # just un-maximized -> match the current console state
            self.root.after(10, lambda: self._set_win_width(
                1520 if self._console_open else 1120))

    def _vol_changed(self, _=None):
        if not self._vol_syncing:
            # latest-wins handoff to the volume thread; the UI thread never
            # touches COM (a busy AudioSrv would freeze the whole window)
            self._vol_target = self.c_vol_var.get() / 100.0

    def _volume_thread(self):
        """Owns ALL Core Audio COM (import, endpoint, get, set) on this one
        thread — the same proven pattern as the sender's _volume_watcher.
        Publishes the current master volume to _vol_now, applies slider
        targets from _vol_target, and re-resolves the default endpoint every
        ~30s so a device switch doesn't leave the slider driving the OLD
        device (no COM error fires on a mere default-device change)."""
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL, CoCreateInstance, GUID
            from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
            clsid = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        except Exception:  # noqa: BLE001
            self._vol_ok = False
            _emit("info", "volume slider disabled — pycaw not available "
                          "(Windows volume keys still work).")
            return
        self._vol_ok = True
        vol = None
        acquired = 0.0
        while not self._closing:
            try:
                now = time.time()
                if vol is None or now - acquired > 30:
                    enum = CoCreateInstance(clsid, IMMDeviceEnumerator,
                                            CLSCTX_ALL)
                    endpoint = enum.GetDefaultAudioEndpoint(0, 1)
                    vol = cast(endpoint.Activate(
                        IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                        POINTER(IAudioEndpointVolume))
                    acquired = now
                t = self._vol_target
                if t is not None:
                    self._vol_target = None
                    vol.SetMasterVolumeLevelScalar(
                        max(0.0, min(1.0, float(t))), None)
                self._vol_now = float(vol.GetMasterVolumeLevelScalar())
            except Exception:  # noqa: BLE001
                vol = None          # re-acquire next lap
                self._vol_now = None
            threading.Event().wait(0.5)

    def _load_balance(self):
        import math
        try:
            with open(BAL_FILE) as f:
                b = float(f.read().strip())
            if not math.isfinite(b):
                return 0.0  # 'nan'/'inf' would hard-pan, not center
            return max(-1.0, min(1.0, b))
        except (OSError, ValueError):
            return 0.0

    def _bal_changed(self, _=None):
        b = round(self.c_bal_var.get()) / 100.0
        try:
            # atomic replace: the sender polls this file every 150ms, and a
            # truncate-then-write would hand it a torn/empty read — an
            # audible one-tick balance jump mid-drag
            tmp = BAL_FILE + ".new"
            with open(tmp, "w") as f:
                f.write(f"{b:+.2f}")
            os.replace(tmp, BAL_FILE)
        except OSError:
            pass  # reader mid-open on the target: next drag tick rewrites

    def _bal_center(self, _=None):
        self.c_bal_var.set(0.0)
        self._bal_changed()

    def _refresh_mode_buttons(self):
        m = current_mode()
        self.mode_lbl.config(
            text=("● Station — the app owns the radio" if m == "station"
                  else "● Windows — native Bluetooth & audio"),
            fg=(ACCENT if m == "station" else FG))
        self.to_station.state(["disabled"] if m == "station" else ["!disabled"])
        self.to_windows.state(["disabled"] if m == "windows" else ["!disabled"])

    def switch_mode(self, mode):
        nice = "Station" if mode == "station" else "Windows"
        if not dark_confirm(
                self.root, f"Switch to {nice} mode?",
                f"This restarts the PC and brings it back up in {nice} mode.\n\n"
                + ("The app will own the Bluetooth radio (iPad + command "
                   "station). Windows Bluetooth/audio will be unavailable."
                   if mode == "station" else
                   "Windows gets its Bluetooth radio back (headphones, etc.). "
                   "The iPad bridge will be offline until you switch back.")
                + "\n\nSave your work first. Restart now?"):
            return
        # station mode needs the boot task installed (one-time UAC)
        if mode == "station" and not ensure_boot_task():
            dark_alert(
                self.root, "Setup needed",
                "Couldn't install the startup task (admin was declined). "
                "Station mode won't auto-start after reboot until it's "
                "installed.")
        set_mode(mode)
        subprocess.run(["shutdown", "/r", "/t", "8", "/c",
                        f"OpenSpan switching to {nice} mode"],
                       creationflags=NO_WINDOW)
        self.status.set(f"Restarting into {nice} mode in ~8s…")

    def _theme(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TButton", background=CARD, foreground=FG,
                     bordercolor=CARD, focuscolor=CARD, relief="flat",
                     padding=8, font=("Segoe UI", 10))
        st.map("TButton", background=[("active", "#2d3444")])
        st.configure("Accent.TButton", background=ACCENT_DIM,
                     foreground="#eafff3", font=("Segoe UI Semibold", 10))
        st.map("Accent.TButton", background=[("active", "#2a8f5c")])
        st.configure("Danger.TButton", background="#53292a",
                     foreground="#ffd9d6", font=("Segoe UI Semibold", 10))
        st.map("Danger.TButton", background=[("active", "#6e3335")])
        # sliders (compact mode's volume/balance)
        st.configure("Horizontal.TScale", background=BG, troughcolor=CARD,
                     bordercolor=CARD, lightcolor=ACCENT_DIM,
                     darkcolor=ACCENT_DIM)
        st.map("TButton",
               foreground=[("disabled", "#5b6172")],
               background=[("disabled", PANEL), ("active", "#2d3444")])
        # LabelFrame (the panel that was glaringly light)
        st.configure("TLabelframe", background=BG, bordercolor="#2d3444",
                     relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                     font=("Segoe UI", 9, "bold"))
        st.configure("TFrame", background=BG)
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        # Notebook tabs (dark)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                     padding=(14, 7), borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", CARD)],
               foreground=[("selected", FG)])
        # Combobox + its drop-down list
        st.configure("TCombobox", fieldbackground=CARD, background=CARD,
                     foreground=FG, arrowcolor=FG, bordercolor="#2d3444",
                     selectbackground=CARD, selectforeground=FG)
        st.map("TCombobox", fieldbackground=[("readonly", CARD)],
               foreground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#eafff3")
        # Treeview (the Bluetooth device list)
        st.configure("Treeview", background=CARD, foreground=FG,
                     fieldbackground=CARD, bordercolor=CARD, borderwidth=0,
                     rowheight=26, font=("Segoe UI", 10))
        st.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                     relief="flat", font=("Segoe UI", 9, "bold"))
        st.map("Treeview.Heading", background=[("active", "#2d3444")])
        st.map("Treeview", background=[("selected", ACCENT_DIM)],
               foreground=[("selected", "#eafff3")])

    def _dark_titlebar(self):
        """Paint the Windows title bar dark (DWM immersive dark mode)."""
        _paint_dark_titlebar(self.root)

    def _manual_bt_begin(self):
        """Manual BT actions (Connect/Disconnect/Forget/Scan) register here
        so auto-reconnect defers to them instead of contending for the
        radio. Manual is never blocked by auto — only the reverse."""
        with self._bt_ops_lock:
            self._manual_bt_ops += 1

    def _manual_bt_end(self):
        with self._bt_ops_lock:
            self._manual_bt_ops = max(0, self._manual_bt_ops - 1)

    def ui(self, fn):
        """Run fn on the Tk main thread. Worker threads must NEVER touch Tk
        directly — not even after(): a background after() racing the UI
        thread hard-crashes the interpreter (PyEval_RestoreThread GIL abort,
        reproduced in the render harness). Closures go on a plain queue that
        the UI thread drains every 50ms; queue.put is unconditionally safe."""
        self._uiq.put(fn)

    def _drain_ui(self):
        """UI-thread pump for ui(): run queued closures, reschedule."""
        try:
            while True:
                try:
                    fn = self._uiq.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass  # e.g. a widget destroyed during shutdown
        finally:
            if not self._closing:
                try:
                    self.root.after(50, self._drain_ui)
                except Exception:  # noqa: BLE001
                    pass  # root gone -> the pump simply ends

    # ---- console ----
    def _log_sink(self, kind, text):
        """Thread-safe entry point for module-level _emit: queue to the UI."""
        self.ui(lambda: self.log(kind, text))

    def log(self, kind, text):
        """Append a timestamped, color-tagged line to the console (UI thread)."""
        try:
            c = self.console
            c.config(state="normal")
            c.insert("end", time.strftime("%H:%M:%S "), "ts")
            c.insert("end", text.rstrip() + "\n", kind)
            if int(c.index("end-1c").split(".")[0]) > 800:  # cap growth
                c.delete("1.0", "200.0")
            c.see("end")
            c.config(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    def _console_clear(self):
        try:
            self.console.config(state="normal")
            self.console.delete("1.0", "end")
            self.console.config(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    # ---- actions ----
    def restart_everything(self, log=None):
        """Restart ONLY the audio pipeline: the PipeWire/WirePlumber services in
        the VM plus the Windows sender. Deliberately does NOT touch the VM or the
        keyboard daemon (openspanble) -- audio and the iPad keyboard are
        independent, so restarting audio must never drop the keyboard."""
        def say(m):
            try:
                if log:
                    log(m)
            except Exception:  # noqa: BLE001
                pass
            self.ui(lambda: self.status.set(m))

        def work():
            say("restarting the audio pipeline (keyboard untouched)…")
            # audio-only: these never touch bluetoothd/the radio/openspanble
            ssh_guest("systemctl restart openspan-wireplumber "
                      "openspan-pipewire-pulse openspan-udprecv", timeout=45)
            try:
                if self.audio_proc and self.audio_proc.poll() is None:
                    self.audio_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self.audio_proc = None
            self._ensure_audio()
            say("audio restarted — wake your headphones to reconnect. "
                "Keyboard was not touched.")
        threading.Thread(target=work, daemon=True).start()

    def _auto_reconnect_audio(self, reason):
        """Autonomously (re)connect the last-used earbuds when they are
        bonded but idle. Fired on the READY edge (the buds page the adapter
        during the ~90s boot, give up, and never retry) and after an iPad
        pairing (the LE burst can knock A2DP off the shared antenna).

        STRUCTURALLY unable to scan or pair: it never calls bt-connect.sh
        (whose unpaired branch scans+pairs) — it runs a connect-ONLY command
        that re-verifies the bond guest-side in the same shell and does
        nothing on any doubt. Targets only devices whose BlueZ Icon says
        audio. Defers to any in-flight manual BT action, fires at most once
        per cooldown window, and pauses for the session after 3 failed
        rounds so it can never sit there paging powered-off buds forever."""
        now = time.time()
        if (self._auto_conn_busy or self._manual_bt_ops > 0
                or self.broadcasting or self._pair_inflight
                or now - self._auto_conn_last < self._auto_conn_cooldown
                or self._auto_conn_fails >= 3):
            return
        self._auto_conn_busy = True
        self._auto_conn_last = now

        def work():
            attempted = False
            ok = False
            try:
                r = ssh_guest("cat /opt/openspan/audio-device.txt 2>/dev/null",
                              timeout=8, quiet=True)
                mac = (r.stdout or "").strip().upper()
                if not re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", mac):
                    return  # no known last device -> nothing to do
                prefs = load_bt_prefs()
                if mac in prefs["blacklist"]:
                    return
                r = ssh_guest("bash /opt/openspan/bt-list.sh", timeout=25,
                              quiet=True)
                name, paired, conn, icon = mac, False, False, ""
                for line in (r.stdout or "").splitlines():
                    p = line.split("|")
                    if len(p) >= 4 and p[0].upper() == mac:
                        name = prefs["renames"].get(p[0], p[1])
                        paired, conn = p[2] == "1", p[3] == "1"
                        icon = p[4] if len(p) > 4 else ""
                        break
                if "audio" not in (icon or ""):
                    return  # never auto-touch anything that isn't audio
                if conn or not paired:
                    return  # already connected, or not bonded (never
                    #         auto-pair -- pairing needs the user's intent)
                _emit("event", f"{reason} — reconnecting “{name}” "
                               "automatically…")
                # connect-only, bond re-verified in the SAME shell: empty or
                # doubtful info -> NOT_BONDED -> we do NOTHING (fail-closed)
                cmd = (f'info=$(bluetoothctl info {mac} 2>/dev/null); '
                       f'echo "$info" | grep -q "Paired: yes" '
                       '|| { echo NOT_BONDED; exit 0; }; '
                       f'echo "$info" | grep -q "Connected: yes" '
                       '&& { echo CONNECTED; exit 0; }; '
                       f'bluetoothctl connect {mac} >/dev/null 2>&1; '
                       'sleep 3; '
                       f'bluetoothctl info {mac} 2>/dev/null '
                       '| grep -q "Connected: yes" '
                       '&& echo CONNECTED || echo NO_LINK')
                attempted = True
                for attempt in (1, 2):
                    r = ssh_guest(cmd, timeout=25, quiet=True)
                    tok = ((r.stdout or "").strip().splitlines() or [""])[-1]
                    if "CONNECTED" in tok:
                        ok = True
                        _emit("event", f"auto-reconnect: “{name}” connected ✓")
                        break
                    if "NOT_BONDED" in tok:
                        break  # bond gone/unreadable -> hands off, no retry
                    if attempt == 1:
                        threading.Event().wait(4)  # buds may need a moment
                if attempted and not ok:
                    more = ("  (auto-reconnect is pausing for this session)"
                            if self._auto_conn_fails + 1 >= 3 else "")
                    _emit("event", f"auto-reconnect: “{name}” didn't respond "
                                   f"— wake the buds, then click Connect.{more}")
                self.ui(self.bt_panel.refresh)
            finally:
                if attempted:
                    self._auto_conn_fails = 0 if ok \
                        else self._auto_conn_fails + 1
                self._auto_conn_busy = False
        threading.Thread(target=work, daemon=True).start()

    # ---- System control (full manual control, nothing hidden) ----
    def stop_vm(self):
        if not dark_confirm(
                self.root, "Stop VM?",
                "Power off the audio/keyboard VM. Audio and the iPad keyboard "
                "stop until you start it again.\n\nStop now?"):
            return
        self.status.set("Stopping VM…")

        def work():
            ssh_guest("journalctl --sync", timeout=6, quiet=True)
            vbox("controlvm", VM, "poweroff")
        threading.Thread(target=work, daemon=True).start()

    def cold_restart_vm(self):
        if not dark_confirm(
                self.root, "Cold-restart VM?",
                "Power-cycle the whole VM (~90s). Audio + keyboard come back "
                "fresh; you re-pair the keyboard on the iPad.\n\nRestart now?"):
            return
        self.status.set("Cold-restarting VM…")
        def work():
            if vm_running():
                ssh_guest("journalctl --sync", timeout=6, quiet=True)
                vbox("controlvm", VM, "poweroff")
                for _ in range(30):
                    if not vm_running():
                        break
                    threading.Event().wait(1)
            start_vm_clean()
        threading.Thread(target=work, daemon=True).start()

    def restart_keyboard(self):
        self.status.set("Restarting keyboard daemon…")
        def work():
            ssh_guest("systemctl restart openspanble", timeout=25)
            self.ui(lambda: self.status.set(
                "Keyboard restarted — forget + re-pair on the iPad."))
        threading.Thread(target=work, daemon=True).start()

    def restart_audio_btn(self):
        self.restart_everything()

    def shutdown_all(self):
        if not dark_confirm(
                self.root, "Shut down everything?",
                "Power off the VM and close the app. Audio, keyboard, portal, "
                "and sender all stop — nothing keeps running.\n\nShut down "
                "now?"):
            return
        self._full_stop()

    # ---- close / tray ----
    def _full_stop(self):
        """The FULL STOP: portal, audio sender, and the VM all go down, then
        the app closes — nothing lingers, next launch is a clean cold boot."""
        self._closing = True  # stop the ui() pump rescheduling past destroy
        if getattr(self, "clip_server", None):
            self.clip_server.stop()  # clipboard offline before teardown
        # best-effort: flush the guest journal to disk before the hard power
        # cut, so the last minutes of Bluetooth events survive for post-mortem
        ssh_guest("journalctl --sync", timeout=6, quiet=True)
        if self._tray:
            self._tray.destroy()
            self._tray = None
        for p in (self.portal_proc, self.audio_proc):
            try:
                if p and p.poll() is None:
                    p.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            if vm_running():
                vbox("controlvm", VM, "poweroff")
        except Exception:  # noqa: BLE001
            pass
        # the single-instance mutex is NOT closed here on purpose: the OS
        # releases it at process exit (even on a crash), and closing the raw
        # handle early would let a second instance start during shutdown
        self.root.after(400, self.root.destroy)

    def _confirm_close(self):
        """X handler. Closing is a full stop (portal + audio + VM), so ask --
        with the option to keep everything running from the system tray. Shown
        as an in-frame overlay (not a separate window); the engine's re-entrancy
        guard handles a second X while it's open."""
        choice = _dialog(
            self.root, "Close OpenSpan?",
            "Closing OpenSpan shuts EVERYTHING down — the input portal, the "
            "audio sender, and the bridge VM. Audio and the iPad keyboard "
            "stop.\n\nSend it to the system tray instead to keep the bridge "
            "running.",
            [("Send to system tray", "tray", "TButton"),
             ("⏻  Yes, shut it down", "shutdown", "Danger.TButton"),
             ("Cancel", "cancel", "TButton")])
        if choice == "tray":
            self._to_tray()
        elif choice == "shutdown":
            self._full_stop()

    def _to_tray(self):
        """Hide to the system tray. EVERYTHING keeps running — VM, audio,
        portal, watchdog ticks. Click the tray icon to bring it back."""
        if self._tray is None:
            try:
                self._tray = TrayIcon("OpenSpan — bridge running", ICON,
                                      self._from_tray)
            except Exception:  # noqa: BLE001
                self._tray = None
        if self._tray is None:
            # tray unavailable -> minimize instead; never strand the app
            # somewhere it can't be brought back from
            self.root.iconify()
            return
        self.root.withdraw()
        _emit("event", "sent to the system tray — everything keeps running. "
                       "Click the tray icon to bring OpenSpan back.")

    def _from_tray(self):
        # the tray callback already arrives on the Tk thread (its hidden
        # window shares this thread's message pump) — marshal anyway
        def show():
            if self._tray:
                self._tray.destroy()
                self._tray = None
            self.root.deiconify()
            self.root.lift()
            try:
                self.root.focus_force()
            except tk.TclError:
                pass
        self.ui(show)

    def _pick_model(self, *_):
        w, h = IPAD_PRESETS[self.model.get()]
        self.canvas.set_ipad_size(w, h)
        self.canvas.save()

    def _portal_changed(self, ok):
        if not ok:
            self.status.set("⚠ iPad not touching a monitor — no portal")

    def toggle_vm(self):
        if vm_running():
            if dark_confirm(self.root, "Stop VM?",
                            "Stop the bridge VM? The iPad will disconnect "
                            "until you start it again."):
                self.vm_btn.config(text="Stopping VM…")
                vbox("controlvm", VM, "acpipowerbutton")
        else:
            self.vm_btn.config(text="Starting VM…")  # immediate feedback
            threading.Thread(target=start_vm_clean, daemon=True).start()

    def toggle_portal(self):
        if self.portal_proc and self.portal_proc.poll() is None:
            self.portal_proc.terminate()
            self.portal_proc = None
            self.portal_btn.config(text="Start portal")  # immediate feedback
            self.log("event", "portal STOPPED — keyboard/mouse no longer "
                              "bridging to the iPad.")
        else:
            try:
                if self._portal_logf:
                    self._portal_logf.close()
            except OSError:
                pass
            self._portal_logf = open(LOG, "a", buffering=1)
            self.portal_proc = subprocess.Popen(
                PORTAL_CMD,
                stdout=self._portal_logf, stderr=self._portal_logf,
                creationflags=NO_WINDOW)
            self.portal_btn.config(text="Stop portal")  # immediate feedback
            self.log("event", "portal STARTED — keyboard/mouse now bridging "
                              "to the iPad.")

    def _ensure_audio(self):
        """(Re)start the Windows->VM audio sender if it isn't running. Captures
        the default output via WASAPI loopback and streams it to the VM, which
        plays it to the connected Bluetooth headphones. Called on launch and on
        every status tick, so it self-heals if the sender ever dies."""
        with self._audio_lock:
            try:
                if self._closing:
                    return  # a respawned sender would outlive the app and
                    #         hold the OpenSpanAudioSender mutex hostage
                if self.audio_proc and self.audio_proc.poll() is None:
                    return
                try:
                    if self._audio_logf:
                        self._audio_logf.close()
                except OSError:
                    pass
                self._audio_logf = open(AUDIO_LOG, "a", buffering=1)
                self.audio_proc = subprocess.Popen(
                    AUDIO_CMD, stdout=self._audio_logf,
                    stderr=self._audio_logf, creationflags=NO_WINDOW)
            except Exception:  # noqa: BLE001
                pass

    def _sync_guest_scripts(self):
        """Deploy app-bundled guest scripts to the VM once it's reachable, so a
        fix in the app also fixes the VM-side logic -- no manual deploy needed.
        Content is streamed over ssh stdin, so there's no shell-escaping of the
        script body."""
        # udp_to_sink.py loads on the next openspan-udprecv restart (the
        # "⟳ Restart audio" button); btready.sh runs at the next VM boot.
        # Syncing never disturbs anything already running.
        jobs = [("guest-bt-connect.sh", "/opt/openspan/bt-connect.sh"),
                (os.path.join("..", "guest", "udp_to_sink.py"),
                 "/opt/openspan/udp_to_sink.py"),
                (os.path.join("..", "guest", "btready.sh"),
                 "/opt/openspan/btready.sh")]
        def work():
            reachable = False
            for _ in range(60):
                if ssh_guest("echo ok", timeout=5, quiet=True).stdout.strip() \
                        == "ok":
                    reachable = True
                    break
                threading.Event().wait(3)
            if not reachable:
                return
            _emit("event", "VM reachable — syncing guest scripts…")
            # idempotent guest prep: keep the journal ON DISK so Bluetooth
            # events survive a VM power-off -- without this, a "Broadcast
            # broke the audio" report can never be diagnosed after the fact
            # (volatile journald is lost the moment the VM powers down)
            ssh_guest("install -d /var/log/journal && "
                      "systemctl kill -s USR1 systemd-journald",
                      timeout=10, quiet=True)
            for local, remote in jobs:
                src = os.path.join(HERE, local)
                if not os.path.exists(src):
                    continue
                try:
                    with open(src, "r", encoding="utf-8", newline="") as f:
                        content = f.read().replace("\r\n", "\n").replace(
                            "\r", "\n")
                    # bytes (not text=True) so Windows never re-adds \r\n on the
                    # ssh stdin -- the guest must receive pure LF or bash breaks
                    # write-then-rename: atomic replace, so a script that is
                    # RUNNING right now (btready.sh during boot) keeps its old
                    # inode instead of being truncated mid-execution
                    subprocess.run(
                        ["ssh", "-p", "2222", "-i", KEY,
                         "-o", "StrictHostKeyChecking=accept-new",
                         "-o", "ConnectTimeout=6", "root@127.0.0.1",
                         f"cat > {remote}.new && chmod +x {remote}.new"
                         f" && mv -f {remote}.new {remote}"],
                        input=content.encode("utf-8"), timeout=20,
                        creationflags=NO_WINDOW)
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=work, daemon=True).start()

    def pair(self):
        # deliberate pairing is a conscious trade: we free the whole radio for
        # a fast broadcast by briefly dropping the earbud audio link (our own
        # silence-feed keeps A2DP transmitting otherwise, which starves the
        # advertising and is why the iPad is slow to SEE the keyboard). Confirm
        # before touching the user's audio.
        if not dark_confirm(
                self.root, "Pair the iPad now?",
                "This briefly disconnects Bluetooth audio so the iPad finds "
                "the keyboard fast — it reconnects automatically the moment "
                "the iPad pairs."):
            return
        # immediate visual acknowledgement (the work runs in a thread).
        # _pair_inflight is set HERE, before the worker: the openspanble
        # restart flaps :9955 (ready -> booting -> ready), and the READY
        # re-edge must not fire auto-reconnect into the middle of it.
        self._pair_inflight = True
        self._broadcast_started = time.time()
        self.pair_btn.config(text="📡  Working…")
        self.pair_btn.state(["disabled"])
        self.status.set("Preparing to broadcast…")
        threading.Thread(target=self._pair_worker, daemon=True).start()

    def _pair_worker(self):
        if not vm_running():
            start_vm_clean()
            for _ in range(45):
                if daemon_status() is not None:
                    break
                threading.Event().wait(2)
        # Restart the keyboard daemon so the iPad pairs to a FRESH GATT server
        # (a stale CCCD/subscription from a prior restart is why a "connected"
        # keyboard silently delivers no input). Bond cleanup is FAIL-CLOSED:
        # a device is removed only when bluetoothctl info EXPLICITLY shows
        # "Connected: no" AND it is not an audio device AND it is not the
        # last audio device on record -- if info comes back empty (busy
        # bluetoothd), the device is KEPT. The old fail-open version could
        # remove the PLAYING earbuds' bond on an empty info, which force-
        # disconnects them mid-song. (ExecStartPre is conditional dual-mode,
        # so the restart does not power-cycle the radio.)
        r = ssh_guest(
            'AUD=$(cat /opt/openspan/audio-device.txt 2>/dev/null); '
            # free the whole radio for a fast broadcast: drop the earbud audio
            # link now (it is reconnected automatically once the iPad pairs)
            '[ -n "$AUD" ] && bluetoothctl disconnect "$AUD" >/dev/null 2>&1; '
            'for d in $(bluetoothctl devices | awk \'{print $2}\'); do '
            '[ "$d" = "$AUD" ] && continue; '
            'info=$(bluetoothctl info "$d" 2>/dev/null); '
            'echo "$info" | grep -q "Connected: no" || continue; '
            'echo "$info" | grep -qi "Icon: audio" && continue; '
            'bluetoothctl remove "$d" >/dev/null 2>&1; '
            'done; '
            'systemctl restart openspanble; '
            'for i in $(seq 20); do ss -ltn 2>/dev/null | grep -q ":9955" '
            '&& break; sleep 1; done; sleep 1; '
            'bluetoothctl pairable on',
            timeout=40)
        if r.returncode != 0:
            # be honest: the guest work failed, so we are NOT broadcasting
            set_advertising(False)
            self._pair_inflight = False
            # we may have already dropped the earbud audio for the burst — the
            # pair isn't happening, so put it back (force past the cooldown)
            self._auto_conn_last = 0.0
            self._auto_conn_fails = 0
            self._auto_reconnect_audio("broadcast failed — restoring audio")

            def failed():
                self.pair_btn.state(["!disabled"])
                self.pair_btn.config(style="TButton",
                                     text="📡  Pair / Broadcast")
                self.status.set("Broadcast failed — see console.")
            self.ui(failed)
            return
        # NOW start advertising -- this is the only place it is ever turned on,
        # and only because the user pressed Pair/Broadcast. (The daemon restart
        # above comes up silent, so the radio is not beaconing until here.)
        if not set_advertising(True):
            _emit("err", "could not start broadcasting — see console.")
        self.broadcasting = True
        _emit("event", "radio freed (audio paused) — NOW BROADCASTING at full "
                       "power; audio returns the moment the iPad pairs.")

        def ok():
            self.pair_btn.state(["!disabled"])
            self.pair_btn.config(style="Accent.TButton",
                                 text="📡  Broadcasting…")
            self.status.set("📡 Broadcasting on the full radio — on the iPad, "
                            "tap \"OpenSpan Keyboard\" to pair")
        self.ui(ok)

    # ---- status tick ----
    def _tick(self):
        threading.Thread(target=self._poll, daemon=True).start()
        self.root.after(3000, self._tick)

    def _poll(self):
        """Worker thread: network/process checks only — every widget update
        happens in _apply_poll on the UI thread."""
        if self._closing:
            return  # shutting down: never respawn anything past _full_stop
        running = vm_running()
        st = daemon_status() if running else None
        on = bool(self.portal_proc and self.portal_proc.poll() is None)
        self._ensure_audio()  # watchdog: relaunch the sender if it died
        aud = bool(self.audio_proc and self.audio_proc.poll() is None)
        # compact mode has no device list on screen, so keep the buds line
        # fresh with a periodic (no-scan) refresh every ~15s
        self._poll_n = getattr(self, "_poll_n", 0) + 1
        if running and self._poll_n % 5 == 0:
            self.bt_panel.refresh(quiet=True)  # routine poll: no console line
        self.ui(lambda: self._apply_poll(running, st, on, aud))

    def _apply_poll(self, running, st, on, aud):
        parts = [f"VM {'●' if running else '○'}"]
        if st:
            parts.append("iPad ● connected" if st.get("kbd_subscribed")
                         else "iPad ○ waiting")
        elif running:
            parts.append("iPad ○ daemon starting")
        parts.append(f"portal {'● ON' if on else '○ off'}")
        parts.append(f"audio {'●' if aud else '○'}")
        # Honest broadcast state, read straight from the daemon -- never a UI
        # guess. If this says BROADCASTING, the machine really is advertising
        # as a Bluetooth keyboard; if it says off, it really is silent.
        if st:
            parts.append("📡 BROADCASTING" if st.get("advertising")
                         else "📡 not broadcasting")
        # UIPI: without admin, input hooks die under any elevated window
        if not is_elevated():
            parts.append("⚠ NOT ADMIN")
        # readiness banner (only reacts on a state change, so no console spam)
        if not running:
            r_state, r_txt, r_col = "stopped", "○  Stopped", MUTED
        elif st is None:
            r_state, r_txt, r_col = "booting", "◐  Booting…  (~90s)", PORTAL
        else:
            r_state, r_txt, r_col = "ready", "●  READY — connect headphones", \
                ACCENT
        if r_state != self._ready_state:
            self._ready_state = r_state
            try:
                self.ready_lbl.config(text=r_txt, fg=r_col)
            except Exception:  # noqa: BLE001
                pass
            _emit("event", {
                "stopped": "VM stopped — everything is down.",
                "booting": "VM up — services starting, hold ~90s…",
                "ready": "READY — the bridge is fully up. Connect your "
                         "headphones.",
            }[r_state])
            if r_state == "ready" and not self.broadcasting \
                    and not self._pair_inflight:
                # the buds try to reconnect on their own during the ~90s
                # boot, give up before the stack is up, and then just sit
                # there -- so reconnect them ourselves once we're READY
                self._auto_reconnect_audio("bridge is READY")
        connected = bool(st and st.get("kbd_subscribed"))
        # console confirmation on the iPad connect/disconnect edge
        if connected != self._ipad_conn:
            if self._ipad_conn is not None or connected:
                if connected:
                    _emit("event", "iPad CONNECTED — keyboard/mouse subscribed "
                          "and live.")
                elif st is not None:
                    _emit("event", "iPad disconnected.")
            self._ipad_conn = connected
        # once the iPad connects: settle the button to a check, auto-start the
        # portal (no manual click), and bring the earbuds back — full steady
        # state without another button press. Clearing broadcasting/_pair_
        # inflight FIRST is required: _auto_reconnect_audio early-returns while
        # either is set.
        if connected and self.broadcasting:
            # the iPad is in -- stop beaconing immediately. Nothing should be
            # advertising as a keyboard once it has served its purpose.
            threading.Thread(target=set_advertising, args=(False,),
                             daemon=True).start()
            self.broadcasting = False
            self._pair_inflight = False
            self.pair_btn.config(style="Accent.TButton",
                                 text="📡  iPad ✓ paired")
            # auto-start the portal so keyboard/mouse bridge immediately
            if not (self.portal_proc and self.portal_proc.poll() is None):
                self.toggle_portal()
                _emit("event", "iPad paired — portal auto-started; "
                               "keyboard/mouse are bridging.")
            # the `on` snapshot predates this auto-start; refresh it so the rest
            # of this tick renders the portal ON (dot + "Stop portal") and never
            # invites a click that would stop what we just started
            on = bool(self.portal_proc and self.portal_proc.poll() is None)
            # WE deliberately dropped audio for the burst, so force the
            # reconnect past its cooldown/backoff: steady state means audio on
            self._auto_conn_last = 0.0
            self._auto_conn_fails = 0
            self._auto_reconnect_audio("iPad paired — reconnecting the earbuds")
        # an abandoned broadcast must not suppress auto-reconnect forever
        if (self.broadcasting or self._pair_inflight) and \
                time.time() - self._broadcast_started > 300:
            # never leave the radio beaconing after an abandoned pair attempt
            threading.Thread(target=set_advertising, args=(False,),
                             daemon=True).start()
            self.broadcasting = False
            self._pair_inflight = False
            self.pair_btn.config(style="TButton", text="📡  Pair / Broadcast")
            _emit("event", "broadcast window expired — press Pair/Broadcast "
                           "again when you're ready to pair.")
            # the burst may have left the earbuds off and no pair ever landed —
            # restore audio so the user isn't stranded without sound. Reset the
            # fail counter too (not just the cooldown): if a prior session hit
            # the 3-fail pause, _auto_reconnect_audio would otherwise no-op and
            # leave the audio we dropped dead — same as the two sibling paths.
            self._auto_conn_last = 0.0
            self._auto_conn_fails = 0
            self._auto_reconnect_audio("broadcast expired — restoring audio")
        # secondary status readout — set AFTER the connect-edge auto-start so
        # `on` reflects the portal we may have just started this tick
        try:
            self.sys_status.set(
                f"VM {'● up' if running else '○ down'}    "
                f"keyboard {'● up' if st is not None else '○ down'}"
                f"{'  (iPad subscribed)' if (st and st.get('kbd_subscribed')) else ''}"
                f"    audio {'● on' if aud else '○ off'}"
                f"    portal {'● on' if on else '○ off'}")
        except Exception:  # noqa: BLE001
            pass
        # while hidden in the tray, make sure the icon still exists (an
        # explorer.exe restart wipes tray icons); if it can't be restored,
        # bring the window back — the app must never be strandable
        if self._tray and not self._closing \
                and self.root.state() == "withdrawn":
            if not self._tray.ensure():
                _emit("event", "tray icon lost — bringing the window back.")
                self._from_tray()
        # ---- compact-mode widgets (cheap; update even when hidden) ----
        colors = {True: ACCENT, False: MUTED}
        self.c_stat["vm"].config(fg=colors[bool(running)])
        self.c_stat["ipad"].config(
            fg=colors[bool(st and st.get("kbd_subscribed"))])
        self.c_stat["audio"].config(fg=colors[bool(aud)])
        self.c_stat["portal"].config(fg=colors[bool(on)])
        self.c_ready.config(text=r_txt, fg=r_col)
        names = self.bt_panel._connected_names if running else []
        self.c_buds.config(
            text="🎧  " + (", ".join(names) if names
                           else "no headphones connected"),
            fg=ACCENT if names else MUTED)
        if self._vol_ok is False and "disabled" not in self.c_vol.state():
            self.c_vol.state(["disabled"])
        v = self._vol_now
        if v is not None and not self._vol_drag \
                and self._vol_target is None:
            self._vol_syncing = True
            self.c_vol_var.set(round(v * 100))
            self._vol_syncing = False
        # `on` may have been refreshed by the connect-edge auto-start above;
        # rebuild the portal token so the status line agrees with reality
        parts = [f"portal {'● ON' if on else '○ off'}" if p.startswith("portal")
                 else p for p in parts]
        cur = self.status.get()
        if not self.broadcasting and not cur.startswith("Radio") \
                and not cur.startswith("⚠"):
            self.status.set("    ".join(parts))
        # keep the Pair button truthful when idle (never mid-broadcast): a
        # settled check while the iPad is live, the call-to-action when it is
        # not. Skipped while a broadcast is in flight so it can't stomp the
        # transient "Working…"/"Broadcasting…" states.
        if not self.broadcasting and not self._pair_inflight \
                and "disabled" not in self.pair_btn.state():
            if connected:
                self.pair_btn.config(style="Accent.TButton",
                                     text="📡  iPad ✓ paired")
            else:
                self.pair_btn.config(style="TButton",
                                     text="📡  Pair / Broadcast")
        self.vm_btn.config(text="Bridge VM ✓" if running
                           else "Start Bridge VM")
        self.portal_btn.config(text="Stop portal" if on else "Start portal")


def _single_instance_lock():
    """Windows named mutex as a single-instance lock. Returns the handle
    (held for the process lifetime) or None if another instance already
    holds it. The OS releases it automatically on exit -- even on a crash --
    so there is no stuck lock and no TCP TIME_WAIT to wait out."""
    try:
        import ctypes
        h = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                "OpenSpanSingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ALREADY_EXISTS
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
            return None
        return h
    except Exception:  # noqa: BLE001
        return True  # never block startup if the mutex mechanism is unavailable


def run_app():
    """The GUI entry point — used by both `python openspan.py` and the
    frozen OpenSpan.exe (via openspan_launcher.py)."""
    lock = _single_instance_lock()
    if lock is None:
        # already running — exit immediately and silently, never stack a
        # window or block on a dialog
        sys.exit(0)

    ensure_ssh_key()  # a fresh clone has no id_openspan; make one before
    #                   anything tries to ssh into the VM
    root = tk.Tk()
    app = App(root)
    # UIPI: a non-elevated OpenSpan gets NO input hooks while an elevated
    # window has focus -- the mouse silently stops crossing, with nothing in
    # any log. Say it plainly up front instead of failing mysteriously later.
    if not is_elevated():
        root.after(800, lambda: _warn_not_elevated(app))
    # X asks first (it's a FULL STOP: portal + audio + VM), and offers
    # "send to system tray" to keep the bridge running instead
    root.protocol("WM_DELETE_WINDOW", app._confirm_close)
    root.mainloop()


def _warn_not_elevated(app):
    _emit("err", "NOT running as administrator. Windows (UIPI) will not give "
                 "OpenSpan keyboard/mouse events while an ELEVATED window has "
                 "focus — the mouse silently stops crossing to the iPad, with "
                 "no error anywhere. If you run ANY app as admin, run OpenSpan "
                 "as admin too.")
    dark_alert(
        app.root, "Run OpenSpan as administrator",
        "OpenSpan is not running as administrator.\n\n"
        "Windows blocks input hooks from a lower-privilege process. So if any "
        "app you use runs as admin (an elevated terminal, for example), the "
        "mouse will silently stop crossing to the iPad whenever that window "
        "has focus — with no error shown anywhere.\n\n"
        "If you run anything as admin, close OpenSpan and relaunch it with "
        "\"Run as administrator\".")


if __name__ == "__main__":
    run_app()
