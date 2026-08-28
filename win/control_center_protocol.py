# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Control Center broker wire protocol -- one frame, one catalog id.

Phase 2 of docs/CONTROL-CENTER.md; the written contract is
docs/CONTROL-CENTER-PROTOCOL.md and this module is its executable half. The
other half is EsotericOS.Shell.ControlBroker/BrokerProtocol.cs, which parses
the same bytes without sharing a line of code with this file. That duplication
is the point: the spec says "both sides independently resolve", and a shared
parser would make the two sides one side wearing two hats.

THIS MODULE LAUNCHES NOTHING AND TALKS TO NOTHING. It has no socket, no pipe,
no ctypes and no subprocess. It turns a catalog id into bytes and bytes back
into a decision, and every function in it is pure. The transport lives in
control_center_client.py, the dispatch lives in the shell, and the activation
lives behind one seam in the shell that Phase 2 never calls.

THE ONE RULE THE WHOLE DESIGN HANGS ON:

    A REQUEST CARRIES A CATALOG ID. IT NEVER CARRIES A COMMAND, A PATH, A URI,
    AN ARGUMENT, AN INTEGRITY LEVEL, OR A ROUTE.

Everything else the broker needs -- what kind of destination that is, what
string to hand Windows, whether it needs elevation -- the broker looks up in
its own copy of the Phase 1 catalog. A caller that has been fully compromised
can therefore ask for a different *page*; it cannot ask for a different
*program*. `build_request` physically cannot express one, because the envelope
has a closed key set and `parse_request` rejects any key outside it.

FAILING CLOSED IS A RETURN VALUE, NOT AN EXCEPTION. Every rejection path
returns a `Refusal` carrying one code from REASON_CODES and a *fixed* detail
string chosen by code, never assembled from the request. A broker that echoed
the offending id into its log would be a broker with a log-injection bug and a
private-data leak in the same line, and the spec forbids both.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import secrets
import struct
import time

# =========================================================================
# VERSION, FRAMING AND BOUNDS
# =========================================================================

PROTOCOL_VERSION = 1

#: Every frame is a 4-byte little-endian unsigned body length, then that many
#: bytes of UTF-8 JSON. Nothing is newline-delimited, so no amount of embedded
#: whitespace, NUL or newline can desynchronise a reader.
HEADER_BYTES = 4
HEADER_FORMAT = "<I"

#: The hard ceiling on one JSON body. A real request is about 150 bytes; the
#: bound is generous enough to survive a longer id and small enough that an
#: attacker cannot make the broker allocate. The length is checked BEFORE the
#: body is read, so an oversize claim costs four bytes, not four gigabytes.
MAX_BODY_BYTES = 4096

#: A catalog id longer than this is malformed by definition. The longest real
#: id in the Phase 1 catalog is 54 characters.
MAX_ID_LENGTH = 96

#: How old a request may be. Clock skew between two processes on one desk is
#: microseconds; thirty seconds is slack for a stalled thread, not for an
#: attacker replaying yesterday's capture.
FRESHNESS_WINDOW_MS = 30_000

#: How long a nonce is remembered so a replay inside the freshness window is
#: still caught. Anything older than this cannot pass the freshness check, so
#: remembering it further is wasted memory.
REPLAY_MEMORY_MS = 2 * FRESHNESS_WINDOW_MS

#: A nonce is 16 random bytes rendered as lowercase hex.
NONCE_BYTES = 16
NONCE_LENGTH = NONCE_BYTES * 2

#: The single verb. There is no "close", no "list", no "run". Adding a second
#: verb is a protocol version bump, not a field value.
KIND_ACTIVATE = "activate"

#: Exactly these keys, no more and no fewer. `parse_request` treats a superset
#: as malformed rather than ignoring the extras, because "ignore what you do
#: not understand" is how a smuggled {"target": "cmd.exe"} gets to sit quietly
#: in a request until someone writes code that reads it.
REQUEST_KEYS = frozenset({"v", "kind", "id", "nonce", "issued"})

# =========================================================================
# REASON CODES
#
# The complete, closed vocabulary. Both sides use these strings; the C# side
# repeats them as constants and its test suite asserts the list matches. A
# refusal always names one of these and never invents prose.
# =========================================================================

