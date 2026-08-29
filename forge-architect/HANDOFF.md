# eos-domain — standing handoff record

This file is the named `handoffRecord` for every guarded generation change of the `eos-domain`
Chair. `prepare()` in the Forge's `kernel/leapfrog.js` hashes it into the packet; when it is
named, no transcript is carried, by design. It is therefore the **entire inheritance** of the
next generation.

**Maintenance contract.** The seated generation updates this file at every substantive
checkpoint: a deliverable produced, a ruling received, an objective changed, a blocker raised
or cleared. A handoff prepared while this file is stale is a defective handoff — the outgoing
seat, not the mechanism, is at fault. Never let a deliverable exist only in a transcript.

---

## Seat

- Role: `eos-domain` — the EsotericOS Chair. Project `esoteric-os`.
- Current: claude lane, generation 2 (seated 2026-08-23 from packet tx
  `7760a2c2-ee0e-442c-890a-e6c6e2d8a7e9`; predecessor codex gen 1
  `01a02d3c-42f3-7140-9e9d-1d127c2091f2`, retained as rollback).
- Why the switch: the codex lane died on a 400 — `opus[1m]` is not available to Codex under a
  ChatGPT account. The requested settings (`opus[1m]`, effort `xhigh`) are honourable only in
  the claude lane.
