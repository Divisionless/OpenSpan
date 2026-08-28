# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hostile-request tests -- the Phase 2 gate of docs/CONTROL-CENTER.md, app side.

The gate is "No Explorer launch path; hostile-request tests pass". This file is
the app half; the shell half is
EsotericOS.Shell.ControlBroker.Tests, which attacks the C# broker's decision
core with the same corpus expressed as raw frames.

NOTHING HERE OPENS A PIPE, A HANDLE, A PROCESS OR A DESTINATION, AND THAT IS
ENFORCED RATHER THAN INTENDED. Before the first assertion,
`control_center_client.NamedPipeTransport` and `_load_libraries` are replaced
with tripwires that raise, and the last assertion checks the tripwires were
still armed at the end. A suite that merely happens not to open a pipe today is
one refactor away from opening one on Doug's desk; this one fails loudly and
the failure names the call.

Run it the way every other suite in win/ runs:

    python win\\test_control_center_broker.py
"""

import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import control_catalog as C                                    # noqa: E402
import control_center_protocol as P                            # noqa: E402
import control_center_client as CC                             # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                              # noqa: BLE001
    pass

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        fails.append(name)
        if detail:
            print("      " + str(detail)[:400])


# =========================================================================
# THE TRIPWIRES. Everything below this line runs offline or fails saying so.
# =========================================================================

class _Tripwire:
    def __init__(self, what):
        self._what = what

    def __call__(self, *args, **kwargs):
        raise AssertionError("the broker test suite touched " + self._what)

    def __getattr__(self, name):
        raise AssertionError("the broker test suite touched "
                             + self._what + "." + name)


_REAL_TRANSPORT = CC.NamedPipeTransport
CC.NamedPipeTransport = _Tripwire("NamedPipeTransport")
CC._load_libraries = _Tripwire("kernel32/advapi32 via ctypes")
CC.current_user_sid = _Tripwire("the process token")

CATALOG = C.build_catalog_from_fixtures()
MANIFEST = P.build_manifest(CATALOG)

# A real, available, medium-integrity Settings page; the only id in this file
# that is ever expected to reach a transport.
GOOD_ID = "eos.settings.about"
# A real entry this Windows edition does not have.
UNAVAILABLE_ID = "eos.mmc.gpedit"
# A real entry whose only Windows route would be Explorer.
CLSID_ID = "eos.control.clsid.38a98528-6cbf-4ca9-8dc0-b1e1d10f7b1b"
# A real entry that genuinely requires elevation.
HIGH_ID = "eos.control.microsoft.devicemanager"

SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"


class RecordingTransport:
    """Stands where the pipe would be. Counts every byte that would have left
    this process, which is how "the request was never sent" is proved rather
    than asserted."""

    def __init__(self, reply=None):
        self.frames = []
        self._reply = reply

    def __call__(self, frame):
        self.frames.append(frame)
        if self._reply is None:
            return P.encode_frame(P.build_response(P.OK, nonce=self._nonce(frame),
                                                   route=P.ROUTE_MS_SETTINGS,
                                                   integrity=P.INTEGRITY_MEDIUM))
        return self._reply(frame)

    @staticmethod
    def _nonce(frame):
        return json.loads(frame[P.HEADER_BYTES:].decode("utf-8"))["nonce"]


def _try(callable_):
    """True when the call raised. Used where refusing by exception is the
    contract -- building a request, naming a pipe -- as opposed to the far more
    common case here of refusing by return value."""
    try:
        callable_()
    except Exception:                                          # noqa: BLE001
        return True
    return False


def _boom(_frame):
    raise OSError(2, "the pipe is not there")


def _agrees(record):
    """The client's resolution and the broker's manifest resolution, compared
    for one record. They are computed by different code over different data
    structures; agreeing on all 175 is the evidence that "both sides
    independently resolve" produces one answer rather than two."""
    local, local_refusal = CC.resolve_local(CATALOG, record.id)
    manifest_record, manifest_refusal = P.resolve(MANIFEST, record.id)
    if local_refusal is not None or manifest_refusal is not None:
        return (local_refusal is not None and manifest_refusal is not None
                and local_refusal.reason == manifest_refusal.reason)
    return (local.target == manifest_record["target"]
            and local.integrity == manifest_record["integrity"]
            and local.route
            == P.ROUTE_BY_DESTINATION_KIND[manifest_record["kind"]])