OK = "ok"
FRAME_OVERSIZE = "frame-oversize"
FRAME_TRUNCATED = "frame-truncated"
FRAME_MALFORMED = "frame-malformed"
VERSION_UNSUPPORTED = "version-unsupported"
SCHEMA_INVALID = "schema-invalid"
ID_MALFORMED = "id-malformed"
ID_UNKNOWN = "id-unknown"
ID_UNAVAILABLE = "id-unavailable"
ROUTE_UNSUPPORTED = "route-unsupported"
REQUEST_STALE = "request-stale"
REQUEST_REPLAYED = "request-replayed"
IDENTITY_REJECTED = "identity-rejected"
CATALOG_UNAVAILABLE = "catalog-unavailable"
ACTIVATION_NOT_ARMED = "activation-not-armed"
ACTIVATION_FAILED = "activation-failed"
TRANSPORT_UNAVAILABLE = "transport-unavailable"
INTERNAL_ERROR = "internal-error"

REASON_CODES = (
    OK, FRAME_OVERSIZE, FRAME_TRUNCATED, FRAME_MALFORMED, VERSION_UNSUPPORTED,
    SCHEMA_INVALID, ID_MALFORMED, ID_UNKNOWN, ID_UNAVAILABLE,
    ROUTE_UNSUPPORTED, REQUEST_STALE, REQUEST_REPLAYED, IDENTITY_REJECTED,
    CATALOG_UNAVAILABLE, ACTIVATION_NOT_ARMED, ACTIVATION_FAILED,
    TRANSPORT_UNAVAILABLE, INTERNAL_ERROR,
)

#: One fixed sentence per code. Chosen by the code, never built from the
#: request, so a response and a log line contain only text that shipped in this
#: file. This is what "no private data" means operationally.
REASON_DETAIL = {
    OK: "accepted",
    FRAME_OVERSIZE: "declared body length exceeds the protocol bound",
    FRAME_TRUNCATED: "frame ended before the declared body length",
    FRAME_MALFORMED: "body is not a UTF-8 JSON object",
    VERSION_UNSUPPORTED: "protocol version is not supported by this broker",
    SCHEMA_INVALID: "envelope does not match the version 1 request schema",
    ID_MALFORMED: "identifier is not a well-formed EsotericOS catalog id",
    ID_UNKNOWN: "identifier is not present in this broker's catalog",
    ID_UNAVAILABLE: "catalog entry is unavailable on this Windows installation",
    ROUTE_UNSUPPORTED: "catalog entry has no Explorer-free activation route",
    REQUEST_STALE: "request timestamp is outside the freshness window",
    REQUEST_REPLAYED: "request nonce has already been used",
    IDENTITY_REJECTED: "connected client is not the owning user",
    CATALOG_UNAVAILABLE: "the broker has no usable catalog manifest",
    ACTIVATION_NOT_ARMED: "activation is disarmed until the Phase 6 arming gate",
    ACTIVATION_FAILED: "the activation route reported failure",
    TRANSPORT_UNAVAILABLE: "the broker pipe could not be reached",
    INTERNAL_ERROR: "the broker refused the request without classifying it",
}

# =========================================================================
# ROUTES
#
# Four routes, matching the spec's activation diagram, and explorer.exe is not
# one of them. `control-panel-clsid` deliberately maps to nothing: a namespace
# CLSID with no canonical name is only reachable through `shell:::{...}`, which
# is Explorer, and "No Control Center route launches or depends on
# explorer.exe" is an acceptance criterion. Such an entry stays in the catalog
# and is refused at dispatch with ROUTE_UNSUPPORTED, which is the spec's
# "visible with a reason" applied to activation.
# =========================================================================

ROUTE_MS_SETTINGS = "ms-settings-uri"
ROUTE_CONTROL_NAME = "control-name"
ROUTE_CONTROL_CPL = "control-cpl"
ROUTE_MMC = "mmc-console"

ROUTES = (ROUTE_MS_SETTINGS, ROUTE_CONTROL_NAME, ROUTE_CONTROL_CPL, ROUTE_MMC)

ROUTE_BY_DESTINATION_KIND = {
    "ms-settings": ROUTE_MS_SETTINGS,
    "control-panel-canonical": ROUTE_CONTROL_NAME,
    "control-panel-cpl": ROUTE_CONTROL_CPL,
    "mmc-console": ROUTE_MMC,
    "control-panel-clsid": None,
}

