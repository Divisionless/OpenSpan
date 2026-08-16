"""LAN nodes (v3.121): identity, discovery, pairing, and what must not be ours.

The load-bearing claim of this row is that NOTHING IS HARDCODED. The service
port is the OS's, the discovery channel is the OS's (or, failing that, an RFC's
multicast group), identity is random, names are labels, and addresses are
learned. Most of this file exists to hold that claim to account -- a promise
about hardcoding is exactly the kind that decays into a constant nobody noticed.

The one thing that cannot be tested here is two real machines. What IS tested
end to end is two Node services in this process, over 127.0.0.1, through the
real TCP edge: they exchange nonces, derive the same six digits, and neither
pairs until both confirm.
"""

import ast
import os
import pathlib
import re
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lan_nodes as L  # noqa: E402
from openspan_targets import (  # noqa: E402
    layout_surfaces, new_device, normalize_config,
)

failures = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)
        if detail:
            print("      " + detail)


HERE = pathlib.Path(__file__).resolve().parent
SRC = (HERE / "lan_nodes.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
# Addresses in this file are RFC 5737 DOCUMENTATION addresses
# (192.0.2.0/24 TEST-NET-1, 198.51.100.0/24 TEST-NET-2), never a real
# subnet. A 10.x or 192.168.x literal in a test reads like somebody's
# actual network, and this repo is published.
MONITORS = [{"name": r"\\.\DISPLAY1", "x": 0, "y": 0, "w": 1920, "h": 1080,
             "primary": True}]


# =========================================================================
# 1. IDENTITY: random, generated once, and never derived from the machine.
# =========================================================================
key_a, key_b = L.new_node_key(), L.new_node_key()
check("a node key is 32 random bytes as hex",
      len(key_a) == 64 and key_a != key_b and int(key_a, 16) >= 0)

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "node.json")
    first = L.load_identity(path, name="Bench")
    second = L.load_identity(path)
    third = L.load_identity(path, name="Something else")
    check("the node key is generated ONCE and is stable across loads",
          first["id"] == second["id"] == third["id"] and len(first["id"]) == 64)
    check("a name given later never re-keys the node",
          third["id"] == first["id"])
    renamed = L.rename_node(path, "Studio laptop")
    check("renaming changes the LABEL and not the identity",
          renamed["id"] == first["id"] and renamed["name"] == "Studio laptop")
    check("the rename is persisted",
          L.load_identity(path)["name"] == "Studio laptop")

    # A corrupt or truncated file must mint a new key rather than raise -- a
    # node that cannot start because its own id file is damaged is a node that
    # has to be repaired by hand on a machine nobody is sitting at.
    pathlib.Path(path).write_text("{not json", encoding="utf-8")
    healed = L.load_identity(path)
    check("a corrupt node.json is replaced, never raised",
          len(healed["id"]) == 64)
    pathlib.Path(path).write_text('{"id": "abc", "name": "x"}',
                                  encoding="utf-8")
    check("a short/invalid id is replaced too",
          len(L.load_identity(path)["id"]) == 64)

check("the default name is READ from the machine at runtime, not stored",
      isinstance(L.default_node_name(), str) and L.default_node_name() != "")


# =========================================================================
# 2. THE ADVERTISEMENT: round-trips, and rejects everything else.
# =========================================================================
me = L.new_node_key()
them = L.new_node_key()
txt = L.service_txt(them, "Other laptop", "0.3.0", paired_with=[me])
peer = L.parse_advert(txt, 51234, address="192.0.2.9", self_id=me, now=1000.0)
check("advert round-trip: id, name, version and port survive",
      peer and peer["id"] == them and peer["name"] == "Other laptop"
      and peer["version"] == "0.3.0" and peer["port"] == 51234)
check("the address is taken from the PACKET, never from the payload",
      peer["address"] == "192.0.2.9" and "address" not in txt
      and not any("192.0.2.9" in str(v) for v in txt.values()))
check("who the peer is already paired with round-trips",
      peer["paired_with"] == [me])

check("our OWN advertisement, echoed back, is not a peer",
      L.parse_advert(L.service_txt(me, "Me", "0.3.0"), 5, self_id=me) is None)