def frame_of(payload_text):
    """A frame whose body is exactly these bytes, honestly length-prefixed."""
    body = payload_text.encode("utf-8") if isinstance(payload_text, str) \
        else payload_text
    return struct.pack(P.HEADER_FORMAT, len(body)) + body


def request_frame(**overrides):
    payload = {"v": P.PROTOCOL_VERSION, "kind": P.KIND_ACTIVATE,
               "id": GOOD_ID, "nonce": "a" * 32, "issued": P.now_ms()}
    payload.update(overrides)
    for key in [k for k, v in payload.items() if v is _ABSENT]:
        del payload[key]
    return frame_of(json.dumps(payload, separators=(",", ":")))


_ABSENT = object()


# =========================================================================
# 1. THE VOCABULARY IS CLOSED AND MATCHES THE WRITTEN SPEC.
# =========================================================================

print("---- the closed vocabularies ----")

DOCUMENTED_REASONS = (
    "ok", "frame-oversize", "frame-truncated", "frame-malformed",
    "version-unsupported", "schema-invalid", "id-malformed", "id-unknown",
    "id-unavailable", "route-unsupported", "request-stale", "request-replayed",
    "identity-rejected", "catalog-unavailable", "activation-not-armed",
    "activation-failed", "transport-unavailable", "internal-error",
)
DOCUMENTED_ROUTES = ("ms-settings-uri", "control-name", "control-cpl",
                     "mmc-console")

check("the reason codes are exactly the ones docs/CONTROL-CENTER-PROTOCOL.md "
      "lists", tuple(P.REASON_CODES) == DOCUMENTED_REASONS,
      set(P.REASON_CODES) ^ set(DOCUMENTED_REASONS))
check("every reason code has a fixed detail sentence",
      all(P.REASON_DETAIL.get(code) for code in P.REASON_CODES))
check("no detail sentence is assembled from anything variable",
      all(isinstance(text, str) and "{" not in text and "%" not in text
          for text in P.REASON_DETAIL.values()))
check("the routes are exactly the four Explorer-free routes",
      tuple(P.ROUTES) == DOCUMENTED_ROUTES)
check("explorer is not a route and not a target grammar",
      not any("explorer" in route.lower() for route in P.ROUTES)
      and not any("shell:::" in pattern.pattern
                  for pattern in P.TARGET_PATTERNS.values()))
check("a namespace CLSID has no route at all",
      P.ROUTE_BY_DESTINATION_KIND["control-panel-clsid"] is None)
check("every Phase 1 destination kind is accounted for in the route table",
      set(P.ROUTE_BY_DESTINATION_KIND) == set(C.DESTINATION_KINDS),
      set(P.ROUTE_BY_DESTINATION_KIND) ^ set(C.DESTINATION_KINDS))
check("a refusal cannot be built with an undeclared reason",
      _try(lambda: P.Refusal("looked-wrong")))
check("a response cannot be built with an undeclared reason",
      _try(lambda: P.build_response("looked-wrong")))


# =========================================================================
# 2. FRAMING.
# =========================================================================

print("\n---- framing ----")

_ok_frame = P.encode_frame({"hello": "world"})
check("a frame round-trips", P.decode_frame(_ok_frame) == {"hello": "world"})
check("the header is a 4-byte little-endian length",
      _ok_frame[:4] == struct.pack("<I", len(_ok_frame) - 4))

_over = struct.pack(P.HEADER_FORMAT, P.MAX_BODY_BYTES + 1) + b"{}"
check("an oversize DECLARATION is refused without reading a body",
      P.decode_frame(_over).reason == P.FRAME_OVERSIZE)
check("a 4GB declaration costs four bytes, not four gigabytes",
      P.decode_frame(struct.pack(P.HEADER_FORMAT, 0xFFFFFFFF)).reason
      == P.FRAME_OVERSIZE)
check("an oversize BODY cannot even be encoded",
      _try(lambda: P.encode_frame({"x": "y" * (P.MAX_BODY_BYTES + 10)})))
check("an empty frame is truncated", P.decode_frame(b"").reason
      == P.FRAME_TRUNCATED)
