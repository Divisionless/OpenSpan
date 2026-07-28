# The position model

OpenSpan drives other machines as an ordinary Bluetooth keyboard and mouse.
Nothing is installed on them. That one constraint creates the hard problem:

> A relative HID link can move a pointer **by** an amount, never **to** a place —
> and it can never be asked where that pointer currently is.

Everything here is the model that answers it. The laws are numbered because they
depend on each other in order: Law 3 is only safe *because* Law 1 holds, and
Law 4 is only safe because Law 3 does.

---

## Law 1 — Conservation

> The model may only change position by an amount the wire also moved. When it
> must be somewhere the wire has not taken it, **send the difference**.

A virtual cursor that advances further than the device's real one — or less far —
drifts, with no mechanism to converge again.

Four earlier mechanisms existed only to hide that drift, and every one of them
discarded motion the wire had already delivered:

| mechanism | what it really did |
|---|---|
| position resume | restored the edge you *left* by, which is a live exit trigger |
| edge pressure | 45 target points per lean, sent to the device, dropped by the model |
| switch cooldown | up to 300 ms of motion, likewise sent and dropped |
| proportional arrival | stretched the overlap across the destination's whole edge; not reversible |

You *can* assert a position — you just have to pay for it. `_place()` asserts,
`_warp()` queues the HID reports that make the assertion true. Net: −141 lines,
+73.

## Law 2 — Measurement by clamp

> You cannot ask a device where its pointer is. But its own window server
> **clamps** that pointer at the edge of its screens — so drive it hard in one
> direction and the coordinate becomes a measurement.

The only way to establish truth with no software on the target. It is also the
only genuinely expensive part of the model, so it runs **once per device per
session**, when a lane comes up and nobody is looking at that screen
(`_park_at_door`), and again only when the position becomes unknowable — a
dropped lane.

Screens rarely form a rectangle. Two tall portraits beside a shorter landscape
make an **L**, and on an L the boundary a shove reaches depends on the *other*
axis:

```
shove left    -> [-5986]           the same everywhere -- safe to shove blind
shove right   -> [-2848, -59]      depends on the other axis
shove up      -> [-2179, -1569]    depends on the other axis
shove down    -> [0, 610]          depends on the other axis
```

So the sequence of shoves that collapses every possible position to one point is
**searched from the rectangles** (`_resync_plan`), never assumed, and it includes
diagonals — a shape with a symmetry (a plus, a T) maps onto itself forever under
axis-aligned shoves and never converges. The search takes the *cheapest* plan,
not the shortest: one diagonal across this desk costs 94 reports where two
straight shoves cost 69.

Verified against ten arrangements including a plus, a T, a Z, a staircase, and
two screens meeting only at a corner — that last one is **reported as impossible
rather than guessed at**.

## Law 3 — Pin the axis you left by

> Leaving an edge needs only **one** thing to become true: the axis you crossed.
> Push hard that way as the last act and never touch the other axis — which
> preserves your position along the edge exactly, for free.

Doug's. It replaced a full two-axis re-sync on every exit, which was correct and
awful: every crossing toured the outside of the arrangement, and on iPadOS an
edge *is* a gesture — the top pulls Notification Centre, the bottom the Dock and
app switcher, the side Slide Over.

| crossing | before | after |
|---|---|---|
| iPad | 110 reports | **1** |
| Managed Mac, top edge | 110 | **8** |
| Managed Mac, right edge | 110 | **12** |

## Law 4 — Momentum arms, it does not gate

> A boundary is crossed on purpose, never by drifting into it. A deliberate push
> **arms** a crossing for 350 ms; momentum is not tested at the instant of
> contact.

Doug's. Testing at contact fails twice: a hand **decelerates as it arrives**, so
the push that carried it there has already decayed; and on the PC side the cursor
**stops at the monitor edge**, so pushing harder moves it no further, reports no
motion, and the gate can never re-arm.

Refusing costs nothing, because at a device's *outer* edge the target's own
window server clamps its pointer exactly where the model clamps. That is only
true when **leaving** a device — so the gate never applies to a seam between two
screens of the same device, where the target's pointer really does flow across.

```
a careful nudge              30 counts/s   stays put
slow work along an edge     400            stays put
an ordinary reposition     1200            crosses
a deliberate push          1500            crosses
a firm flick               4000            crosses
```

Known limit: a push from a standstill while already *against* an edge cannot be
seen — Windows clamps the cursor and reports nothing. Reading raw input rather
than cursor position would fix it.

---

## Three facts that decided arguments

