# EsotericOS Control Center — frozen specification

Board row: `v3.153 [control-center-v2]` (docs/plan/plan.json). Status: Phase 0 complete —
Doug released the documentation pause 2026-08-23 ("begin").

Provenance: drafted by eos-domain codex generation 1 (transcript sha256
72d9fbc1fc588c2424fdbcb97fd817d77b24bef41bcdbb129112e8039c6243c6), recovered and persisted by
claude generation 2. This file is canonical; the copy in forge-architect/HANDOFF.md is the
handoff carry. A design revision is a new dated section here, not a rewrite.

---

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