check("a header-only frame is truncated",
      P.decode_frame(b"\x01\x00\x00").reason == P.FRAME_TRUNCATED)
check("a body shorter than declared is truncated",
      P.decode_frame(struct.pack(P.HEADER_FORMAT, 50) + b"{}").reason
      == P.FRAME_TRUNCATED)
check("a zero-length body is malformed",
      P.decode_frame(struct.pack(P.HEADER_FORMAT, 0)).reason
      == P.FRAME_MALFORMED)
check("trailing bytes behind a complete frame are malformed, not ignored",
      P.decode_frame(_ok_frame + b'{"id":"eos.x.y"}').reason
      == P.FRAME_MALFORMED)
check("invalid UTF-8 is malformed",
      P.decode_frame(frame_of(b"\xff\xfe\x00garbage")).reason
      == P.FRAME_MALFORMED)
check("an embedded NUL is malformed",
      P.decode_frame(frame_of('{"a":"b\u0000c"}')).reason == P.FRAME_MALFORMED)
check("a JSON array is not an object",
      P.decode_frame(frame_of('["eos.settings.about"]')).reason
      == P.FRAME_MALFORMED)
check("a bare JSON string is not an object",
      P.decode_frame(frame_of('"eos.settings.about"')).reason
      == P.FRAME_MALFORMED)
check("unparseable JSON is malformed",
      P.decode_frame(frame_of("{not json")).reason == P.FRAME_MALFORMED)
check("a DUPLICATE KEY is refused, because the two parsers disagree about "
      "which one wins",
      P.decode_frame(frame_of('{"id":"eos.settings.about","id":"eos.evil.x"}'))
      .reason == P.FRAME_MALFORMED)
check("NaN is refused even though Python's decoder accepts it",
      P.decode_frame(frame_of('{"issued":NaN}')).reason == P.FRAME_MALFORMED)
check("Infinity is refused for the same reason",
      P.decode_frame(frame_of('{"issued":Infinity}')).reason
      == P.FRAME_MALFORMED)
check("deep nesting is refused rather than crashing the parser",
      isinstance(P.decode_frame(frame_of("[" * 900 + "]" * 900)), P.Refusal))


# =========================================================================
# 3. THE IDENTIFIER GRAMMAR IS AN ALLOW-LIST.
#
# Every hostile string a caller might reach for, in one place. None of them can
# become a request, so none of them can reach a route, so none of them can
# reach the seam.
# =========================================================================

print("\n---- the identifier grammar ----")

HOSTILE_IDS = [
    ("empty", ""),
    ("whitespace only", "   "),
    ("prefix only", "eos"),
    ("two segments", "eos.settings"),
    ("trailing dot", "eos.settings.about."),
    ("leading dot", ".eos.settings.about"),
    ("empty segment", "eos..settings.about"),
    ("trailing space", "eos.settings.about "),
    ("trailing newline", "eos.settings.about\n"),
    ("leading newline", "\neos.settings.about"),
    ("embedded newline", "eos.settings\n.about"),
    ("uppercase", "EOS.SETTINGS.ABOUT"),
    ("wrong prefix", "win.settings.about"),
    ("relative traversal", "eos.settings.../../windows/system32/cmd"),
    ("bare traversal", "../../../windows/system32/cmd.exe"),
    ("absolute path", "C:\\Windows\\System32\\cmd.exe"),
    ("unc path", "\\\\attacker\\share\\payload.exe"),
    ("forward slashes", "eos/settings/about"),
    ("backslashes", "eos\\settings\\about"),
    ("semicolon command", "eos.settings.about;calc.exe"),
    ("ampersand command", "eos.settings.about&calc.exe"),
    ("pipe command", "eos.settings.about|calc.exe"),
    ("shell substitution", "eos.settings.$(calc.exe)"),
    ("backtick substitution", "eos.settings.`calc.exe`"),
    ("quote break-out", "eos.settings.about\" \"calc"),
    ("single quote break-out", "eos.settings.about' 'calc"),
    ("embedded NUL", "eos.settings.about\x00.evil"),
    ("percent encoding", "eos.settings.about%2e%2e%2f"),
    ("cyrillic homoglyph", "eos.settings.\u0430bout"),
    ("right-to-left override", "eos.settings.\u202eabout"),
    ("a settings uri, not an id", "ms-settings:display"),
    ("a control canonical name, not an id", "Microsoft.DeviceManager"),
    ("a console filename, not an id", "services.msc"),
    ("a cpl filename, not an id", "main.cpl"),
    ("a shell namespace path", "shell:::{38A98528-6CBF-4CA9-8DC0-B1E1D10F7B1B}"),
    ("explorer invocation", "explorer.exe shell:::{ED7BA470}"),
    ("overlong", "eos.settings." + "a" * 200),
    ("too many segments", "eos." + ".".join("abcdefgh")),
    ("sql-shaped", "eos.settings.about' OR '1'='1"),
    ("json-shaped", '{"id":"eos.settings.about"}'),
    ("null byte suffix", "eos.settings.about\x00"),
    ("tab", "eos.settings.\tabout"),
    ("carriage return", "eos.settings.about\r"),
]