check("a foreign service on the same channel is rejected",
      L.parse_advert({"other": "1", "node": them}, 5, self_id=me) is None)
check("a wrong protocol generation is rejected, never guessed at",
      L.parse_advert(dict(txt, esotericos="99"), 5, self_id=me) is None)
check("garbage in the id is rejected",
      L.parse_advert(dict(txt, node="zzzz"), 5, self_id=me) is None)
check("a truncated id is rejected",
      L.parse_advert(dict(txt, node=them[:20]), 5, self_id=me) is None)
for bad in (0, -1, 70000, "not a port", None):
    if L.parse_advert(txt, bad, self_id=me) is not None:
        check(f"an impossible port ({bad!r}) is rejected", False)
        break
else:
    check("an impossible port is rejected", True)
check("a non-dict payload is rejected, never unpacked",
      L.parse_advert(b"\x00\x01garbage", 5, self_id=me) is None
      and L.parse_advert(None, 5, self_id=me) is None)


# =========================================================================
# 3. THE mDNS FALLBACK WIRE FORMAT (RFC 6762 -- a standard's numbers).
# =========================================================================
packet = L.build_announce("Studio [abcd1234]", L.SERVICE_TYPE, 49876, txt)
found = L.parse_announce(packet, L.SERVICE_TYPE)
check("mDNS announce round-trips instance, port and TXT",
      "Studio [abcd1234]" in found
      and found["Studio [abcd1234]"]["port"] == 49876
      and found["Studio [abcd1234]"]["txt"]["node"] == them)
check("...and the parsed TXT still becomes a peer",
      L.parse_advert(found["Studio [abcd1234]"]["txt"],
                     found["Studio [abcd1234]"]["port"], self_id=me) is not None)