- Authority: owns `D:\_EsotericOS\app` (platform, board, radio custody, display/arrangement,
  VM, docs) and the dissolved Builder's duties over `D:\_EsotericOS\shell` (never touch
  `shell\stable\`; substantive C# is written or adversarially reviewed by an Opus subagent).
  Does **not** own the Forge (`E:\esoteric-path-core`) — read-only; changes there are
  consultations to the Keeper.

## Current objective

`domainmap` — Draft the EsotericOS domain map and Control Center specification.

State as of 2026-08-23 (claude gen 2 checkpoint): **both halves drafted.**
- Control Center spec canonical at `docs/CONTROL-CENTER.md` (frozen; the copy below is the
  handoff carry). Board row `v3.153 [control-center-v2]` added at board rev 27, status todo.
- Domain map v1 skeleton at `docs/MAP.md` (subjects + stack, live/dormant/blocked, drafted
  against board rev 27).
No source, build, startup contract, or running process has changed.

## Open work

1. Control Center **Phase 1 in flight** (Doug: "start phase 1", 2026-08-24) — catalog
   service, discovery adapters, dedup, availability resolver; gate is deterministic
   inventory tests. Spec `docs/CONTROL-CENTER.md`; row v3.153.
2. Architect consultation drafted at `docs/forge/ARCHITECT-CONSULTATION-leapfrog.md`
   (prepare() record default/refusal; role-aware cancel + void `d7642def`; prove `7760a2c2`
   then archive `7ba2619a` — until that sequence runs, no future handoff of this seat can be
   prepared). Doug carries it to the Architect.
3. `GEMINI.md` disposition — violates COUNCIL.md vendor rule ("no GEMINI.md exists
   anywhere"); untracked, held uncommitted, Doug to rule delete vs keep-as-orphan.
4. Two open subject questions in `docs/MAP.md` are Doug's to name: the files-grid unblock
   condition (v3.132) and the custody wedge verdict (v3.131).

`maplink` is **closed** — see rulings.

## Structural notes on record

- **Restructure SETTLED (Doug, 2026-08-26):** the seat keeps the name **Overseer** — the
  right hand implicit in it. Constellation: Doug + Overseer at the top of a flat house;
  **Executor** does the development work coding the OS; **Architect** does any Forge IDE
  work needed to bridge the gap between Overseer, Executor, and the rest of the Forge, and
  keeps all seats not named; choirs under the Overseer. **No Keeper is seated** — the duty
  is held by the Overseer with re-ask triggers: a second territory, record-keeping crowding
  his turns, or generation churn outpacing this record. Charter and AGENTS.md rewritten to
  this constellation 2026-08-26 (this changes the charter hash — the next prepare() re-hashes;
  the packet hash of 2026-08-23 is historical). Mechanics still the Architect's: flatten,
  uncross esoteric-os.json ("CROSSED ON PURPOSE, 2026-08-24"), reporting lines. Refinement (same night): NO FOLDERS — "there is just
  the top level now"; no "EsotericOS" or "The Forge" containers; one flat house; projects
  survive only as territories (codebase manifests) in the chains view. Map commission v2:
  three chains (house command, territory, chain of a command to a developer), territories
  as columns, every node an editable text viewer, AI invocable as a prompt at the top of
  any charter/territory/seat, highlight-to-flyout invocation with selection as context.

- **Naming law applied 2026-08-24** (COUNCIL.md § The naming law, ruled 2026-08-23): this
  seat is the **Overseer**; "Architect" is exclusively the forge-domain seat. `CLAUDE.md`
  and `AGENTS.md` recharted — envelope injector attribution is the Forge Architect's, seat
  work is Overseer work, Forge internals are the Forge Architect's. The cwd name
  `forge-architect/` is a flagged stale echo; renaming is Forge plumbing, the Architect's.
- **Fork rule (Doug, via the Architect):** this single-seat project currently holds both
  halves of the split — Keeper (EsotericOS's life inside the Forge) and Developer (the OS
  itself) — un-split. The moment the project grows custom tooling, it needs a Keeper AND a
  Builder; raise it with Doug then rather than absorbing the tooling into this seat.
- **The split arrived (Doug, 2026-08-26), verified against the roster:**
  `projects/esoteric-os.json` declares three personas. **eos-domain / Overseer**
  (`forge-architect\`) — Doug's will, consistency across everything, charter watch, poses
  the deep questions. **eos-builder / Luthier** (`forge-builder\`) — toolmaker of
  EsotericOS's life inside the Forge; sat 2026-08-26, charter drafted, pending Doug's
  ratification. **eos-developer / Executor** (`forge-executor\`) — builds the exterior
  Windows application. Luthier's mandate (Doug): the room we think in is at the state of
  the art — in THINKING; "we don't know what we don't know"; "as we burn away
  inefficiency, those parts will become louder in the rising quiet." Refinement as
  discovery: he builds what is commissioned, reports what has become loud, Doug rules.
  Scope boundary pending ratification ("the room, not the talk"): Luthier's inputs are
  commissions, friction reports, and his own use of the instrument — never the content of
  Doug–Overseer deliberation or the Executor's operations. Known housekeeping: his
  `AGENTS.md` is dormancy-era stale, regenerate at ratification; his first heard absence
  (ctx.spawn) was already documented at KERNEL.md:274 — its fate is the Architect's. **Executor** — Developer-half, builds the
  actual exterior Windows application (attends technical detail work Doug routes to it).
  **Overseer** — interprets Doug's will, ensures consistency across everything, watches
  that the charter is fulfilled, and poses the deep questions — through tooling Luthier
  builds, so technical weight stays out of the Overseer's context.
- **Luthier dissolved (Doug, 2026-08-26, same day it was sat).** Every commission and heard
  absence of the seat landed in the Forge Architect's house, and Doug had independently
  commissioned the Architect for the general system ("a visual check for agents that are
  procedurally out of order — a flag in my UI"). Doug archived the seat without ceremony.
  Doctrine preserved beneath a dissolution banner at `forge-builder/CLAUDE.md`; `AGENTS.md`
  there rewritten lawful-dormant. All tooling re-routed to the Architect in one
  consolidated carry (see below); `eos-builder` re-sits only under the fork rule. The
  Asking's contract stays Overseer-side: questions at `forge-architect/asking/questions.md`
  (Set 1 verbatim), answers due durably under `forge-architect/asking/answers/`.

## Doug rulings on record

- 2026-08-29 **a machine restart is the supported recovery path; the app does not attempt
  orchestrated teardown.** Doug: *"those buttons have never worked very well due to just how
  complex everything is that we are doing. I would always rather restart the machine than try
  to deal with all the race conditions that spawn with those buttons."* Stop VM, Cold-restart
  VM, Restart keyboard, Restart audio and Shut down everything were removed and their
  now-unreachable helpers deleted with them. **This is a deliberate absence, not a missing
  feature — do not restore a shutdown control.** What must keep working is the session-end
  chain: `WM_QUERYENDSESSION`/`WM_ENDSESSION`, VirtualBox saving VM state at host shutdown, and
  `start_vm_clean()` discarding it to force a cold guest boot. That chain is what makes "just
  restart" correct.
- 2026-08-29 **a radio is owned by the device that claims it.** No mode toggle: the arrangement
  is derived from the assignments-by-controller-MAC. A stored `radio_mode` that disagreed with
  the assignments beside it was the cause of the empty-dropdown bug, and a toggle that can go
  stale against reality is the defect.
- 2026-08-29 **fault remedies are not settings.** Repair, custody and layout controls appear
  only when an audit finds a fault, and a fault must be **confirmed across two consecutive
  audits** before it is shown — a transient PHANTOM during boot is normal, a persistent one is
  the fault. A healthy desk shows nothing.

- 2026-08-27 **law 10 — no nested scrollers.** One vertical scroller per window; containers
  adapt to contents; where adapting is infeasible bound the **content** and raise the
  objection. Charter law 10, board row v3.154.
- 2026-08-27 **law 10 scope: app GUI only.** The shell fork and `docs/plan/plan.html`
  violations found by the inventory are **out of scope** and are not to be fixed under this
  law. Recorded on v3.154 so they are not silently forgotten.
- 2026-08-27 **exemptions are specific and situational** — there is no blanket modal/popover
  carve-out. Each case is judged on its own facts; a general exemption may not be inferred.
- 2026-08-27 **the Console becomes a separate surface**, and may then scroll lawfully as that
  surface's own single scroller. This supersedes the in-page Console treatment committed
  today (fitted container + 2000-line content cap) and reopens v3.148's "five sections, one
  document" contract. Not yet designed or built.

- 2026-08-23 "begin" — released the Control Center **documentation** pause (spec persisted,
  row v3.153 added, MAP.md drafted under this ruling).
- 2026-08-23 MAP.md linkage scope: **A — per-project convention only.** Each project keeps
  `docs/MAP.md` in its own root; `projects/<id>.json` is the resolver; D2's map untouched;
  no Keeper consultation; `maplink` closed as already-satisfied. Reopen condition for scope B
  is the Operations Manager's (Research crystal watch: cross-project map/index rediscovery);
  this seat carries no standing watch. Board log rev 28.
- No running process or boot contract changes before a separately approved arm (spec
  §Acceptance criteria, final clause).

## Committed state

- **Checkpoint 2026-08-23: epoch commit `0ac927655622c70e3361deca0fd33c4ca1185c4b`** on
  `multidevice` — "control-center spec + domain map v1 + mapscope-A (board rev 28)". Carries
  `docs/CONTROL-CENTER.md`, `docs/MAP.md`, `docs/plan/plan.json` (rev 28), and this record.
  Everything produced under both packet objectives is inside it; a successor rebuilds from
  this commit plus this file, nothing else.
- Remaining untracked, deliberately: `forge-architect\AGENTS.md`, `forge-architect\GEMINI.md`
  (sibling vendor charters — not this seat's work product; no ruling yet).

## Handoff-mechanism note for the successor

The packet's `expected` digests verify **seating**, not **work product**. When this record is
named, that gap is closed: the packet carries `handoffRecord.hash` over this file. If you were
seated from a packet whose `handoffRecord` is null and whose `outgoingTranscriptTail` names a
transcript, treat recovery as unverified until you have extracted and hashed the final
`agent_message` yourself — a bounded tail (last 25 records sufficed at ~96 KB of a 15.6 MB
file on 2026-08-23) is the measured approach.

---

## The frozen deliverable — Technical horizon: EsotericOS Control Center

*(Verbatim. Drafted by eos-domain codex gen 1, recovered and re-verified by claude gen 2 —
sha256 of the transcript copy `72d9fbc1fc588c2424fdbcb97fd817d77b24bef41bcdbb129112e8039c6243c6`.
This section is the deliverable; do not edit it except by Doug's word. A design revision is a
new dated section, not a rewrite.)*

The target is one searchable EsotericOS surface for every user-addressable Windows management
destination—without making explorer.exe part of the architecture.

This is a frozen design only. I have not changed source, the board, builds, startup contracts,
or running processes.

### Scope

Included:

- Windows Settings pages
- Legacy Control Panel items
- Administrative consoles
- Security, recovery, update, storage, device, network, application, account, accessibility,
  privacy, and personalization controls
- Hardware-, edition-, and third-party controls actually registered on the machine

Excluded:

- Arbitrary System32 executables
- Duplicate search results that lead to the same page
- Automatic configuration changes
- Controls unavailable on the installed Windows edition without an explicit explanation

This machine currently exposes:

- 1,131 Windows searchable-setting records
- 41 registered Control Panel namespace items
- 18 .cpl modules
- 20 installed MMC consoles

The 1,131 records are mostly keywords and sub-settings, not 1,131 distinct destinations.
EsotericOS will use them as search aliases and deduplicate them into comprehensible pages.

### Logical organization

The GUI will present these stable EsotericOS groups:

- Display and sound
- Devices and input
- Network and sharing
- Apps and defaults
- Accounts and sign-in
- Personalization
- Accessibility
- Privacy and security
- Time and language
- Storage and recovery
- Updates and diagnostics
- Administration

Each item will have:

- Stable EsotericOS identity
- Friendly title and search aliases
- Windows destination type
- Availability state and reason
- Required integrity level
- Source: Microsoft catalog, local registration, or third-party registration

### Catalog architecture

A new UI-independent catalog service will merge four sources:

1. Microsoft's versioned `ms-settings:` catalog, filtered by Windows build and known
   requirements. Microsoft documents `LaunchUriAsync` as the supported desktop activation path
   and publishes the URI inventory. (Microsoft Settings activation reference)
2. The installed Windows `AllSystemSettings_*.xml` index, used for local search vocabulary and
   page-presence evidence.
3. Registered Control Panel namespace entries, including third-party controls. Canonical names
   are stable, non-localized identifiers and are Microsoft's preferred launch contract.
   (Canonical Control Panel names)
4. Installed administrative consoles and .cpl modules, admitted through explicit capability
   rules rather than blindly listing every executable. (Executing Control Panel items)

Deduplication will prefer the modern page while retaining materially different classic
controls under "Advanced" or "Classic."

### Activation architecture

The EsotericOS GUI must remain elevated because its input hooks must work over elevated
applications. Windows Settings must run at Medium integrity.

That boundary explains today's failure:

- Direct URI activation from High integrity failed with 0x87B20C15.
- Direct packaged-app activation failed with the same HRESULT.
- Explorer normally conceals the integrity transition by acting as a Medium broker.
- Preliminary linked-token launches failed with Win32 errors 5 and 1346, making that path too
  brittle for the product contract.

The recommended design is therefore an EsotericOS Shell broker:

```
Elevated EsotericOS GUI
        │ validated item ID only
        ▼
