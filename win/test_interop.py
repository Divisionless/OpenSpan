"""Coexistence checks: the EsotericOS input-capture lease, the clipboard
privacy markers, and the keymap guard's teeth.

Creates no Tk root, installs no hook, sends nothing on any socket, and never
touches the real clipboard or the real lease name. Every runtime lease check
runs against a PRIVATE mutex named `Local\\OpenSpan.InteropTest.<pid>.<n>`, so
running this file while EsotericOS is live cannot suspend it even for an
instant.

Why so much of this is STRUCTURAL rather than behavioural. The failure this
guards against does not show up in a run: a Windows mutex is owned by the
THREAD that acquired it, and `ReleaseMutex` from any other thread returns FALSE
and leaves the mutex HELD -- with no holder death, so nothing self-heals and
EsotericOS is suspended indefinitely. A test that merely exercises enter/leave
on one thread passes happily while the shipped code releases from four. So the
ownership rule is asserted against the source: no thread but the lease thread
may name a mutex primitive at all.

The same reasoning applies to the keymap. EsotericOS shipped a preset that
rewrote Ctrl+A to Home and its suite ASSERTED that behaviour, so the bug shipped
with a passing test certifying it. Nothing here records what the code currently
does; everything states what it may not do.

Cited throughout: D:\\OpenSpan\\docs\\INTEROP.md (ours) and
D:\\EsotericOS\\docs\\INTEROP.md (theirs, read-only).
"""

import ast
import contextlib
import ctypes
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import openspan_portal as P            # noqa: E402
import openspan as A                   # noqa: E402
import test_keymap_safety as G         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL_PY = os.path.join(HERE, "openspan_portal.py")
APP_PY = os.path.join(HERE, "openspan.py")
THEIR_SPEC = r"D:\EsotericOS\docs\INTEROP.md"
OUR_SPEC = os.path.join(os.path.dirname(HERE), "docs", "INTEROP.md")

# Transcribed from D:\EsotericOS\docs\INTEROP.md, "The stand-down contract".
# A literal, not an import: if their spec changes, this line must be changed by
# a person who read the change.
PUBLISHED_NAME = "Local\\EsotericOS.InputCaptureLease"

# The Win32 primitives that can take or give a mutex. Any of these appearing
# outside the lease class is the ship-blocker.
MUTEX_PRIMITIVES = {"CreateMutexW", "WaitForSingleObject", "ReleaseMutex"}

FAILURES = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (
        "" if condition or not detail else f"\n       {detail}"))
    if not condition:
        FAILURES.append(name)


# ---- tiny AST toolkit -------------------------------------------------