#: A target is checked against the shape its route requires, on both sides,
#: every time. The manifest is trusted data, but "trusted data" is how a bad
#: line in a generated file becomes an argument to a process, so it is checked
#: anyway. These patterns admit exactly the 175 Phase 1 targets and nothing
#: containing a space, slash, quote, ampersand or dot-dot.
TARGET_PATTERNS = {
    ROUTE_MS_SETTINGS: re.compile(r"ms-settings:[a-z0-9-]{1,64}\Z"),
    ROUTE_CONTROL_NAME: re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}"
                                   r"(?:\.[A-Za-z0-9]{1,31}){0,3}\Z"),
    ROUTE_CONTROL_CPL: re.compile(r"[A-Za-z0-9_]{1,32}\.cpl\Z"),
    ROUTE_MMC: re.compile(r"[A-Za-z0-9_]{1,32}\.msc\Z"),
}

INTEGRITY_MEDIUM = "medium"
INTEGRITY_HIGH = "high"
INTEGRITY_LEVELS = (INTEGRITY_MEDIUM, INTEGRITY_HIGH)

AVAILABILITY_UNAVAILABLE = "unavailable"

#: The id grammar. Lowercase ASCII, digits, dots and hyphens, always starting
#: `eos.`, always at least three segments. It is an allow-list, so every
#: traversal, quoting, injection and encoding trick fails on the same line:
#: there is no backslash, no forward slash, no space, no quote, no percent, no
#: NUL and no non-ASCII character that can appear in a well-formed id.
ID_PATTERN = re.compile(r"eos\.[a-z0-9]{1,32}(?:\.[a-z0-9-]{1,64}){1,6}\Z")

NONCE_PATTERN = re.compile(r"[0-9a-f]{%d}\Z" % NONCE_LENGTH)


# =========================================================================
# THE PIPE NAME
#
# Per-user by construction, not by convention. Both sides derive it from the
# same current-user SID, so two accounts on one machine cannot collide and
# neither side has to be told where to connect.
# =========================================================================

PIPE_PREFIX = "EsotericOS.ControlBroker"

_SID_PATTERN = re.compile(r"S-1-(?:5|12)-\d{1,10}(?:-\d{1,10}){0,8}\Z")


def pipe_name(user_sid: str) -> str:
    """`EsotericOS.ControlBroker.<sid>` -- the bare name, no `\\\\.\\pipe\\`.

    Raises on a SID that is not a SID. A caller that cannot name the user has
    no business opening a per-user channel, and a silently-wrong pipe name is
    worse than an exception: it is a pipe somebody else can own.
    """
    sid = (user_sid or "").strip().upper()
    if not _SID_PATTERN.match(sid):
        raise ValueError("not a well-formed user SID")
    return PIPE_PREFIX + "." + sid


def pipe_path(user_sid: str) -> str:
    r"""The full `\\.\pipe\...` path the client opens."""
    return r"\\.\pipe" + "\\" + pipe_name(user_sid)


def pipe_sddl(user_sid: str) -> str:
    """The security descriptor the broker creates its pipe with.

    `O:<sid>G:<sid>D:P(A;;GA;;;<sid>)S:(ML;;NW;;;ME)`

    Read it left to right. The user owns it; the DACL is Protected, so nothing
    is inherited from anywhere; there is exactly ONE access-allowed ace and it
    names the user's own SID. Administrators, SYSTEM, INTERACTIVE, Everyone and
    Anonymous are absent -- not denied, absent, which is stronger, because a
    deny ace can be argued with by an ace added later and a Protected DACL with
    one entry cannot. The mandatory label pins the object at Medium with
    no-write-up, so a Low-integrity process (a sandboxed browser, a scripting
    host) cannot write to the pipe at all. The elevated GUI is High integrity
    and writing DOWN the integrity ladder is always permitted, which is the
    whole reason this broker exists.
    """
    sid = (user_sid or "").strip().upper()
    if not _SID_PATTERN.match(sid):
        raise ValueError("not a well-formed user SID")
    return f"O:{sid}G:{sid}D:P(A;;GA;;;{sid})S:(ML;;NW;;;ME)"


# =========================================================================
# FRAMING
# =========================================================================

@dataclass(frozen=True)
class Refusal:
    """A closed door with a name on it. Never carries request text."""

    reason: str
    detail: str = ""

    def __post_init__(self):
        if self.reason not in REASON_CODES:
            raise ValueError("refusal with an undeclared reason code")
        if not self.detail:
            object.__setattr__(self, "detail", REASON_DETAIL[self.reason])

    @property
    def ok(self) -> bool:
        return False


