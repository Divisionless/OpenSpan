"""Structural guards for the System desk AI usage panel."""

import ast
import pathlib


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


HERE = pathlib.Path(__file__).parent
source = (HERE / "openspan.py").read_text(encoding="utf-8")
tree = ast.parse(source)
app = next(node for node in tree.body
           if isinstance(node, ast.ClassDef) and node.name == "App")
init = next(node for node in app.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__")
worker = next(node for node in app.body
              if isinstance(node, ast.FunctionDef)
              and node.name == "_usage_worker")
worker_nodes = set(ast.walk(worker))
worker_source = ast.get_source_segment(source, worker)

check("the System desk AI usage section exists",
      'desk = _section(pane_system, "System desk — AI usage")' in source)

usage_imports = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.Import, ast.ImportFrom))
                 and ((isinstance(node, ast.Import)
                       and any(alias.name == "usage_monitor"
                               for alias in node.names))
                      or (isinstance(node, ast.ImportFrom)
                          and node.module == "usage_monitor"))]
check("usage_monitor is imported lazily inside the usage worker only",
      len(usage_imports) == 1 and usage_imports[0] in worker_nodes
      and not any(node in usage_imports for node in tree.body))

worker_threads = [node for node in ast.walk(init)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "Thread"
                  and any(keyword.arg == "target"
                          and isinstance(keyword.value, ast.Attribute)
                          and keyword.value.attr == "_usage_worker"
                          for keyword in node.keywords)]
check("the usage worker thread is daemonized",
      len(worker_threads) == 1
      and any(keyword.arg == "daemon"
              and isinstance(keyword.value, ast.Constant)
              and keyword.value.value is True
              for keyword in worker_threads[0].keywords))

sleeps = [node for node in ast.walk(worker)
          if isinstance(node, ast.Call)
          and isinstance(node.func, ast.Attribute)
          and node.func.attr == "sleep"
          and node.args and isinstance(node.args[0], ast.Constant)
          and isinstance(node.args[0].value, (int, float))]
check("the usage refresh interval is at least 600 seconds",
      bool(sleeps) and min(node.args[0].value for node in sleeps) >= 600)
check("the usage worker marshals updates through self.ui",
      "self.ui(" in worker_source)
check("both no-local-data fallbacks exist",
      "Codex   no local data (has it run on this machine?)" in source
      and "Claude  no local data" in source)

reader_calls = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("codex_snapshot", "claude_burn")]
check("the usage readers are called only inside the worker",
      len(reader_calls) == 2
      and all(node in worker_nodes for node in reader_calls))
