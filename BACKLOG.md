# Backlog

Not started. Recorded so it is not lost, in the order it was raised.

---

## A dying portal must not leave a button held  ·  do this first

Doug, 28 July: *"when the program closes it does not hold down a button — just
now when you closed it it left a held down to the managed mac and i had to
disable bluetooth to recover it."*

**Severity is the point.** A held button on a machine you are not sitting at,
with no keyboard or mouse attached to it, is unrecoverable from that machine.
Disabling Bluetooth was the only way out. Everything else in this file is
convenience; this one strands the user.

A HID report is STATE, so whatever the last report said is what the device
believes until something says otherwise. Kill the portal mid-drag and the last
thing it said was "button down", forever.

**The graceful path is the easy half.** Portal shutdown, the app's Stop button
and window close should each send a full release to every connected lane before
exiting — `{"cmd":"mouse","buttons":0,...}` and `{"cmd":"kbd","mods":0,"keys":[]}`.

**The graceful path is also not enough.** A hard kill cannot run cleanup, and
that is exactly how the exe is replaced during development (`taskkill /F`), how
a crash ends, and how Windows shutdown can end it. So the release has to be
owned by something that survives the portal's death:

> **The guest daemon should release everything when its command socket closes.**

It already accepts one connection per lane and can see the disconnect. On close:
zero the buttons, zero the keys, once. That covers every way the Windows side can
die, including the ones it cannot anticipate — and it costs two reports.

Belt and braces worth considering alongside it: a watchdog on the daemon that
releases if no command arrives for N seconds while a button is held. A button
held for thirty seconds with no other traffic is not a drag, it is a corpse.


---

## Target settings panel

An inline panel per device (never a pop-out) recording what OpenSpan silently
depends on **on the target machine**. Both of the long hunts on 28 July were the
same shape: the app depending on a setting nobody had written down.

Each row states the requirement *and* the consequence, so the panel doubles as
the explanation of why it matters.

| what | what we need | what breaks if it is wrong |
|---|---|---|
| OS and version | free text | decides which rows below apply at all |
| each display's resolution | the **points** the OS reports, not the panel's native pixels | every distance on that screen, by the ratio |
| displays arrangement | must mirror the desk layout | crossings land on the wrong screen |
| keyboard navigation | on (macOS) | Tab will not move between dialog buttons |
| delay until repeat | not the shortest | accent picker appears during ordinary typing |
| **pointer acceleration on/off** | know which. Apple's own toggle: **System Settings › Mouse › Advanced** (confirmed on Tahoe 26.5.1 — nobody finds it by accident, so the panel must say where it is) | with it OFF the curve is linear and compensation must be **disabled**, not adjusted — the app currently assumes it is on and cannot tell |
| **Windows' own pointer settings** | speed at 10, Enhance pointer precision ON | established 29 July: with our accel at 0 and the target's inverted out, the chain has NO acceleration unless EPP supplies it, and speed >10 doubles the source quantum. See the DEVLOG. |
| **tracking-speed slider position** | mirror the actual value | `_APPLE_CURVE` is the curve at the DEFAULT slider position. At any other position every compensated calculation is wrong by whatever that slider does, silently. |