def _object_no_duplicates(pairs):
    """Reject `{"id": "eos.settings.about", "id": "anything else"}`.

    THIS IS NOT PEDANTRY, IT IS THE ONE PLACE TWO INDEPENDENT PARSERS ARE MOST
    LIKELY TO DISAGREE. Python's json keeps the LAST duplicate key;
    System.Text.Json's JsonDocument hands out the FIRST. A protocol with two
    implementations that disagree about which value wins is a protocol with a
    smuggling channel: the client validates one id and the broker dispatches
    another. Neither side accepts a duplicate key at all, so the question never
    gets asked.
    """
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate object key")
        seen[key] = value
    return seen


def _reject_constant(name):
    """NaN, Infinity and -Infinity are JavaScript, not JSON. Python's decoder
    accepts them and System.Text.Json does not; refusing them here keeps the
    two sides agreeing on the same reason code for the same bytes."""
    raise ValueError("non-finite JSON constant: " + str(name))


def encode_frame(payload: dict) -> bytes:
    """One dict to one frame. Raises if the caller built something oversize."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True,
                      ensure_ascii=True).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("frame body exceeds the protocol bound")
    return struct.pack(HEADER_FORMAT, len(body)) + body


def decode_frame(frame: bytes):
    """bytes -> dict, or a Refusal. Never raises on hostile input.

    The order matters and is the same on both sides: header first, bound
    second, completeness third, encoding fourth, JSON fifth, object-ness sixth.
    Checking the declared length before touching the body is what makes
    "oversized fails closed" cheap instead of a denial of service.
    """
    if not isinstance(frame, (bytes, bytearray)):
        return Refusal(FRAME_MALFORMED)
    if len(frame) < HEADER_BYTES:
        return Refusal(FRAME_TRUNCATED)
    declared = struct.unpack(HEADER_FORMAT, bytes(frame[:HEADER_BYTES]))[0]
    if declared > MAX_BODY_BYTES:
        return Refusal(FRAME_OVERSIZE)
    if declared == 0:
        return Refusal(FRAME_MALFORMED)
    body = bytes(frame[HEADER_BYTES:])
    if len(body) < declared:
        return Refusal(FRAME_TRUNCATED)
    if len(body) > declared:
        # Trailing bytes are a second request smuggled behind the first, or a
        # reader the sender expects to be sloppy. This protocol is one frame
        # per exchange, so extra bytes are malformed rather than ignored.
        return Refusal(FRAME_MALFORMED)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return Refusal(FRAME_MALFORMED)
    if "\x00" in text:
        return Refusal(FRAME_MALFORMED)
    try:
        payload = json.loads(text, object_pairs_hook=_object_no_duplicates,
                             parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return Refusal(FRAME_MALFORMED)
    if not isinstance(payload, dict):
        return Refusal(FRAME_MALFORMED)
    return payload


# =========================================================================
# REQUESTS
# =========================================================================

@dataclass(frozen=True)
class Request:
    """A parsed, structurally valid request. Says nothing about the id's
    meaning -- resolution is the caller's job and is done against a catalog,
    not against the request."""

    version: int
    kind: str
    record_id: str
    nonce: str
    issued_ms: int

    @property
    def ok(self) -> bool:
        return True


def new_nonce() -> str:
    return secrets.token_hex(NONCE_BYTES)


def now_ms() -> int:
    return int(time.time() * 1000)


def valid_record_id(record_id) -> bool:
    return (isinstance(record_id, str)
            and len(record_id) <= MAX_ID_LENGTH
            and bool(ID_PATTERN.match(record_id)))


def build_request(record_id: str, *, nonce: str | None = None,
                  issued_ms: int | None = None) -> dict:
    """The only way to make a request, and it takes one id.

    There is no parameter for a command, a target, a route or an integrity
    level, because there is no field for one. This signature is the security
    boundary written as a function.
    """
    if not valid_record_id(record_id):
        raise ValueError("not a well-formed EsotericOS catalog id")
    return {
        "v": PROTOCOL_VERSION,
        "kind": KIND_ACTIVATE,
        "id": record_id,
        "nonce": nonce if nonce is not None else new_nonce(),
        "issued": issued_ms if issued_ms is not None else now_ms(),
    }


def parse_request(frame: bytes):
    """Frame -> Request, or Refusal. The broker's first gate.

    Deliberately ordered cheapest-and-most-structural first so that the reason
    a request was refused is the *earliest* thing wrong with it. A caller
    sending a stale request with a bad id learns the id is bad, because a
    well-formed envelope is a precondition for the timestamp meaning anything.
    """
    payload = decode_frame(frame)
    if isinstance(payload, Refusal):
        return payload

    version = payload.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        return Refusal(SCHEMA_INVALID)
    if version != PROTOCOL_VERSION:
        return Refusal(VERSION_UNSUPPORTED)

    if set(payload.keys()) != REQUEST_KEYS:
        return Refusal(SCHEMA_INVALID)

    if payload.get("kind") != KIND_ACTIVATE:
        return Refusal(SCHEMA_INVALID)

    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or not NONCE_PATTERN.match(nonce):
        return Refusal(SCHEMA_INVALID)

    issued = payload.get("issued")
    if not isinstance(issued, int) or isinstance(issued, bool) or issued < 0:
        return Refusal(SCHEMA_INVALID)

    record_id = payload.get("id")
    if not isinstance(record_id, str):
        return Refusal(SCHEMA_INVALID)
    if not valid_record_id(record_id):
        return Refusal(ID_MALFORMED)

    return Request(version=version, kind=KIND_ACTIVATE, record_id=record_id,
                   nonce=nonce, issued_ms=issued)


def freshness(request: Request, now: int | None = None):
    """None when fresh, a Refusal when not. Symmetric window: a request from
    the future is as suspect as one from the past, and a client whose clock
    runs fast is a bug rather than a licence."""
    current = now_ms() if now is None else now
    if abs(current - request.issued_ms) > FRESHNESS_WINDOW_MS:
        return Refusal(REQUEST_STALE)
    return None


class NonceLedger:
    """Remembers nonces long enough to catch a replay, and no longer.

    Bounded by construction: entries older than REPLAY_MEMORY_MS are dropped on
    every observation, so an attacker cannot grow it, and an entry that has
    been dropped can no longer pass the freshness check anyway.
    """

    def __init__(self, memory_ms: int = REPLAY_MEMORY_MS):
        self._memory_ms = int(memory_ms)
        self._seen: dict = {}

    def __len__(self):
        return len(self._seen)

    def observe(self, nonce: str, now: int | None = None):
        """None when the nonce is new, a Refusal when it has been seen."""
        current = now_ms() if now is None else now
        cutoff = current - self._memory_ms
        if self._seen:
            for key in [k for k, v in self._seen.items() if v < cutoff]:
                del self._seen[key]
        if nonce in self._seen:
            return Refusal(REQUEST_REPLAYED)
        self._seen[nonce] = current
        return None


# =========================================================================
# RESPONSES
# =========================================================================

RESPONSE_KEYS = frozenset({"v", "nonce", "ok", "reason", "detail", "route",
                           "integrity", "elevation"})


def build_response(reason: str, *, nonce: str = "", route: str = "",
                   integrity: str = "", elevation: bool = False) -> dict:
    """The broker's answer. `detail` is looked up, never composed.

    `nonce` is echoed only when the broker actually parsed one; a malformed
    frame gets an empty nonce rather than a guess, because echoing bytes the
    broker could not parse is how a responder becomes an oracle.
    """
    if reason not in REASON_CODES:
        raise ValueError("response with an undeclared reason code")
    if nonce and not NONCE_PATTERN.match(nonce):
        nonce = ""
    return {
        "v": PROTOCOL_VERSION,
        "nonce": nonce,
        "ok": reason == OK,
        "reason": reason,
        "detail": REASON_DETAIL[reason],
        "route": route if route in ROUTES else "",
        "integrity": integrity if integrity in INTEGRITY_LEVELS else "",
        "elevation": bool(elevation),
    }


@dataclass(frozen=True)
class Response:
    reason: str
    nonce: str = ""
    route: str = ""
    integrity: str = ""
    elevation: bool = False
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.reason == OK


def parse_response(frame: bytes):
    """Frame -> Response, or Refusal. The client is as suspicious of the
    broker as the broker is of the client; a compromised or confused broker
    must not be able to hand the GUI an unrecognised reason string to show a
    person, or an unrecognised route to act on."""
    payload = decode_frame(frame)
    if isinstance(payload, Refusal):
        return payload
    if payload.get("v") != PROTOCOL_VERSION:
        return Refusal(VERSION_UNSUPPORTED)
    if not set(payload.keys()) <= RESPONSE_KEYS:
        return Refusal(SCHEMA_INVALID)
    reason = payload.get("reason")
    if reason not in REASON_CODES:
        return Refusal(SCHEMA_INVALID)
    if not isinstance(payload.get("ok"), bool):
        return Refusal(SCHEMA_INVALID)
    if payload["ok"] != (reason == OK):
        return Refusal(SCHEMA_INVALID)
    nonce = payload.get("nonce", "")
    if not isinstance(nonce, str) or (nonce and not NONCE_PATTERN.match(nonce)):
        return Refusal(SCHEMA_INVALID)
    route = payload.get("route", "")
    if route and route not in ROUTES:
        return Refusal(SCHEMA_INVALID)
    integrity = payload.get("integrity", "")
    if integrity and integrity not in INTEGRITY_LEVELS:
        return Refusal(SCHEMA_INVALID)
    return Response(reason=reason, nonce=nonce, route=route,
                    integrity=integrity,
                    elevation=bool(payload.get("elevation", False)),
                    detail=REASON_DETAIL[reason])


# =========================================================================
# THE BROKER'S CATALOG MANIFEST
#
# The broker does not run Python and must not ask the client what an id means.
# It gets its own copy: a generated, flat, boring file with one line of fact
# per catalog record and no aliases, titles, evidence or search vocabulary --
# nothing the GUI needs and the dispatcher does not.
#
# "Both sides independently resolve" is then literally true. The client
# resolves against control_catalog and refuses locally; the broker resolves
# against this manifest and refuses again; the two never compare notes, and the
# broker's answer is the one that dispatches.
# =========================================================================

MANIFEST_SCHEMA = 1
MANIFEST_FILENAME = "control-catalog-manifest.json"


def build_manifest(catalog) -> dict:
    """A Phase 1 Catalog in, the broker's flat manifest out.

    Unavailable records are INCLUDED. Dropping them would make the broker
    answer ID_UNKNOWN for a page that exists but is not on this edition, and
    the difference between "there is no such thing" and "this Windows does not
    have it" is exactly the reason the spec makes availability carry a reason.
    """
    records = []
    for record in catalog.records:
        records.append({
            "id": record.id,
            "kind": record.destination_kind,
            "target": record.destination_target,
            "integrity": record.integrity,
            "availability": record.availability,
        })
    records.sort(key=lambda item: item["id"])
    return {
        "schema": MANIFEST_SCHEMA,
        "revision": catalog.revision,
        "records": records,
    }


def write_manifest(path: str, catalog) -> str:
    """Deterministic bytes: sorted keys, sorted records, LF, no timestamp.

    A generated file with a timestamp in it is a file that is different every
    build, which means nobody can tell a regenerated manifest from a tampered
    one by looking. This one is a pure function of the catalog.
    """
    payload = build_manifest(catalog)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")
    return path


def resolve(manifest: dict, record_id: str):
    """The client-side twin of the broker's lookup: (record, None) or
    (None, Refusal). Same order, same codes, no shared state."""
    if not isinstance(manifest, dict) or manifest.get("schema") \
            != MANIFEST_SCHEMA:
        return None, Refusal(CATALOG_UNAVAILABLE)
    if not valid_record_id(record_id):
        return None, Refusal(ID_MALFORMED)
    found = None
    for record in manifest.get("records") or ():
        if record.get("id") == record_id:
            found = record
            break
    if found is None:
        return None, Refusal(ID_UNKNOWN)
    if found.get("availability") == AVAILABILITY_UNAVAILABLE:
        return None, Refusal(ID_UNAVAILABLE)
    route = ROUTE_BY_DESTINATION_KIND.get(found.get("kind"))
    if route is None:
        return None, Refusal(ROUTE_UNSUPPORTED)
    target = found.get("target") or ""
    pattern = TARGET_PATTERNS[route]
    if not isinstance(target, str) or not pattern.match(target):
        return None, Refusal(ROUTE_UNSUPPORTED)
    if found.get("integrity") not in INTEGRITY_LEVELS:
        return None, Refusal(ROUTE_UNSUPPORTED)
    return found, None
