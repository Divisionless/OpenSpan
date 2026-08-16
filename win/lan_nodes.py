"""LAN nodes: another EsotericOS on this network, paired once, by key.

A LAN node is a device whose lane is TCP to a peer EsotericOS. It is the
answer to the machines Bluetooth cannot help with: a PC you own and can
install software on, where the limit was never the OS's HID stack but the
distance between two desks.

THREE THINGS ARE DELIBERATELY NOT OURS TO CHOOSE.

1. THE SERVICE PORT. It is whatever the OS hands out -- `bind(("", 0))`, read
   back, advertised. Nothing connects to a number this file knows; every peer
   connects to the port the advertisement carried. A second EsotericOS on the
   same machine, a machine already using our "favourite" port, a firewall that
   pinholes one number -- none of them are a case to handle, because there is
   no number to collide with. The port changes every launch and that is the
   design, not a tolerance.

2. THE DISCOVERY CHANNEL. Windows 10 1809+ ships DNS-SD in `dnsapi.dll`
   (DnsServiceRegister / DnsServiceBrowse / DnsServiceDeRegister). That is the
   OS's own service discovery, the same protocol Bonjour speaks, so a Mac or an
   iPad can find this node later without anything new being invented. Where the
   API is missing we fall back to a minimal RFC 6762 mDNS on 224.0.0.251:5353 --
   a standard's number, not a number we picked. `DISCOVERY_PATH` names which one
   is live and the Console says so at startup.

3. IDENTITY. A node is 32 random bytes. The name is a LABEL, defaulting to the
   machine name read at runtime and editable afterwards; renaming a node never
   re-pairs it, and no peer is ever addressed by IP, index, or position. The
   address is learned from whoever sent the packet and is never stored as
   identity.

Everything above the `--- the network edge ---` line is pure and has no socket
in it, so the tests drive the whole protocol -- beacon encode/decode, peer
expiry, the pairing handshake, signing -- without a network. The edge below it
is small on purpose, and the loopback test in test_lan_nodes.py drives two real
Node services over loopback through it.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import json
import os
import socket
import struct
import threading
import time


# ---- the only constants, and where each one comes from ---------------------

# RFC 6763 service type. `_esotericos._tcp` is a name, not a port.
SERVICE_TYPE = "_esotericos._tcp.local"
# RFC 6762 section 3: the mDNS link-local multicast group and port. Standards,
# used only by the fallback path.
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
# How often the fallback path re-announces, and how long a peer survives
# without being heard from. The OS DNS-SD path gets its refresh from the OS.
ANNOUNCE_INTERVAL = 2.0
PEER_TTL = 10.0
# How long the two humans have to compare the code on their two screens.
PAIR_WINDOW = 60.0
# The advertisement's protocol generation. A peer announcing anything else is
# not something we know how to talk to, and is dropped rather than guessed at.
PROTOCOL = 1
NODE_KEY_BYTES = 32
NONCE_BYTES = 32
# Biggest datagram we will read off the group in one go. Not a protocol
# number -- just a ceiling, so a hostile sender cannot size our buffer.
RECV_MAX = 65535


# ============================================================================
# identity
# ============================================================================

def new_node_key():
    """32 random bytes as hex. THE identity of this node, forever.

    Not derived from the machine: not the hostname, not a MAC, not a disk
    id, not a user name. A derived id would make two nodes on cloned
    hardware the same node, would change when the user renames their PC, and
    would put a machine fact in a file that gets copied around. Random is the
    only one of those that is stable AND says nothing.
    """
    return binascii.hexlify(os.urandom(NODE_KEY_BYTES)).decode("ascii")


def default_node_name():
    """This machine's name, READ AT RUNTIME. Never baked into anything.

    It is a starting label the user can change, so a wrong or ugly answer here
    costs a rename and nothing else -- which is why every fallback below is
    acceptable and none of them is a failure.
    """
    for source in (lambda: os.environ.get("COMPUTERNAME", ""),
                   socket.gethostname):
        try:
            name = str(source() or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        if name:
            return name.split(".")[0]
    return "EsotericOS node"


def load_identity(path, name=None):
    """This node's {id, name, created} from `path`, generating it once.

    The KEY is generated exactly once and then never again: a caller that finds
    a valid file gets that file's key back untouched, whatever else it asks for.
    Re-keying a node would silently un-pair it from every peer that has already
    stored it, and would look from the other desk like the node vanished.
    """
    record = {}
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            record = loaded
    except (OSError, ValueError):
        record = {}
    node_id = str(record.get("id") or "").strip().lower()
    if len(node_id) != NODE_KEY_BYTES * 2 or not _is_hex(node_id):
        node_id = new_node_key()
        record = {"id": node_id,
                  "name": str(name or record.get("name")
                              or default_node_name()),
                  "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
        save_identity(path, record)
        return record
    record["id"] = node_id
    if not str(record.get("name") or "").strip():
        record["name"] = str(name or default_node_name())
        save_identity(path, record)
    return record


def save_identity(path, record):
    """Write node.json atomically. Never raises into a caller."""
    return _write_json(path, {"id": str(record.get("id") or ""),
                              "name": str(record.get("name") or ""),
                              "created": str(record.get("created") or "")})


def rename_node(path, name):
    """Change the LABEL. The key -- the identity -- is untouched by design."""
    record = load_identity(path)
    record["name"] = str(name or "").strip() or default_node_name()
    save_identity(path, record)
    return record


def _is_hex(text):
    try:
        binascii.unhexlify(text)
        return True
    except (binascii.Error, ValueError):
        return False


def _write_json(path, value):
    tmp = str(path) + ".new"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# ============================================================================
# the advertisement (transport-neutral: the same record over DNS-SD or mDNS)
# ============================================================================

def service_txt(node_id, name, version, paired_with=()):
    """The TXT key/values that ride with the SRV record.

    NO ADDRESS IS IN HERE. The port is in SRV because the OS puts it there, and
    the address is whatever the packet came from -- a node that stated its own
    IP would be stating the one fact it is least qualified to know (which of
    its interfaces the listener reached it on) and would be wrong on every
    multi-homed machine.
    """
    return {
        "esotericos": str(PROTOCOL),
        "node": str(node_id),
        "name": str(name or ""),
        "version": str(version or ""),
        "paired": ",".join(str(p) for p in (paired_with or ())),
    }


def instance_name(name, node_id):
    """The DNS-SD instance label: a human name plus enough key to disambiguate.

    Two laptops given the same name by their owner are a normal thing to
    own. The first
    eight hex of the key makes the browse list readable without making the
    label load-bearing -- the full id in TXT is what anything actually keys on.
    """
    label = str(name or "").strip() or "EsotericOS"
    label = label.replace(".", " ").strip() or "EsotericOS"
    return f"{label} [{str(node_id)[:8]}]"


def parse_advert(txt, port, address=None, self_id=None, now=None):
    """A peer record from an advertisement, or None if it is not one of ours.

    This is the ONLY place an advertisement becomes a peer, so it is the only
    place that has to be suspicious. Anything on a multicast group is written
    by someone else; garbage, a truncated packet, another application's service
    and a replay of our own advertisement all arrive here identically.
    """
    if not isinstance(txt, dict):
        return None
    try:
        if int(str(txt.get("esotericos", ""))) != PROTOCOL:
            return None
    except (TypeError, ValueError):
        return None
    node_id = str(txt.get("node", "")).strip().lower()
    if len(node_id) != NODE_KEY_BYTES * 2 or not _is_hex(node_id):
        return None
    if self_id and node_id == str(self_id).lower():
        return None            # our own advertisement, echoed back to us
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 1 <= port <= 65535:
        return None
    paired = [p for p in str(txt.get("paired", "")).split(",") if p]
    return {
        "id": node_id,
        "name": str(txt.get("name", "")) or node_id[:8],
        "version": str(txt.get("version", "")),
        "port": port,
        "address": str(address or ""),
        "paired_with": paired,
        "seen": float(now if now is not None else time.time()),
    }


class PeerTable:
    """Who is on this network right now. Keyed by node id, never by address.

    A peer's ADDRESS is a property of the peer, not its name: the same laptop
    on wifi and then on ethernet is one node with one key that moved, and the
    table records the move instead of growing a second row. That is the whole
    reason identity is not positional.
    """

    def __init__(self, ttl=PEER_TTL):
        self.ttl = float(ttl)
        self._peers = {}
        self._lock = threading.Lock()

    def see(self, peer):
        """Record (or refresh) one peer. Returns True when it is newly here."""
        if not peer or not peer.get("id"):
            return False
        with self._lock:
            fresh = peer["id"] not in self._peers
            self._peers[peer["id"]] = dict(peer)
            return fresh

    def forget(self, node_id):
        with self._lock:
            return self._peers.pop(str(node_id), None) is not None

    def expire(self, now=None):
        """Drop everything not heard from within the TTL; returns their ids."""
        now = float(now if now is not None else time.time())
        with self._lock:
            gone = [nid for nid, peer in self._peers.items()
                    if now - float(peer.get("seen", 0)) > self.ttl]
            for nid in gone:
                self._peers.pop(nid, None)
        return gone

    def live(self, now=None):
        """Every peer still inside the TTL, newest name first seen order."""
        now = float(now if now is not None else time.time())
        with self._lock:
            return [dict(p) for p in self._peers.values()
                    if now - float(p.get("seen", 0)) <= self.ttl]

    def get(self, node_id):
        with self._lock:
            peer = self._peers.get(str(node_id))
            return dict(peer) if peer else None

    def __len__(self):
        with self._lock:
            return len(self._peers)


# ============================================================================
# pairing: a code both humans read, and a secret neither of them types
# ============================================================================

def _ordered(node_a, node_b, nonce_a, nonce_b):
    """The two sides in one canonical order, so both compute the same bytes.

    Sorted by NODE ID, not by who dialled: initiator and responder must derive
    an identical code, and "who connected first" is the one fact the two sides
    disagree about.
    """
    pairs = sorted(((str(node_a), _as_bytes(nonce_a)),
                    (str(node_b), _as_bytes(nonce_b))), key=lambda p: p[0])
    key = ("".join(p[0] for p in pairs)).encode("utf-8")
    nonces = pairs[0][1] + pairs[1][1]
    return key, nonces


def _as_bytes(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return binascii.unhexlify(str(value))


def pairing_code(node_a, node_b, nonce_a, nonce_b):
    """The six digits shown on BOTH screens. Symmetric by construction.

    HMAC-SHA256 over the two fresh nonces, keyed by the two node ids sorted.
    Each side contributes 32 bytes it generated this second, so neither side
    can steer the code and a replayed handshake produces a different one.

    Six digits is the security budget of a number a human compares across a
    room: an attacker who is on the LAN, has hijacked the advertisement, and
    is racing a 60-second window still has to guess it, and gets one attempt.
    """
    key, nonces = _ordered(node_a, node_b, nonce_a, nonce_b)
    digest = hmac.new(key, nonces, hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:3], 'big') % 1000000:06d}"


def shared_secret(node_a, node_b, nonce_a, nonce_b):
    """The long-term key the two nodes sign every later message with.

    DOMAIN-SEPARATED from the code deliberately. The code is three bytes of a
    digest over these same inputs and it is displayed on two screens and said
    out loud; if the secret were the rest of that digest, showing the code
    would be leaking key material. A different message means the code tells an
    observer nothing about the secret.
    """
    key, nonces = _ordered(node_a, node_b, nonce_a, nonce_b)
    return hmac.new(key, b"esotericos-pair-secret\x00" + nonces,
                    hashlib.sha256).hexdigest()


def new_nonce():
    return binascii.hexlify(os.urandom(NONCE_BYTES)).decode("ascii")


def sign(secret, payload):
    """Hex HMAC-SHA256 of a message under a pairing's shared secret."""
    return hmac.new(_as_bytes(secret), _message_bytes(payload),
                    hashlib.sha256).hexdigest()


