"""Run every win/test_*.py in its own interpreter and print one line each.

    C:\\Python313\\python.exe win\\run_all_tests.py

Each file is a standalone script (no pytest); a file passes when its exit code
is 0. Output per file is kept in the scratch log printed at the end, so a
failure can be read without re-running the whole sweep.
"""
import glob
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
log_dir = tempfile.mkdtemp(prefix="esotericos-tests-")
ok = fail = 0
t0 = time.time()
for f in files:
    name = os.path.basename(f)
    r = subprocess.run([sys.executable, f], capture_output=True, text=True,
                       cwd=HERE, timeout=600)
    with open(os.path.join(log_dir, name + ".log"), "w", encoding="utf-8") as fh:
        fh.write(r.stdout or "")
        fh.write(r.stderr or "")
    if r.returncode == 0:
        ok += 1
        print(f"OK   {name}")
    else:
        fail += 1
        print(f"FAIL {name}  (exit {r.returncode})")
print(f"\n{ok} OK, {fail} FAIL of {len(files)} files in {time.time() - t0:.0f}s")
print(f"logs: {log_dir}")
sys.exit(1 if fail else 0)