**Doug's is at notch 6 of 10 as of 29 July**, so this is live, not theoretical: if
that is not the default position the inversion is wrong by whatever the slider
does. It cannot break the position model (every crossing re-syncs against the
target's own clamp) but it does degrade within-screen precision, and a scalar
`sensitivity` can only cancel it at one speed. One number settles it:
`defaults read -g com.apple.mouse.scaling`.

**Research is the wrong approach; measure instead.** Doug cannot run `defaults` on
a managed Mac, and neither will anyone he sends this to — a fix that needs a
terminal on the target is not a fix. But the app already owns the wire and can
clamp to a known origin, which is a ruler:

1. shove left until the target's window server clamps — x is now a **fact**
2. send single reports of one fixed magnitude, one at a time
3. the user says when the pointer stops moving (the far clamp)
4. `pixels_per_report = union_width / reports_counted`

That is `apple_pixels(M)` measured on whatever slider position is actually set,
with nothing installed on the target. Four magnitudes gives four curve points —
the same method the shipped table came from, driven from inside the app.

Better than mirroring the slider for two reasons: it works without knowing
Apple's mapping at all, on any Mac; and it survives an OS update, which a
hardcoded table does not. Store the measured points per device; fall back to
`_APPLE_CURVE` when a device has never been calibrated, so nothing changes for
anyone who does not run it. Strictly opt-in — it deliberately drags the pointer
across a whole screen.

**If the mapping is ever wanted anyway:** It is not enough to
store the number — we need to know what macOS actually does with it. Whether it
scales the input magnitude before the curve, scales the output after it, or
selects a different curve entirely, decides how the compensation must be
adjusted. Establish that from IOHIDFamily / measurement, then mirror it.

## Per-device crossing pressure

`CROSS_SPEED` is one global constant in raw mouse counts per second. It should
be per device, with a slider.

A hand speed that is a deliberate shove across a 32" 4K panel is a flick across
a 10" iPad — same counts, entirely different intent relative to the surface. The
iPad in particular needs its own value because of its size and resolution.

Consider whether the default should be derived from the surface (extent, or
points per inch) rather than from a constant, so a newly added device starts
somewhere sensible instead of somewhere arbitrary.

## Smaller

- **Pushing from a standstill while already against an edge cannot be seen.**
  Windows clamps the cursor and reports no movement, so the momentum gate never
  arms. Reading raw input rather than cursor position would fix it.
- **Clipping transmitted motion at an edge.** A crossing has to reach an edge,
  and the motion carrying the model past it is already on the wire before
  routing sees it. On iPadOS that overshoot is a gesture. Needs `_route_motion`
  to report how much it consumed.

## Profiles  ·  ~~done 29 July~~

Shipped as **Arrangements** — see the DEVLOG entry for 29 July. A profile carries
the desk (screens, positions, sizes, resolutions, devices, input settings) and
never carries `radio` or `port`, which follow the hardware. The selected
arrangement is written through on every save, so there is no unsaved state to
lose on a switch. Covered by `win/test_profiles.py`.

Still open, deliberately:

- **`vm_name` and the guest's own settings** are not in an arrangement either.
  Nothing needs them to be yet.
- **A device in an arrangement that this machine no longer has** loads with no
  radio and a freshly allocated port. It appears in the list, greyed, and cannot
  connect. That is honest, but there is no way to say "drop it from here".

## A radio the VM has lost  ·  ~~done 29 July~~

Shipped. The Bluetooth tab now says how many of this machine's radios the guest
actually holds, and offers one button to hand back any it has lost. See the
DEVLOG entry for 29 July.

Still open:

- **Nothing notices on its own.** The check runs when the panel is built and
  after a reclaim. A dongle pulled mid-session shows as a device that will not
  connect until something asks again. `_status_watcher` already polls every lane
  at 0.8s and would be the place, throttled hard -- two VBoxManage calls are not
  free.
- **A filter per port cannot tell two identical dongles apart.** Both TP-Links
  are `2357:0604`, so which physical socket becomes which `hci` is up to arrival
  order. It does not matter today because the app resolves each device's
  controller by MAC, but a filter with a serial number or port path would make
  the mapping stated rather than incidental.

## Automatic reconnect while the portal is on

Definite, per Doug. If the portal is running, a device that drops should come
back on its own.

**The portal being on is the whole condition, and it is the right one.** It is a
statement of intent: these machines are in use right now. With the portal off,
silence is what the user asked for, and reconnecting would fight a deliberate
Disconnect — one of the four verbs, and the only way to hand a device back.

Where it goes: `_status_watcher` already polls every lane at 0.8 s, already knows
`kbd_subscribed`, and already clears `_last_seen` when a lane drops. The trigger
exists; what is missing is the action.

What it must not do:

- **hammer a device that is off or asleep.** The audio auto-reconnect already
  learned this — it pauses after three consecutive failures (`_auto_conn_fails`)
  and defers while any device is mid-verb (`_any_device_busy`). Follow that.
- **override an explicit Disconnect.** A device the user disconnected on purpose
  stays disconnected until they say otherwise, portal or no portal.
- **reconnect during pairing.** Pair owns the lane while it runs.

Known trigger worth handling first, because it is reproducible: macOS tears down
and re-establishes Bluetooth across the login → user-session handoff, so a Mac
drops once during sign-in and currently needs a manual Connect at exactly the
moment the user has no keyboard.


## Publishing

**Nothing is published.** `main` is at `f60a712` — the single-iPad build from
before any of this. Branch `multidevice` is **62 commits ahead** and holds the
entire multi-device layer, the position model, and everything from the 27–28
July session. `github.com/Divisionless/OpenSpan` and
`douglasknoll.com/openspan.html` both still show the old build.

Held deliberately, pending Doug's go. When it happens, in order:

1. **Scrub before anything else.** Real hardware MACs have leaked into tests and
   docs once before and a test caught it, not me. Scan with
   `([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}` — the **dash** form is the one that
   got missed last time — across the whole tree including `BACKLOG.md`,
   `DEVLOG.md`, `POSITION_MODEL.md` and every test. Also check for the guest SSH
   key and anything naming the employer or the client.

   Checked 29 July: **every tracked file is clean.** One real dongle address had
   reached `DEVLOG.md` (the laptop NAT entry) and is now redacted. The files
   holding the rest — `bt_prefs.json`, `openspan_config.json`, and now
   `profiles/` — are all gitignored, so a fresh clone has no desk in it at all.
   Worth re-running rather than trusting this line: history still contains
   whatever was committed before the ignores existed.
2. **`openspan_config.json` is untracked**, so there is nothing to decide about
   what it ships as — a clone starts with no config and the app builds one from
   the live monitors. Confirm that a first run on a clean machine is actually
   pleasant, since nobody has ever done one.
3. **Merge `multidevice` → `main`**, then push. One merge, not a rebase: the
   history is the record of how the position model was arrived at, and the
   DEVLOG references it.
4. **README** needs rewriting for N devices. It still describes one iPad.
5. **`douglasknoll.com/openspan.html`** — the hero screenshot is the pre-
   four-verb UI and predates the Devices panel, the arrangement canvas and the
   crossing options. Needs a fresh screenshot from Doug (Firefox-first rule:
   deploy, hand him the live link, he eyeballs it).
6. **Decide what to say about the position model.** `POSITION_MODEL.md` is
   written for a hostile reader and is the strongest thing in the repo; it should
   probably be linked from the README and from the site.

## Open questions from testing, unresolved

Recorded so they are not quietly forgotten. None are blocking.

- **Ctrl+Z on the Mac.** The wire is provably correct — `mods=0x01
  keys=['0x1d']`, held 80 ms, clean release — so whatever happens to it happens
  after it leaves this machine. Ctrl+Alt+V's theft cannot explain it, since Z is
  not a hotkey. The five-second discriminator not yet run: **press Ctrl+A in
  TextEdit.** Selects all ⇒ the per-keyboard modifier swap is reaching our
  device. Cursor jumps to start of line ⇒ it is not.
- **The accent picker on the Mac.** Last seen before the compensated-lane flood
  fix and the all-clear heartbeat. May be gone. If it returns, do not debug by
  hand — run a timestamped `btmon` capture on that radio while typing, which
  settles what leaves the air versus what we send.
- **Whether the mouse side buttons are ever seen at all.** `portal.log` will now
  say `mouse side button detected` the first time one arrives, from either hook.
  If that line never appears, that mouse reports them a third way and the
  press-to-jump feature cannot fire.