def parse(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return source, tree, parents


def dotted(node):
    """'self.lease.acquire' for an Attribute chain; '' for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif parts:
        parts.append("?")
    return ".".join(reversed(parts))


def chain(node, parents):
    out = []
    cur = parents.get(node)
    while cur is not None:
        out.append(cur)
        cur = parents.get(cur)
    return out


def enclosing(node, parents, types):
    for cur in chain(node, parents):
        if isinstance(cur, types):
            return cur
    return None


def calls(tree, leaf):
    """Every Call whose function name's last component is `leaf`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name.split(".")[-1] == leaf:
                yield node, name


def catches_everything(handler):
    if handler.type is None:
        return True
    return dotted(handler.type).split(".")[-1] in ("Exception", "BaseException")


def is_guarded(node, parents, stop=None):
    """True if some enclosing Try (not crossing `stop`) catches Exception.

    A `finally`-only Try does not count: it re-raises, and a raise inside a
    low-level hook procedure is a dead keyboard.
    """
    previous = node
    for cur in chain(node, parents):
        if stop is not None and cur is stop:
            return False
        if isinstance(cur, ast.Try) and previous in cur.body \
                and any(catches_everything(h) for h in cur.handlers):
            return True
        previous = cur
    return False


def transitively_guarded(tree, parents):
    """Names of functions whose EVERY call site sits under a try/except.

    A win32 call inside one of these is guarded even with no local try. That is
    exactly the shape the lease uses on purpose: _take() and _give() are called
    only from _run(), inside _run's try/except Exception, so a fault there is
    logged and the thread keeps running. Demanding a local try in every method
    would be asserting a style, not the property that matters.
    """
    sites = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            sites.setdefault(dotted(node.func).split(".")[-1], []).append(node)
    return {leaf for leaf, nodes in sites.items()
            if nodes and all(is_guarded(n, parents) for n in nodes)}


PORTAL_SRC, PORTAL_TREE, PORTAL_PARENTS = parse(PORTAL_PY)
APP_SRC, APP_TREE, APP_PARENTS = parse(APP_PY)
PORTAL_GUARDED = transitively_guarded(PORTAL_TREE, PORTAL_PARENTS)

PORTAL_FUNCS = {}
for _cls in ast.walk(PORTAL_TREE):
    if isinstance(_cls, ast.ClassDef):
        for _fn in _cls.body:
            if isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                PORTAL_FUNCS[(_cls.name, _fn.name)] = _fn
LEASE_CLASS = next(n for n in ast.walk(PORTAL_TREE)
                   if isinstance(n, ast.ClassDef)
                   and n.name == "InputCaptureLease")


# ---- 1. the name --------------------------------------------------------

def test_name():
    print("\n-- 1. the published mutex name --")
    check("LEASE_NAME is byte-for-byte the published name",
          P.LEASE_NAME == PUBLISHED_NAME
          and P.LEASE_NAME.encode("utf-8") == PUBLISHED_NAME.encode("utf-8"),
          f"{P.LEASE_NAME!r} != {PUBLISHED_NAME!r}")
    # `Local\` scopes the object to this logon session, which is the point: a
    # capture in one user session must not silence EsotericOS in another.
    check("the name is session-scoped (Local\\)",
          P.LEASE_NAME.startswith("Local\\"), P.LEASE_NAME)
    check("no stray whitespace or case drift",
          P.LEASE_NAME == P.LEASE_NAME.strip()
          and "EsotericOS.InputCaptureLease" in P.LEASE_NAME)
    # Their spec is the source of truth for their half; check we still agree
    # with the file rather than only with this file's memory of it.
    if os.path.exists(THEIR_SPEC):
        with open(THEIR_SPEC, encoding="utf-8") as handle:
            their = handle.read()
        check("the name appears verbatim in their published spec",
              PUBLISHED_NAME in their, THEIR_SPEC)
    else:
        print(f"       (skipped: {THEIR_SPEC} not present)")
    # A mutex, not an event. An abandoned event fails silently and permanently.
    check("the object is created with CreateMutexW, not CreateEventW",
          "CreateMutexW" in PORTAL_SRC and "CreateEventW" not in PORTAL_SRC)
    check("the acquire timeout is 1000 ms and never zero",
          P.LEASE_TIMEOUT_MS == 1000)
    check("WAIT_ABANDONED is 0x80 and WAIT_TIMEOUT is 0x102",
          P._WAIT_ABANDONED == 0x80 and P._WAIT_TIMEOUT == 0x102)
    # Named-object convention, agreed with EsotericOS: their prefix is
    # EsotericOS., ours is OpenSpan., and nothing unprefixed is claimed.
    # Single-line string constants only: a docstring that MENTIONS the name is
    # documentation, not a claim on a kernel object.
    ours = [n.value for n in ast.walk(PORTAL_TREE)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "\n" not in n.value
            and ("Local\\" in n.value or "Global\\" in n.value)]
    stray = [n for n in ours
             if not n.startswith(("Local\\EsotericOS.", "Local\\OpenSpan.",
                                  "Global\\OpenSpan."))]
    check("every named object is prefixed EsotericOS. or OpenSpan.",
          not stray, f"{stray}")


# ---- 2. thread ownership: the ship-blocker ------------------------------

def test_thread_ownership():
    print("\n-- 2. one thread owns the handle --")
    inside = set(ast.walk(LEASE_CLASS))
    owner_methods = {"_create", "_take", "_give", "_close", "_run"}

    for leaf in sorted(MUTEX_PRIMITIVES):
        sites = list(calls(PORTAL_TREE, leaf))
        outside = [f"line {n.lineno}: {name}"
                   for n, name in sites if n not in inside]
        check(f"{leaf} is called only inside InputCaptureLease",
              sites and not outside, f"{outside}")
        wrong = []
        for node, name in sites:
            fn = enclosing(node, PORTAL_PARENTS, ast.FunctionDef)
            if fn is None or fn.name not in owner_methods:
                wrong.append(f"line {node.lineno} in "
                             f"{fn.name if fn else '<module>'}")
        check(f"{leaf} is called only from a lease-thread method",
              not wrong, f"{wrong}")

    # The lease-thread-only methods must be unreachable from anywhere else.
    # _run is the thread body; everything else is called from it.
    for leaf in ("_take", "_give", "_close"):
        bad = []
        for node, _name in calls(PORTAL_TREE, leaf):
            fn = enclosing(node, PORTAL_PARENTS, ast.FunctionDef)
            if fn is None or fn.name != "_run":
                bad.append(f"line {node.lineno} in "
                           f"{fn.name if fn else '<module>'}")
        check(f"{leaf}() is called only from _run (the lease thread)",
              not bad, f"{bad}")

    # _run must be used exactly once, as a Thread target, in start().
    targets = []
    for node, _name in calls(PORTAL_TREE, "Thread"):
        for kw in node.keywords:
            if kw.arg == "target" and dotted(kw.value).endswith("_run"):
                fn = enclosing(node, PORTAL_PARENTS, ast.FunctionDef)
                targets.append(fn.name if fn else "<module>")
    check("_run is started as a thread exactly once, from start()",
          targets == ["start"], f"{targets}")

    # The Portal side may reach the lease ONLY through the two guarded helpers.
    allowed = {"__init__", "_lease_acquire", "_lease_release", "run"}
    touchers = set()
    for node in ast.walk(PORTAL_TREE):
        if isinstance(node, ast.Attribute) and node.attr == "lease" \
                and isinstance(node.value, ast.Name) and node.value.id == "self":
            fn = enclosing(node, PORTAL_PARENTS, ast.FunctionDef)
            touchers.add(fn.name if fn else "<module>")
    check("self.lease is referenced only by __init__/run and the two helpers",
          touchers <= allowed, f"{sorted(touchers - allowed)}")

    # enter()/leave() are the only capture-state transitions, and each posts.
    enter_fn = PORTAL_FUNCS[("Portal", "enter")]
    leave_fn = PORTAL_FUNCS[("Portal", "leave")]
    check("enter() posts an acquire",
          any(dotted(n.func).endswith("_lease_acquire")
              for n, _ in calls(enter_fn, "_lease_acquire")))
    check("leave() posts a release",
          any(dotted(n.func).endswith("_lease_release")
              for n, _ in calls(leave_fn, "_lease_release")))
    for fn, label in ((enter_fn, "enter"), (leave_fn, "leave")):
        prims = [n.lineno for leaf in MUTEX_PRIMITIVES
                 for n, _ in calls(fn, leaf)]
        check(f"{label}() names no mutex primitive itself", not prims,
              f"lines {prims}")

    # The threads that actually call leave(): a hook proc, the sender, and the
    # status watcher -- none of which acquired anything. This is the exact
    # shape that would have shipped a permanently-held mutex.
    hook_and_watcher = ["_kbd_proc", "_mouse_proc", "_route_motion",
                        "_jump_nearest", "_status_watcher", "send",
                        "_enter_nearest", "_switch_target", "sender"]
    offenders = []
    for name in hook_and_watcher:
        fn = PORTAL_FUNCS.get(("Portal", name))
        if fn is None:
            offenders.append(f"{name} MISSING -- this list is stale")
            continue
        for leaf in MUTEX_PRIMITIVES:
            offenders += [f"{name}:{n.lineno}" for n, _ in calls(fn, leaf)]
    check("no hook, watcher or sender path names a mutex primitive",
          not offenders, f"{offenders}")

    # Runtime proof, in case the source ever grows an indirection the AST
    # cannot see: drive the lease from many threads, watch which thread does
    # the work.
    name = fr"Local\OpenSpan.InteropTest.{os.getpid()}.threads"

    class Recording(P.InputCaptureLease):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.op_threads = []

        def _create(self):
            self.op_threads.append(threading.get_ident())
            return super()._create()

        def _take(self, handle):
            self.op_threads.append(threading.get_ident())
            return super()._take(handle)

        def _give(self, handle):
            self.op_threads.append(threading.get_ident())
            return super()._give(handle)

    lease = Recording(name=name)
    lease._log = lambda _message: None
    lease.start(wait=3.0)
    workers = []
    for i in range(6):
        want = (i % 2 == 0)
        workers.append(threading.Thread(
            target=(lease.acquire if want else lease.release)))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    deadline = time.time() + 3.0
    while lease._q.unfinished_tasks and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    caller_ids = {w.ident for w in workers} | {threading.get_ident()}
    check("every handle operation ran on the lease thread",
          set(lease.op_threads) == {lease._thread.ident},
          f"ops on {sorted(set(lease.op_threads))}, "
          f"lease thread {lease._thread.ident}")
    check("no calling thread ever touched the handle",
          not (set(lease.op_threads) & caller_ids),
          f"{sorted(set(lease.op_threads) & caller_ids)}")
    lease.stop()


# ---- 3. every ctypes call is guarded ------------------------------------

def test_guarded():
    print("\n-- 3. no lease fault can reach the input path --")
    # The module-level prototyping of _k32 must not be able to fail an import.
    proto = [n for n in ast.walk(PORTAL_TREE)
             if isinstance(n, ast.Call) and dotted(n.func).endswith("WinDLL")]
    check("the private kernel32 binding is created inside a try",
          proto and all(is_guarded(n, PORTAL_PARENTS) for n in proto))

    for leaf in sorted(MUTEX_PRIMITIVES | {"CloseHandle"}):
        sites = list(calls(PORTAL_TREE, leaf))
        unguarded = []
        for node, _name in sites:
            if is_guarded(node, PORTAL_PARENTS):
                continue                       # a try/except right here
            fn = enclosing(node, PORTAL_PARENTS, ast.FunctionDef)
            if fn is not None and fn.name in PORTAL_GUARDED:
                continue                       # every caller is guarded
            unguarded.append(node.lineno)
        check(f"every {leaf} call is guarded, locally or by its only caller",
              sites and not unguarded, f"lines {unguarded}")

    # The two methods a hook thread actually calls must not be able to raise
    # even if `self.lease` is something unexpected.
    for name in ("_lease_acquire", "_lease_release"):
        fn = PORTAL_FUNCS[("Portal", name)]
        body = [n for n in fn.body if not isinstance(n, ast.Expr)
                or not isinstance(n.value, ast.Constant)]
        check(f"{name}() is a bare try/except Exception and nothing else",
              len(body) == 1 and isinstance(body[0], ast.Try)
              and any(catches_everything(h) for h in body[0].handlers))

    for name in ("_post", "start", "_log"):
        fn = next(n for n in LEASE_CLASS.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)
                 and any(catches_everything(h) for h in n.handlers)]
        check(f"InputCaptureLease.{name}() catches Exception", bool(tries))

    # _run's dispatch is wrapped, so a fault in _take/_give cannot kill the
    # thread. A dead lease thread still reporting `held` would suspend
    # EsotericOS forever, which is worse than never having signalled at all.
    run_fn = next(n for n in LEASE_CLASS.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_run")
    dispatch = [n for n, _ in calls(run_fn, "_take")]
    check("_run wraps its dispatch in try/except Exception",
          dispatch and all(is_guarded(n, PORTAL_PARENTS) for n in dispatch))

    # A mutex that will not create must degrade to silence, not to an
    # exception. `Local\OpenSpan\A\B` is refused with ERROR_PATH_NOT_FOUND (3)
    # -- a name may not carry path separators past its namespace prefix.
    # Measured, not assumed: an over-length name is NOT refused (a 400-char
    # mutex name creates cleanly on this build of Windows), so length would
    # have been a test that quietly never fired.
    records = []
    broken = P.InputCaptureLease(name="Local\\OpenSpan\\NoSuch\\Namespace")
    broken._log = records.append
    broken.start(wait=3.0)
    broken.acquire()
    broken.release()
    time.sleep(0.3)
    check("a mutex that will not create is a silent no-op",
          broken.available is False and broken.held is False
          and broken._thread.is_alive(),
          f"available={broken.available} held={broken.held} "
          f"alive={broken._thread.is_alive()}")
    check("...and it says so once, in the log",
          any("coexistence signalling OFF" in r for r in records),
          f"{records}")
    broken.stop()


# ---- 4. absent EsotericOS is a no-op ------------------------------------

def _abandon(name):
    """Take `name` on a thread and let that thread die still holding it.

    This is the hard-kill path: the OpenSpan GUI runs `taskkill /T /F` on the
    portal for every geometry change, so a capture can end with no code of ours
    running at all.
    """
    took = threading.Event()

    def work():
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_long,
                                     ctypes.c_wchar_p]
        k32.WaitForSingleObject.restype = ctypes.c_ulong
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        handle = k32.CreateMutexW(None, False, name)
        if handle and k32.WaitForSingleObject(handle, 1000) in (0, 0x80):
            took.set()
        # returns WITHOUT ReleaseMutex -> Windows marks the mutex ABANDONED
    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    return took.is_set()


