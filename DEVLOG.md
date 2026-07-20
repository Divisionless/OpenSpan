# OpenSpan — Devlog

The honest build log: what it took, what broke, what I threw away. OpenSpan
was built in about a week (early–mid July 2026) in close collaboration with
Claude (Anthropic) as my engineering partner — I set direction and tested
everything on real hardware; Claude wrote code, diagnosed from logs and
source, and ran adversarial multi-agent reviews. The Bluetooth radio was
always mine to test — no AI can see whether the earbuds are blinking.

This log keeps the dead-ends in on purpose. The wrong turns are the story.

---

## The one hard constraint

One PC. **One Intel Bluetooth radio**, passed through into a headless Debian
VM. That radio does two jobs at once:

- **A2DP audio** → Bluetooth earbuds
- **BLE HID keyboard + mouse** → the iPad

Every hard problem in the project traces back to that single antenna
time-sharing two roles. "One antenna, two jobs" is the whole saga.

---

## Rough timeline

### ~Jul 5–6 — getting audio to survive
- Goal: route Windows audio to BT earbuds through the VM, and have it *stay*.
- **Bruise:** first cut ran the audio stack on a hand-rolled `dbus-daemon`.
  It accepted the boot-time WirePlumber connection, then refused every
  reconnect. Endless restart failures.
- **Fix:** rebuilt the standard, documented way — PipeWire/WirePlumber/BlueZ
  on a real persistent systemd *user* bus (`loginctl enable-linger root` →
  `/run/user/0/bus`). This was the crux. Do not hand-roll dbus.
- **Bruise:** audio dropped ~5s into any silence. Cheap TWS earbuds fully
  disconnect when the A2DP transport idle-suspends.
- **Fix:** two things hold it, and it took an audit of the live VM to get
  this right. The WirePlumber bluez config is edited to disable suspend
  (`suspend-timeout=0`, `pause-on-idle=false`, `with-logind=false`), *and*
  the UDP bridge feeds real-time silence during gaps so the transport never
  idles. I'd earlier written that the config didn't exist and the feed was
  the whole story — wrong on both counts. Verify against ground truth, don't
  trust your own notes.
- Contention gremlin: Debian's stock per-login PipeWire services spawned a
  competing WirePlumber on every SSH login that grabbed then dropped the
  endpoints — "it dropped the instant I typed." Masked them.

### ~Jul 6 — keyboard + audio on the same radio
- Proved BLE HID (iPad) and A2DP (earbuds) can run together on the one radio.
- The breakage was never the protocol — it was **operational coupling**.
  Keep them segregated: restarting audio must never touch the keyboard, and
  vice versa.

### ~Jul 9 — the mouse-lag rabbit hole
- Mouse felt laggy. Root cause was self-inflicted: BlueZ/kernel default LE
  interval is 30–50ms, and something was actively *slowing the iPad down* to
  it.
- **Bruise:** cranked the interval to 7.5ms. Mouse got great — and the audio
  garbled constantly. 7.5ms services the iPad ~133×/sec and starves A2DP of
  airtime. One antenna, two jobs.
- **Bruise:** added per-interval mouse coalescing to compensate. Reverted it
  — added risk, wasn't the cause.
- **Fix:** settled on 15–30ms (`Min=12`/`Max=24`). Snappy mouse, airtime left
  for clean audio. The balance point.

### ~Jul 10 — the connect script I kept breaking
- **Bruise (repeated):** rewrote `bt-connect.sh` three times (v2/v3/v4) with
  "honest state reporting," stop-scan-before-pair, retry loops. Every version
  *broke the connect* and produced false "the earbuds didn't respond"
  reports. The buds were fine.
- **Lesson:** BlueZ purges an unpaired discovered device ~1s after the scan
  stops. Pair *during* the live scan. Reverted to the basic version and left
  it alone. (Hard rule that came out of this: stop guessing, diagnose from
  data — every wrong guess cost a real hardware test cycle.)
