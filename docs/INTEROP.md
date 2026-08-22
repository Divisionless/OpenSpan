# Interoperating with other input software

OpenSpan bridges this PC's keyboard and mouse to an iPad or a Mac. While it is
capturing, its two low-level hooks — installed at `win/openspan_portal.py:2648`
(`WH_MOUSE_LL`) and `:2650` (`WH_KEYBOARD_LL`) — swallow input wholesale and
re-create it on the target device. The pointer is not on this desktop. Anything
else on this machine that acts on that input is acting on input its user cannot
see.

This document is OpenSpan's half of a two-sided contract. The other half —
what EsotericOS does, what it claims, and why — is
[`D:\EsotericOS\docs\INTEROP.md`](file:///D:/EsotericOS/docs/INTEROP.md), and it
is cited here rather than restated. One source of truth per product; a
paraphrase drifts, and the drift is invisible until it matters.

---

## 1. The stand-down contract

OpenSpan holds one named Windows mutex for exactly as long as it owns this
machine's keyboard and mouse:

```
Local\EsotericOS.InputCaptureLease
```

The name is defined once, at `win/openspan_portal.py:418`, and asserted
byte-for-byte against the published spelling by `win/test_interop.py`. It
carries the `EsotericOS.` prefix because they defined the contract; the prefix
names its author, not its owner. `Local\` scopes it to this logon session, so a
capture in one session cannot silence anything in another.

- **Acquired** when capture begins — `Portal.enter()`, `win/openspan_portal.py:1914`,
  the method's first statement.
- **Released** when capture ends — `Portal.leave()`, `win/openspan_portal.py:1977`,
  immediately after `self.active = False`.

Acquisition uses a **1000 ms timeout, never zero** (`LEASE_TIMEOUT_MS`,
`win/openspan_portal.py:422`). Their spec asks for this and gives the reason:
EsotericOS probes the lease by taking it and handing it straight back, so a
zero-timeout acquire can lose that race and wrongly read a free lease as
contended.

`WAIT_ABANDONED` (0x80) is treated as a **successful acquire**, not an error
(`_take`, `win/openspan_portal.py:577`). That is the whole reason the contract
specifies a mutex: if a holder dies without releasing, Windows hands the next
waiter `WAIT_ABANDONED` and ownership transfers. An event left signalled by a
dead process stays signalled forever, and the counterpart suspends itself
permanently with nothing alive to reset it.

That is not hypothetical here. The OpenSpan GUI hard-kills the portal with
`taskkill /PID <pid> /T /F` (`win/openspan.py:1487`) on every geometry change,
which is two clicks away in normal use. No teardown code of ours runs on that
path. Abandonment is the mechanism that makes it survivable, and it is verified
end to end by `win/test_interop.py` (section 4b), which takes the mutex on a
thread, lets that thread die still holding it, and asserts the next acquire
reclaims it and says so in the log.

### If the acquire times out, capture proceeds anyway

`_take` logs the timeout and captures without the lease
(`win/openspan_portal.py:577`). **The lease is a courtesy signal, not a
permission gate.** Nothing may make this keyboard wait on other software before
reaching the device it is already pointed at — a user whose input is halfway to
a tablet cannot negotiate.

---

## 2. Why a dedicated lease thread

**A Windows mutex is owned by the thread that acquired it. `ReleaseMutex` from
any other thread returns FALSE and the mutex stays held** — and because nobody
died, nothing self-heals.

"Acquire where capture starts, release where capture ends" would have shipped a
permanently-held lease here, because capture in OpenSpan does not start and end
on one thread. The call sites, current as of this document:

| | `win/openspan_portal.py` | Thread |
|---|---|---|
| `enter()` | 2084 (`_mouse_proc`) | mouse hook |
| | 2267 (`_kbd_proc`) | keyboard hook |
| | 1110 (`_enter_nearest`) | whichever hook invoked the jump |
| `leave()` | 1799, 1871 (`_route_motion`) | mouse hook |
| | 2214 (`_kbd_proc`, the Esc×3 panic bail) | keyboard hook |
| | 1046 (`_jump_nearest`) | mouse hook |
| | 851, 866 (`send`) | sender |
| | **921 (`_status_watcher`)** | **its own thread** |

Line 921 is the one that decides the design: a dropped lane tears capture down
from the status watcher, a thread that never acquired anything. Releasing there
would fail silently and leave the counterpart suspended for as long as the
portal ran.

So **one thread owns the handle for its entire life** — `InputCaptureLease`,
`win/openspan_portal.py:448`, started once from `Portal.run()` at
`win/openspan_portal.py:2644`, *before* the hooks are installed. `enter()` and
`leave()` do not touch the handle; they call `_lease_acquire`
(`win/openspan_portal.py:1887`) and `_lease_release` (`:1901`), which put a
single bool on a `queue.Queue`. Nothing else in the process references the
handle.

`win/test_interop.py` asserts this against the source rather than against a run,
because the bug is invisible in a run: a test that exercises enter/leave on one
thread passes happily while the shipped code releases from four. It checks that
`CreateMutexW`, `WaitForSingleObject` and `ReleaseMutex` appear nowhere outside
`InputCaptureLease`; that `_take`/`_give`/`_close` are called only from `_run`;
that `self.lease` is referenced only by `__init__`, `run` and the two helpers;
and that no hook, watcher or sender path names a mutex primitive at all. It then
drives the lease from six threads at once and asserts every handle operation ran
on the lease thread.

### Failure is logged, never raised

`enter()` and `leave()` run **inside low-level hook procedures**. An exception
raised there is a dead keyboard and mouse, on a machine whose input may already
be on another device. So:

- The posting side (`_post`, `_lease_acquire`, `_lease_release`) cannot raise
  and cannot block.
- The lease thread cannot die: `_run`'s dispatch is wrapped, and a fault is
  logged and ignored. A dead lease thread still reporting `held` would suspend
  the counterpart forever — worse than never having signalled.
- A mutex that will not create degrades to silence: `available` stays False, the
  thread drains its queue, and capture behaves exactly as it did before this
  contract existed.

Every transition prints a `[lease]` line to `portal.log`. A collision that
reaches the user as "my shortcut stopped working", with nothing in any log, is
the outcome this exists to prevent.

### GUI-side reset

The GUI process never captures and never holds the lease. But it is what kills
the portal, so it collects the abandonment at the moment it knows the holder is
gone, rather than leaving it for the counterpart's next probe —
`_clear_input_capture_lease`, `win/openspan.py:1520`, called from
`win/openspan.py:6857` (full stop), `:6989` (portal restarted for a geometry
change) and `:7067` (portal stopped). It runs on a throwaway thread so a
contended wait cannot stall the UI, and it acquires and releases on that one
thread. This is belt-and-braces: correctness comes from abandonment, and this
only ever shortens the window.

---

## 3. What the lease does NOT cover

**This section is the part of the contract that was missing on both sides.**

The lease makes the counterpart stand down *while OpenSpan is capturing*. But
several OpenSpan claims are designed to fire **when OpenSpan is idle** — which
is precisely when the counterpart is fully live and the lease is released. The
lease is a complete answer for the wholesale swallow of §4c and **no answer at
all** for §4a and §4b.

Hook order decides nothing in anyone's favour: whichever hook installed last
sees the event first, and OpenSpan returns 1 on all of these.

**Neither side moves. Both sides list them.** They are claimed even when the
lease is not held.

---

## 4. What OpenSpan claims

**OpenSpan calls `RegisterHotKey` zero times.** Every claim below is made by
swallowing inside one of the two low-level hooks. Consequence for anyone
checking for conflicts: **nothing OpenSpan claims will appear in a
`RegisterHotKey` conflict scan on either side.** A hotkey-registration audit
will report a clean bill and be wrong. This list is the only inventory.

OpenSpan also **never injects keys into Windows** — no `SendInput`, no
`keybd_event` anywhere in `win/`. Its only Windows-side injection is
`SetCursorPos`.

### 4a. Keyboard chords that fire while OpenSpan is NOT capturing

| Chord | `win/openspan_portal.py` | Behaviour |
|---|---|---|
| `Ctrl+Alt+I` | 2261 | Enter the first ready device — hands this keyboard to another machine. Swallowed. |
| `Ctrl+Alt+Q` | 2259 | `return 1` and nothing else. **Swallowed; does nothing.** See §7. |
| `Ctrl+Alt+Shift+V` | 2272 | Paste from PC: fires the iPad's fetch shortcut, or types the clipboard to the captured device. Swallowed **unconditionally**. |
| `Ctrl+Alt+Shift+C` | 2291 | Ask the iPad to push its clipboard to the PC. Swallowed **unconditionally**. |

Two asymmetries worth knowing, because a downstream hook sees them:

- All four swallow the key-**down** and let the key-**up** through to
  `CallNextHookEx`. Another hook therefore sees a bare `I`/`Q`/`V`/`C` key-up
  with no matching key-down, which desynchronises held-key tracking. The same is
  true of the third `Esc` of a panic bail.
- `Ctrl+Alt+Shift+V` and `Ctrl+Alt+Shift+C` `return 1` with no `self.active`
  guard, so they are swallowed even when nothing is captured and the handler
  does nothing — including with Win also held. `Ctrl+Alt+Q`/`I` at 2259/2261 do
  gate on `not self.active`.

### 4b. Mouse buttons — invisible to every conventional conflict check

Governed by `cross_requires_side_button` and `side_button_jumps_nearest` in
`openspan_config.json`; both are currently **on**, in the config and in all
three profiles.

| Button | `win/openspan_portal.py` | Behaviour |
|---|---|---|
| XButton1 / XButton2, idle | 2066 | `_set_side()`, then swallowed |
| XButton1 / XButton2, capturing | 2170 | swallowed |
| `VK_BROWSER_BACK` / `VK_BROWSER_FORWARD` (0xA6/0xA7) | 259, 2221 | The same two physical buttons as many mice report them, on the **keyboard** hook. Swallowed globally, down and up. |

Two things follow, and they are the reason this section exists:

1. With `cross_requires_side_button` on, **OpenSpan eats mouse Back/Forward
   system-wide** whether or not it is capturing. Mouse back/forward in a browser
   is gone on this PC while the portal runs.
2. With `side_button_jumps_nearest` on, a bare side-button press is a verb:
   `_set_side` (`win/openspan_portal.py:1241`) → `_jump_now` (`:1056`). **One
   mouse button, no chord, no edge — and the keyboard and mouse are on another
   machine.** That is the most destructive claim OpenSpan makes and it is
   invisible to a hotkey audit.

EsotericOS binds no mouse buttons today, so there is no collision now. **Both
physical side buttons are reserved explicitly, in both the forms Windows
reports them**, so that the first mouse gesture any counterpart ever adds does
not land on an action that strands the user's input on a tablet.

### 4c. While capturing — the total swallow

This is a blanket, not a list, and it is what the lease is for. It is covered
completely.

- Verbatim lanes (`win/openspan_portal.py:2235`): every key forwarded raw and
  swallowed. Only Esc×3 survives.
- Non-verbatim lanes (`:2306`): every key swallowed.
- Mouse (`:2086`–`:2190`): motion, all buttons, wheel. All swallowed. The cursor
  is re-centred on the primary screen on every event.
- Panic bail (`:2200`): three plain `Esc` within 2 s while captured → `leave()`.

### 4d. Chords sent on the wire, never to Windows

`FKA_FETCH` and `FKA_PUSH` (`win/openspan_portal.py:173`–`174`) are HID reports
to the target daemon. They read as `Ctrl+Option+G` and `Ctrl+Option+H` on
iPadOS. **Windows never sees them and they cannot reach another hook.**

Worth stating because it looks like a collision and is not: EsotericOS moved one
of its features onto `Ctrl+Alt+G`, and `FKA_FETCH` is `Ctrl+Opt+G`. Different
machines; the two never meet. A user physically pressing `Ctrl+Alt+G` on the
iPad lane cannot trigger it either — `modifier_remap {"alt": "cmd"}` sends it as
`Ctrl+Cmd+G`.

---

## 5. Named objects OpenSpan owns

| Name | Kind |
|---|---|
| `OpenSpanSingleInstance` | mutex — one GUI instance (`win/openspan.py:8987`) |
| `Local\EsotericOS.InputCaptureLease` | mutex — this contract; theirs by name, held by us |

The convention, so it stays true: **their prefix is `EsotericOS.`, ours is
`OpenSpan.`**, and the lease is the single deliberate exception because they
defined it. `win/test_interop.py` asserts that every `Local\`/`Global\` string
constant in the portal carries one of those two prefixes.

---

## 6. Elevation

The EsotericOS control GUI is admin-only. Its unflagged (`asInvoker`) packaged
executable is only a bootstrap: when its token is not elevated, it releases the
single-instance mutex, asks Windows to run the exact same command through
`ShellExecute(..., "runas", ...)`, and exits before keys, radios, audio, or the
VM are touched. Failure or cancellation is inert. There is no Ignore path.

At interactive sign-in, `tools\app-autostart.ps1` registers the per-user
`EsotericOS App (elevated)` task with RunLevel Highest and a short delay, then
removes the legacy HKCU Run value only after verifying that exact task. This
makes elevation a boot contract instead of a dialog outcome.

**The consequence, stated plainly: a non-elevated process receives nothing from
its low-level hooks while an elevated window has focus — silently, with the
hooks still reporting as installed.** So while an elevated OpenSpan window has
focus, EsotericOS gestures do not fire. UIPI is declining to deliver; no
stand-down contract can fix it, in either direction.

This costs more than it first appears, and the correction belongs in both docs:
an OpenSpan window is focused precisely when OpenSpan is **not** capturing —
arranging screens, editing a display, working through settings — because
capturing means the pointer has left this machine. The dead zone is common in
normal use, not rare.

The mandatory elevation deliberately chooses reliable cross-integrity input
bridging over a medium-integrity control process. Peer gesture consumers must
account for that boundary; lowering the EsotericOS GUI is not a supported
workaround.

---

## 7. Chords: who moved, and one that should move back

EsotericOS moved `Ctrl+Alt+I → Ctrl+Alt+G` and `Ctrl+Alt+Q → Ctrl+Alt+K` because
OpenSpan claims them. The principle both sides hold: **where a collision's two
sides carry unequal consequences, the milder one moves.** A failed convenience
shortcut is an inconvenience; a failed chord in a product bridging your keyboard
to another device can leave you unable to get input back. OpenSpan wins by
default, and would whichever product had been written first.

`Ctrl+Alt+I` is a genuine OpenSpan claim (`win/openspan_portal.py:2261`) and
their move was correct.

**`Ctrl+Alt+Q` is not.** At `win/openspan_portal.py:2259` it is `return 1` and
nothing else — a legacy stub with no feature behind it, commented as such. They
paid a real cost to avoid a claim that is not one. **OpenSpan should delete
those two lines and release the chord**, after which EsotericOS may take its
original binding back if it prefers. That change is recorded here and has not
been made; it is a live input-path edit and belongs in its own step.

Two near-misses, documented because neither side moves:

| EsotericOS | OpenSpan | Distance |
|---|---|---|
| `Ctrl+Alt+V` — clipboard history | `Ctrl+Alt+Shift+V` — paste from PC | one `Shift` |
| `Ctrl+Alt+C` — colour meter | `Ctrl+Alt+Shift+C` — push iPad clipboard to PC | one `Shift` |

Distinct chords; `RegisterHotKey` requires an exact modifier match, so a
`Shift`-carrying chord does not fire their binding. But a slipped `Shift` puts
the user in the wrong one of two commands, and one of them moves relayed
clipboard text that can carry passwords. Their history gate failing closed (§8)
is what makes that safe rather than merely unlikely.

`Ctrl+Alt+V` reaching them at all was OpenSpan yielding already: the comment at
`win/openspan_portal.py:2276` records that OpenSpan used to take plain
`Ctrl+Alt+V` and gave it back. That release is why their binding works today.

---

## 8. Clipboard

**Every byte OpenSpan writes to the Windows clipboard came off an iPad over the
LAN relay, and `CLIPBOARD_DESIGN.md` §4 is explicit that it carries passwords.**

There is exactly one clipboard write in the tree: `set_clipboard_text`,
`win/openspan.py:2795`, reached from the relay's single `POST /clip` handler at
`win/openspan.py:3003`. `win/test_interop.py` asserts that `SetClipboardData` is
called from nowhere else and that no call site opts out of marking.

Every such write is marked with four opt-out formats, registered once at import
(`win/openspan.py:2771`):

| Format | Carries |
|---|---|
| `Clipboard Viewer Ignore` | presence |
| `ExcludeClipboardContentFromMonitorProcessing` | presence |
| `CanIncludeInClipboardHistory` | DWORD `0` |
| `CanUploadToCloudClipboard` | DWORD `0` |

The first three are what EsotericOS's history gate reads, and that gate fails
closed — their spec, "Clipboard". The fourth is not in their gate; it is what
stops Windows itself syncing the payload to the Microsoft account.

Four details are load-bearing and were verified by execution, not reasoning:

1. **`EmptyClipboard` first.** It wipes everything, markers from a previous
   write included, so the markers are re-placed on every write.
2. **Markers and payload in the same Open/Close pair.** Listeners are notified
   once, on `CloseClipboard` — that is what makes them atomic to an observer.
   Their order among themselves is invisible to a format listener; markers go
   first so that an abort midway leaves markers and no text, rather than
   unmarked password text.
3. **Markers carry real data, never `NULL`.** `SetClipboardData(fmt, NULL)`
   means delayed rendering: the format is advertised and the *owner window* is
   asked for the bytes later via `WM_RENDERFORMAT`. `OpenClipboard(NULL)` leaves
   the clipboard with no owner, so nothing could ever render it. Independently,
   EsotericOS reads `CanIncludeInClipboardHistory`'s DWORD rather than only
   testing presence.
4. **Fail closed.** If the formats will not register, the write is refused and
   the relay returns 500 (`win/openspan.py:3008`). An unmarked write of relayed
   text is the one outcome this exists to prevent, and the iPad shortcut can
   simply be run again.

`RegisterClipboardFormatW` is idempotent, case-insensitive and process-wide, so
OpenSpan's format ids and EsotericOS's agree by construction rather than by
arrangement. `win/test_interop.py` re-registers the names in upper case and
asserts identical ids.

**Visible behaviour change, stated because it is a trade and not a side effect:
text copied on the iPad no longer appears in Win+V on this PC and no longer
syncs to the Microsoft account.** That is correct for password-bearing relay
traffic. It is the only thing marking changes.

Not fixed by marking, and unchanged: `GET /clip` ships the *Windows* clipboard
to the iPad over plain HTTP. That remains the token + `LocalSubnet` story.

### What the lease does not order

`Portal.enter()` posts the acquire as its first statement
(`win/openspan_portal.py:1914`) and reads the clipboard sequence number near its
end (`:1946`). The post is asynchronous by necessity — waiting on the lease
thread inside a hook procedure would risk Windows' `LowLevelHooksTimeout`
(~300 ms) silently unhooking us, which is the failure the queue exists to avoid.
So the acquire is *requested* before the clipboard read but is not *guaranteed*
to have completed.

The consequence is narrow and worth naming: EsotericOS's paste-and-match-style
replaces and restores the clipboard within a few hundred milliseconds. A
crossing that lands inside that window could ship the transient stripped text to
the iPad. Bounded by the lease in the overwhelming majority of cases, not
ordered by it in principle. Nobody has observed it; it is written down because
an unstated race is the kind that gets rediscovered as a bug report.

---

## 9. Keymaps: the incident this project learned from

**Credit where it is due: EsotericOS found this one the hard way and told us.**
One of its macOS keyboard presets rewrote `Ctrl+A` to `Home` globally, which
destroyed select-all across Windows — and its test suite *asserted that
behaviour*, so the bug shipped with a passing test certifying it.

The general lesson, which is not about keyboards: **a test that records what the
code currently does cannot catch a wrong default. It can only certify it.** The
rule has to describe what the software may not do, and it has to be written by
someone thinking about the user rather than about the diff.

OpenSpan has the same shape of exposure. `openspan_keymap.json` supports
per-device modifier remapping and per-chord overrides; "Edit keymap"
(`win/openspan.py:5667`) hands the raw JSON to `os.startfile` with no validation between
there and the wire; the loader (`win/openspan_portal.py:807`) **silently drops**
any token it does not recognise; and the matcher takes the first match.

One structural difference, in OpenSpan's favour and stated so the risk is not
overrated: `_emit_kbd` (`win/openspan_portal.py:2414`) is reached only from
paths gated on `self.active`, and while active every key is swallowed from
Windows anyway. **No keymap entry can change what Windows receives.** The
exposure is aimed at the target device, while the lease is held.

`win/test_keymap_safety.py` is the guard. It checks the shipped file against
rules stating what a keymap may not do:

| | Rule |
|---|---|
| R1 | `from` modifiers must be ones the hook can actually report |
| R2 | every token resolves — nothing is silently dropped |
| R3 | `to` names a key; `from` names exactly one |
| R4 | `from` sets are unique (first match wins, silently) |
| **R5** | **a shipped default may change a universal chord's modifiers, never its key.** `ctrl+a → cmd+a` passes; `ctrl+a → home` fails |
| R6 | no override may name a safety chord (Ctrl+Alt+Del, Ctrl+Shift+Esc, Win+L, Alt+F4, Alt+Tab) |
| R7 | `modifier_remap` keys physical, values target-valid, no two explicit remaps collide |
| R8 | OpenSpan's own PC-side chords stay disjoint from EsotericOS's published table |

R5 is their incident, generalised. R2 catches the nastier variant: `"control"`
is not a name the loader knows, so `{"from": ["control","a"]}` loads as *plain
`a`* — and every unmodified `a` typed at the device would send Cmd+A. One
character's typo.

The shipped `openspan_keymap.json` passes all of it. `win/test_interop.py`
section 6 proves the guard has teeth by feeding it `ctrl+a → home` and asserting
R5 fires.

One honest note on R7. The first formulation asserted "every iPad modifier stays
reachable". That **fails today**: with `alt → cmd`, Option is unreachable on the
iPad, and physical Win also arrives as Cmd. Both are intended. R7 was scoped to
explicit entries rather than encoding that merge as an assertion — writing a test
that fails on correct behaviour, and writing one that certifies a quirk, are the
same mistake from opposite directions.

---

## 10. How to verify any of this

```
C:\Python313\python.exe D:\_EsotericOS\app\win\test_interop.py
C:\Python313\python.exe D:\_EsotericOS\app\win\test_keymap_safety.py
```

Neither creates a window, installs a hook, opens a socket, touches the real
clipboard, or touches the real lease name — every runtime lease check runs
against a private `Local\OpenSpan.InteropTest.<pid>.<n>` mutex, so running them
while EsotericOS is live cannot suspend it for an instant.

Live behaviour is in `D:\_EsotericOS\app\portal.log`: every lease transition, reclaim
and timeout prints a `[lease]` line.