def verify(secret, payload, signature):
    """CONSTANT-TIME check, the same way the clipboard relay does it.

    compare_digest over bytes, never `==` over str: a plain comparison leaks
    how many leading characters were right through its own timing, and the str
    form raises on non-ASCII input, which turns a tampered message into an
    exception instead of a rejection.
    """
    try:
        expected = sign(secret, payload)
    except (binascii.Error, ValueError, TypeError):
        return False
    return hmac.compare_digest(expected.encode("ascii"),
                               str(signature or "").encode("utf-8",
                                                           "replace"))


def _message_bytes(payload):
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


# ============================================================================
# the peer store
# ============================================================================

def load_peers(path):
    """{node_id: {id, name, secret, paired_at}}. Never raises."""
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    peers = {}
    for node_id, record in loaded.items():
        if not isinstance(record, dict) or not record.get("secret"):
            continue
        peers[str(node_id)] = {
            "id": str(node_id),
            "name": str(record.get("name") or node_id[:8]),
            "secret": str(record.get("secret")),
            "paired_at": str(record.get("paired_at") or ""),
        }
    return peers


def save_peers(path, peers):
    return _write_json(path, {k: dict(v) for k, v in (peers or {}).items()})


def remember_peer(path, node_id, name, secret):
    peers = load_peers(path)
    peers[str(node_id)] = {
        "id": str(node_id),
        "name": str(name or str(node_id)[:8]),
        "secret": str(secret),
        "paired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_peers(path, peers)
    return peers


def forget_peer(path, node_id):
    peers = load_peers(path)
    existed = peers.pop(str(node_id), None) is not None
    save_peers(path, peers)
    return existed


# ============================================================================
# the desk model: a paired node is a DEVICE
# ============================================================================

# A node's lane is TCP to a peer EsotericOS -- not a radio, not a daemon port
# we allocated. `lane` says which, and it is the field the input row (v3.122)
# will branch on.
LANE_LAN = "lan"
KIND_NODE = "node"


def node_device(config, node_id, name, live_monitors=None):
    """A paired node as a device, with ONE PLACEHOLDER SCREEN.

    NOT a device with zero displays, and the reason is mechanical rather than
    aesthetic: `normalize_config` skips any device whose display list is empty
    (`if not displays: continue`), so a zero-display node would be silently
    deleted from the config on the very next load -- pairing would appear to
    work and then quietly undo itself. Giving it one rectangle labelled with
    the node's name means every existing invariant (unique display ids, port
    allocation, portal geometry, the canvas's hit-testing) holds unchanged and
    not one existing test moves.

    The placeholder is REPLACED, not added to, when desk federation lands in
    v3.124 and the peer's real screens arrive: `placeholder: True` on the
    display is how that row will know it may throw this one away.
    """
    from openspan_targets import new_device
    device = new_device(config, name=name, live_monitors=live_monitors)
    device["id"] = f"node-{str(node_id)[:12]}"
    device["kind"] = KIND_NODE
    device["lane"] = LANE_LAN
    device["node_id"] = str(node_id)
    displays = device.get("displays") or []
    for index, display in enumerate(displays):
        display["id"] = f"{device['id']}-{index + 1}"
        display["name"] = str(name or "node")
        display["placeholder"] = True
    return device


def node_device_id(node_id):
    return f"node-{str(node_id)[:12]}"


# ============================================================================
# the firewall, without a netsh line for a human to retype
# ============================================================================

# THE RULE IS A PROGRAM RULE, NOT A PORT RULE, and it has to be: the service
# port is handed out by the OS and is different every launch, so a port rule
# would be stale the moment the app restarted. Allowing the executable covers
# the advertised port whatever it turns out to be, and covers mDNS too.
#
# NOTHING HERE RUNS IT. install\make-portable.ps1 ships bake-in.ps1, which adds
# the rule once when the user runs it elevated, and the app's own in-window
# "Allow EsotericOS through the firewall" action runs exactly this command --
# on a click, which is the consent. It is never run behind the user's back.
FIREWALL_RULE_NAME = "EsotericOS"


def firewall_commands(exe_path, rule_name=FIREWALL_RULE_NAME):
    """The (inbound, outbound) netsh commands that let this program talk.

    Scoped to the PRIVATE profile: a LAN desk is a home/work network, and a
    node has no business accepting connections on a coffee-shop wifi.
    """
    exe = str(exe_path)
    return [
        f'netsh advfirewall firewall add rule name="{rule_name}" '
        f'dir=in action=allow program="{exe}" enable=yes profile=private',
        f'netsh advfirewall firewall add rule name="{rule_name}" '
        f'dir=out action=allow program="{exe}" enable=yes profile=private',
    ]


def firewall_explanation(exe_path, rule_name=FIREWALL_RULE_NAME):
    """What the Console says when inbound looks blocked. Program, never port."""
    return (
        "Windows Firewall is refusing inbound connections to this node. The "
        f"rule needed allows the PROGRAM {os.path.basename(str(exe_path))} — "
        "not a port number: this node's service port is assigned by the OS and "
        "is different every launch, so a port rule would be wrong by the next "
        "restart. Use “Allow EsotericOS through the firewall” below (it asks "
        "for elevation and runs the rule), or run bake-in.ps1 once as "
        "administrator."
    )


# ============================================================================
# --- the network edge -------------------------------------------------------
# Everything below here touches a socket or the OS. It is deliberately thin,
# and every class in it is swappable for a fake in the tests.
# ============================================================================

DISCOVERY_OS = "os-dnssd"
# windns.h: the asynchronous DNS APIs report "started, ask the
# callback" with this, alongside the more familiar ERROR_IO_PENDING.
DNS_REQUEST_PENDING = 9506
DISCOVERY_MDNS = "mdns-fallback"


# ---- minimal RFC 6762 / RFC 1035 wire format (the fallback path only) ------

def _encode_name(name):
    out = b""
    for label in str(name).strip(".").split("."):
        raw = label.encode("utf-8")[:63]
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _decode_name(data, offset, depth=0):
    """Read a DNS name, following compression pointers. Returns (name, offset).

    The pointer chase is bounded. A packet from the network can point a label
    at itself, and an unbounded follow is an infinite loop inside a receive
    thread -- which is a hang, not a crash, and therefore the kind that gets
    diagnosed as "the app stopped seeing peers".
    """
    labels = []
    jumped = False
    end = offset
    while depth < 16:
        if offset >= len(data):
            raise ValueError("name runs past the packet")
        length = data[offset]
        if length == 0:
            offset += 1
            if not jumped:
                end = offset
            return ".".join(labels), end
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated compression pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                end = offset + 2
            if pointer >= len(data):
                raise ValueError("compression pointer out of range")
            offset = pointer
            jumped = True
            depth += 1
            continue
        offset += 1
        if offset + length > len(data):
            raise ValueError("truncated label")
        labels.append(data[offset:offset + length].decode("utf-8", "replace"))
        offset += length
    raise ValueError("compression pointer loop")


def encode_txt(txt):
    """A TXT rdata blob: length-prefixed key=value strings."""
    out = b""
    for key, value in sorted((txt or {}).items()):
        item = f"{key}={value}".encode("utf-8")[:255]
        out += bytes([len(item)]) + item
    return out or b"\x00"


def decode_txt(blob):
    """TXT rdata (or a list of strings) back to a dict. Never raises."""
    txt = {}
    if isinstance(blob, (list, tuple)):
        items = [str(s) for s in blob]
    else:
        items, offset = [], 0
        blob = bytes(blob or b"")
        while offset < len(blob):
            length = blob[offset]
            offset += 1
            if length == 0:
                continue
            items.append(blob[offset:offset + length].decode("utf-8",
                                                             "replace"))
            offset += length
    for item in items:
        if "=" in item:
            key, _, value = item.partition("=")
            txt[key] = value
    return txt


def build_announce(instance, service, port, txt, host=None, ttl=120):
    """One mDNS response carrying PTR + SRV + TXT for this node.

    No A record: the receiver takes the address off the packet it just read,
    which is the only address that is definitely reachable from where it is
    sitting. See parse_advert -- a node stating its own IP is the fault this
    avoids.
    """
    service = str(service)
    full = f"{instance}.{service}"
    host = str(host or f"{instance}.local").replace(" ", "-")
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, 3, 0, 0)
    ptr = (_encode_name(service) + struct.pack("!HHI", 12, 0x0001, ttl))
    ptr_rd = _encode_name(full)
    ptr += struct.pack("!H", len(ptr_rd)) + ptr_rd
    srv_rd = struct.pack("!HHH", 0, 0, int(port)) + _encode_name(host)
    srv = (_encode_name(full) + struct.pack("!HHI", 33, 0x8001, ttl)
           + struct.pack("!H", len(srv_rd)) + srv_rd)
    txt_rd = encode_txt(txt)
    txt_rr = (_encode_name(full) + struct.pack("!HHI", 16, 0x8001, ttl)
              + struct.pack("!H", len(txt_rd)) + txt_rd)
    return header + ptr + srv + txt_rr