for junk in (b"", b"\x00", b"\xff" * 40, os.urandom(200),
             packet[:len(packet) // 2], b"A" * 3000):
    if L.parse_announce(junk, L.SERVICE_TYPE) != {}:
        check("garbage on the multicast group yields nothing", False, repr(junk[:16]))
        break
else:
    check("garbage on the multicast group yields nothing, and never raises", True)

check("a QUERY is not mistaken for an answer",
      L.parse_announce(L.build_query(L.SERVICE_TYPE), L.SERVICE_TYPE) == {})
check("another application's service on the group is ignored",
      L.parse_announce(L.build_announce("Printer", "_ipp._tcp.local", 631,
                                        {"a": "b"}), L.SERVICE_TYPE) == {})

# A compression pointer that points at itself is a HANG, not a crash, and a
# hang inside a receive thread presents as "the app stopped seeing peers".
loop = bytes([0, 0]) + b"\x84\x00" + b"\x00\x00\x00\x01\x00\x00\x00\x00" \
       + b"\xc0\x0c" + b"\x00\x0c\x00\x01\x00\x00\x00\x78\x00\x02\xc0\x0c"
done = threading.Event()
threading.Thread(target=lambda: (L.parse_announce(loop), done.set()),
                 daemon=True).start()
check("a self-referential compression pointer terminates instead of hanging",
      done.wait(3.0))


# =========================================================================
# 4. THE PEER TABLE: keyed by node id, expires, and follows a moving address.
# =========================================================================
table = L.PeerTable(ttl=10.0)
table.see(L.parse_advert(txt, 5000, address="192.0.2.9", self_id=me, now=100.0))
check("a peer is live inside the TTL", len(table.live(now=105.0)) == 1)
check("...and gone after it", table.live(now=111.0) == [])
check("expire() reports exactly who it dropped",
      table.expire(now=111.0) == [them] and len(table) == 0)

table.see(L.parse_advert(txt, 5000, address="192.0.2.9", self_id=me, now=100.0))
table.see(L.parse_advert(txt, 6001, address="198.51.100.4", self_id=me,
                         now=102.0))
live = table.live(now=103.0)
check("the same node on a new address is ONE peer that moved, not two",
      len(live) == 1 and live[0]["address"] == "198.51.100.4"
      and live[0]["port"] == 6001)


# =========================================================================
# 5. PAIRING: symmetric six digits, a separate secret, both-or-nothing.
# =========================================================================
node_a, node_b = L.new_node_key(), L.new_node_key()
nonce_a, nonce_b = L.new_nonce(), L.new_nonce()
code_a = L.pairing_code(node_a, node_b, nonce_a, nonce_b)
code_b = L.pairing_code(node_b, node_a, nonce_b, nonce_a)
check("both desks derive the SAME code, whoever dialled",
      code_a == code_b)
check("the code is exactly six digits", len(code_a) == 6 and code_a.isdigit())
check("a different nonce gives a different code -- no replay",
      L.pairing_code(node_a, node_b, nonce_a, L.new_nonce()) != code_a)

secret_a = L.shared_secret(node_a, node_b, nonce_a, nonce_b)
secret_b = L.shared_secret(node_b, node_a, nonce_b, nonce_a)
check("both sides derive the same shared secret", secret_a == secret_b)
check("the secret is NOT the rest of the code's digest -- showing the code on "
      "two screens leaks no key material",
      not secret_a.startswith(f"{int(code_a):x}") and len(secret_a) == 64)

sig = L.sign(secret_a, {"op": "hello", "n": 1})
check("sign/verify round-trips", L.verify(secret_b, {"op": "hello", "n": 1}, sig))
check("a tampered BODY is detected",
      not L.verify(secret_b, {"op": "hello", "n": 2}, sig))
check("a tampered SIGNATURE is detected",
      not L.verify(secret_b, {"op": "hello", "n": 1}, sig[:-1] + "0"))
check("a wrong secret is detected",
      not L.verify(L.shared_secret(node_a, node_b, nonce_a, L.new_nonce()),
                   {"op": "hello", "n": 1}, sig))
check("a missing signature is a rejection, not a crash",
      not L.verify(secret_a, {"op": "hello"}, None)
      and not L.verify(secret_a, {"op": "hello"}, ""))
check("a non-ASCII signature is rejected rather than raising",
      not L.verify(secret_a, {"op": "hello"}, "ü" * 64))

verify_src = ast.get_source_segment(SRC, next(
    n for n in ast.walk(TREE)
    if isinstance(n, ast.FunctionDef) and n.name == "verify"))
check("verify uses compare_digest over BYTES, never ==",
      "compare_digest" in verify_src and ".encode(" in verify_src)

# -- both must confirm ---------------------------------------------------
sess_a = L.PairSession(node_a, node_b)
sess_b = L.PairSession(node_b, node_a)
check("the responder derives a code from the initiator's hello",
      sess_b.accept(sess_a.offer()) is not None)
check("...and the initiator derives the identical one from the reply",
      sess_a.accept(sess_b.offer()) == sess_b.code)
check("nobody is paired yet", not sess_a.done and not sess_b.done)
sess_a.confirm_here()
check("ONE desk confirming pairs nothing", not sess_a.done)
check("...even after its confirmation is accepted by the other side",
      sess_b.accept_confirm(sess_a.confirm_message()) and not sess_b.done)
sess_b.confirm_here()
check("the second desk's own press is still required", sess_b.done)
sess_a.accept_confirm(sess_b.confirm_message())
check("both confirmed: both sides are done", sess_a.done and sess_b.done)

forged = dict(sess_b.confirm_message())
forged["sig"] = "0" * 64
check("a forged confirmation is refused",
      not L.PairSession(node_a, node_b).accept_confirm(forged))
stale = L.PairSession(node_a, node_b, now=time.time() - L.PAIR_WINDOW - 5)
check("a pairing older than the window has expired", stale.expired())
check("a hello with a bad nonce is refused",
      L.PairSession(node_a, node_b).accept(
          {"op": "pair-hello", "node": node_b, "nonce": "zz", "proto": 1})
      is None)
check("a node cannot pair with itself",
      L.PairSession(node_a, node_b).accept(
          {"op": "pair-hello", "node": node_a, "nonce": L.new_nonce(),
           "proto": 1}) is None)


# =========================================================================
# 6. peers.json round-trip.
# =========================================================================
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "peers.json")
    check("an absent peer store is {}, never an exception", L.load_peers(path) == {})
    L.remember_peer(path, node_b, "Studio laptop", secret_a)
    peers = L.load_peers(path)
    check("a stored peer round-trips id, name and secret",
          peers[node_b]["secret"] == secret_a
          and peers[node_b]["name"] == "Studio laptop")
    L.remember_peer(path, node_a, "Bench", secret_b)
    check("a second pairing does not displace the first",
          set(L.load_peers(path)) == {node_a, node_b})
    check("unpairing removes exactly one", L.forget_peer(path, node_a)
          and set(L.load_peers(path)) == {node_b})
    check("unpairing something absent is False, not a crash",
          not L.forget_peer(path, "nope"))
    pathlib.Path(path).write_text('{"x": {"name": "no secret"}}',
                                  encoding="utf-8")
    check("a record with no secret is not a pairing", L.load_peers(path) == {})
    pathlib.Path(path).write_text("[]", encoding="utf-8")
    check("a peers.json of the wrong shape is {}", L.load_peers(path) == {})


# =========================================================================
# 7. A NODE IS A DEVICE -- and survives normalize_config.
#
# THE DECISION, RECORDED: a node device carries ONE PLACEHOLDER DISPLAY rather
# than zero. normalize_config contains `if not displays: continue`, so a
# zero-display device is silently DELETED on the next load: pairing would
# appear to work and then quietly undo itself, with nothing said. The
# placeholder keeps every existing invariant intact and moves no existing test.
# =========================================================================
config = normalize_config({}, MONITORS)
device = L.node_device(config, node_b, "Studio laptop", live_monitors=MONITORS)
config["devices"].append(device)
check("a node device is kind=node on lane=lan, keyed by the peer's NODE ID",
      device["kind"] == "node" and device["lane"] == "lan"
      and device["node_id"] == node_b)
check("its id is derived from the node key, not from a position",
      device["id"] == L.node_device_id(node_b) and node_b[:12] in device["id"])
check("it has exactly one placeholder display, named after the node",
      len(device["displays"]) == 1
      and device["displays"][0]["placeholder"] is True
      and device["displays"][0]["name"] == "Studio laptop")

reloaded = normalize_config(config, MONITORS)
kept = [d for d in reloaded["devices"] if d["id"] == device["id"]]
check("normalize_config KEEPS a node device (the zero-display trap)",
      len(kept) == 1, "a zero-display device would have been dropped here")
check("...and preserves kind, lane and node_id across the round trip",
      kept and kept[0]["kind"] == "node" and kept[0]["lane"] == "lan"
      and kept[0]["node_id"] == node_b)
check("...and the placeholder flag survives, so federation can replace it",
      kept and kept[0]["displays"][0].get("placeholder") is True)
surfaces = layout_surfaces(reloaded)
check("layout_surfaces renders the node as one named surface",
      any(s["target"] == device["id"] for s in surfaces))
check("an ordinary device is untouched by any of this -- no kind, no lane",
      new_device(normalize_config({}, MONITORS)).get("kind") is None)

zero = dict(device, displays=[])
dropped = normalize_config({"monitors": MONITORS, "devices": [zero]}, MONITORS)
check("the trap is real: a node device with NO displays is dropped on load",
      dropped["devices"] == [],
      "this is why node_device ships a placeholder")


# =========================================================================
# 8. NOTHING IS HARDCODED.
# =========================================================================
consts = [n.value for n in ast.walk(TREE)
          if isinstance(n, ast.Constant) and isinstance(n.value, int)]
# The numbers this row was originally specified with, plus the ones already in
# use elsewhere in the app. Not one of them may appear: the service port is the
# OS's, and the discovery port is an RFC's.
TEMPTATIONS = {9955, 9956, 9957, 9958, 9959, 4010, 8080, 5000}
check("not one invented port number survives anywhere in the module",
      not (set(consts) & TEMPTATIONS),
      str(sorted(set(consts) & TEMPTATIONS)))
# 9506 is DNS_REQUEST_PENDING, a windns.h status code, and it is the ONLY
# 9000-range constant here -- named, so it cannot be mistaken for a port.
nine_k = sorted({c for c in consts if 9000 <= c <= 9999})
check("the one 9000-range constant is the named Windows status code",
      nine_k == [L.DNS_REQUEST_PENDING], str(nine_k))
check("the only port constant is the RFC 6762 mDNS one",
      L.MDNS_PORT == 5353 and L.MDNS_GROUP == "224.0.0.251")
check("the service type is a NAME, not a number", L.SERVICE_TYPE.endswith("._tcp.local"))

# Every IPv4 literal in the module must be one of the two documented standards
# addresses. A machine's address has no business in source.
addresses = set(re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", SRC))
check("no machine address is written into the source",
      addresses <= {"224.0.0.251", "0.0.0.0"}, str(sorted(addresses)))
check("no machine name is written into the source",
      not re.search(r"(?i)\b(DESKTOP-[A-Z0-9]+|LAPTOP-[A-Z0-9]+)\b", SRC))
this_machine = (os.environ.get("COMPUTERNAME", "") or socket.gethostname())
check("this build machine's own name is not in the source",
      this_machine.lower() not in SRC.lower())

# The identity must not be derivable from the machine. A key that came from a
# hostname or a MAC would make two clones one node. Asserted on the CALLS the
# function actually makes, not on the words in its docstring -- a prose check
# fails the moment somebody writes down what the code deliberately does not do.
key_fn = next(n for n in ast.walk(TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "new_node_key")
key_calls = {(getattr(c.func, "attr", None) or getattr(c.func, "id", None))
             for c in ast.walk(key_fn) if isinstance(c, ast.Call)}
check("the node key is made of os.urandom and nothing else",
      "urandom" in key_calls
      and not (key_calls & {"gethostname", "getnode", "uuid1", "uuid4",
                            "getlogin", "gethostbyname"}),
      str(sorted(c for c in key_calls if c)))


# =========================================================================
# 9. THE FIREWALL RULE ALLOWS A PROGRAM, NOT A PORT.
# =========================================================================
rules = L.firewall_commands(r"C:\Somewhere\EsotericOS.exe")
check("two rules: inbound and outbound", len(rules) == 2
      and "dir=in" in rules[0] and "dir=out" in rules[1])
check("both name the PROGRAM",
      all('program="C:\\Somewhere\\EsotericOS.exe"' in r for r in rules))
check("NEITHER names a port -- the service port changes every launch",
      not any(("localport" in r or "protocol=" in r) for r in rules))
check("scoped to the private profile, not to every network",
      all("profile=private" in r for r in rules))
explain = L.firewall_explanation(r"C:\Somewhere\EsotericOS.exe")
check("the explanation says program, and says WHY it is not a port",
      "PROGRAM" in explain and "every launch" in explain
      and "localport" not in explain)


# =========================================================================
# 10. THREADING LAW: no socket call on a caller's thread; no Tk in here.
# =========================================================================
check("lan_nodes never imports or touches Tk",
      "Toplevel" not in SRC and "messagebox" not in SRC and "tkinter" not in SRC)

FUNCS = {n.name: n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}
NET_CALLS = ("create_connection", "sendall", "recv", "recvfrom", "sendto",
             "accept", "bind", "listen")


def touches_network(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in NET_CALLS:
                return True
    return False


# The functions allowed to block on a socket, and the ONE reason each is safe:
# every one of them is a thread target or is called only from one.
WORKERS = {"_pair_worker", "_accept_loop", "_serve", "_serve_pair",
           "_finish_pair", "_read_loop", "_announce_loop", "announce_once",
           "next", "start", "_open", "register", "browse", "deregister",
           "stop", "_reap_loop"}
offenders = sorted(name for name, node in FUNCS.items()
                   if touches_network(node) and name not in WORKERS)
check("no unexpected function blocks on a socket", not offenders, str(offenders))

thread_targets = set()
for node in ast.walk(TREE):
    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Thread":
        for kw in node.keywords:
            if kw.arg == "target":
                value = kw.value
                if isinstance(value, ast.Attribute):
                    thread_targets.add(value.attr)
                elif isinstance(value, ast.Name):
                    thread_targets.add(value.id)
for name in ("_pair_worker", "_accept_loop", "_reap_loop", "_read_loop",
             "_announce_loop"):
    check(f"{name} only ever runs on a worker thread", name in thread_targets)

begin = FUNCS["begin_pair"]
check("begin_pair -- what the Pair button calls -- starts a thread and does "
      "no I/O itself",
      not touches_network(begin) and "_pair_worker" in thread_targets)

# ...and the UI side of it, in the app.
APP = (HERE / "openspan.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP)
APP_FUNCS = {n.name: n for n in ast.walk(APP_TREE)
             if isinstance(n, ast.FunctionDef)}
for name in ("_start_lan_node", "_refresh_node_rows", "_node_row", "_pair_node",
             "_confirm_node", "_unpair_node", "_allow_firewall",
             "_sync_node_devices"):
    check(f"App.{name} exists", name in APP_FUNCS)
    if name not in APP_FUNCS:
        continue
    body = ast.get_source_segment(APP, APP_FUNCS[name]) or ""
    check(f"App.{name} raises no Toplevel and no native dialog",
          "Toplevel" not in body and "messagebox" not in body)
check("the node's on_change reaches Tk only through the ui() marshal",
      "self.ui(self._refresh_node_rows)" in APP)
check("starting the node happens on a worker thread, not in __init__'s body",
      "esotericos-node-start" in APP)


# =========================================================================
# 11. THE EPHEMERAL PORT, AND THE LOOPBACK TWO-NODE PAIRING.
#
# Two real NodeService objects in this process, over 127.0.0.1, through the
# real TCP edge. Discovery is stood in for -- multicast in a test suite would
# depend on the machine's network -- but every byte of the pairing is real.
# =========================================================================
class LoopbackBus:
    """An in-process stand-in for the multicast group. Carries what the real
    edges carry and nothing more: an instance label, a port, and TXT."""

    def __init__(self):
        self.records = {}
        self.listeners = []

    def publish(self, instance, port, txt):
        self.records[instance] = (int(port), dict(txt))
        for fn in list(self.listeners):
            for name, (p, t) in list(self.records.items()):
                fn(name, p, t, "127.0.0.1")

    def withdraw(self, instance):
        self.records.pop(instance, None)


class LoopbackDiscovery:
    name = "loopback"

    def __init__(self, bus):
        self.bus = bus
        self.instance = None

    def register(self, instance, port, txt, service=L.SERVICE_TYPE):
        self.instance = instance
        self.bus.publish(instance, port, txt)
        return True

    def browse(self, on_peer, service=L.SERVICE_TYPE):
        self.bus.listeners.append(on_peer)
        for name, (port, txt) in list(self.bus.records.items()):
            on_peer(name, port, txt, "127.0.0.1")
        return True

    def deregister(self):
        if self.instance:
            self.bus.withdraw(self.instance)
        return True

    def stop_browse(self):
        return True


def wait_for(predicate, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


with tempfile.TemporaryDirectory() as d:
    bus = LoopbackBus()
    id_a = L.load_identity(os.path.join(d, "a-node.json"), name="Desk A")
    id_b = L.load_identity(os.path.join(d, "b-node.json"), name="Desk B")
    peers_a = os.path.join(d, "a-peers.json")
    peers_b = os.path.join(d, "b-peers.json")
    quiet = lambda kind, text: None  # noqa: E731
    node_1 = L.NodeService(id_a, peers_a, version="test",
                           discovery=LoopbackDiscovery(bus), notify=quiet)
    node_2 = L.NodeService(id_b, peers_b, version="test",
                           discovery=LoopbackDiscovery(bus), notify=quiet)
    try:
        check("node A binds and advertises", node_1.start())
        check("node B binds and advertises", node_2.start())
        check("each node got a real port from the OS",
              node_1.port > 0 and node_2.port > 0)
        check("the two ports differ -- nothing was reserved for either",
              node_1.port != node_2.port)
        check("the port is not one of the numbers we used to hardcode",
              node_1.port not in (9955, 9956, 9957, 9958, 4010))
        check("the ADVERTISED port is the one the OS assigned",
              bus.records[L.instance_name("Desk A", id_a["id"])][0]
              == node_1.port)

        check("A sees B on the network",
              wait_for(lambda: any(p["id"] == id_b["id"]
                                   for p in node_1.unpaired())))
        check("B sees A", wait_for(lambda: any(p["id"] == id_a["id"]
                                               for p in node_2.unpaired())))
        seen = node_1.table.get(id_b["id"])
        check("B's advertised port is what A will dial -- learned, not assumed",
              seen and seen["port"] == node_2.port
              and seen["address"] == "127.0.0.1")

        # -- the pairing itself, over a real TCP connection ------------
        node_1.begin_pair(id_b["id"])
        check("both desks reach a pairing session",
              wait_for(lambda: id_b["id"] in node_1.sessions
                       and id_a["id"] in node_2.sessions))
        s1 = node_1.sessions.get(id_b["id"])
        s2 = node_2.sessions.get(id_a["id"])
        check("both desks show the SAME six digits",
              s1 and s2 and s1.code == s2.code and len(s1.code) == 6)
        check("the nonces really were exchanged over the wire",
              s1.peer_nonce == s2.self_nonce and s2.peer_nonce == s1.self_nonce)

        node_1.confirm(id_b["id"])
        time.sleep(0.6)
        check("ONE desk confirming pairs nobody",
              L.load_peers(peers_a) == {} and L.load_peers(peers_b) == {})

        node_2.confirm(id_a["id"])
        check("both confirmed: A stored B",
              wait_for(lambda: id_b["id"] in L.load_peers(peers_a)))
        check("both confirmed: B stored A",
              wait_for(lambda: id_a["id"] in L.load_peers(peers_b)))
        stored_a = L.load_peers(peers_a)[id_b["id"]]
        stored_b = L.load_peers(peers_b)[id_a["id"]]
        check("and they hold the SAME shared secret, derived not transmitted",
              stored_a["secret"] == stored_b["secret"]
              and len(stored_a["secret"]) == 64)
        check("the secret never appeared in any advertisement",
              not any(stored_a["secret"] in str(t)
                      for _p, t in bus.records.values()))
        check("status counts report the pairing",
              node_1.status_counts()["paired"] == 1
              and node_1.status_counts()["seen"] >= 1)

        # -- a signed message is accepted; a tampered one is not -------
        body = {"op": "ping"}
        conn = socket.create_connection(("127.0.0.1", node_2.port), timeout=5)
        conn.sendall(L.encode_line({"node": id_a["id"], "body": body,
                                    "sig": L.sign(stored_a["secret"], body)}))
        check("a correctly signed message is accepted",
              (L.Frames(conn).next(timeout=5) or {}).get("op") == "ok")
        conn.close()
        conn = socket.create_connection(("127.0.0.1", node_2.port), timeout=5)
        conn.sendall(L.encode_line({"node": id_a["id"], "body": {"op": "evil"},
                                    "sig": L.sign(stored_a["secret"], body)}))
        check("a tampered message is denied",
              (L.Frames(conn).next(timeout=5) or {}).get("op") == "denied")
        conn.close()
        conn = socket.create_connection(("127.0.0.1", node_2.port), timeout=5)
        conn.sendall(L.encode_line({"node": L.new_node_key(), "body": body,
                                    "sig": "0" * 64}))
        check("an unknown node gets nothing",
              (L.Frames(conn).next(timeout=5) or {}).get("op") == "denied")
        conn.close()

        check("unpairing forgets the secret",
              node_1.unpair(id_b["id"]) and L.load_peers(peers_a) == {})
    finally:
        node_1.stop()
        node_2.stop()


# =========================================================================
# 12. THE DISCOVERY PATH IS CHOSEN, REPORTED, AND HAS A FALLBACK.
# =========================================================================
edge, path = L.make_discovery(prefer_os=False)
check("with the OS API refused, the fallback is chosen and named",
      path == L.DISCOVERY_MDNS and isinstance(edge, L.MdnsFallback))
check("the fallback binds the RFC group and port, not ours",
      edge.group == "224.0.0.251" and edge.port == 5353)

edge, path = L.make_discovery(prefer_os=True)
check("preferring the OS yields one of exactly two known paths",
      path in (L.DISCOVERY_OS, L.DISCOVERY_MDNS))
print(f"      (this machine resolves to: {path})")
check("the OS edge advertises its availability truthfully",
      L.OsDnsSd.available() == (path == L.DISCOVERY_OS))


if failures:
    print(f"\nRESULT: {len(failures)} FAILED")
    raise SystemExit(1)
print("\nRESULT: ALL LAN NODE TESTS PASSED")
