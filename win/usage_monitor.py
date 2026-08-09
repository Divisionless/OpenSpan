"""Read Codex and Claude usage data without retaining transcript content."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sqlite3


UTC = dt.timezone.utc
_SNAPSHOT_FILE_LIMIT = 10


def _default_home(directory: str) -> Path | None:
    profile = os.environ.get("USERPROFILE")
    return Path(profile) / directory if profile else None


def _window(days: int) -> tuple[dt.datetime, dt.datetime] | None:
    if days <= 0:
        return None
    today = dt.datetime.now(UTC).date()
    first = today - dt.timedelta(days=days - 1)
    start = dt.datetime.combine(first, dt.time.min, tzinfo=UTC)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min,
                              tzinfo=UTC)
    return start, end


def _codex_event(line: str) -> dict | None:
    """Return only the permitted fields from a Codex meter event."""
    try:
        record = json.loads(line)
        payload = record["payload"]
        if record.get("type") != "event_msg" or payload.get("type") != "token_count":
            return None
        primary = payload["rate_limits"]["primary"]
        usage = payload["info"]["total_token_usage"]
        return {
            "used_percent": primary["used_percent"],
            "window_minutes": primary["window_minutes"],
            "resets_at": primary["resets_at"],
            "plan_type": payload["rate_limits"]["plan_type"],
            "thread_total_tokens": usage["total_tokens"],
            "as_of": record.get("timestamp"),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def codex_snapshot(codex_home=None) -> dict | None:
    """Return the newest available Codex weekly-plan meter snapshot."""
    home = Path(codex_home) if codex_home is not None else _default_home(".codex")
    if home is None:
        return None
    sessions = home / "sessions"
    if not sessions.is_dir():
        return None

    candidates = []
    try:
        for path in sessions.rglob("rollout-*.jsonl"):
            try:
                if path.is_file() and not path.is_symlink():
                    candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    for file_mtime, path in candidates[:_SNAPSHOT_FILE_LIMIT]:
        latest = None
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
                for line in stream:
                    event = _codex_event(line)
                    if event is not None:
                        latest = event
        except OSError:
            continue
        if latest is not None:
            if not latest["as_of"]:
                latest["as_of"] = dt.datetime.fromtimestamp(file_mtime, UTC).isoformat()
            return latest
    return None


def codex_burn(days=7, codex_home=None) -> dict:
    """Bucket thread lifetime totals by creation date within a UTC window.

    A thread spanning multiple days is intentionally assigned wholly to its
    creation date because the database exposes only a lifetime total.
    """
    window = _window(int(days))
    if window is None:
        return {"total": 0}
    home = Path(codex_home) if codex_home is not None else _default_home(".codex")
    if home is None:
        return {"total": 0}
    database = home / "state_5.sqlite"
    if not database.is_file():
        return {"total": 0}

    start, end = window
    result = {}
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            rows = connection.execute(
                "SELECT created_at, tokens_used FROM threads "
                "WHERE created_at >= ? AND created_at < ?",
                (start.timestamp(), end.timestamp()),
            )
            for created_at, tokens_used in rows:
                try:
                    day = dt.datetime.fromtimestamp(float(created_at), UTC).date().isoformat()
                    amount = int(tokens_used or 0)
                except (OverflowError, TypeError, ValueError):
                    continue
                result[day] = result.get(day, 0) + amount
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return {"total": 0}

    result["total"] = sum(result.values())
    return result


def _empty_components() -> dict:
    return {"fresh": 0, "cache": 0, "out": 0}


def _empty_claude_burn() -> dict:
    return {"days": {}, "totals": _empty_components()}


def _claude_usage(line: str) -> tuple[dt.datetime, dict, bool] | None:
    """Parse one record and return no transcript fields."""
    try:
        record = json.loads(line)
        if record.get("type") != "assistant":
            return None
        timestamp = record["timestamp"]
        when = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        when = when.astimezone(UTC)
        usage = record["message"]["usage"]
        components = {
            "fresh": int(usage.get("input_tokens", 0) or 0),
            "cache": (int(usage.get("cache_creation_input_tokens", 0) or 0)
                      + int(usage.get("cache_read_input_tokens", 0) or 0)),
            "out": int(usage.get("output_tokens", 0) or 0),
        }
        return when, components, bool(record.get("isSidechain"))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def claude_burn(days=7, claude_home=None) -> dict:
    """Sum Claude assistant usage by UTC day and main/subagent context."""
    window = _window(int(days))
    if window is None:
        return _empty_claude_burn()
    home = Path(claude_home) if claude_home is not None else _default_home(".claude")
    if home is None:
        return _empty_claude_burn()
    projects = home / "projects"
    if not projects.is_dir():
        return _empty_claude_burn()

    start, end = window
    cutoff = start.timestamp()
    by_day = {}
    totals = _empty_components()
    try:
        candidates = projects.rglob("*.jsonl")
        for path in candidates:
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                if path.stat().st_mtime < cutoff:
                    continue
                with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
                    for line in stream:
                        parsed = _claude_usage(line)
                        if parsed is None:
                            continue
                        when, components, sidechain = parsed
                        if not start <= when < end:
                            continue
                        day = when.date().isoformat()
                        bucket = by_day.setdefault(day, {
                            "main": _empty_components(),
                            "subagents": _empty_components(),
                        })
                        context = bucket["subagents" if sidechain else "main"]
                        for component, amount in components.items():
                            context[component] += amount
                            totals[component] += amount
            except OSError:
                continue
    except OSError:
        return _empty_claude_burn()

    return {"days": by_day, "totals": totals}


def _local_reset(value) -> str:
    try:
        return dt.datetime.fromtimestamp(float(value), UTC).astimezone().strftime(
            "%Y-%m-%d %H:%M %Z")
    except (OverflowError, TypeError, ValueError, OSError):
        return "unknown"


def _print_probe() -> None:
    snapshot = codex_snapshot()
    if snapshot is None:
        print("Codex meter: no data")
    else:
        print(
            "Codex meter: weekly {:.1f}% used, resets {}, as of {}, plan {}".format(
                float(snapshot["used_percent"]),
                _local_reset(snapshot["resets_at"]),
                snapshot["as_of"],
                snapshot["plan_type"],
            )
        )

    codex = codex_burn(7)
    print("Codex burn (last 7 UTC days):")
    for day, amount in sorted((key, value) for key, value in codex.items()
                              if key != "total"):
        print(f"  {day}  {amount}")
    print(f"  total       {codex['total']}")

    claude = claude_burn(7)
    print("Claude burn (last 7 UTC days):")
    for day, bucket in sorted(claude["days"].items()):
        main = bucket["main"]
        subagents = bucket["subagents"]
        print(
            f"  {day}  main fresh {main['fresh']} cache {main['cache']} "
            f"out {main['out']} | subagents fresh {subagents['fresh']} "
            f"cache {subagents['cache']} out {subagents['out']}"
        )
    totals = claude["totals"]
    print(
        f"  spend-ish (fresh+out): {totals['fresh'] + totals['out']}, "
        f"cache: {totals['cache']}"
    )


if __name__ == "__main__":
    _print_probe()