- Launch gotcha: Windows blocked the unsigned Python interpreter ("an
  administrator has blocked this app"). Root cause wasn't what I first
  guessed (OneDrive/MOTW) — it was a `RUNASADMIN` AppCompat flag on
  `pythonw.exe` forcing elevation. Fix: a same-dir unflagged copy,
  `openspanw.exe`. First local git snapshot taken here.

### Jul 10–12 — the sprint (this is where most commits live)
- **Latency:** the audio was decent right after a restart and got worse over
  a session. Two culprits: a hidden 64KB stdin pipe to `pw-play` (~341ms of
  buffered audio nobody accounted for) and an open-loop silence injector that
  *ratcheted* the queue upward on every late packet and never reclaimed it.
  Capped the pipe, debounced the injector.
- **Two-senders garble:** audio garbled after a restart. Not the volume code
  (measured, innocent) — a race in the sender watchdog spawned *two* senders
  onto the one UDP port. Named mutex + launch lock.
- **Broadcast killed the music:** hitting Pair/Broadcast instantly dropped
  A2DP. Two real bugs: (1) a systemd drop-in had silently *reset* the
  keyboard unit's `ExecStartPre` list back to an unconditional radio
  power-cycle — `systemctl cat`, not just reading the script, is how you see
  the truth; (2) the bond-cleanup was **fail-open** — an empty `bluetoothctl
  info` (busy bluetoothd) matched neither guard, so it removed the *playing*
  earbuds' bond mid-song. Made it fail-closed.
- **Auto-reconnect:** the buds page the adapter during the ~90s VM boot, give
  up, and never retry — so I was the retry. Taught the app to reconnect them
  itself, structurally unable to scan or pair (an adversarial review proved
  the first version *could* reach the scan branch — fixed).
- **Two-way clipboard:** plain Ctrl+C/Ctrl+V now sync both machines (Apple
  Shortcuts + a token-guarded LAN relay + FKA key-combo triggers the PC sends
  itself). Found a day-one latent bug on the way: the ctypes clipboard code
  never declared its Win32 handle types, so 64-bit handles were being
  truncated — it had been working by allocation-address luck the whole time.
- **Compact mode + exe:** collapsible panel with volume + L/R balance;
  packaged the whole thing into one `OpenSpan.exe`.

### Jul 12 — publication prep
- Assessed readiness with a fresh-eyes review. Verdict: **not ready** — for
  two independent reasons.
- **The catch that mattered:** every commit was authored under my real name
  and the tree carried my home network's fingerprint (device MACs, LAN IP,
  monitor layout). A `git push` would have doxxed me permanently. Scrubbed
  the personal data, squashed history to one clean commit. (Casualty: the
  granular commit timeline — hence this hand-written log.)
- Pruned `guest/` from 158 files to 39 (it was a dumped working directory —
  ~120 throwaway experiment scripts). Rewrote the README, which had gone
  stale enough to tell people the *wrong* USB mode. Added SSH-key
  self-provisioning and a create-VM script.

### Jul 13 — after v1.0: the volume-curve bug
- Swapped to a second (identical) pair of earbuds mid-session; they came in
  **ear-splitting**. First reflex was a guess — "these buds are just louder
  hardware." Wrong — and saying it out loud, then flagging it for a human
  check, is what saved it.
- A five-agent adversarial review of the whole volume chain (four read-only
  investigators + an adjudicator) refuted the guess and found **two separate
  things**:
  - *The hot arrival:* a Bluetooth sink WirePlumber had no saved volume for
    came up at unity 100%. These sinks are A2DP absolute-volume
    (`HW_VOLUME_CTRL`), so 100% *is* the earbuds' hardware max — a never-seen
    pair starts at full. (I first mis-read it as one pair changing its MAC on
    reset; the device list showed two physically distinct pairs. Human ground
    truth beat the log inference.)
  - *The real bug:* the volume slider had no usable range — everything above
    ~25% was "too loud." The sender read the Windows master's *slider position*
    (`GetMasterVolumeLevelScalar`, a tapered 0–1) and used it as a raw
    amplitude. At 5% that applied −26.7 dB where the slider truly meant
    −46.2 dB (~20 dB too hot), and linear faders pile all their range into the
    bottom.
- **Fix:** read the actual level in dB (`GetMasterVolumeLevel`) and apply
  `10^(dB/20)`, so the earbuds track the same perceptual curve as the Windows
  slider. Tagged v1.0.1.

### Jul 14 — the copy that killed the mouse (and wasn't our bug)

The worst bug in the project, and the most instructive.

- **Symptom:** copy anything in Windows → the mouse silently stops crossing to
  the iPad. The iPad still shows connected. Restarting the portal fixes it,
  every single time.
