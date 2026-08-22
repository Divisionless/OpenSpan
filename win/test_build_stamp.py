# SPDX-License-Identifier: AGPL-3.0-or-later
"""The build stamp: which build is this, and is it a test build?

Doug, 2026-08-10: version number in blue at the bottom left; a test build says
so in yellow. In the single-page GUI it is the document footer, reachable at
the end of the same scrollbar as every control."""

import ast
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import openspan


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


ROOT = pathlib.Path(__file__).parent.parent
source = (ROOT / "win" / "openspan.py").read_text(encoding="utf-8")
tree = ast.parse(source)


class FrozenAs:
    """Pretend to be a frozen build of a given exe name."""

    def __init__(self, exe):
        self.exe = str(ROOT / exe)

    def __enter__(self):
        self._frozen = getattr(sys, "frozen", None)
        self._exe = sys.executable
        sys.frozen = True
        sys.executable = self.exe
        return self

    def __exit__(self, *_):
        if self._frozen is None:
            del sys.frozen
        else:
            sys.frozen = self._frozen
        sys.executable = self._exe


check("there is a version to show", bool(openspan.VERSION))

text, is_test = openspan.build_stamp()
check("running from source is honestly a test build",
      is_test and "source" in text and openspan.VERSION in text)

# The canonical exe is the only build that is NOT a test build.
if (ROOT / "EsotericOS.exe").exists():
    with FrozenAs("EsotericOS.exe"):
        text, is_test = openspan.build_stamp()
    check("the shipped exe is not labelled a test build", not is_test)
    check("the shipped stamp carries version and build time",
          openspan.VERSION in text and "·" in text)

with FrozenAs("EsotericOS-next.exe"):
    text, is_test = openspan.build_stamp()
check("a staged -next build IS a test build", is_test)
check("a test stamp names the exe, so two staged builds are distinguishable",
      "EsotericOS-next.exe" in text)

# ---- how it is rendered ------------------------------------------------------

check("blue is reserved for the build stamp",
      'BUILD_BLUE = "#5AA9FF"' in source
      and source.count("BUILD_BLUE") >= 2)
check("a test build is called out in yellow",
      "BUILD_TEST_YELLOW" in source
      and 'text="TEST BUILD"' in source)
check("the stamp is left-aligned in the page footer",
      'foot = tk.Frame(bridge, bg=BG)' in source
      and 'foot.pack(fill="x"' in source
      and 'self.build_lbl.pack(side="left")' in source)

# One document footer, not one copy per section.
app = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "App")
init = next(n for n in app.body
            if isinstance(n, ast.FunctionDef) and n.name == "__init__")
body = ast.get_source_segment(source, init)
check("the footer is built once, in the scrolling document",
      body.count("foot = tk.Frame(bridge, bg=BG)") == 1
      and body.count("build_stamp()") == 1)