def _hold(name, release):
    """Take `name` on a thread and KEEP holding it until `release` is set."""
    took = threading.Event()

    def work():
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_long,
                                     ctypes.c_wchar_p]
        k32.WaitForSingleObject.restype = ctypes.c_ulong
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        handle = k32.CreateMutexW(None, False, name)
        if handle and k32.WaitForSingleObject(handle, 1000) in (0, 0x80):
            took.set()
            release.wait(5.0)
            k32.ReleaseMutex(handle)
    thread = threading.Thread(target=work)
    thread.start()
    took.wait(3.0)
    return thread


def test_behaviour():
    print("\n-- 4. behaviour with and without a counterpart --")
    pid = os.getpid()

    # (a) NOBODY ELSE PRESENT. This is the shipped case on a machine without
    # EsotericOS: one CreateMutexW and an uncontended wait per crossing.
    records = []
    lease = P.InputCaptureLease(name=fr"Local\OpenSpan.InteropTest.{pid}.solo")
    lease._log = records.append
    lease.start(wait=3.0)
    check("the mutex is created", lease.available is True, f"{records}")
    started = time.time()
    lease.acquire()
    deadline = time.time() + 2.0
    while not lease.held and time.time() < deadline:
        time.sleep(0.005)
    elapsed = time.time() - started
    check("an uncontended acquire succeeds", lease.held is True, f"{records}")
    check("...and does not wait anywhere near the 1000 ms timeout",
          elapsed < 0.5, f"{elapsed*1000:.0f} ms")
    lease.release()
    deadline = time.time() + 2.0
    while lease.held and time.time() < deadline:
        time.sleep(0.005)
    check("release hands it back", lease.held is False, f"{records}")
    # Redundant posts must not inflate the recursion count: a mutex is
    # re-entrant for its owner, and two acquires would need two releases.
    for _ in range(3):
        lease.acquire()
    time.sleep(0.2)
    lease.release()
    deadline = time.time() + 2.0
    while lease.held and time.time() < deadline:
        time.sleep(0.005)
    check("three acquires then one release leaves it FREE",
          lease.held is False, f"{records}")
    check("...because redundant posts are collapsed, not stacked",
          sum(1 for r in records if "ACQUIRED" in r) == 2,
          f"{[r for r in records if 'ACQUIRED' in r]}")
    lease.stop()

    # (b) THE HARD-KILL PATH. A holder dies without releasing; the next waiter
    # must reclaim rather than block forever. This is the entire reason the
    # contract specifies a mutex and not an event.
    records = []
    name = fr"Local\OpenSpan.InteropTest.{pid}.abandoned"
    lease = P.InputCaptureLease(name=name)
    lease._log = records.append
    lease.start(wait=3.0)          # holds a handle, keeping the object alive
    check("abandonment fixture took the mutex", _abandon(name))
    lease.acquire()
    deadline = time.time() + 2.0
    while not lease.held and time.time() < deadline:
        time.sleep(0.005)
    check("WAIT_ABANDONED is treated as a SUCCESSFUL acquire",
          lease.held is True, f"{records}")
    check("...and the reclaim is logged, not silent",
          any("WAIT_ABANDONED" in r for r in records), f"{records}")
    lease.release()
    time.sleep(0.2)
    lease.stop()

    # (c) A LIVE HOLDER. Capture must proceed anyway: the lease is a courtesy
    # signal, never a permission gate. Nothing may make this keyboard wait on
    # other software to reach the device it is already pointed at.
    records = []
    name = fr"Local\OpenSpan.InteropTest.{pid}.contended"
    release = threading.Event()
    holder = _hold(name, release)
    lease = P.InputCaptureLease(name=name, timeout_ms=300)
    lease._log = records.append
    lease.start(wait=3.0)
    started = time.time()
    lease.acquire()
    deadline = time.time() + 3.0
    while not any("TIMEOUT" in r for r in records) and time.time() < deadline:
        time.sleep(0.01)
    check("a contended acquire times out instead of blocking forever",
          any("TIMEOUT" in r for r in records), f"{records}")
    check("...and reports that capture proceeds without the lease",
          any("Capturing anyway" in r for r in records), f"{records}")
    check("...and does not claim to hold it", lease.held is False)
    check("...within its own timeout, not the default",
          time.time() - started < 2.0)
    release.set()
    holder.join(5.0)
    lease.stop()

    # (d) The Portal wiring itself, with no hook installed and no mutex: the
    # two helpers must tolerate `lease` being None, which is how the routing
    # tests construct a Portal.
    portal = P.Portal.__new__(P.Portal)
    check("a Portal built without __init__ has lease None",
          portal.lease is None)
    try:
        portal._lease_acquire()
        portal._lease_release()
        raised = None
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("...and the helpers are no-ops rather than exceptions",
          raised is None, f"{raised!r}")