- **I shipped three fixes. All three were wrong.** (1) `leave()` did a blocking
  socket send inside the hook proc — real hygiene problem, not this bug.
  (2) Re-install the hook when it goes deaf. (3) Run the hooks on a replaceable
  thread. Each one was aimed at the hook code, and each one failed.
- **So I stopped guessing and instrumented it.** A heartbeat, a callback
  counter, exception capture, per-call timing. The data was brutal and
  unambiguous: the hook procs **never blocked**, **never threw**,
  `SetWindowsHookEx` kept returning **valid handles** — and delivered **zero
  callbacks** while the cursor was provably moving. Six re-installs in a row:
  nothing. Brand-new threads: nothing. Only a **new process** ever restored it.
- **The answer was Windows, not us. UIPI:** a non-elevated process receives
  **no low-level input hooks while an elevated window has focus.** I was
  copying inside an admin terminal. The instant it had focus, the portal went
  deaf. "Restarting the portal fixed it" only because restarting **steals focus
  back**.
- **Fix:** run OpenSpan elevated. It now detects this, warns at launch, shows
  `⚠ NOT ADMIN` in the status bar, and the README states it as a requirement.
  **Every line of watchdog scaffolding written to chase it was deleted.**
- **Lesson:** it looked *exactly* like a bug in our code, so I kept fixing our
  code. The instrumentation is what ended it — not cleverness. When three fixes
  in a row miss, stop fixing and start measuring.

### Jul 14 — broadcasting is now opt-in (consent, not default)

Doug caught this: the iPad was reconnecting on its own, and the app never said
it was broadcasting. It wasn't a bug so much as a bad default — the daemon
registered the BLE advertisement **at boot and never unregistered it**, so the
machine advertised as a Bluetooth keyboard 24/7 and any bonded iPad would
silently reconnect.

Now: the daemon comes up **silent**. Only **Pair/Broadcast** turns advertising
on, it turns itself **off** the moment the iPad is in (and on a failed or
abandoned pair), and the status bar reports the **daemon's real state** —
`📡 BROADCASTING` or `📡 not broadcasting` — never a UI guess. A bonded iPad
now *cannot* reconnect without you asking. Headphone auto-reconnect is
untouched.

---

## Operating principles that shaped it

- **I test the radio; the AI doesn't.** No AI can see whether the buds are
  blinking, so it would draw confident wrong conclusions. Bluetooth
  verification was always mine. Every fix got baked into the app; I tested
  when I could.
- **No guessing.** Diagnose from logs, config, source, process lists — or say
  "I don't know yet, here's the one thing I'd measure." Every wrong guess is
  a real hardware test cycle.
- **Keep the dead-ends visible.** N bugs that look separate are often one
  wrong assumption wearing different hats. Laying failures side by side is
  what reveals the pattern.
- **Adversarial review.** Big changes went through multi-agent reviews (one
  round ran 22 agents) that tried to *refute* each finding before I trusted
  it. They caught real ones: a native tray crash, a retry loop that could
  fire five Bluetooth scans per click, a COM call that could freeze the UI.

---

## Fast-pair: the radio-contention lesson

The iPad was slow to *see* the keyboard when pairing. The tempting story is
"boost the advertising power." The real one: there is no separate low-power
advertising mode — the advertisement is registered once at boot and never
touched. What starves discovery is that the single radio time-shares BLE
advertising with A2DP audio, and our own silence-feed (the fix that stops the
earbuds dropping mid-song) keeps A2DP *transmitting even when nothing is
playing*. So muting the PC does nothing; the audio link has to actually drop.