def parse_announce(data, service=SERVICE_TYPE):
    """{instance: {"port": n, "txt": {...}}} from an mDNS packet.

    Returns {} for anything that is not a well-formed response about `service`
    -- every other application's mDNS traffic, a truncated datagram, and pure
    garbage all land here, and none of them may raise into the receive loop.
    """
    found = {}
    try:
        data = bytes(data or b"")
        if len(data) < 12:
            return {}
        _, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        if not flags & 0x8000:
            return {}                      # a query, not an answer
        offset = 12
        for _ in range(qd):
            _, offset = _decode_name(data, offset)
            offset += 4
        suffix = "." + str(service).strip(".")
        for _ in range(an + ns + ar):
            name, offset = _decode_name(data, offset)
            if offset + 10 > len(data):
                break
            rtype, _rclass, _ttl, rdlen = struct.unpack(
                "!HHIH", data[offset:offset + 10])
            offset += 10
            rdata = data[offset:offset + rdlen]
            if len(rdata) < rdlen:
                break
            offset += rdlen
            if not name.endswith(suffix):
                continue
            instance = name[:-len(suffix)]
            if not instance:
                continue
            slot = found.setdefault(instance, {"port": 0, "txt": {}})
            if rtype == 33 and len(rdata) >= 6:            # SRV
                slot["port"] = struct.unpack("!H", rdata[4:6])[0]
            elif rtype == 16:                              # TXT
                slot["txt"] = decode_txt(rdata)
    except (ValueError, struct.error, IndexError):
        return {}
    return {k: v for k, v in found.items() if v.get("txt")}


