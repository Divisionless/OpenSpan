# EsotericOS Control Broker Protocol, version 1

Board row: `v3.153 [control-center-v2]`, Phase 2 — "Authenticated
Medium-integrity shell broker". This document is subordinate to
[`CONTROL-CENTER.md`](CONTROL-CENTER.md), which is frozen and governs. Where
this document appears to say something the frozen spec does not, the frozen
spec wins and this document is wrong.

Two independent implementations, no shared code:

| Side | Implementation |
|---|---|
| App, elevated, High integrity | `app/win/control_center_protocol.py`, `app/win/control_center_client.py` |
| Shell, Medium integrity | `shell/Cairo Desktop/EsotericOS.Shell.ControlBroker/` |

The duplication is the design. The frozen spec requires that *both sides
independently resolve the ID*; a shared parser or a generated stub would make
the two sides one side wearing two hats, and a single implementation cannot
disagree with itself when it is wrong.

---

## 1. The one rule

> **A request carries a catalog ID. It never carries a command, a path, a URI,
> an argument, an integrity level, or a route.**

Everything else the broker needs — what kind of destination the ID names, what
string to hand Windows, whether elevation is genuinely required — the broker
looks up in **its own** copy of the Phase 1 catalog. A fully compromised client
can therefore ask for a different *page*. It cannot ask for a different
*program*. The request envelope has a closed key set and any key outside it is
refused, so there is no field through which a command could travel even
unnoticed.

## 2. Channel

- **Name:** `\\.\pipe\EsotericOS.ControlBroker.<user-SID>`, derived on both
  sides from the current process token's user SID. Per-user by construction,
  not by convention.
- **Descriptor:** `O:<sid>G:<sid>D:P(A;;GA;;;<sid>)S:(ML;;NW;;;ME)`.
  The DACL is *Protected* with exactly one access-allowed ACE naming the user's
  own SID. Administrators, SYSTEM, INTERACTIVE, Everyone and Anonymous are
  **absent**, not denied — a deny ACE can be argued with by an ACE added after
  it; a protected DACL with one entry cannot. The mandatory label pins the pipe
  at Medium with no-write-up, so Low-integrity processes cannot write to it at
  all. High → Medium writes are always permitted, which is the reason the
  broker exists.
- **Created with** `FILE_FLAG_FIRST_PIPE_INSTANCE` (a squatted name is a
  startup failure, never a silent interception) and
  `PIPE_REJECT_REMOTE_CLIENTS` (no remote SMB caller on a one-desk channel).
- **Client connects at** `SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION`: the
  server may *read* the caller's SID, and may not *use* the caller's
  High-integrity token to open anything.
- One connection, one request, one response, then disconnect. Byte mode; the
  framing below is the only message boundary.

## 3. Framing

```
+--------------------------+---------------------------+
| 4 bytes, LE uint32       | that many bytes           |
| declared body length     | UTF-8 JSON object         |
+--------------------------+---------------------------+
```

- **Bound: 4096 bytes of body.** A real request is about 150 bytes.
- The declared length is validated **before the body is read**, so an oversize
  claim costs four bytes rather than four gigabytes.
- Not newline-delimited, so no embedded whitespace, newline or NUL can
  desynchronise a reader.
- Trailing bytes after the declared body are **malformed**, not ignored: extra
  bytes are a second request smuggled behind the first.
- **Duplicate object keys are refused by both sides.** Python's `json` keeps
  the *last* duplicate; .NET's `JsonDocument` hands out the *first*. Accepting
  duplicates would be a smuggling channel in which the client validates one ID
  and the broker dispatches another.
- `NaN`, `Infinity`, `-Infinity`, comments and trailing commas are refused.
  Max nesting depth 8 (shell) / recursion-bounded (app).

## 4. Request

```json
{"v":1,"kind":"activate","id":"eos.settings.display",
 "nonce":"<32 lowercase hex>","issued":<unix epoch milliseconds>}
```

Exactly these five keys — a subset **or** a superset is `schema-invalid`.
`kind` has exactly one legal value; a second verb is a version bump, not a
field value. Types are checked, never coerced: the string `"1"` is not the
number `1`, and `true` is not `1` either.

**ID grammar:** `eos\.[a-z0-9]{1,32}(?:\.[a-z0-9-]{1,64}){1,6}`, anchored at
both absolute ends, maximum 96 characters. It is an allow-list, so every
traversal, quoting, injection and encoding trick fails on the same line: a
well-formed ID contains no slash, backslash, space, quote, percent, NUL,
semicolon, ampersand or non-ASCII character.

**Freshness:** `|now − issued| > 30 000 ms` is `request-stale`. Symmetric — a
request from the future is as suspect as one from the past.

**Replay:** a nonce is accepted once. The ledger remembers nonces for 60 000 ms
and drops older entries on every observation, so it cannot be grown by an
attacker and an entry old enough to be forgotten can no longer pass the
freshness check anyway.

## 5. Response

```json
{"detail":"...","elevation":false,"integrity":"medium","nonce":"<echo>",
 "ok":true,"reason":"ok","route":"ms-settings-uri","v":1}
```

- `detail` is **looked up from the reason code**, never composed and never
  built from the request. This is what "no private data" means operationally: a
  response and a log line contain only text that shipped in the source.
- `nonce` is echoed **only when the broker actually parsed one**. A malformed
  frame gets an empty nonce rather than a guess, because echoing bytes the
  broker could not parse turns the responder into an oracle.
- `route` and `integrity` are the **broker's** resolution, not the client's.
  The client compares them with its own and reports a divergence
  (`ActivationResult.divergent`) rather than proceeding.
- Keys are emitted in alphabetical order on both sides, so identical content
  produces byte-identical frames.

## 6. Decision order

