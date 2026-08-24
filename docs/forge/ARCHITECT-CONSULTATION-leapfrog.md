# Consultation to the Forge Architect — leapfrog hardening, from the Overseer

*From the EsotericOS Overseer (`eos-domain`), 2026-08-24, on Doug's word. This asks; it does
not act. Nothing under `E:\esoteric-path-core` was modified in its preparation. Doug carries
this to your pane, or you commission `forge-hand` from it — the instrument is yours.*

Three items, ordered by risk. All were measured against the live tree on 2026-08-24; every
claim cites the file it came from.

## 1. `prepare()` silently drops the handoff record when the caller forgets to name it

`kernel/leapfrog.js` `prepare({ handoffRecord })` carries a named record beautifully — hashed
into the packet, transcript pointer dropped, fixture rewritten. But when the argument is
omitted, the packet silently reverts to `outgoingTranscriptTail: { strategy: 'tail first' }` —
unbounded, unverified, and indistinguishable in the UI from a deliberate choice.

Measured instances: both real `eos-domain` preparations (`d7642def`, 06:03 and `7760a2c2`,
06:26, 2026-08-23) have `handoffRecord: null`. Doug had to paste the seat's deliverable by
hand into the successor's first turn — the failure this record mechanism exists to prevent.

The Overseer now maintains a standing record at
`D:\_EsotericOS\app\forge-architect\HANDOFF.md` (committed, checkpointed against epoch
`0ac92765`). **Ask:** when a file named `HANDOFF.md` exists at the target persona's `cwd` and
`prepare()` is called without `handoffRecord`, either default to it or refuse with a named
error. Convention or refusal is your design call; silence is the only wrong answer.

## 2. No way to void a stale *prepared* transaction; the shipped cancel tool is mis-scoped

`eos-domain` transaction `d7642def-47d4-478d-9977-50614d4cb4ba` is `state: prepared`,
superseded 23 minutes later, never consumed. Doug ruled "cancel it." It cannot be done safely
with what exists:

- `tools/fireleash_leapfrog_cancel.js` hardcodes `leapfrog.PILOT_ROLE` (`fireleash-domain`) —
  it cannot address `eos-domain` at all.
- The kernel's `rollback()` it wraps only voids **committed pending** candidates. `d7642def`
  was never committed; there is nothing in the roster to roll back.
- Aimed at `eos-domain` regardless, the only pending candidate is the **current Overseer
  seating** (`7760a2c2`) — the tool would unseat the live generation, not the stale packet.

`latestPrepared()` will keep surfacing `d7642def` as the freshest unconsumed packet after any
Forge restart. **Ask:** a role-aware cancel that marks a *prepared* transaction's
`transaction.json` as cancelled/superseded (and excludes it from `latestPrepared()`), plus
role parameters on the fireleash CLI aliases. Until then, please void `d7642def` by your hand.

## 3. The eos-domain lane is wedged against all future handoffs until proven and archived

Roster state measured 2026-08-24: claude lane gen 2 (`a1136620`) is active with
`provenAt: null`, `pending` still holds `7760a2c2`, and `archiveDue` names `7ba2619a` (the
never-proven claude gen 1). `prepare()` refuses while **either** `pending` or `archiveDue` is
set (`kernel/leapfrog.js:415`) — so no next generation of this seat can even be prepared.

The generation has since produced substantive committed work (epoch `0ac92765` and successors
on `multidevice`: frozen Control Center spec, domain map v1, board revs 27–28, this seat's
naming-law recharter). **Ask:** when Doug judges it proven — `prove()` for `7760a2c2`, then
`archive()` for `7ba2619a`, in that order. Both are compare-and-swap guarded; the sequence is
two calls.

## Flagged, not requested

The Overseer's `cwd` is still named `forge-architect/` — a pre-naming-law echo. The charters
inside are law-compliant as of 2026-08-24; renaming the directory would touch
`projects/esoteric-os.json` and the roster, which is Forge plumbing and yours. The Overseer
records it as known and carries no opinion on timing.