for _label, _value in HOSTILE_IDS:
    check("the grammar refuses: " + _label, not P.valid_record_id(_value),
          repr(_value))

check("the grammar refuses a non-string", not P.valid_record_id(None)
      and not P.valid_record_id(1234) and not P.valid_record_id(["eos.a.b"]))
check("the grammar accepts every one of the 175 real catalog ids",
      all(P.valid_record_id(record.id) for record in CATALOG.records),
      [r.id for r in CATALOG.records if not P.valid_record_id(r.id)][:5])
check("build_request refuses every hostile identifier",
      all(_try(lambda v=value: P.build_request(v))
          for _, value in HOSTILE_IDS))


# =========================================================================
# 4. THE ENVELOPE IS CLOSED.
# =========================================================================

print("\n---- the request envelope ----")

_parsed = P.parse_request(request_frame())
check("a well-formed request parses", isinstance(_parsed, P.Request)
      and _parsed.record_id == GOOD_ID)
check("a smuggled extra key is refused, not ignored",
      P.parse_request(request_frame(target="cmd.exe")).reason
      == P.SCHEMA_INVALID)
check("a smuggled route is refused",
      P.parse_request(request_frame(route="control-name")).reason
      == P.SCHEMA_INVALID)
check("a smuggled integrity level is refused",
      P.parse_request(request_frame(integrity="high")).reason
      == P.SCHEMA_INVALID)
check("a smuggled elevation flag is refused",
      P.parse_request(request_frame(elevate=True)).reason == P.SCHEMA_INVALID)
check("a missing id is refused",
      P.parse_request(request_frame(id=_ABSENT)).reason == P.SCHEMA_INVALID)
check("a missing nonce is refused",
      P.parse_request(request_frame(nonce=_ABSENT)).reason == P.SCHEMA_INVALID)
check("a missing timestamp is refused",
      P.parse_request(request_frame(issued=_ABSENT)).reason
      == P.SCHEMA_INVALID)
check("a second verb is refused",
      P.parse_request(request_frame(kind="run")).reason == P.SCHEMA_INVALID)
check("an absent verb is refused",
      P.parse_request(request_frame(kind=_ABSENT)).reason == P.SCHEMA_INVALID)
check("a future protocol version is named, not merely rejected",
      P.parse_request(request_frame(v=2)).reason == P.VERSION_UNSUPPORTED)
check("a version zero is refused",
      P.parse_request(request_frame(v=0)).reason == P.VERSION_UNSUPPORTED)
check("a string version is a schema fault, not a version fault",
      P.parse_request(request_frame(v="1")).reason == P.SCHEMA_INVALID)
check("a boolean version is a schema fault",
      P.parse_request(request_frame(v=True)).reason == P.SCHEMA_INVALID)
check("a string timestamp is refused",
      P.parse_request(request_frame(issued="1700000000000")).reason
      == P.SCHEMA_INVALID)
check("a fractional timestamp is refused",
      P.parse_request(request_frame(issued=1.5)).reason == P.SCHEMA_INVALID)
check("a negative timestamp is refused",
      P.parse_request(request_frame(issued=-1)).reason == P.SCHEMA_INVALID)
check("a boolean timestamp is refused",
      P.parse_request(request_frame(issued=True)).reason == P.SCHEMA_INVALID)