def build_query(service=SERVICE_TYPE):
    """A PTR question for the service type -- what a fallback node asks."""
    return (struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
            + _encode_name(service) + struct.pack("!HH", 12, 0x0001))


# ---- discovery edge A: the OS's own DNS-SD --------------------------------

class OsDnsSd:
    """Windows' DNS-SD, through dnsapi.dll. Present since Windows 10 1809.

    Registering here means the node is discoverable by anything that speaks
    DNS-SD -- including Bonjour on a Mac or an iPad -- with no protocol of ours
    on the wire. The OS owns the announcements, the refresh, and the goodbye.
    """

    def __init__(self):
        import ctypes
        self._ctypes = ctypes
        self._dll = ctypes.WinDLL("dnsapi.dll")
        for export in ("DnsServiceRegister", "DnsServiceDeRegister",
                       "DnsServiceBrowse", "DnsServiceBrowseCancel",
                       "DnsServiceConstructInstance", "DnsServiceFreeInstance"):
            getattr(self._dll, export)      # AttributeError => not available
        self._instance = None
        self._registered = False
        self._browse_handle = None
        self._callbacks = []                # ctypes thunks MUST outlive the call
        self._on_peer = None
        self._service = SERVICE_TYPE

    name = DISCOVERY_OS

    @staticmethod
    def available():
        try:
            OsDnsSd()
            return True
        except (OSError, AttributeError):
            return False

    # -- structures ------------------------------------------------------
    def _structs(self):
        """The three windns.h structures, BUILT ONCE AND CACHED.

        The cache is not an optimisation. ctypes types are compared by
        IDENTITY, so a second call to this method used to mint a second,
        unrelated DNS_SERVICE_INSTANCE class -- and assigning the pointer we
        registered with into a request typed against the new class raised
        TypeError. That surfaced as deregister() quietly returning False and
        the node's advertisement outliving the process: peers went on seeing a
        machine that was no longer listening.
        """
        cached = getattr(self, "_struct_cache", None)
        if cached is not None:
            return cached
        ctypes = self._ctypes
        from ctypes import wintypes as wt

        class DNS_SERVICE_INSTANCE(ctypes.Structure):
            _fields_ = [("pszInstanceName", ctypes.c_wchar_p),
                        ("pszHostName", ctypes.c_wchar_p),
                        ("ip4Address", ctypes.POINTER(wt.DWORD)),
                        ("ip6Address", ctypes.c_void_p),
                        ("wPort", wt.WORD), ("wPriority", wt.WORD),
                        ("wWeight", wt.WORD), ("dwPropertyCount", wt.DWORD),
                        ("keys", ctypes.POINTER(ctypes.c_wchar_p)),
                        ("values", ctypes.POINTER(ctypes.c_wchar_p)),
                        ("dwInterfaceIndex", wt.DWORD)]

        class DNS_SERVICE_REGISTER_REQUEST(ctypes.Structure):
            _fields_ = [("Version", wt.ULONG), ("InterfaceIndex", wt.ULONG),
                        ("pServiceInstance",
                         ctypes.POINTER(DNS_SERVICE_INSTANCE)),
                        ("pRegisterCompletionCallback", ctypes.c_void_p),
                        ("pQueryContext", ctypes.c_void_p),
                        ("hCredentials", ctypes.c_void_p),
                        ("unicastEnabled", wt.BOOL)]

        class DNS_SERVICE_BROWSE_REQUEST(ctypes.Structure):
            _fields_ = [("Version", wt.ULONG), ("InterfaceIndex", wt.ULONG),
                        ("QueryName", ctypes.c_wchar_p),
                        ("pBrowseCallback", ctypes.c_void_p),
                        ("pQueryContext", ctypes.c_void_p)]

        self._struct_cache = (DNS_SERVICE_INSTANCE,
                              DNS_SERVICE_REGISTER_REQUEST,
                              DNS_SERVICE_BROWSE_REQUEST)
        return self._struct_cache

    # -- register --------------------------------------------------------
    def register(self, instance, port, txt, service=SERVICE_TYPE):
        ctypes = self._ctypes
        INSTANCE, REGISTER, _ = self._structs()
        self._service = service
        full = f"{instance}.{str(service).strip('.')}.local".replace(
            ".local.local", ".local")
        keys = list(txt.keys())
        values = [str(txt[k]) for k in keys]
        arr = ctypes.c_wchar_p * max(1, len(keys))
        key_arr = arr(*[ctypes.c_wchar_p(k) for k in keys]) if keys else arr()
        val_arr = arr(*[ctypes.c_wchar_p(v) for v in values]) if keys else arr()
        self._dll.DnsServiceConstructInstance.restype = ctypes.POINTER(INSTANCE)
        host = f"{default_node_name()}.local".replace(" ", "-")
        inst = self._dll.DnsServiceConstructInstance(
            ctypes.c_wchar_p(full), ctypes.c_wchar_p(host), None, None,
            ctypes.c_ushort(int(port)), 0, 0, ctypes.c_ulong(len(keys)),
            key_arr, val_arr)
        if not inst:
            raise OSError("DnsServiceConstructInstance failed")
        self._instance = inst
        done = threading.Event()
        state = {"status": None}

        CB = ctypes.WINFUNCTYPE(None, ctypes.c_ulong, ctypes.c_void_p,
                                ctypes.POINTER(INSTANCE))

        def _complete(status, _ctx, _inst):
            state["status"] = int(status)
            done.set()

        thunk = CB(_complete)
        self._callbacks.append(thunk)      # never let this be collected
        request = REGISTER()
        request.Version = 1                # DNS_QUERY_REQUEST_VERSION1
        request.InterfaceIndex = 0
        request.pServiceInstance = inst
        request.pRegisterCompletionCallback = ctypes.cast(thunk,
                                                          ctypes.c_void_p)
        request.pQueryContext = None
        request.hCredentials = None
        request.unicastEnabled = False
        rc = self._dll.DnsServiceRegister(ctypes.byref(request), None)
        # THESE ARE SUCCESS, NOT FAILURE. The call is asynchronous: it returns
        # ERROR_IO_PENDING (997) or DNS_REQUEST_PENDING (9506) and the
        # completion callback carries the real status. Reading 9506 as an
        # error is how this first refused to register at all, on a machine
        # where the API works perfectly -- the edge reported "DNS-SD not
        # available" and fell back to mDNS for no reason.
        if rc not in (0, 997, DNS_REQUEST_PENDING):
            raise OSError(f"DnsServiceRegister failed ({rc})")
        done.wait(10.0)
        if state["status"] not in (None, 0):
            raise OSError(f"DNS-SD registration refused ({state['status']})")
        self._registered = True
        return True

    def deregister(self):
        """Take the advertisement down. ALWAYS called on exit.

        A registration that outlives the process leaves a node advertising a
        port nothing is listening on, and every peer on the network then shows
        a machine that cannot be paired with. The OS does clean up eventually;
        eventually is not while somebody is looking at the list.
        """
        if not self._registered or not self._instance:
            return False
        ctypes = self._ctypes
        INSTANCE, REGISTER, _ = self._structs()
        instance = self._instance
        try:
            done = threading.Event()
            CB = ctypes.WINFUNCTYPE(None, ctypes.c_ulong, ctypes.c_void_p,
                                    ctypes.POINTER(INSTANCE))
            thunk = CB(lambda status, ctx, inst: done.set())
            self._callbacks.append(thunk)
            request = REGISTER()
            request.Version = 1
            request.InterfaceIndex = 0
            request.pServiceInstance = instance
            request.pRegisterCompletionCallback = ctypes.cast(thunk,
                                                              ctypes.c_void_p)
            request.pQueryContext = None
            request.hCredentials = None
            request.unicastEnabled = False
            rc = self._dll.DnsServiceDeRegister(ctypes.byref(request), None)
            if rc in (0, 997, DNS_REQUEST_PENDING):
                # The de-registration is asynchronous like the registration.
                # Freeing the instance while the OS is still reading it is a
                # use-after-free in somebody else's thread, so wait first.
                done.wait(5.0)
            self._dll.DnsServiceFreeInstance(instance)
        except Exception:  # noqa: BLE001 -- shutdown must not raise
            return False
        finally:
            self._registered = False
            self._instance = None
        return True

    # -- browse ----------------------------------------------------------
    def browse(self, on_peer, service=SERVICE_TYPE):
        """Start browsing. `on_peer(instance, port, txt)` from an OS thread."""
        ctypes = self._ctypes
        INSTANCE, _, BROWSE = self._structs()
        self._on_peer = on_peer
        query = f"{str(service).strip('.')}.local".replace(".local.local",
                                                           ".local")

        CB = ctypes.WINFUNCTYPE(None, ctypes.c_ulong, ctypes.c_void_p,
                                ctypes.c_void_p)

        def _found(status, _ctx, records):
            # THIS RUNS ON AN OS THREAD. Nothing Tk, nothing that can raise:
            # an exception escaping a ctypes callback becomes a native fault,
            # which is the crash class this codebase has already paid for.
            try:
                if int(status) != 0 or not records:
                    return
                for instance, port, txt in self._walk_records(records,
                                                              service):
                    self._on_peer(instance, port, txt)
            except Exception:  # noqa: BLE001
                pass

        thunk = CB(_found)
        self._callbacks.append(thunk)
        request = BROWSE()
        request.Version = 1
        request.InterfaceIndex = 0
        request.QueryName = query
        request.pBrowseCallback = ctypes.cast(thunk, ctypes.c_void_p)
        request.pQueryContext = None
        handle = ctypes.c_void_p()
        rc = self._dll.DnsServiceBrowse(ctypes.byref(request),
                                        ctypes.byref(handle))
        if rc not in (0, 997, DNS_REQUEST_PENDING):
            raise OSError(f"DnsServiceBrowse failed ({rc})")
        self._browse_handle = handle
        return True

    def stop_browse(self):
        if self._browse_handle is None:
            return False
        try:
            self._dll.DnsServiceBrowseCancel(
                self._ctypes.byref(self._browse_handle))
        except Exception:  # noqa: BLE001
            return False
        finally:
            self._browse_handle = None
        return True

    def _walk_records(self, records, service):
        """Pull (instance, port, txt) out of a DNS_RECORD chain."""
        ctypes = self._ctypes
        from ctypes import wintypes as wt

        class DNS_RECORD(ctypes.Structure):
            pass

        class SRV_DATA(ctypes.Structure):
            _fields_ = [("pNameTarget", ctypes.c_wchar_p),
                        ("wPriority", wt.WORD), ("wWeight", wt.WORD),
                        ("wPort", wt.WORD), ("Pad", wt.WORD)]

        class TXT_DATA(ctypes.Structure):
            _fields_ = [("dwStringCount", wt.DWORD),
                        ("pStringArray", ctypes.c_wchar_p * 1)]

        class DATA(ctypes.Union):
            _fields_ = [("SRV", SRV_DATA), ("TXT", TXT_DATA),
                        ("raw", ctypes.c_byte * 64)]

        DNS_RECORD._fields_ = [("pNext", ctypes.POINTER(DNS_RECORD)),
                               ("pName", ctypes.c_wchar_p),
                               ("wType", wt.WORD), ("wDataLength", wt.WORD),
                               ("Flags", wt.DWORD), ("dwTtl", wt.DWORD),
                               ("dwReserved", wt.DWORD), ("Data", DATA)]

        suffix = "." + f"{str(service).strip('.')}.local".replace(
            ".local.local", ".local")
        node = ctypes.cast(records, ctypes.POINTER(DNS_RECORD))
        slots, guard = {}, 0
        while node and guard < 256:
            guard += 1
            record = node.contents
            name = str(record.pName or "")
            if name.endswith(suffix):
                instance = name[:-len(suffix)]
                slot = slots.setdefault(instance, {"port": 0, "txt": {}})
                if record.wType == 33:                       # SRV
                    slot["port"] = int(record.Data.SRV.wPort)
                elif record.wType == 16:                      # TXT
                    # pStringArray is declared [1] but is really dwStringCount
                    # long, laid out inline right after the count. Cast at its
                    # own address and index it; reading through the declared
                    # array would stop at the first string.
                    count = min(int(record.Data.TXT.dwStringCount), 64)
                    base = ctypes.cast(
                        ctypes.addressof(record.Data.TXT)
                        + TXT_DATA.pStringArray.offset,
                        ctypes.POINTER(ctypes.c_wchar_p))
                    slot["txt"] = decode_txt(
                        [base[i] for i in range(count) if base[i]])
            node = record.pNext
        return [(inst, s["port"], s["txt"]) for inst, s in slots.items()
                if s["txt"]]


