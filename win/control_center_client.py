# SPDX-License-Identifier: AGPL-3.0-or-later

"""The elevated app's end of the Control Center broker channel.

Phase 2 of docs/CONTROL-CENTER.md, app side. The GUI is Phase 3; nothing here
draws, and nothing here launches. This module resolves a catalog id against
the Phase 1 catalog, refuses locally if it cannot, and otherwise puts one
framed request on a per-user named pipe and reads one framed answer back.

WHY THE CLIENT RESOLVES AT ALL, GIVEN THE BROKER RESOLVES AGAIN.

Not for security -- the client's answer is thrown away by the broker, and that
is the design. It resolves for three other reasons:

  * A bad id is refused in-process, with the same reason code the broker would
    have used, without a round trip and without teaching an attacker anything
    by timing.
  * The GUI needs the route, the integrity level and the elevation badge to
    draw the item BEFORE anyone clicks it, and it must get those from the
    catalog rather than from a broker reply it has not asked for yet.
  * A disagreement between the two resolutions is a real fault -- a stale
    manifest deployed beside the shell, a half-finished upgrade -- and it is
    only detectable because both sides answer independently and the answers
    are compared afterwards. `ActivationResult.divergent` is that check.

THE TRANSPORT IS INJECTABLE AND THE TESTS INJECT IT. `request_activation`
takes a `transport` callable of bytes -> bytes. The real one is
`NamedPipeTransport`; the suite never constructs it, so the whole app-side test
run touches no pipe, no handle and no other process.

ONE WINDOWS DETAIL THAT IS SECURITY, NOT PLUMBING: the pipe is opened with
SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, and the choice of level is
load-bearing in both directions.

This app runs elevated. A pipe server is allowed to impersonate the client that
connects to it, and a Medium-integrity squatter that reached the name first
could therefore borrow a High-integrity token -- a local elevation of privilege
with a twenty-year history. IDENTIFICATION level lets the server READ who
connected (which the broker needs, because it refuses any caller that is not
the owning user) while forbidding it to USE that identity to open anything.
Privilege flows downwards only, which is the entire reason the broker exists.

SECURITY_ANONYMOUS would be one notch safer against a squatter and would break
the broker's own identity check, since the server would see the Anonymous SID
instead of the user's. The residual risk after IDENTIFICATION is denial of
service by name squatting, which is visible -- the destination does not open --
rather than silent. The shell side closes even that with
FILE_FLAG_FIRST_PIPE_INSTANCE, which makes a squatted name a startup failure
instead of a quiet interception.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import control_center_protocol as P                            # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 5.0


# =========================================================================
# LOCAL RESOLUTION -- against the real Phase 1 catalog, not the manifest.
# =========================================================================

@dataclass(frozen=True)
class LocalResolution:
    """What the app believes about an id before it asks anyone."""

    record_id: str
    route: str
    target: str
    integrity: str
    availability: str

    @property
    def elevation(self) -> bool:
        return self.integrity == P.INTEGRITY_HIGH


def resolve_local(catalog, record_id: str):
    """(LocalResolution, None) or (None, Refusal). Same ladder as the broker.

    The order is deliberately identical to `control_center_protocol.resolve`
    and to the C# `ControlBrokerCore`: malformed, unknown, unavailable, no
    route, bad target. Three implementations agreeing on the order is what
    makes a divergence mean something.
    """
    if not P.valid_record_id(record_id):
        return None, P.Refusal(P.ID_MALFORMED)
    record = catalog.get(record_id)
    if record is None:
        return None, P.Refusal(P.ID_UNKNOWN)
    if record.availability == P.AVAILABILITY_UNAVAILABLE:
        return None, P.Refusal(P.ID_UNAVAILABLE)
    route = P.ROUTE_BY_DESTINATION_KIND.get(record.destination_kind)
    if route is None:
        return None, P.Refusal(P.ROUTE_UNSUPPORTED)
    if not P.TARGET_PATTERNS[route].match(record.destination_target or ""):
        return None, P.Refusal(P.ROUTE_UNSUPPORTED)
    if record.integrity not in P.INTEGRITY_LEVELS:
        return None, P.Refusal(P.ROUTE_UNSUPPORTED)
    return LocalResolution(record_id=record_id, route=route,
                           target=record.destination_target,
                           integrity=record.integrity,
                           availability=record.availability), None


# =========================================================================
# THE RESULT THE GUI WILL EVENTUALLY RENDER
# =========================================================================

@dataclass(frozen=True)
class ActivationResult:
    """One request, one outcome, always with a named reason.

    `refused_locally` distinguishes "the app would not ask" from "the broker
    said no", which matters when reading a log: the first is a catalog problem
    on this side, the second is a catalog or policy problem on the other.
    """

    reason: str
    detail: str
    refused_locally: bool
    local: LocalResolution | None = None
    response: P.Response | None = None

    @property
    def ok(self) -> bool:
        return self.reason == P.OK

    @property
    def elevation(self) -> bool:
        if self.response is not None:
            return self.response.elevation
        return bool(self.local and self.local.elevation)

    @property
    def divergent(self) -> bool:
        """True when the broker resolved the same id differently.

        Never a reason to proceed and never silently ignored: if the two sides
        disagree about the route or the integrity level of an id, one of them
        is running against a catalog the other has never seen.
        """
        if self.local is None or self.response is None:
            return False
        if not self.response.ok:
            return False
        return (self.response.route != self.local.route
                or self.response.integrity != self.local.integrity)


def _refused(refusal: P.Refusal, local=None, response=None,
             locally: bool = True) -> ActivationResult:
    return ActivationResult(reason=refusal.reason, detail=refusal.detail,
                            refused_locally=locally, local=local,
                            response=response)


# =========================================================================
# THE TRANSPORT
# =========================================================================

_kernel32 = None
_advapi32 = None


def _load_libraries():
    """Load kernel32/advapi32 with EXPLICIT prototypes for everything used.

    Not optional politeness. Without a declared restype, ctypes treats a
    returned HANDLE as a 32-bit int, so GetCurrentProcess()'s pseudo-handle
    (HANDLE)-1 arrives at OpenProcessToken as 0x00000000FFFFFFFF instead of
    0xFFFFFFFFFFFFFFFF and the call fails with ERROR_INVALID_HANDLE. The same
    truncation silently corrupts every handle this module passes. Declaring
    the prototypes is the difference between calling Windows and calling
    something that looks like Windows on a 32-bit machine.
    """
    global _kernel32, _advapi32
    if _kernel32 is not None:
        return _kernel32, _advapi32

    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentThread.argtypes = []
    kernel32.GetCurrentThread.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL,
        wintypes.DWORD]
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    kernel32.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
    kernel32.CancelSynchronousIo.restype = wintypes.BOOL

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    _kernel32, _advapi32 = kernel32, advapi32
    return _kernel32, _advapi32


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SECURITY_ANONYMOUS = 0x00000000
SECURITY_IDENTIFICATION = 0x00010000
SECURITY_SQOS_PRESENT = 0x00100000
ERROR_PIPE_BUSY = 231
NMPWAIT_USE_DEFAULT_WAIT = 0x00000000
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1


def current_user_sid() -> str:
    """The SID of the account this process runs as, as a string.

    Read from this process's own token. Not from the environment, not from a
    name lookup: the pipe's ACL is built from a SID and a name can be spoofed
    long before a token can.
    """
    from ctypes import wintypes

    kernel32, advapi32 = _load_libraries()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_QUERY, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0,
                                     ctypes.byref(size))
        if size.value < ctypes.sizeof(ctypes.c_void_p):
            raise OSError(ctypes.get_last_error(),
                          "GetTokenInformation reported no TOKEN_USER")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, buffer,
                                            size, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
        # TOKEN_USER is SID_AND_ATTRIBUTES: { PSID Sid; DWORD Attributes; }
        sid_pointer = ctypes.c_void_p.from_buffer(buffer)
        text = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid_pointer,
                                               ctypes.byref(text)):
            raise OSError(ctypes.get_last_error(),
                          "ConvertSidToStringSidW failed")
        try:
            return text.value
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


class NamedPipeTransport:
    """bytes in, bytes out, over the per-user broker pipe. Never used by tests.

    Synchronous by choice -- one request, one answer, one connection -- with a
    watchdog that cancels the pending I/O rather than letting a wedged or
    squatting server hang the elevated GUI's thread forever.
    """

    def __init__(self, user_sid: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._sid = user_sid or current_user_sid()
        self._timeout = float(timeout)

    @property
    def path(self) -> str:
        return P.pipe_path(self._sid)

    def __call__(self, frame: bytes) -> bytes:
        kernel32, _ = _load_libraries()
        handle = kernel32.CreateFileW(
            self.path, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING,
            SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, None)
        if handle == INVALID_HANDLE_VALUE or handle is None:
            error = ctypes.get_last_error()
            if error == ERROR_PIPE_BUSY:
                if not kernel32.WaitNamedPipeW(
                        self.path, int(self._timeout * 1000)):
                    raise OSError(ctypes.get_last_error(),
                                  "the broker pipe was busy")
                handle = kernel32.CreateFileW(
                    self.path, GENERIC_READ | GENERIC_WRITE, 0, None,
                    OPEN_EXISTING,
                    SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, None)
            if handle == INVALID_HANDLE_VALUE or handle is None:
                raise OSError(ctypes.get_last_error(),
                              "the broker pipe could not be opened")
        watchdog = self._start_watchdog()
        try:
            self._write(handle, frame)
            header = self._read_exact(handle, P.HEADER_BYTES)
            declared = int.from_bytes(header, "little")
            if declared > P.MAX_BODY_BYTES:
                # An answer this side cannot bound is not read at all.
                raise OSError(0, "the broker answered with an oversize frame")
            return header + self._read_exact(handle, declared)
        finally:
            watchdog.cancel()
            kernel32.CloseHandle(handle)

    def _start_watchdog(self) -> threading.Timer:
        from ctypes import wintypes

        kernel32, _ = _load_libraries()
        # DUPLICATE_SAME_ACCESS (2): the pseudo-handle GetCurrentThread returns
        # is only meaningful on the calling thread, and the watchdog runs on a
        # different one, so it has to be duplicated into a real handle first.
        thread = wintypes.HANDLE()
        kernel32.DuplicateHandle(
            kernel32.GetCurrentProcess(), kernel32.GetCurrentThread(),
            kernel32.GetCurrentProcess(), ctypes.byref(thread), 0, False, 2)

        def cancel():
            try:
                kernel32.CancelSynchronousIo(thread)
            finally:
                kernel32.CloseHandle(thread)

        timer = threading.Timer(self._timeout, cancel)
        timer.daemon = True
        timer.start()
        return timer

    @staticmethod
    def _write(handle, data: bytes) -> None:
        kernel32, _ = _load_libraries()
        written = ctypes.c_ulong(0)
        offset = 0
        while offset < len(data):
            chunk = data[offset:]
            if not kernel32.WriteFile(handle, chunk, len(chunk),
                                      ctypes.byref(written), None):
                raise OSError(ctypes.get_last_error(),
                              "writing to the broker pipe failed")
            if written.value == 0:
                raise OSError(0, "the broker pipe accepted no bytes")
            offset += written.value

    @staticmethod
    def _read_exact(handle, count: int) -> bytes:
        kernel32, _ = _load_libraries()
        out = bytearray()
        read = ctypes.c_ulong(0)
        while len(out) < count:
            buffer = ctypes.create_string_buffer(count - len(out))
            if not kernel32.ReadFile(handle, buffer, count - len(out),
                                     ctypes.byref(read), None):
                raise OSError(ctypes.get_last_error(),
                              "reading from the broker pipe failed")
            if read.value == 0:
                raise OSError(0, "the broker closed the pipe early")
            out += buffer.raw[:read.value]
        return bytes(out)


# =========================================================================
# THE ONE PUBLIC CALL
# =========================================================================

def request_activation(catalog, record_id: str, transport=None,
                       *, nonce: str | None = None,
                       issued_ms: int | None = None) -> ActivationResult:
    """Ask the broker to open one catalog entry. Returns; never raises.

    `transport` is a callable of bytes -> bytes. Passing None builds a real
    NamedPipeTransport, which is why every test passes one.

    Note what is NOT here: no target, no route, no arguments, no elevation
    flag, no override. There is no parameter through which a caller of this
    function can influence what the broker runs. It picks an id, and that is
    the entire extent of its authority.
    """
    local, refusal = resolve_local(catalog, record_id)
    if refusal is not None:
        return _refused(refusal)

    try:
        payload = P.build_request(record_id, nonce=nonce, issued_ms=issued_ms)
        frame = P.encode_frame(payload)
    except ValueError:
        return _refused(P.Refusal(P.ID_MALFORMED), local=local)

    try:
        # Construction is inside the guard on purpose: NamedPipeTransport
        # reads the process token, and a token read that fails must surface as
        # transport-unavailable like every other channel fault rather than as
        # an exception escaping into whatever called this.
        if transport is None:
            transport = NamedPipeTransport()
        answer = transport(frame)
    except OSError:
        return _refused(P.Refusal(P.TRANSPORT_UNAVAILABLE), local=local)
    except Exception:                                          # noqa: BLE001
        return _refused(P.Refusal(P.TRANSPORT_UNAVAILABLE), local=local)

    parsed = P.parse_response(answer)
    if isinstance(parsed, P.Refusal):
        return _refused(parsed, local=local, locally=False)
    if parsed.nonce and parsed.nonce != payload["nonce"]:
        # An answer to a question this call did not ask.
        return _refused(P.Refusal(P.SCHEMA_INVALID), local=local, locally=False)
    return ActivationResult(reason=parsed.reason, detail=parsed.detail,
                            refused_locally=False, local=local,
                            response=parsed)


def export_manifest(catalog, path: str) -> str:
    """Write the broker's independent catalog copy.

    Deployment wiring is Phase 4; this is the generator, kept next to the
    client so the two halves of the contract are regenerated together.
    """
    return P.write_manifest(path, catalog)


if __name__ == "__main__":                                     # pragma: no cover
    import control_catalog

    _catalog = control_catalog.build_catalog_from_fixtures()
    if "--export-manifest" in sys.argv:
        index = sys.argv.index("--export-manifest")
        destination = (sys.argv[index + 1] if len(sys.argv) > index + 1
                       else os.path.join(HERE, P.MANIFEST_FILENAME))
        print("wrote " + export_manifest(_catalog, destination))
        raise SystemExit(0)
    print("pipe: " + P.pipe_path(current_user_sid()))
    print("sddl: " + P.pipe_sddl(current_user_sid()))
    print(f"{len(_catalog.records)} catalog records addressable")