Fixed, on the shell side, and no step runs before the step that makes its input
meaningful:

1. **Identity** — authenticate before parsing anything the caller wrote. A
   caller whose SID could not be determined arrives as `null`, and `null` is
   not the owner.
2. **Framing** — bound and shape before any content is believed.
3. **Schema** — closed key set, closed verb, typed fields.
4. **Freshness** — cheap, and it runs *before* the replay ledger so an attacker
   cannot grow the ledger with ancient nonces.
5. **Replay** — the only step that mutates state, and it is bounded.
6. **Resolution** — against the broker's own manifest. The ID is used as a
   *key*, never as a *value*.
7. **Dispatch** — the seam.

The ladder inside step 6 is identical, in order, in all three implementations:
malformed → unknown → unavailable → no route → bad target shape → bad
integrity.

## 7. Routes

Four, matching the frozen spec's activation diagram. **`explorer.exe` is not
one of them and there is no fifth entry to add one to.**

| Destination kind (Phase 1) | Route | Target grammar |
|---|---|---|
| `ms-settings` | `ms-settings-uri` | `ms-settings:[a-z0-9-]{1,64}` |
| `control-panel-canonical` | `control-name` | `[A-Za-z][A-Za-z0-9]{0,31}(\.[A-Za-z0-9]{1,31}){0,3}` |
| `control-panel-cpl` | `control-cpl` | `[A-Za-z0-9_]{1,32}\.cpl` |
| `mmc-console` | `mmc-console` | `[A-Za-z0-9_]{1,32}\.msc` |
| `control-panel-clsid` | **none** | — |

`control-panel-clsid` maps to nothing deliberately. A namespace CLSID with no
canonical name is reachable only through `shell:::{…}`, which is Explorer, and
*"No Control Center route launches or depends on explorer.exe"* is an
acceptance criterion. Such an entry stays in the catalog and is refused at
dispatch with `route-unsupported` — the frozen spec's "visible with a reason",
applied to activation instead of to search.

Target grammars admit no space, quote, slash, backslash, ampersand, pipe,
percent or dot-dot, which makes argument quoting a non-problem *by
construction* rather than by care. They are checked on manifest load, again at
decision, and a third time inside the seam.

## 8. Elevation

Elevation is a property of the **catalog entry**, never of the request. There
is no field a caller can set to ask for it and no field it can set to avoid it.
A `high` integrity entry dispatches through the seam's explicit elevated
branch, the response carries `"elevation": true`, and the launch log records
`integrity=high`. Nothing silently elevates: the item is marked in the catalog
before it is drawn, the answer says so, the log says so, and the consent prompt
is Windows' own.

## 9. Reason codes

Closed vocabulary; both sides assert the list matches this table.

| Code | Meaning |
|---|---|
| `ok` | accepted |
| `frame-oversize` | declared body length exceeds the protocol bound |
| `frame-truncated` | frame ended before the declared body length |
| `frame-malformed` | body is not a UTF-8 JSON object |
| `version-unsupported` | protocol version is not supported by this broker |
| `schema-invalid` | envelope does not match the version 1 request schema |
| `id-malformed` | identifier is not a well-formed EsotericOS catalog id |
| `id-unknown` | identifier is not present in this broker's catalog |
| `id-unavailable` | catalog entry is unavailable on this Windows installation |
| `route-unsupported` | catalog entry has no Explorer-free activation route |
| `request-stale` | request timestamp is outside the freshness window |
| `request-replayed` | request nonce has already been used |
| `identity-rejected` | connected client is not the owning user |
| `catalog-unavailable` | the broker has no usable catalog manifest |
| `activation-not-armed` | activation is disarmed until the Phase 6 arming gate |
| `activation-failed` | the activation route reported failure |
| `transport-unavailable` | the broker pipe could not be reached (client-side only) |
| `internal-error` | the broker refused the request without classifying it |

Every path out of the broker is one of these. There is no path that returns
nothing, and no path that returns prose.

## 10. The broker's catalog manifest

`control-catalog-manifest.json`, generated from the Phase 1 catalog by
`control_center_client.export_manifest`. Deterministic bytes: sorted keys,
sorted records, LF, **no timestamp** — a generated file with a timestamp in it
is a file nobody can distinguish from a tampered one by looking.

```json
{"schema":1,"revision":"…",
 "records":[{"id":"…","kind":"…","target":"…","integrity":"…","availability":"…"}]}
```

Unavailable records are **included**, so the broker can tell "there is no such
thing" (`id-unknown`) from "this Windows does not have it" (`id-unavailable`).
Load is **all-or-nothing**: the file is deterministic output of a pure
function, so one malformed record means tampering or a build bug, and the
honest answer is `catalog-unavailable` for everything rather than serving the
records that happened to survive.

## 11. The activation seam

`EsotericOS.Shell.ControlBroker/WindowsActivationSeam.cs` is the **only** file
in the Control Center that can start a process. Everything else decides; this
acts.

It ships **disarmed**. `Activate` returns `activation-not-armed` and returns
*before* the route switch unless `WindowsActivationSeam.Arm(
"phase-6-live-acceptance-approved-by-doug")` has been called in-process. There
is no configuration file, environment variable, registry value or command-line
flag that can arm it, because each of those is a way for it to become armed
without anyone deciding to arm it. Phase 6 arms it; Phase 6 is gated on Doug.

The arming gate is not a substitute for the seam being unreached — it is the
second thing that has to fail. The hostile-request suite proves the seam is
never reached; the gate means a bug that reached it would still open nothing.

## 12. What Phase 2 does not do

- It does not launch anything, including in tests.
- It does not register the broker project in `Cairo Desktop.sln`, start it from
  the shell, or deploy the manifest. That is Phase 4.
- It does not draw. That is Phase 3.
