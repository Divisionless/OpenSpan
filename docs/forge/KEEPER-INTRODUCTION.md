# Introduction to the Keeper of the Forge — from EsotericOS

*Paste this into the Keeper's pane as one message. It asks; it does not act. Nothing in it
touches `E:\esoteric-path-core\forge\` — every file named here lives under `D:\_EsotericOS\`.*

---

Keeper — this is a consultation, not a request for role work: analyse and advise. Doug is
incorporating a second project into the Forge, and this message introduces it so that the kernel
learns nothing it should not and the surfaces we would drop in obey your gates on the first try.

**Who is speaking.** The EsotericOS session (Claude), working with Doug in `D:\_EsotericOS\`.
Not a Forge role; not seated. We do not hold, request, or intend to take the checkout.

**What EsotericOS is** — three trees, one board:

| tree | what | mode we would ask for |
|---|---|---|
| `D:\_EsotericOS\app` | the bridge: Python/Tk control app + Debian VM that publishes Bluetooth HID/audio to an iPad and a managed Mac. Git, branch `multidevice`. 53 test suites (`win\test_*.py`), `build_exe.py`, `swap-build.ps1` (hot swap of the running exe; the VM stays up). | read/write |
| `D:\_EsotericOS\shell` | EsotericOS Shell — a fork of Cairo Desktop (Apache-2.0 origin, fork GPL-3.0-or-later). C#/WPF. Contains `EsotericOS.Files` (a Finder-like browser, alpha) and `EsotericOS.DeskPanel` (a desktop-resident panel). Local commits only; push URLs are `no_push` by Doug's order. | read/write |
| `D:\_EsotericOS\managedshell` | fork of ManagedShell (the shell plumbing library). | read/write |
| `D:\_EsotericOS\legacy-csharp` | the previous C# program; reference only. | read |
| `D:\_EsotericOS\preservation` | binaries ledger and cold backups. | read; `cold-backups` forbidden |

The board is `D:\_EsotericOS\app\docs\plan\plan.json` (v3), served locally at
`http://127.0.0.1:7350/plan.html` by `C:\Users\Douglas Knoll\.claude\skills\plan\plan_server.py`.
Live truth files the app writes every paint tick: `D:\_EsotericOS\app\status.json` (bridge state)
and `D:\_EsotericOS\app\openspan_config.json` (the desk arrangement).

**Two facts you must hold about this project, because they touch the Forge itself:**

1. The shell work will, when Doug says so, replace `explorer.exe` as the Windows shell on this
   machine. The Forge is an Electron app running on that desktop. Its compatibility is a blocking
   row on our board (`v3.114 forge-compat`); nothing swaps until the Forge is verified under the
   new shell. Anything the kernel relies on Explorer for — tray, taskbar, toasts, file dialogs,
   `shell.showItemInFolder`, work area — is exactly what we need you to name.
2. Everything under `D:\_EsotericOS\shell` is private and local until Doug advises otherwise. A
   surface must never push, publish, or reference a public artifact of it.

**What we would like to bring, each as one file, and the questions we need answered before
writing any of them** — please answer from the code, not from memory, and correct any wrong
assumption in the list:

- **A project entry.** We have read `E:\esoteric-path-core\forge\projects\d2.json` and would mirror
  its shape as `projects\esotericos.json` (id `esotericos`, roots as in the table above, `forbidden`
  including `D:\_EsotericOS\preservation\cold-backups`, seats keeper/builder/designer as in d2).
  Question: is `projects\*.json` the whole mechanism — which fields does the kernel actually read,
  which are advisory, and does a project scope which surfaces mount, or are surfaces global?
- **`board.surface.js`** — the existing board, not a copy. Question: may a surface embed a local
  HTTP page (`127.0.0.1:7350`) in an iframe or webview under the kernel's CSP/sandbox, or is the
  sanctioned way to read `plan.json` directly and render natively?
- **`desk.surface.js` (EsotericOS)** — status dots + the arrangement map from the two live files
  above. Question: does a name collision with the existing `surfaces/desk.surface.js` matter (id
  vs filename), and what is the id convention for a second project's surfaces (`esotericos.desk`?).
- **`bridge-build.surface.js`** — buttons: run the 53 suites, `build_exe.py`, `swap-build.ps1`,
  the shell `dotnet build`. Question: KERNEL.md §5 says no shell in the path — what is the
  sanctioned way for a surface to run a script by argv array and stream its exit code and output?
  If there is none yet, say so; we will not compose commands to get around it.
- **A router for our tree.** D2 has Cain's `CLAUDE.md`; `D:\_EsotericOS\app` has none. Question:
  does the kernel inject a project's `CLAUDE.md`/charter into a seated agent's turn, or is that the
  Claude CLI's own cwd behaviour, so the file need only exist at the project root?

**Rules of ours that a surface would carry into your house:** no native dialogs; nothing
machine-identifying in a committed file; no scheduled task, loop or timer without Doug's specific
go; never restart or rebuild over what he is using; ASCII-only PowerShell; the credential file
`D:\_SERVER\.secrets\api-keys.txt` is never read by anything in the Forge.

We will write nothing into `forge\` until you have answered and Doug says the incorporation he is
mid-flight on has landed.

**Next Action Item:** yours — the answers above, from the code; then Doug's word; then we drop
the project entry and the first surface, one file at a time, gates run before each is called done.