# ---- 5. clipboard marking at every relay write site ---------------------

MARK_NAMES = [
    "Clipboard Viewer Ignore",
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
]


def test_clipboard():
    print("\n-- 5. relay clipboard writes are marked --")
    names = [n for n, _v in A._CLIP_MARK_NAMES]
    check("all four privacy formats are declared, spelled exactly",
          names == MARK_NAMES, f"{names}")
    # The two that carry data must carry 0. EsotericOS reads
    # CanIncludeInClipboardHistory's DWORD, not merely its presence
    # (ClipboardMonitor.cs). A nonzero value would mean "yes, keep it".
    values = dict(A._CLIP_MARK_NAMES)
    check("CanIncludeInClipboardHistory carries DWORD 0",
          values["CanIncludeInClipboardHistory"] == 0)
    check("CanUploadToCloudClipboard carries DWORD 0",
          values["CanUploadToCloudClipboard"] == 0)
    ids = [fmt for fmt, _v in A.CLIPBOARD_PRIVACY_FORMATS]
    check("every format registered to a real id",
          len(ids) == 4 and all(ids) and len(set(ids)) == 4, f"{ids}")
    check("marking is available, so writes are not failing closed",
          A.CLIPBOARD_MARKING_AVAILABLE is True)
    # RegisterClipboardFormatW is case-insensitive and process-independent, so
    # our ids and EsotericOS's agree by construction rather than by agreement.
    user32 = ctypes.windll.user32
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    again = [user32.RegisterClipboardFormatW(n.upper()) for n in MARK_NAMES]
    check("...and the same names resolve to the same ids from anywhere",
          again == ids, f"{again} != {ids}")

    # There must be exactly ONE clipboard writer in the tree.
    writers = []
    for tree, parents, label in ((APP_TREE, APP_PARENTS, "openspan.py"),
                                 (PORTAL_TREE, PORTAL_PARENTS,
                                  "openspan_portal.py")):
        for node, _name in calls(tree, "SetClipboardData"):
            fn = enclosing(node, parents, ast.FunctionDef)
            writers.append(f"{label}:{fn.name if fn else '<module>'}")
    check("SetClipboardData is called only from set_clipboard_text",
          writers and set(writers) == {"openspan.py:set_clipboard_text"},
          f"{sorted(set(writers))}")

    fn = next(n for n in ast.walk(APP_TREE)
              if isinstance(n, ast.FunctionDef)
              and n.name == "set_clipboard_text")
    args = [a.arg for a in fn.args.args]
    check("set_clipboard_text takes a `private` flag defaulting to True",
          args[-1] == "private" and fn.args.defaults
          and getattr(fn.args.defaults[-1], "value", None) is True,
          f"{args}")

    sets = sorted(n.lineno for n, _ in calls(fn, "SetClipboardData"))
    empties = sorted(n.lineno for n, _ in calls(fn, "EmptyClipboard"))
    opens = sorted(n.lineno for n, _ in calls(fn, "OpenClipboard"))
    closes = sorted(n.lineno for n, _ in calls(fn, "CloseClipboard"))
    check("there are two SetClipboardData sites: markers, then payload",
          len(sets) == 2 and sets[0] < sets[1], f"{sets}")
    # EmptyClipboard wipes markers from a previous write, so it must come
    # first and the markers must be re-placed on every write.
    check("EmptyClipboard precedes both", empties and empties[0] < sets[0],
          f"empty {empties} sets {sets}")
    # One Open/Close pair. Listeners are notified once, on CloseClipboard --
    # that is what makes the markers and the payload atomic to an observer.
    check("markers and payload share ONE Open/Close pair",
          len(opens) == 1 and len(closes) == 1
          and opens[0] < sets[0] and closes[0] > sets[1],
          f"open {opens} sets {sets} close {closes}")
    close_node = next(n for n, _ in calls(fn, "CloseClipboard"))
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)
             and any(close_node in ast.walk(s) for s in n.finalbody)]
    check("CloseClipboard is in a finally, so no path leaves it open",
          bool(tries))
    # Markers need REAL DATA. SetClipboardData(fmt, NULL) means delayed
    # rendering, and OpenClipboard(NULL) leaves the clipboard with no owner to
    # render it -- the format would be advertised and forever unreadable.
    marker_call = next(n for n, _ in calls(fn, "SetClipboardData"))
    check("the marker handle is a real allocation, never NULL",
          any(isinstance(n, ast.Call) and dotted(n.func).endswith("_alloc_dword")
              for n in ast.walk(fn))
          and not any(isinstance(a, ast.Constant) and a.value is None
                      for a in marker_call.args))

    # FAIL CLOSED: if marking is unavailable the write is refused, and refused
    # BEFORE the clipboard is opened. An unmarked write of relayed text is the
    # single outcome this mechanism exists to prevent.
    guard = [n for n in ast.walk(fn) if isinstance(n, ast.If)
             and "CLIPBOARD_MARKING_AVAILABLE" in ast.dump(n.test)]
    check("an unavailable marker set refuses the write",
          len(guard) == 1 and guard[0].lineno < opens[0],
          f"guard at {[g.lineno for g in guard]}, OpenClipboard at {opens}")
    saved = A.CLIPBOARD_MARKING_AVAILABLE
    try:
        A.CLIPBOARD_MARKING_AVAILABLE = False
        refused = A.set_clipboard_text("interop test -- never written")
    finally:
        A.CLIPBOARD_MARKING_AVAILABLE = saved
    check("...and returns False without touching the clipboard",
          refused is False)

    # EVERY relay write site must be marked. There is one, and it must not opt
    # out: relayed text is exactly the text that carries passwords.
    optouts = []
    sites = []
    for tree, parents, label in ((APP_TREE, APP_PARENTS, "openspan.py"),
                                 (PORTAL_TREE, PORTAL_PARENTS,
                                  "openspan_portal.py")):
        for node, _name in calls(tree, "set_clipboard_text"):
            enclosing_fn = enclosing(node, parents, ast.FunctionDef)
            if enclosing_fn is not None \
                    and enclosing_fn.name == "set_clipboard_text":
                continue                       # the definition itself
            sites.append(f"{label}:"
                         f"{enclosing_fn.name if enclosing_fn else '<module>'}")
            for kw in node.keywords:
                if kw.arg == "private" \
                        and getattr(kw.value, "value", True) is not True:
                    optouts.append(f"{label}:{node.lineno}")
            for arg in node.args[1:]:
                if getattr(arg, "value", True) is not True:
                    optouts.append(f"{label}:{node.lineno} (positional)")
    check("the relay has exactly one clipboard write site",
          sites == ["openspan.py:_post"], f"{sites}")
    check("no call site opts out of marking", not optouts, f"{optouts}")