# ---- discovery edge B: a minimal mDNS of our own --------------------------

class MdnsFallback:
    """RFC 6762 on 224.0.0.251:5353, for a Windows without the DNS-SD API.

    Not a general mDNS responder: it announces one service and reads answers
    about that same service. Everything else on the group is ignored, which is
    both correct and the only safe posture for a socket reading packets that
    anyone on the LAN can write.
    """

    name = DISCOVERY_MDNS

    def __init__(self, group=MDNS_GROUP, port=MDNS_PORT):
        self.group = group
        self.port = int(port)
        self._sock = None
        self._instance = None
        self._port_out = 0
        self._txt = {}
        self._service = SERVICE_TYPE
        self._on_peer = None
        self._stop = threading.Event()
        self._threads = []
        self.bind_error = ""

    def _open(self):
        if self._sock is not None:
            return self._sock
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                             socket.IPPROTO_UDP)
        # SO_REUSEADDR on a UDP MULTICAST socket is the documented way to share
        # a group with the OS's own responder, and is NOT the Windows TCP
        # hijack this codebase bans -- that ban is about accepting connections
        # on a port another process owns. Without it, binding 5353 fails on any
        # machine that already runs a Bonjour service.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", self.port))
            sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton(self.group) + socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        except OSError as exc:
            self.bind_error = str(exc)
            sock.close()
            raise
        sock.settimeout(1.0)
        self._sock = sock
        return sock

    def register(self, instance, port, txt, service=SERVICE_TYPE):
        self._instance, self._port_out = instance, int(port)
        self._txt, self._service = dict(txt), service
        self._open()
        thread = threading.Thread(target=self._announce_loop,
                                  name="esotericos-mdns-announce", daemon=True)
        thread.start()
        self._threads.append(thread)
        return True

    def deregister(self):
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        return True

    def browse(self, on_peer, service=SERVICE_TYPE):
        self._on_peer, self._service = on_peer, service
        self._open()
        thread = threading.Thread(target=self._read_loop,
                                  name="esotericos-mdns-read", daemon=True)
        thread.start()
        self._threads.append(thread)
        return True

    def stop_browse(self):
        self._stop.set()
        return True

    def _announce_loop(self):
        while not self._stop.is_set():
            self.announce_once()
            self._stop.wait(ANNOUNCE_INTERVAL)

    def announce_once(self):
        sock = self._sock
        if sock is None or not self._instance:
            return False
        try:
            packet = build_announce(self._instance, self._service,
                                    self._port_out, self._txt)
            sock.sendto(packet, (self.group, self.port))
            sock.sendto(build_query(self._service), (self.group, self.port))
            return True
        except OSError:
            return False

    def _read_loop(self):
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                data, addr = sock.recvfrom(RECV_MAX)
            except socket.timeout:
                continue
            except OSError:
                return
            for instance, slot in parse_announce(data, self._service).items():
                try:
                    self._on_peer(instance, slot["port"], slot["txt"],
                                  addr[0])
                except Exception:  # noqa: BLE001
                    pass