check("a short nonce is refused",
      P.parse_request(request_frame(nonce="abc")).reason == P.SCHEMA_INVALID)
check("an uppercase nonce is refused",
      P.parse_request(request_frame(nonce="A" * 32)).reason
      == P.SCHEMA_INVALID)
check("a non-hex nonce is refused",
      P.parse_request(request_frame(nonce="z" * 32)).reason
      == P.SCHEMA_INVALID)
check("an object where a string belongs is refused",
      P.parse_request(request_frame(id={"$ne": None})).reason
      == P.SCHEMA_INVALID)
check("a hostile identifier inside a well-formed envelope is named "
      "id-malformed",
      P.parse_request(request_frame(id="../../windows/system32/cmd.exe"))
      .reason == P.ID_MALFORMED)
check("a hostile identifier is refused BEFORE it can be resolved",
      P.parse_request(request_frame(id="eos.settings.about;calc")).reason
      == P.ID_MALFORMED)


# =========================================================================
# 5. FRESHNESS AND REPLAY.
# =========================================================================

print("\n---- freshness and replay ----")

_now = 1_700_000_000_000
_fresh = P.parse_request(request_frame(issued=_now))
check("a request at the current instant is fresh",
      P.freshness(_fresh, _now) is None)
check("a request one millisecond inside the window is fresh",
      P.freshness(_fresh, _now + P.FRESHNESS_WINDOW_MS) is None)
check("a request one millisecond outside the window is stale",
      P.freshness(_fresh, _now + P.FRESHNESS_WINDOW_MS + 1).reason
      == P.REQUEST_STALE)
check("a request from the FUTURE is as stale as one from the past",
      P.freshness(_fresh, _now - P.FRESHNESS_WINDOW_MS - 1).reason
      == P.REQUEST_STALE)
check("a captured request replayed a day later is stale",
      P.freshness(_fresh, _now + 86_400_000).reason == P.REQUEST_STALE)

_ledger = P.NonceLedger()
_nonce = "b" * 32
check("a nonce is accepted once", _ledger.observe(_nonce, _now) is None)
check("the same nonce immediately after is a replay",
      _ledger.observe(_nonce, _now).reason == P.REQUEST_REPLAYED)
check("a replay one second later is still a replay",
      _ledger.observe(_nonce, _now + 1000).reason == P.REQUEST_REPLAYED)
check("a different nonce is not a replay",
      _ledger.observe("c" * 32, _now) is None)
_ledger.observe("d" * 32, _now)
_before = len(_ledger)
_ledger.observe("e" * 32, _now + P.REPLAY_MEMORY_MS + 1)
check("the ledger forgets what can no longer pass the freshness check",
      len(_ledger) < _before + 1,
      f"{_before} entries before, {len(_ledger)} after")
check("the ledger cannot be grown without bound by an attacker",
      len(_ledger) <= 2, len(_ledger))


# =========================================================================
# 6. THE CHANNEL IS BOUND TO ONE USER'S SID.
# =========================================================================

print("\n---- the pipe name and its ACL ----")

check("the pipe name carries the user's SID",
      P.pipe_name(SID) == "EsotericOS.ControlBroker." + SID)
check("the pipe path is a local pipe path",
      P.pipe_path(SID).startswith("\\\\.\\pipe\\"))
check("two different users get two different pipes",
      P.pipe_name(SID) != P.pipe_name(
          "S-1-5-21-1111111111-2222222222-3333333333-1002"))
check("a non-SID cannot name a pipe",
      _try(lambda: P.pipe_name("Administrator"))
      and _try(lambda: P.pipe_name(""))
      and _try(lambda: P.pipe_name("S-1-5-21-../../etc")))

_sddl = P.pipe_sddl(SID)
check("the descriptor names the user as owner and group",
      _sddl.startswith("O:" + SID + "G:" + SID))