# ---- 6. the keymap guard has teeth --------------------------------------

def test_keymap_guard():
    print("\n-- 6. the keymap guard fails on a universal chord --")
    # EsotericOS's incident, expressed in OUR grammar. Nothing in the loader
    # rejects it: `home` is a real key name, so this loads clean and every
    # Ctrl+A typed at the device becomes Home.
    bad = {"modifier_remap": {"alt": "cmd"},
           "overrides": [{"from": ["ctrl", "a"], "to": ["home"]}]}
    directory = tempfile.mkdtemp(prefix="openspan-interop-")
    path = os.path.join(directory, "openspan_keymap.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(bad, handle)

    saved_keymap, saved_failures = G.KEYMAP, list(G.FAILURES)
    buffer = io.StringIO()
    code = None
    try:
        G.KEYMAP = path
        G.FAILURES.clear()
        with contextlib.redirect_stdout(buffer):
            try:
                G.main()
            except SystemExit as exc:
                code = exc.code
        fired = list(G.FAILURES)
    finally:
        G.KEYMAP = saved_keymap
        G.FAILURES.clear()
        G.FAILURES.extend(saved_failures)
        try:
            os.remove(path)
            os.rmdir(directory)
        except OSError:
            pass

    check("ctrl+a -> home is REJECTED (exit 1)", code == 1, f"exit {code}")
    check("...by the rule that names it: R5, the universal-chord rule",
          any(f.startswith("R5 ") for f in fired), f"{fired}")

    # And the shipped file must still pass -- with the rules unchanged. A guard
    # that only ever fails is as useless as one that only ever passes.
    buffer = io.StringIO()
    code = 0
    saved_failures = list(G.FAILURES)
    try:
        G.FAILURES.clear()
        with contextlib.redirect_stdout(buffer):
            try:
                G.main()
            except SystemExit as exc:
                code = exc.code
        shipped = list(G.FAILURES)
    finally:
        G.FAILURES.clear()
        G.FAILURES.extend(saved_failures)
    check("the SHIPPED openspan_keymap.json passes the same rules",
          code in (0, None) and not shipped, f"{shipped}")
    check("...and ctrl+a -> cmd+a is among what it allows",
          "ctrl" in buffer.getvalue() and "R5" in buffer.getvalue())

    # The guard is only as good as its coverage of the chords we actually
    # swallow from Windows -- the ones the lease does NOT cover, because they
    # fire precisely when nothing is captured.
    for chord in (("ctrl", "alt", "i"), ("ctrl", "alt", "shift", "v"),
                  ("ctrl", "alt", "shift", "c")):
        check(f"R8 still checks {'+'.join(chord)} against their table",
              chord in G.OPENSPAN_CHORDS)


# ---- 7. the doc exists and cites rather than restates -------------------

def test_docs():
    print("\n-- 7. our half of the contract is written down --")
    check("D:\\OpenSpan\\docs\\INTEROP.md exists", os.path.exists(OUR_SPEC),
          OUR_SPEC)
    if not os.path.exists(OUR_SPEC):
        return
    with open(OUR_SPEC, encoding="utf-8") as handle:
        doc = handle.read()
    check("it cites their spec rather than restating it",
          "EsotericOS\\docs\\INTEROP.md" in doc
          or "EsotericOS/docs/INTEROP.md" in doc)
    check("it publishes the mutex name", PUBLISHED_NAME in doc)
    check("it states the elevation consequence",
          "UIPI" in doc and "asInvoker" in doc)
    check("it records the Ctrl+A -> Home incident, credited",
          "Ctrl+A" in doc and "Home" in doc and "EsotericOS" in doc)
    check("it lists the claims the lease does NOT cover",
          "Ctrl+Alt+Shift+V" in doc and "Back" in doc)


def main():
    test_name()
    test_thread_ownership()
    test_guarded()
    test_behaviour()
    test_clipboard()
    test_keymap_guard()
    test_docs()
    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S)")
        for name in FAILURES:
            print("   *", name)
        raise SystemExit(1)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