**A HID keyboard report is state, not an event.** The host derives presses by
*diffing* consecutive reports, so a duplicate introduced anywhere — TCP, D-Bus,
BlueZ, the link layer — is a **no-op by construction**. The only way to produce
two keystrokes is a genuine present → absent → present on the wire. That single
sentence eliminated an entire class of theories about doubled input.

**Summing two deliberate movements cancels them.** A re-sync is three movements
whose whole purpose is that the device clamps *between* them. Batching a tick's
motion into one net vector made them sum to nearly nothing, so every re-sync on a
device that was not already keeping its reports separate had been a no-op since
the day it was written. Splitting a large delta is always safe; only **merging**
destroys movement.

**Physical inches and display points are different spaces.** The desk layout is
in inches; a window server joins displays in *points*. They agree only where
neighbouring panels have the same points per inch. A 32" 4K panel beside a 32"
1440p one differs by exactly 1.50×, and no arrangement of rectangles reconciles
that — a similarity transform has one scale factor and this needs two.

**Corners are ambiguous.** A diagonal into a corner satisfies *two* edges at
once, so which surface you land on comes down to whichever overshoot happened to
be larger on that report. Corners are also where people reach for things. Both
problems disappear by not crossing there; the dead band is taken from the
**shared overlap**, so both sides of every boundary agree on where it lives.

---

## What holds it honest

`win/test_portal_invariants.py` asserts properties, not examples, against the
**live** desk layout — the only arrangement that has ever found one of these:

- the model takes *all* the motion the wire carried; no gate eats any
- entry lands at the edge named by the crossing, whatever was remembered
- an arrival never lands on another live crossing
- every model jump is paid for in HID reports
- crossing a seam and coming straight back returns you where you were
- a cold re-sync lands in the same place from every starting position
- driving hard into a corner never crosses anywhere
- a re-sync survives the sender and really does clamp

`win/test_resync_shapes.py` checks the shove planner against ten deliberately
awkward arrangements, end to end, against an independent model of how a pointer
clamps.

**Count edges, not reports.** A click whose press and release fell inside one
8 ms tick was emitted once, carrying the *post*-release state — the press never
left. Counting reports cannot see that; counting rising edges can. And a click
that does not register is exactly what makes a person click a second time, which
is how a *lost* event gets reported as a *duplicated* one.

---

## The recommended mode for a complex arrangement: press to jump

Everything above solves *edge crossing* — moving between machines by pushing the
pointer at a border, the way a single desktop works. It is the right default and
it now behaves correctly.

But edge crossing has a cost that grows with the arrangement, and it is
geometric rather than a defect:

- **Edges only partly overlap.** A 17" monitor beside a 32" panel shares about
  half that panel's edge. The rest is a wall, correctly, because nothing is
  there. On this desk the PC covers only the bottom 53% of Mac Display 3's right
  edge, so returning to the PC means being in the bottom half.
- **Distance is real.** Reaching a border means driving there — 3,840 points
  across one 4K panel before the boundary even begins.
- **A corner is ambiguous**, so the ends of every boundary are deliberately
  dead, which costs a little more of each edge.
- **Not everything is adjacent.** A device with no neighbour on a side has
  nothing to cross to, and never can.

None of that is fixable by better geometry, because the geometry is telling the
truth. The arrangement is a **map**, and edge crossing makes you drive it.

**So: hold a mouse side button and the press itself crosses.** It takes the edge
the pointer is nearest and goes to whatever lies that way, ignoring the
adjacency graph entirely — nearest by distance to the rectangle, only in the
direction of travel, skipping any device whose lane is not ready. Press again to
hop again. While it is held, the corner bands and the momentum requirement both
lift, because an explicit request is not an accident and needs no second opinion.

The reasoning in one line: **the adjacency graph exists to make an accidental
crossing land somewhere sensible. A pressed button is not an accident** — so the
question stops being "what does the layout say is next to this edge" and becomes
"what is over there", which is what the hand meant.

For two adjacent screens, edge crossing is lovely and this is unnecessary. From
about three devices — or any arrangement where surfaces do not tile neatly —
this is the mode to use. Both remain available and both are always correct; this
one is simply faster to think in.

**Turning it on:** the two checkboxes beside "Invert scroll wheel". The second
is indented under the first because it only means anything when that is on.

**If the button appears to do nothing**, check `portal.log` for
`mouse side button detected`. Side buttons do not all arrive the same way — some
mice send `WM_XBUTTON` on the mouse hook, others send browser back/forward on the
keyboard hook. Both are accepted. If neither line ever appears, that mouse
reports them some third way. And if the option is on while no side button has
ever arrived, crossing falls back to a deliberate push rather than refusing
everything, so the pointer can always reach the control that turns it off.