Per-user authenticated named pipe
        │
        ▼
Medium-integrity EsotericOS Shell
        ├── Windows Settings URI activation
        ├── control.exe /name canonical-id
        ├── MMC console activation
        └── explicit elevated route when genuinely required
```

Security rules:

- The pipe is ACL-bound to the current user SID.
- Requests contain catalog IDs, never arbitrary commands.
- Both sides independently resolve the ID.
- Unknown, malformed, stale, or oversized requests fail closed.
- Launch logs record identity, route, integrity, and result without private data.
- Items requiring elevation are visibly marked; nothing silently elevates or modifies Windows.

A standalone broker remains the fallback if EsotericOS must later operate without its shell,
but it would add another process, installer contract, and recovery path.

### GUI design

The existing single-page System section gains a "Windows Control Center" block:

- Seven common actions: Display, Sound, Network, Bluetooth, Installed apps, Security, and Update
- Search across titles, synonyms, legacy names, and local Windows search vocabulary
- Category filter
- Hierarchical results list with a dark scrollbar
- Availability and privilege badges
- Mouse double-click, keyboard Enter, and one explicit Open button
- "Show unavailable" support with concrete reasons
- No new navigation rail or page lifecycle; it remains part of the existing scrolling document

### Delivery phases

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Board row v3.153 and frozen technical specification | Doug resumes work |
| 1 | Immutable catalog, discovery adapters, deduplication, availability resolver | Deterministic inventory tests |
| 2 | Authenticated Medium-integrity shell broker | No Explorer launch path; hostile-request tests pass |
| 3 | Searchable single-page GUI section | Mouse, keyboard, filtering, dark-mode and lifecycle tests |
| 4 | Cross-repository integration and packaging | Full app and shell builds; known baseline preserved |
| 5 | Isolated app and shell candidates | Hash manifests and rollback artifacts |
| 6 | Live acceptance | Display, Installed apps, Control Panel and one MMC console open correctly |

### Acceptance criteria

The feature is complete when:

- No Control Center route launches or depends on explorer.exe.
- Display and Installed Apps open from the elevated EsotericOS GUI.
- Windows Settings launches at Medium integrity.
- Administrative tools use their declared integrity level.
- Every locally registered Control Panel item is represented or carries an exclusion reason.
- Microsoft Settings destinations are version-gated and searchable.
- Third-party registered controls appear automatically.
- Search terms such as "uninstall," "monitor," "startup," "firewall," and "Bluetooth" reach
  the correct destinations.
- Unsupported controls remain visible with a reason instead of disappearing.
- The broker cannot execute arbitrary paths or arguments.
- The existing single-page, dark-scrollbar, admin, Bluetooth-isolation, and Desktop-role
  contracts remain intact.
- No running process or boot contract changes before a separately approved arm.

### Longer horizon

After the launch surface is proven:

- Add read-only state summaries using supported APIs: display topology, audio endpoints,
  storage pressure, update state, network adapters, and installed applications.
- Gradually replace external Windows panels with safe EsotericOS-native controls where
  supported APIs exist.
- Preserve Windows panels as an escape hatch rather than recreating undocumented internals.
- Extend the same catalog over LAN nodes so one EsotericOS desk can open the correct local or
  remote management surface.
- Add versioned Windows 11 adapters without changing the GUI's logical categories.
