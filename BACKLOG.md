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

- **Auto-reconnect across the login → user-session handoff.** macOS tears down
  and re-establishes Bluetooth at that boundary, so a device drops once during
  sign-in and needs a manual Connect. Not a fault — but worth handling.
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