check("the DACL is Protected -- nothing is inherited", "D:P(" in _sddl)
check("there is exactly ONE access-allowed ace", _sddl.count("(A;;") == 1)
check("that one ace names the user's own SID", "(A;;GA;;;" + SID + ")" in _sddl)
check("Everyone is absent from the descriptor", ";;;WD)" not in _sddl)
check("Authenticated Users is absent", ";;;AU)" not in _sddl)
check("Administrators is absent", ";;;BA)" not in _sddl)
check("SYSTEM is absent", ";;;SY)" not in _sddl)
check("Anonymous is absent", ";;;AN)" not in _sddl)
check("Interactive is absent", ";;;IU)" not in _sddl)
check("the mandatory label pins the pipe at Medium with no-write-up",
      "S:(ML;;NW;;;ME)" in _sddl)
check("a non-SID cannot build a descriptor",
      _try(lambda: P.pipe_sddl("S-1-5-21-x")))


# =========================================================================
# 7. RESOLUTION, INDEPENDENTLY, ON BOTH SIDES OF THE SAME LADDER.
# =========================================================================

print("\n---- resolution ----")

check("the manifest carries every catalog record",
      len(MANIFEST["records"]) == len(CATALOG.records) == 175,
      len(MANIFEST["records"]))
check("the manifest carries no title, alias, evidence or search vocabulary",
      set(MANIFEST["records"][0]) == {"id", "kind", "target", "integrity",
                                      "availability"},
      set(MANIFEST["records"][0]))
check("the manifest is deterministic -- same catalog, identical bytes",
      json.dumps(P.build_manifest(CATALOG), sort_keys=True)
      == json.dumps(P.build_manifest(CATALOG), sort_keys=True))
check("the manifest carries no timestamp to make two builds differ",
      "generated" not in MANIFEST and "timestamp" not in MANIFEST)
check("unavailable records stay in the manifest so the broker can say why",
      any(r["availability"] == "unavailable" for r in MANIFEST["records"]))

_record, _refusal = P.resolve(MANIFEST, GOOD_ID)
check("a good id resolves against the manifest", _refusal is None
      and _record["target"] == "ms-settings:about")
check("an unknown id is unknown",
      P.resolve(MANIFEST, "eos.settings.nosuchpage")[1].reason == P.ID_UNKNOWN)
check("a malformed id is malformed, not unknown",
      P.resolve(MANIFEST, "../../cmd.exe")[1].reason == P.ID_MALFORMED)
check("an entry this Windows does not have is unavailable, not unknown",
      P.resolve(MANIFEST, UNAVAILABLE_ID)[1].reason == P.ID_UNAVAILABLE)
check("a CLSID-only entry has no Explorer-free route and says so",
      P.resolve(MANIFEST, CLSID_ID)[1].reason == P.ROUTE_UNSUPPORTED)
check("a manifest with the wrong schema resolves nothing",
      P.resolve({"schema": 99, "records": MANIFEST["records"]}, GOOD_ID)[1]
      .reason == P.CATALOG_UNAVAILABLE)
check("a tampered target that no longer matches its route is refused",
      P.resolve({"schema": 1, "records": [
          {"id": GOOD_ID, "kind": "ms-settings",
           "target": "ms-settings:about & calc.exe",
           "integrity": "medium", "availability": "available"}]}, GOOD_ID)[1]
      .reason == P.ROUTE_UNSUPPORTED)
check("a tampered target holding a path is refused",
      P.resolve({"schema": 1, "records": [
          {"id": "eos.mmc.services", "kind": "mmc-console",
           "target": "..\\..\\windows\\system32\\cmd.exe",
           "integrity": "medium", "availability": "available"}]},
          "eos.mmc.services")[1].reason == P.ROUTE_UNSUPPORTED)
check("every resolvable catalog record has a target its route accepts",
      all(P.resolve(MANIFEST, r.id)[1] is None
          or P.resolve(MANIFEST, r.id)[1].reason
          in (P.ID_UNAVAILABLE, P.ROUTE_UNSUPPORTED)
          for r in CATALOG.records))

_local, _local_refusal = CC.resolve_local(CATALOG, GOOD_ID)
check("the client resolves the same id to the same route as the manifest",
      _local_refusal is None and _local.route == P.ROUTE_MS_SETTINGS
      and _local.target == _record["target"])
check("client and manifest agree on every one of the 175 records",
      all(_agrees(record) for record in CATALOG.records))
check("the client marks a high-integrity entry as needing elevation",
      CC.resolve_local(CATALOG, HIGH_ID)[0].elevation)
