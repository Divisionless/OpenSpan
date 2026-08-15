"""The four faults of 2026-08-15, made detectable.

Not one of them announced itself. The 149 MiB of binaries sat in history for
weeks. The boot task failed silently for nine days. The benchmark binary was
destroyed and survived undetected until someone happened to check a hash.
Every one was detectable; none was detected.

They were also all the same two shapes:

  A FACT RESTATED INSTEAD OF DERIVED -- .gitignore naming one exe rather than
  the class, a boot script hardcoding a root it is sitting in, a build script
  printing basename(ROOT) as if it were the path.

  A SAFETY CONDITIONED ON A NARROW TRIGGER -- build_exe preserving the
  outgoing binary only when the target was LOCKED, so the one unanticipated
  path (not locked, still precious) is the path that destroyed the benchmark.

This file will not catch a new shape. It catches these, and the argument for
it is that these four were two shapes repeated.
"""

import ast
import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent      # ...\app
TREE = ROOT.parent                                         # ...\_EsotericOS

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        fails.append(name)
        if detail:
            print("      " + detail)


# =========================================================================
# 1. THE BINARY LEDGER IS EXECUTABLE.
#
# preservation/binaries/README.md declares every kept build with its
# SHA-256. Until now that was prose: the benchmark could be destroyed and
# the document went on describing it. A declared binary must either exist
# and hash to what is claimed, or be explicitly marked lost.
# =========================================================================

ledger_path = TREE / "preservation" / "binaries" / "README.md"
check("the binary ledger exists", ledger_path.is_file(), str(ledger_path))

if ledger_path.is_file():
    ledger = ledger_path.read_text(encoding="utf-8")
    # | `Name.exe` | `sha…` | ... -- hash may be truncated with an ellipsis.
    rows = re.findall(
        r"\|\s*`([^`]+\.exe)`\s*\|\s*`([0-9a-fA-F]{16,64})[^`]*`", ledger)
    check("the ledger declares at least one binary", bool(rows),
          "no rows matched; has the table format changed?")

    missing, wrong = [], []
    for name, declared in rows:
        # A row marked BYTES LOST is a gravestone, not a live claim. Skip it
        # entirely: the benchmark's NAME has since been reused by a later
        # build, so a file does sit at that path -- it is simply not, and
        # never again will be, the binary this row describes. Verifying it
        # would report a loss that is already recorded, every run, forever.
        if re.search(re.escape(name) + r"[\s\S]{0,600}?BYTES LOST", ledger) or \
           re.search(r"BYTES LOST[\s\S]{0,600}?" + re.escape(name), ledger):
            continue
        candidates = [TREE / "preservation" / "binaries" / name, ROOT / name]
        found = next((p for p in candidates if p.is_file()), None)
        if found is None:
            missing.append(name)
            continue
        digest = hashlib.sha256(found.read_bytes()).hexdigest()
        if not digest.startswith(declared.lower()):
            wrong.append(f"{name}: declared {declared.lower()[:16]}… "
                         f"but is {digest[:16]}…")

    check("every binary the ledger declares still exists", not missing,
          ", ".join(missing))
    # This is the check that would have caught the benchmark being overwritten
    # the minute it happened, instead of by accident hours later.
    check("every kept binary still hashes to what the ledger claims",
          not wrong, "; ".join(wrong))
    check("a binary recorded as lost says so in the ledger",
          "BYTES LOST" in ledger or not rows,
          "the benchmark loss must stay on the record, not be quietly dropped")


# =========================================================================
# 2. AN OPERATIONAL SCRIPT MAY NOT WRITE OUT A PATH INTO THIS TREE.
#
# It derives its root from where it sits. All three path faults were this:
# OpenSpan-boot.ps1's $ROOT, install-boot-task.ps1's task command, and
# build_exe.py printing basename(ROOT). Comments are exempt -- several of
# them exist precisely to explain this rule.
# =========================================================================

