"""Agent monitor: what Claude and Codex have spent, read from local data.

EsotericOS's first optional module, and the proof that the module contract is
usable for something real rather than a hello-world.

Doug's scope, in his words: "all of this information is shoved into the claude
code desktop app and the codex desktop app, i just want it visible in the
esoteric OS admin panel." So this reads what those tools already wrote on this
machine. It opens no socket and sends nothing anywhere.

It also draws nothing. It registers one command, "report", and returns rows of
(label, value); the host renders them. That is the whole contract, and it is
why this module cannot hang or corrupt the window it appears in -- it has no
way to reach it.

The reads themselves live in usage_monitor, which predates this and is tested
on its own. This module is the seam between that reader and the host, and it
is deliberately thin: the interesting failure here is "no local data on this
machine", not arithmetic.
"""

from __future__ import annotations

import time


class AgentMonitor:
    """Lifecycle: activate registers the report command, deactivate drops it."""

    # The host checks these against plugin.json before it will activate
    # anything, deliberately: the manifest is a claim made by a folder, and
    # this is the same claim made by the code that will actually run. A folder
    # renamed or a manifest edited to impersonate another module fails here
    # rather than loading as whatever it said it was.
    id = "agent-monitor"
    api_version = 1

    def __init__(self):
        self._host = None

    # ---- lifecycle ---------------------------------------------------------

    def activate(self, host):
        self._host = host
        host.register_command("report", "Refresh agent usage", self.report)
        host.log("info", "reading local Claude and Codex usage")

    def deactivate(self):
        # The bridge disposes the registration itself; there is nothing of our
        # own to release. No thread, no file handle, no socket -- on purpose.
        self._host = None

    # ---- the report --------------------------------------------------------

    def report(self):
        """Rows for the host to draw. Raising here is caught by the guard, but
        an empty panel explains nothing, so each half degrades to a sentence
        that says which of the two tools has no data on this machine."""
        return [self._codex_row(), self._claude_row()]

    def _codex_row(self):
        try:
            import usage_monitor
            snapshot = usage_monitor.codex_snapshot()
        except Exception as exc:  # noqa: BLE001
            return ("Codex", f"could not read local usage ({type(exc).__name__})")
        if snapshot is None:
            return ("Codex", "no local data — has it run on this machine?")
        try:
            resets = time.strftime(
                "%b %d", time.localtime(float(snapshot["resets_at"])))
            return ("Codex", f"{float(snapshot['used_percent']):.1f}% of the "
                             f"weekly window · resets {resets} · "
                             f"plan {snapshot['plan_type']}")
        except (KeyError, TypeError, ValueError):
            return ("Codex", "local data is present but not in a shape this "
                             "module understands")

    def _claude_row(self):
        try:
            import usage_monitor
            burn = usage_monitor.claude_burn(7)
        except Exception as exc:  # noqa: BLE001
            return ("Claude", f"could not read local usage ({type(exc).__name__})")
        days = (burn or {}).get("days") or {}
        if not days:
            return ("Claude", "no local data on this machine")
        try:
            today = days.get(time.strftime("%Y-%m-%d", time.gmtime()), {})
            zero = {"fresh": 0, "cache": 0, "out": 0}
            main = today.get("main", zero)
            subs = today.get("subagents", zero)
            totals = burn["totals"]
            spend = totals["fresh"] + totals["out"]
            return ("Claude",
                    f"today {(main['out'] + subs['out']) / 1e6:.1f}M out + "
                    f"{(main['fresh'] + subs['fresh']) / 1e3:.1f}k fresh · "
                    f"7d spend {spend / 1e6:.1f}M "
                    f"(cache {totals['cache'] / 1e9:.1f}B)")
        except (KeyError, TypeError, ValueError):
            return ("Claude", "local data is present but not in a shape this "
                              "module understands")