check("the client does not mark a medium entry as needing elevation",
      not CC.resolve_local(CATALOG, GOOD_ID)[0].elevation)
check("21 catalog entries are marked high integrity and none is silent",
      sum(1 for r in CATALOG.records if r.integrity == "high") == 21)


# =========================================================================
# 8. THE CLIENT NEVER SENDS WHAT IT WOULD NOT ACCEPT.
#
# The strongest app-side claim available: for every hostile identifier, the
# recording transport sees ZERO bytes. Nothing was sent, so nothing could be
# resolved, routed or activated on the other side.
# =========================================================================

print("\n---- the client refuses locally, before the wire ----")

_silent = RecordingTransport()
_reasons = set()
for _label, _value in HOSTILE_IDS:
    _result = CC.request_activation(CATALOG, _value, _silent)
    _reasons.add(_result.reason)
check("no hostile identifier put a single byte on the transport",
      _silent.frames == [], len(_silent.frames))
check("every hostile identifier was refused locally",
      all(CC.request_activation(CATALOG, value, _silent).refused_locally
          for _, value in HOSTILE_IDS))
check("every hostile identifier was refused as id-malformed",
      _reasons == {P.ID_MALFORMED}, _reasons)

for _label, _value, _expected in (
        ("an unknown but well-formed id", "eos.settings.nosuchpage", P.ID_UNKNOWN),
        ("an unavailable entry", UNAVAILABLE_ID, P.ID_UNAVAILABLE),
        ("a CLSID-only entry", CLSID_ID, P.ROUTE_UNSUPPORTED)):
    _before = len(_silent.frames)
    _result = CC.request_activation(CATALOG, _value, _silent)
    check("refused locally without a round trip: " + _label,
          _result.reason == _expected and len(_silent.frames) == _before,
          _result.reason)

_sent = RecordingTransport()
_result = CC.request_activation(CATALOG, GOOD_ID, _sent)
check("a good id does reach the transport", len(_sent.frames) == 1)
check("and the round trip succeeds", _result.ok and not _result.refused_locally)
_body = json.loads(_sent.frames[0][P.HEADER_BYTES:].decode("utf-8"))
check("the frame that left carries exactly five keys",
      set(_body) == {"v", "kind", "id", "nonce", "issued"}, set(_body))
check("the frame that left carries the id and nothing resembling a command",
      _body["id"] == GOOD_ID
      and not any(isinstance(v, str) and ("." in v and v.endswith(".exe"))
                  for v in _body.values()))
check("the frame that left carries no target, route, path or integrity",
      not ({"target", "route", "path", "integrity", "command", "argument",
            "elevate"} & set(_body)))
check("the frame that left is far inside the size bound",
      len(_sent.frames[0]) < 256, len(_sent.frames[0]))
check("each request gets a fresh nonce",
      CC.request_activation(CATALOG, GOOD_ID, _sent) is not None
      and json.loads(_sent.frames[0][4:])["nonce"]
      != json.loads(_sent.frames[-1][4:])["nonce"])


# =========================================================================
# 9. THE CLIENT IS AS SUSPICIOUS OF THE BROKER AS THE BROKER IS OF IT.
# =========================================================================

print("\n---- hostile answers ----")


def answering(builder):
    return RecordingTransport(reply=lambda frame: builder(frame))


def _nonce_of(frame):
    return json.loads(frame[P.HEADER_BYTES:].decode("utf-8"))["nonce"]


