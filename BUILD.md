# Building OpenSpan.exe (single file)

OpenSpan ships as source, but it also packages into ONE standalone
`OpenSpan.exe` — no Python install needed on the target machine.

## Build

```
C:\Python313\python.exe D:\OpenSpan\build_exe.py
```

Needs PyInstaller (`pip install --user pyinstaller`; already present here).
Produces `D:\OpenSpan\OpenSpan.exe` (~64 MB). It runs **in place** — the app
anchors every data file on the exe's own folder, and `D:\OpenSpan\` already
holds them, so there's nothing to assemble. `OpenSpan.bat` and the Start-Menu
shortcut auto-prefer the exe when it exists and fall back to the Python
entry point otherwise.

## How the one file does three processes

The GUI spawns the input portal and audio sender as separate processes. In a
single-file exe there's only one binary, so it re-invokes itself with a role
flag — `openspan_launcher.py` is the entry script and dispatches:

| Invocation | Role |
|---|---|
| `OpenSpan.exe` | control GUI (default) |
| `OpenSpan.exe --portal` | input portal (openspan_portal.py) |
| `OpenSpan.exe --audio` | audio sender (win_audio_send.py) |
| `OpenSpan.exe --setup` | monitor-arrangement window |

`sys.frozen` switches every module's path anchor from `__file__` to
`sys.executable`, so configs/keys/guest-scripts resolve next to the exe.

## What must sit next to OpenSpan.exe

Everything the source layout already has at `ROOT` (= `D:\OpenSpan`):
`openspan_settings.json`, `openspan_config.json`, `openspan_keymap.json`,
`id_openspan`, `mode.txt`, `openspan.ico`, and the `win\` + `guest\` folders
(the guest scripts are read off disk and streamed to the VM on launch). To
hand OpenSpan to another machine, copy the exe **plus those files**.

## Not committed

`OpenSpan.exe`, `build/`, `dist/`, and `*.spec` are gitignored — the binary
is rebuilt on demand, not stored in the repo.

## No-console / no-elevation

Built `--noconsole` (GUI, no console flash) with PyInstaller's `runw`
bootloader, and unsigned-but-unflagged, so it launches unelevated — dodging
the same shell-reputation gate that made the `openspanw.exe` interpreter copy
necessary for the raw `.py` path.