Fix: pressing Pair now asks first ("briefly disconnect Bluetooth audio to pair
fast?"), then drops the earbud link to give the broadcast the whole radio,
auto-starts the input portal the moment the iPad bonds (no second click),
settles the button to a check, and reconnects the earbuds on its own. Freeing
the audio *is* full-speed advertising.

The pre-hardware adversarial review (8 agents — five failure lenses, each
finding put to a refuter) caught two bugs before they cost a radio session:
the audio-restore on the timeout/failure paths could silently no-op if a prior
session had hit the 3-fail reconnect pause (the fail counter wasn't reset like
the two sibling paths), and a stale `on` snapshot mislabeled the just-started
portal button "Start portal" for one tick — a click in that window would have
stopped the portal it had just started. Both fixed; a headless harness now
pins the whole flow (28 checks) so it can't silently regress.

---

## The tray crash: a bug only the packed exe could have

Sending the app to the system tray hard-crashed the packed exe — twice, back to
back — leaving the window gone but the audio and portal child processes still
running (orphaned). Nothing reproduced it from source. The Windows error log had
the answer, exact and unguessable: faulting module `_ctypes.pyd`, exception
`0xc000041d` ("an exception occurred during a user callback").

The chain: the tray uses a ctypes `WNDPROC` callback. If that callback raises,
ctypes tries to print the traceback to `sys.stderr` — but a `--noconsole`
PyInstaller build sets `sys.stderr = None`, and writing to `None` from inside a
Win32 callback faults the whole process. Source never hit it because source has
a real stderr.

Two fixes plus a guard against ever shipping it blind again: the frozen launcher
now points `stdout`/`stderr` at a real log file when they're `None`, so a
callback exception is logged instead of fatal; the `WNDPROC` is wrapped so no
exception can escape it at all; and a hidden `--traytest` role exercises the
exact tray path inside the packed binary and prints OK/FAIL, so the fix was
verified in the *frozen exe* — not just from source — before it went back.

Lesson banked: a windowed frozen build is a different runtime. Any C callback
needs a writable stderr and a body that cannot raise, and "passes from source"
is not "passes as an exe."

---

## The iPad as a normal Bluetooth device

The pairing UI had accreted its own vocabulary — "Pair / Broadcast", a separate
"portal" button, a "re-pair to reset the bond" escape hatch — each one a piece
of BLE plumbing promoted to a button. It worked, but it made you think about
advertising and GATT subscriptions instead of a keyboard.

The reframe: the PC is a Bluetooth *peripheral*, so model it like any other
Bluetooth device — **Pair, Connect, Disconnect, Unpair** — and hide the rest.
It maps cleanly onto the radio, because for a peripheral *advertising is the
connect/disconnect lever*. Connect = advertise so the bonded iPad reconnects;
Disconnect = drop the link and stop advertising, so nothing can re-attach and
the iPad's on-screen keyboard comes back and stays; Unpair = forget the bond.
The old broadcast/bounce/portal machinery is still there — it just lives behind
those four verbs now. Bond state is read live from BlueZ by a self-healing
periodic read, so the buttons reflect reality and even notice a "Forget This
Device" done on the iPad itself.

Getting the concurrency right took four adversarial-review passes: a re-entry
guard so no path can start two pair workers at once; a lock so pressing
Disconnect to *cancel* an in-flight pair can't interleave with the worker
committing the broadcast; and — the lesson banked — a "clever" self-latching
bond-state cache that a review caught stranding a real bond as "not paired"
when a startup read timed out. The fix was to stop being clever: a plain
periodic read that self-heals beats a latch that can get stuck.

## A window that doesn't tear

The frameless header drag shredded — dragging left black gaps and torn strips
until you let go. The cause wasn't lag. Driving the move through Tk's
`geometry()` invalidated the whole client area on every mouse-move, and the
flood of motion events starved `WM_PAINT`, so the heavy canvas never repainted
mid-drag. The fix is to move the window with a raw `SetWindowPos` size-free blit
instead: Windows relocates the existing pixels with no invalidation, so there is
nothing to repaint and nothing to starve. Smooth — and still safe from the
reentrancy rule, because it is a synchronous Win32 call from a normal callback,
with no modal move loop.

---

## Status

Working & tested: BLE keyboard + mouse, edge crossing, keymap remaps,
Bluetooth audio (volume + balance), the iPad managed as a normal Bluetooth
device (Pair / Connect / Disconnect / Unpair), themed in-frame dialogs, a lean
one-window UI with a collapsible console + system tray, a tear-free frameless
window, headphone auto-reconnect, single-file exe. Clean repo, honest docs.

**Still in progress — the two-way clipboard.** The plumbing works (FKA chords →
Apple Shortcuts → a token-guarded LAN relay), but it is *not* finished: the
iPad Shortcut's token can drift out of sync with the relay's and paste then
returns a "bad token" error instead of your text, and the setup is not yet
documented well enough for anyone else to reproduce reliably. Treat clipboard
sync as experimental until that's sorted.

Reproducible VM: `create-vm.ps1` + `guest/provision.sh` turn a fresh Debian
into the working bridge — validated on a fresh clone (software) and confirmed
end-to-end on the radio + iPad. Tagged v1.0; v1.0.1 fixes the volume-slider curve.

Built by Douglas Perianu Knoll, with Claude.