_cases = [
    # Garbage read as a header is an absurd declared length, so it is caught by
    # the bound before anything tries to make sense of the body. That is the
    # bound doing its job, and the reason code names what actually happened.
    ("bytes that are not a frame at all", lambda f: b"not a frame at all",
     P.FRAME_OVERSIZE),
    ("an answer whose body is not JSON",
     lambda f: struct.pack("<I", 10) + b"not json!!", P.FRAME_MALFORMED),
    ("an empty answer", lambda f: b"", P.FRAME_TRUNCATED),
    ("a truncated answer", lambda f: struct.pack("<I", 900) + b"{}",
     P.FRAME_TRUNCATED),
    ("an oversize answer", lambda f: struct.pack("<I", 99999) + b"{}",
     P.FRAME_OVERSIZE),
    ("an answer from another protocol version",
     lambda f: P.encode_frame({"v": 7, "reason": "ok", "ok": True}),
     P.VERSION_UNSUPPORTED),
    ("an answer with an invented reason code",
     lambda f: P.encode_frame({"v": 1, "reason": "looked-fine", "ok": True,
                               "nonce": _nonce_of(f)}), P.SCHEMA_INVALID),
    ("an answer whose ok disagrees with its reason",
     lambda f: P.encode_frame({"v": 1, "reason": "id-unknown", "ok": True,
                               "nonce": _nonce_of(f)}), P.SCHEMA_INVALID),
    ("an answer with an invented route",
     lambda f: P.encode_frame({"v": 1, "reason": "ok", "ok": True,
                               "nonce": _nonce_of(f),
                               "route": "explorer-shell"}), P.SCHEMA_INVALID),
    ("an answer with a smuggled extra key",
     lambda f: P.encode_frame({"v": 1, "reason": "ok", "ok": True,
                               "nonce": _nonce_of(f),
                               "command": "calc.exe"}), P.SCHEMA_INVALID),
    ("an answer to a different question",
     lambda f: P.encode_frame({"v": 1, "reason": "ok", "ok": True,
                               "nonce": "f" * 32}), P.SCHEMA_INVALID),
]
for _label, _builder, _expected in _cases:
    _result = CC.request_activation(CATALOG, GOOD_ID, answering(_builder))
    check("the client refuses " + _label, _result.reason == _expected,
          _result.reason)

_result = CC.request_activation(CATALOG, GOOD_ID,
                                RecordingTransport(reply=_boom))
check("a transport that throws becomes transport-unavailable, not a crash",
      _result.reason == P.TRANSPORT_UNAVAILABLE)

_divergent = RecordingTransport(reply=lambda f: P.encode_frame(
    P.build_response(P.OK, nonce=_nonce_of(f), route=P.ROUTE_MMC,
                     integrity=P.INTEGRITY_HIGH)))
_result = CC.request_activation(CATALOG, GOOD_ID, _divergent)
check("a broker that resolved the same id differently is flagged as divergent",
      _result.divergent)
check("agreement is not flagged as divergent",
      not CC.request_activation(CATALOG, GOOD_ID, RecordingTransport())
      .divergent)


# =========================================================================
# 10. THE TRIPWIRES HELD.
# =========================================================================

print("\n---- the tripwires ----")

check("NamedPipeTransport was never constructed in this run",
      isinstance(CC.NamedPipeTransport, _Tripwire))
check("no ctypes library was loaded in this run",
      isinstance(CC._load_libraries, _Tripwire))
check("the process token was never read in this run",
      isinstance(CC.current_user_sid, _Tripwire))
check("asking with no transport hits the tripwire and reports "
      "transport-unavailable rather than launching anything",
      CC.request_activation(CATALOG, GOOD_ID).reason == P.TRANSPORT_UNAVAILABLE)

# The app modules are scanned as SYNTAX, not as text. A grep would trip over
# the comments that explain why there is no Explorer route -- which is exactly
# the kind of false alarm that gets a guard deleted -- so this walks the parse
# tree and looks at imports, called names and string literals only.
_LAUNCH_NAMES = {"subprocess", "system", "startfile", "popen", "spawnl",
                 "spawnv", "execv", "execl", "ShellExecuteW", "ShellExecuteExW",
                 "CreateProcessW", "LaunchUriAsync"}


def _launch_syntax(path):
    import ast

    tree = ast.parse(open(path, encoding="utf-8").read())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name in _LAUNCH_NAMES]
        elif isinstance(node, ast.ImportFrom):
            if node.module in _LAUNCH_NAMES:
                hits.append(node.module)
        elif isinstance(node, ast.Attribute) and node.attr in _LAUNCH_NAMES:
            hits.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in _LAUNCH_NAMES:
            hits.append(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if "explorer.exe" in low or "shell:::" in low or ".exe" in low:
                hits.append(node.value[:60])
    return hits


for _name in ("control_center_protocol.py", "control_center_client.py"):
    _hits = _launch_syntax(os.path.join(HERE, _name))
    check(_name + " contains no launch syntax and no executable or Explorer "
          "literal", _hits == [], _hits)


print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
