# Compaction prompt — EsotericOS, the new direction

You are mid-collaboration with Doug on **EsotericOS**. Read this whole prompt,
then continue as if the break never happened.

## What the product IS now

- **EsotericOS** = the living Python/Tk + Linux-VM program formerly named
  OpenSpan, at **D:\OpenSpan**, rebranded *entirely* to the **Astral Compass**
  identity (commit `df8afa6`): kit-token palette (arcane violet/void ladder;
  functional amber kept for state), two-tone wordmark (Esoteric=Lunar,
  OS=Arcane), kit app icon, hand-assembled tray ico (six exact kit PNGs,
  byte-verified), Inter-with-Segoe-fallback resolved at runtime (Inter not
  yet installed on this machine). Doug's verdict on sight: "it's beautiful."
- **EsotericOLD-S** = the old C#/.NET 8 WPF program at **D:\EsotericOS_Antigrav**
  (clean at `1c7f1f1`, GPL-3.0, 1,207 tests, no remote; bundle + brand-kit zip
  backed up to `E:\EsotericOS-recovery\2026-08-09\` with hashes). It is
  **inspiration/raw material only** — features get dragged into the Python
  product **one at a time, on Doug's explicit picks**. Its handoff doc:
  `D:\EsotericOS_Antigrav\docs\HANDOFF-OPENSPAN-MERGE.md` (seven codebase laws
  worth honoring when porting). Brand kit:
  `D:\EsotericOS_Antigrav\EsotericOS_Astral_Compass_Brand_Kit_v1.0\` (SVG
  masters + tokens = production truth; tray-* exports exact; reference/ is
  reference only).
- **Plumbing deliberately stays OpenSpan-named**: VM "OpenSpan-Codex", guest
  paths /opt/openspan, systemd units, mutex, ports, advertised BT device
  names ("OpenSpan iPad" etc.). Pipes, not paint. Advertised-name rebrand is
  a future slice gated on Doug's own Bluetooth testing.

## The feature board (the center of the new direction)

- **`D:\OpenSpan\docs\plan\plan.json`** (canonical, v2) +
  **`plan.html`** (Doug's live editor). Server: plan-skill `plan_server.py`
  via the firewalled python on **port 7351** (7350 is squatted by a stale
  shakedown server from another session — left alone). Doug edits at
  `http://127.0.0.1:7351/plan.html`.
- **85 items**: 4 Foundations (keyboard interception/router, WinEvent window
  tracker + window service, stable monitor identity, tray runtime) + 81
  features from the old program's generated FEATURE_STATUS ground truth (42
  with working C# reference code, 38 spec-only, statuses baked into cost
  notes). Nine clusters (v2 milestones): Foundations, Window management,
  Spaces & displays, Keyboard & pointer, Capture & files, Launcher &
  clipboard, Shell & desktop, System controls, Platform & plumbing.
- **Selection model is OPT-IN**: everything defaults to status `deferred`
  (culled, 45% opacity, sunk). Doug **promotes** rows he wants by clicking
  the status chip (`deferred → todo`). Deferred ≠ deleted: rows keep SID +
  estimates. Delete exists only for "never".
- Board UX shipped: frozen top stack that loads in its stuck state and never
  moves; left rail nav with instant offset-correct jumps + scrollY-delta
  scrollspy; live filter box (hides empty groups, dims rail links).
  Milestones section is collapsed ("determined later — the work items are
  the board"): milestones are DERIVED from Doug's picks, never hand-edited.
- **Plan-format duties**: when Doug says he's done editing, re-read plan.json
  immediately and reconcile aloud. Record actuals as slices complete; grade
  estimates against actuals out loud. **Stale-tab hazard**: any change I make
  to plan.json on disk requires Doug to REFRESH before touching the board,
  or his tab autosaves the old world back.

## Immediate next step (where we stopped)

Doug is promoting his starter set on the board. The moment he says done:
1. Read plan.json, name his picks back to him, reconcile.
2. Propose real milestones over exactly the survivors (update plan.json,
   version-bump per format).
3. Open the first slice: ONE feature, smallest verified step. Porting order
   respects deps (hook/window features need their Foundation rows first).
   For "working C# reference" rows, read the C# module + its tests in
   D:\EsotericOS_Antigrav first, then port semantics (not syntax) into the
   product's existing patterns; translate the relevant tests.

## Product state beneath the board

- Running build = **iteration-3 line + recovery cycle + rebrand**, launched
  as D:\OpenSpan\EsotericOS.exe (admin — the app enforces its own elevation
  gate at openspan.py:_elevation_gate for hook survival under UIPI).
  OpenSpan.exe = promoted v3; OpenSpan.exe.prev = sealed v2;
  STABLE-V2.md return path on both disks.
- Recovery machinery landed (tasks #2/#12 arc): explicit PnP handoff
  (_pnp_kick/explicit_handoff), gentle_release before every VM stop, honest
  ACPI cold-restart, busctl preflight with retry ladder, SIGKILL agent unit,
  Return-Radios-To-Windows.bat. Suite ~600 checks green across win/ + guest/.
- Task board (session tasks #3–#11) = the OpenSpan-era roadmap (peer
  identity, installer, second laptop + iPad#2, Input Director kill, one
  workspace, edge-hop, volume normalizer, UI polish, audio self-heal). It
  continues under the EsotericOS name and will interleave with feature
  ports; several map naturally onto board rows.

## How Doug works (non-negotiable, compressed)

One small verified slice; his "go" is approval, never double-confirm. He
verifies live himself — say exactly what to check. Never kill/rebuild over
his running exe; build to a staged name, he relaunches. Only HE tests
Bluetooth; never touch VM radios. Nothing machine-identifying in repo or
build. His observations are expert diagnostics — reason from them first.
Read logs myself. Hostile-fact-checker honesty. End replies with a Next
Action Item; link every artifact he must judge. Show web things in his real
Chrome (rail/board changes: verify in a scratch tab, never his). Memory
index + openspan-* memory files carry the deeper history.