# Dated records describe what was true when written; editing them to agree
# with the present would falsify the only account of what happened.
HISTORICAL = re.compile(
    r"(DEVLOG|AUDIT-|CODE_REVIEW|buildlog|maintenance-|HANDOFF|COMPACTION"
    r"|BACKLOG|TECHNICAL_NOTES|BUILD\.md|README|CLIPBOARD|POSITION_MODEL"
    r"|briefs|docs\\plan|docs/plan)")
SKIP_DIRS = re.compile(r"\\\.git\\|\\vm\\|\\build\\|\\dist\\|__pycache__"
                       r"|\\\.worktrees\\|\\modules\\|preservation")

TREE_PATH = re.compile(r"[Dd]:[\\/](?:_EsotericOS|OpenSpan)", re.I)


def code_only(path):
    """Source with comments and docstrings removed."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return text
        drop = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    drop.append(body[0])
        lines = text.splitlines()
        for node in drop:
            for i in range(node.lineno - 1, node.end_lineno):
                lines[i] = ""
        return "\n".join(l.split("#")[0] if "#" in l else l for l in lines)
    return "\n".join(l.split("#")[0] for l in text.splitlines())


offenders = []
for path in list(ROOT.glob("*.ps1")) + list(ROOT.glob("*.py")) + \
        list((ROOT / "win").glob("*.py")):
    if HISTORICAL.search(str(path)) or SKIP_DIRS.search(str(path)):
        continue
    if path.name.startswith("test_"):
        continue          # tests name paths on purpose; they are the check
    for n, line in enumerate(code_only(path).splitlines(), 1):
        if TREE_PATH.search(line):
            offenders.append(f"{path.name}:{n}")

check("no operational script writes out a path into this tree",
      not offenders, ", ".join(offenders[:6]))


# =========================================================================
# 3. IGNORE THE CLASS, NOT THE FILENAME -- AND NOTHING RUNTIME IS TRACKED.
# =========================================================================

gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
for pattern in ("*.exe", "*.exe.prev", "module_settings.json",
                "audio_gain.txt"):
    check(f"ignored by class: {pattern}",
          any(l.strip() == pattern for l in gitignore.splitlines()))

try:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
        timeout=60).stdout.splitlines()
except Exception as exc:                                    # noqa: BLE001
    tracked = None
    print(f"      (git unavailable: {exc})")

if tracked is not None:
    RUNTIME = re.compile(
        r"\.exe$|\.exe\.prev$|\.bak$|module_settings\.json$|audio_gain\.txt$"
        r"|^bt_prefs\.json$|^openspan_config\.json$|^profiles/", re.I)
    leaked = [f for f in tracked if RUNTIME.search(f)]
    # Runtime state beside source is how 149 MiB of binaries got committed,
    # and how module_settings.json slipped in tonight.
    check("no runtime state or binary is tracked", not leaked,
          ", ".join(leaked[:6]))


# =========================================================================
# 4. NOTHING REPLACES AN ARTIFACT WITHOUT KEEPING IT.
#
# The benchmark died because preservation ran only when the target was
# locked. Unconditional now -- and asserted here so a later refactor cannot
# quietly restore the condition.
# =========================================================================

build_src = (ROOT / "build_exe.py").read_text(encoding="utf-8")
build_code = code_only(ROOT / "build_exe.py")
overwrite = build_code.find("shutil.copy2(built, target)")
preserve = build_code.find(".exe.prev")
check("the build preserves the outgoing binary before overwriting it",
      0 <= preserve < overwrite,
      f"preserve at {preserve}, overwrite at {overwrite}")
check("preservation is unconditional, not gated on the target being locked",
      re.search(r"if not staged and os\.path\.exists\(target\)", build_src)
      is not None)
check("a build that cannot preserve refuses rather than proceeding",
      "REFUSING to overwrite" in build_src)


print("\nRESULT: " + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