def make_discovery(prefer_os=True):
    """(edge, path_name). The OS's DNS-SD when it exists, otherwise mDNS."""
    if prefer_os:
        try:
            return OsDnsSd(), DISCOVERY_OS
        except (OSError, AttributeError):
            pass
    return MdnsFallback(), DISCOVERY_MDNS


# ---- the TCP service + the pairing state machine ---------------------------

class PairSession:
    """One in-flight pairing, from either side. PURE -- no socket in here.

    Both halves of a pairing run this same object, which is what makes the
    "both must confirm" rule testable without two machines: the test drives two
    sessions against each other exactly as two nodes do.
    """

    def __init__(self, self_id, peer_id, self_nonce=None, now=None):
        self.self_id = str(self_id)
        self.peer_id = str(peer_id)
        self.self_nonce = self_nonce or new_nonce()
        self.peer_nonce = None
        self.started = float(now if now is not None else time.time())
        self.we_confirmed = False
        self.they_confirmed = False
        self.state = "waiting"

    def offer(self):
        return {"op": "pair-hello", "node": self.self_id,
                "nonce": self.self_nonce, "proto": PROTOCOL}

    def accept(self, message):
        """Take the peer's hello. Returns the six-digit code, or None."""
        if not isinstance(message, dict) or message.get("op") != "pair-hello":
            return None
        try:
            if int(message.get("proto", 0)) != PROTOCOL:
                return None
        except (TypeError, ValueError):
            return None
        nonce = str(message.get("nonce") or "")
        if len(nonce) != NONCE_BYTES * 2 or not _is_hex(nonce):
            return None
        node = str(message.get("node") or "")
        if not node or node == self.self_id:
            return None
        self.peer_id, self.peer_nonce = node, nonce
        self.state = "code"
        return self.code

    @property
    def code(self):
        if not self.peer_nonce:
            return None
        return pairing_code(self.self_id, self.peer_id,
                            self.self_nonce, self.peer_nonce)

    @property
    def secret(self):
        if not self.peer_nonce:
            return None
        return shared_secret(self.self_id, self.peer_id,
                             self.self_nonce, self.peer_nonce)

    def expired(self, now=None):
        now = float(now if now is not None else time.time())
        return now - self.started > PAIR_WINDOW

    def confirm_here(self):
        """The human on THIS desk pressed “Same code”."""
        if self.peer_nonce:
            self.we_confirmed = True
        return self.done

    def confirm_message(self):
        """The signed proof this side sends. Signing it with the secret proves
        we derived the same nonces; the code proves a human read them."""
        return {"op": "pair-confirm", "node": self.self_id,
                "sig": sign(self.secret, f"confirm:{self.peer_id}")}

    def accept_confirm(self, message):
        """The peer's confirmation, verified. Constant-time, tamper-detecting."""
        if not isinstance(message, dict) or not self.peer_nonce:
            return False
        if message.get("op") != "pair-confirm":
            return False
        if not verify(self.secret, f"confirm:{self.self_id}",
                      message.get("sig")):
            return False
        self.they_confirmed = True
        return True

    @property
    def done(self):
        """BOTH desks, or nothing. One-sided confirmation pairs neither."""
        return bool(self.we_confirmed and self.they_confirmed
                    and self.peer_nonce)


