# Backlog

Not started. Recorded so it is not lost, in the order it was raised.

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
| **pointer acceleration on/off** | know which | with it OFF the curve is linear and compensation must be **disabled**, not adjusted — the app currently assumes it is on and cannot tell |
| **tracking-speed slider position** | mirror the actual value | `_APPLE_CURVE` is the curve at the DEFAULT slider position. At any other position every compensated calculation is wrong by whatever that slider does, silently. |

**The slider needs research before it can be mirrored.** It is not enough to
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

## Profiles

Named, switchable configurations.

Doug's, one word, so the scope is his to set. The open question is where the line
falls, because the config already mixes two kinds of thing:

- **belongs in a profile** — the desk arrangement, which devices exist, their
  displays and sizes, per-device input settings, the side-button rule
- **belongs to the machine** — `vm_name`, daemon ports, radio assignments,
  bonds. Those follow the hardware, not the situation.

Switching must reload the portal, which `portal_signature` already handles for
free: a profile switch is a config change like any other.

Worth knowing before designing it: bonds live on the guest per radio, so two
profiles that assign the same radio to different devices would fight over one
lane. Either a profile owns its radio assignments (and switching re-provisions),
or it does not touch them at all.

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
   key, `bt_prefs.json`, and anything naming the employer or the client.
2. **Decide what `openspan_config.json` ships as.** The repo copy currently
   describes Doug's actual desk — three devices, real radios, real screen sizes.
   It should ship empty, or as an obviously-fictional example.
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
