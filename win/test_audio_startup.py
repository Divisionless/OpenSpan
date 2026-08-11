"""The audio lane must survive starting before Windows has an audio endpoint.

2026-08-11, after a cold restart: the lane died at import with

    OSError: [Errno -9996] Invalid device info
      win_audio_send.py, line 139, in get_device_info_by_index

and never came back. The endpoint was not missing -- it was late. The app
launches seconds after login, ahead of WASAPI enumeration, so
`defaultOutputDevice` was an index resolving to nothing. Audio was then
silently absent, with the traceback buried in audio_send.log.

win_audio_send.py runs its whole body at import, so it cannot be imported
here. The retry function is lifted out of the AST and exercised against fake
device stacks instead -- which is the point anyway: the behaviour under a
late endpoint is what broke.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
SRC = (ROOT / "win" / "win_audio_send.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


# ---- lift _default_output out of a module that cannot be imported -----------

fn = next((n for n in ast.walk(TREE)
           if isinstance(n, ast.FunctionDef) and n.name == "_default_output"),
          None)
check("the default-output lookup is a function, not a bare import-time call",
      fn is not None)


class FakePa:
    paWASAPI = 13


class FakeAudio:
    """A device stack that only becomes real after `ready_after` attempts."""

    def __init__(self, ready_after=0, host=None, info=None):
        self.ready_after = ready_after
        self.calls = 0
        self._host = host if host is not None else {"defaultOutputDevice": 8}
        self._info = info if info is not None else {"name": "Speakers (Yeti)"}

    def get_host_api_info_by_type(self, _kind):
        return self._host

    def get_device_info_by_index(self, idx):
        self.calls += 1
        if self.calls <= self.ready_after:
            raise OSError(-9996, "Invalid device info")
        return self._info


def run(ready_after, attempts=5):
    """Exec the lifted function against a fake stack. Sleep is counted, not slept."""
    slept = []
    audio = FakeAudio(ready_after=ready_after)
    ns = {
        "p": audio,
        "pa": FakePa,
        "time": type("T", (), {"sleep": staticmethod(lambda s: slept.append(s))}),
        "sys": type("S", (), {"stderr": None}),
        "print": lambda *a, **k: None,
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<lifted>", "exec"), ns)
    return ns["_default_output"](attempts=attempts, wait=1.0), slept, audio


# ---- the endpoint is there straight away -----------------------------------

(host, info), slept, audio = run(ready_after=0)
check("a ready endpoint is returned immediately",
      info is not None and info["name"] == "Speakers (Yeti)")
check("a ready endpoint costs no waiting at all", slept == [])
check("the host api info comes back too, for the caller's index",
      host is not None and host["defaultOutputDevice"] == 8)

# ---- the endpoint is late, which is the actual failure --------------------

(host, info), slept, audio = run(ready_after=3)
check("a late endpoint is waited for rather than fatal", info is not None)
check("it waited exactly as many times as the endpoint was absent",
      len(slept) == 3)

# ---- the endpoint never arrives -------------------------------------------

(host, info), slept, audio = run(ready_after=99, attempts=4)
check("an endpoint that never arrives returns None instead of raising",
      host is None and info is None)
check("giving up is bounded by the attempt count", len(slept) == 3)

# ---- and the caller must survive that None ---------------------------------
# The fallback below it ("any loopback device") predates this fix and is what
# makes returning None acceptable. Dying before reaching it was the whole bug.

src_after = SRC.split("wasapi, default_out = _default_output()")[1]
check("the loopback name-match is guarded against a missing default",
      "if default_out is not None:" in src_after
      and src_after.index("if default_out is not None:")
      < src_after.index("default_out[\"name\"] in d[\"name\"]"))
check("the fallback to the first loopback device still exists",
      "if lb is None:" in src_after)
check("only a genuinely empty loopback list is fatal",
      "No WASAPI loopback device found." in src_after)

# The original defect in one line: an unguarded index lookup at module level.
top_level_calls = [
    n for n in TREE.body
    if isinstance(n, ast.Assign)
    and isinstance(n.value, ast.Call)
    and isinstance(n.value.func, ast.Attribute)
    and n.value.func.attr == "get_device_info_by_index"
]
check("no unguarded get_device_info_by_index runs at import time",
      not top_level_calls)