def encode_line(message):
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def decode_line(raw):
    try:
        value = json.loads(bytes(raw).decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


class NodeService:
    """This node on the LAN: a TCP listener on an OS-assigned port, an
    advertisement carrying that port, and a table of who else is out there.

    Every socket operation happens on a worker thread. Callers hand in `notify`
    (a plain callable taking (kind, text)) and `on_change` (called when the
    peer table or a pairing moves); the app marshals both onto the Tk thread
    itself -- nothing here knows what Tk is, which is exactly why the
    reentrancy law cannot be broken from in here.
    """

    def __init__(self, identity, peers_path, version="", discovery=None,
                 notify=None, on_change=None, service=SERVICE_TYPE):
        self.identity = dict(identity)
        self.peers_path = peers_path
        self.version = str(version)
        self.service = service
        self.notify = notify or (lambda kind, text: None)
        self.on_change = on_change or (lambda: None)
        self.peers = load_peers(peers_path)
        self.table = PeerTable()
        self.sessions = {}                 # peer node id -> PairSession
        self.port = 0                      # assigned by the OS in start()
        self.discovery = discovery
        self.discovery_path = ""
        self.inbound_blocked = False
        self._server = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads = []

    # -- lifecycle -------------------------------------------------------
    def start(self):
        """Bind, advertise, browse. Returns True when the node is on the LAN."""
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # NO SO_REUSEADDR. On Windows that lets a second process bind a
            # port this one already owns and silently steal connections; the
            # port is ephemeral anyway, so there is nothing to reuse.
            server.bind(("", 0))           # 0 = "OS, you choose"
            server.listen(8)
            server.settimeout(1.0)
        except OSError as exc:
            self.notify("err", f"LAN node could not open a listener: {exc}")
            return False
        self._server = server
        self.port = int(server.getsockname()[1])
        thread = threading.Thread(target=self._accept_loop,
                                  name="esotericos-node-accept", daemon=True)
        thread.start()
        self._threads.append(thread)

        if self.discovery is None:
            self.discovery, self.discovery_path = make_discovery()
        else:
            self.discovery_path = getattr(self.discovery, "name", "custom")
        label = instance_name(self.identity.get("name"), self.identity["id"])
        try:
            self.discovery.register(label, self.port, self._txt(), self.service)
            self.discovery.browse(self._on_advert, self.service)
        except OSError as exc:
            self.inbound_blocked = True
            self.notify("err", f"LAN discovery could not start: {exc}")
            return False
        self.notify("event",
                    f"LAN node “{self.identity.get('name')}” is on this "
                    f"network. Discovery: {self.discovery_path}. Service port "
                    f"{self.port} (assigned by the OS this launch; it changes "
                    "every start, which is why the firewall rule allows the "
                    "program and not a port).")
        thread = threading.Thread(target=self._reap_loop,
                                  name="esotericos-node-reap", daemon=True)
        thread.start()
        self._threads.append(thread)
        return True

    def stop(self):
        self._stop.set()
        try:
            if self.discovery is not None:
                self.discovery.stop_browse()
                self.discovery.deregister()
        except Exception:  # noqa: BLE001
            pass
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        return True

    def _txt(self):
        return service_txt(self.identity["id"], self.identity.get("name"),
                           self.version, sorted(self.peers))

    def readvertise(self):
        """Re-publish after a rename or a pairing change. Best effort."""
        if self.discovery is None or not self.port:
            return False
        label = instance_name(self.identity.get("name"), self.identity["id"])
        try:
            self.discovery.deregister()
            self.discovery.register(label, self.port, self._txt(), self.service)
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- discovery -------------------------------------------------------
    def _on_advert(self, instance, port, txt, address=None):
        peer = parse_advert(txt, port, address=address,
                            self_id=self.identity["id"])
        if peer is None:
            return
        peer["instance"] = str(instance)
        if self.table.see(peer):
            self.on_change()

    def _reap_loop(self):
        while not self._stop.is_set():
            if self.table.expire():
                self.on_change()
            for peer_id, session in list(self.sessions.items()):
                if session.expired():
                    self.sessions.pop(peer_id, None)
                    self.notify("event", "pairing timed out — the code is only "
                                         f"good for {int(PAIR_WINDOW)}s.")
                    self.on_change()
            self._stop.wait(1.0)

    def unpaired(self):
        """Peers on the LAN we have not paired with. What the pane lists."""
        return [p for p in self.table.live() if p["id"] not in self.peers]

    def paired_live(self):
        return [p for p in self.table.live() if p["id"] in self.peers]

    # -- pairing ---------------------------------------------------------
    def begin_pair(self, peer_id):
        """Dial a peer and exchange nonces. Worker thread; never blocks the UI."""
        peer = self.table.get(peer_id)
        if peer is None:
            self.notify("err", "that node is no longer on the network.")
            return False
        thread = threading.Thread(target=self._pair_worker, args=(peer,),
                                  name="esotericos-pair", daemon=True)
        thread.start()
        self._threads.append(thread)
        return True

    def _pair_worker(self, peer):
        session = PairSession(self.identity["id"], peer["id"])
        try:
            # The ADVERTISED port, always. Never a number from this file.
            conn = socket.create_connection((peer["address"], peer["port"]),
                                            timeout=10.0)
        except OSError as exc:
            self.inbound_blocked = True
            self.notify("err", f"could not reach “{peer['name']}”: {exc}. "
                               "If that node just started, its firewall may be "
                               "refusing inbound connections to the program.")
            self.on_change()
            return
        try:
            frames = Frames(conn)
            conn.sendall(encode_line(session.offer()))
            if session.accept(frames.next(timeout=10.0)) is None:
                self.notify("err", "that node did not answer as an "
                                   "EsotericOS node.")
                return
            with self._lock:
                self.sessions[peer["id"]] = session
            self.on_change()
            self.notify("event",
                        f"pairing with “{peer['name']}”: the code is "
                        f"{session.code}. It must match on BOTH screens, and "
                        "both of you press “Same code”.")
            self._finish_pair(session, conn, frames, peer["name"])
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _accept_loop(self):
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(target=self._serve, args=(conn, addr),
                                      name="esotericos-node-conn", daemon=True)
            thread.start()

    def _serve(self, conn, addr):
        try:
            frames = Frames(conn)
            message = frames.next(timeout=10.0)
            if not message:
                return
            if message.get("op") == "pair-hello":
                self._serve_pair(conn, addr, frames, message)
                return
            # Every non-pairing message is signed under a pairing's secret.
            # An unsigned one from an unknown node is exactly what a scan of
            # the LAN looks like, and it gets nothing.
            node = str(message.get("node") or "")
            record = self.peers.get(node)
            if not record or not verify(record["secret"],
                                        message.get("body", {}),
                                        message.get("sig")):
                conn.sendall(encode_line({"op": "denied"}))
                return
            conn.sendall(encode_line({"op": "ok"}))
        except (OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _serve_pair(self, conn, addr, frames, message):
        session = PairSession(self.identity["id"],
                              str(message.get("node") or ""))
        if session.accept(message) is None:
            return
        known = self.table.get(session.peer_id)
        name = known["name"] if known else session.peer_id[:8]
        with self._lock:
            self.sessions[session.peer_id] = session
        conn.sendall(encode_line(session.offer()))
        self.on_change()
        self.notify("event",
                    f"“{name}” wants to pair. The code is {session.code}. It "
                    "must match on BOTH screens, and both of you press "
                    "“Same code”.")
        self._finish_pair(session, conn, frames, name)

    def _finish_pair(self, session, conn, frames, name):
        """Wait for BOTH confirmations, then store the peer. Worker thread."""
        deadline = session.started + PAIR_WINDOW
        sent = False
        while time.time() < deadline and not self._stop.is_set():
            if session.we_confirmed and not sent:
                try:
                    conn.sendall(encode_line(session.confirm_message()))
                    sent = True
                except OSError:
                    break
            if not session.they_confirmed:
                message = frames.next(timeout=0.25)
                if message:
                    session.accept_confirm(message)
                elif frames.closed and sent:
                    break
            if session.done:
                self.peers = remember_peer(self.peers_path, session.peer_id,
                                           name, session.secret)
                with self._lock:
                    self.sessions.pop(session.peer_id, None)
                self.readvertise()
                self.notify("ok", f"paired with “{name}”. It is a device on "
                                  "this desk now; its lane is TCP over the "
                                  "LAN.")
                self.on_change()
                return True
            time.sleep(0.05)
        with self._lock:
            self.sessions.pop(session.peer_id, None)
        if not session.done:
            self.notify("event", f"pairing with “{name}” did not complete — "
                                 "both desks have to press “Same code” inside "
                                 f"{int(PAIR_WINDOW)}s.")
        self.on_change()
        return False

    def confirm(self, peer_id):
        """“Same code” was pressed HERE. The other desk still has to press it."""
        session = self.sessions.get(str(peer_id))
        if session is None:
            return False
        session.confirm_here()
        self.on_change()
        return True

    def unpair(self, peer_id):
        existed = forget_peer(self.peers_path, peer_id)
        self.peers = load_peers(self.peers_path)
        self.readvertise()
        self.on_change()
        return existed

    def status_counts(self):
        return {"seen": len(self.table.live()), "paired": len(self.peers)}


class Frames:
    """Newline-delimited JSON off one socket, BUFFERED.

    The buffer is the whole point. The pairing loop polls with a short timeout
    while it waits for a human to press a button, so a read landing in the
    middle of a frame is not an edge case -- it is what normally happens. An
    unbuffered reader throws those bytes away and the frame never arrives,
    which presents as a pairing that hangs until it times out.

    Bounded, so a peer cannot exhaust this process by never sending a newline.
    """

    def __init__(self, conn, limit=65536):
        self.conn = conn
        self.buf = bytearray()
        self.limit = int(limit)
        self.closed = False

    def next(self, timeout=None):
        """The next decoded frame, or None if one is not available yet."""
        while True:
            index = self.buf.find(b"\n")
            if index >= 0:
                line = bytes(self.buf[:index])
                del self.buf[:index + 1]
                return decode_line(line)
            if self.closed or len(self.buf) > self.limit:
                return None
            try:
                self.conn.settimeout(timeout)
                chunk = self.conn.recv(4096)
            except socket.timeout:
                return None
            except OSError:
                self.closed = True
                return None
            if not chunk:
                self.closed = True
                return None
            self.buf += chunk
