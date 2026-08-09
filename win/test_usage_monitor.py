"""Synthetic coverage for the read-only usage readers."""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import tempfile

import usage_monitor as usage


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


HERE = pathlib.Path(__file__).parent
UTC = dt.timezone.utc
today = dt.datetime.now(UTC).date()


def write_lines(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            if isinstance(record, str):
                stream.write(record + "\n")
            else:
                stream.write(json.dumps(record) + "\n")


with tempfile.TemporaryDirectory(dir=HERE) as temporary:
    root = pathlib.Path(temporary)
    codex_home = root / "codex-home"
    sessions = codex_home / "sessions" / "2026" / "08" / "09"

    older = sessions / "rollout-complete.jsonl"
    write_lines(older, [
        {"type": "session_meta"},
        "not-json",
        {
            "timestamp": "2026-08-09T12:34:56Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {
                        "used_percent": 12.5,
                        "window_minutes": 10080,
                        "resets_at": 1786320000,
                    },
                    "secondary": None,
                    "plan_type": "team",
                },
                "info": {"total_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "cache_write_input_tokens": 5,
                    "output_tokens": 30,
                    "reasoning_output_tokens": 10,
                    "total_tokens": 165,
                }},
            },
        },
    ])
    newest = sessions / "rollout-started.jsonl"
    write_lines(newest, [{"type": "session_meta"}])
    now = dt.datetime.now(UTC).timestamp()
    os.utime(older, (now - 20, now - 20))
    os.utime(newest, (now - 10, now - 10))

    snapshot = usage.codex_snapshot(codex_home)
    check("snapshot falls back past a newer token-count-less rollout",
          snapshot == {
              "used_percent": 12.5,
              "window_minutes": 10080,
              "resets_at": 1786320000,
              "plan_type": "team",
              "thread_total_tokens": 165,
              "as_of": "2026-08-09T12:34:56Z",
          })

    database = codex_home / "state_5.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE threads (id TEXT, tokens_used INTEGER, model TEXT, "
            "created_at INTEGER, updated_at INTEGER, source TEXT, cli_version TEXT)"
        )
        one_day = today - dt.timedelta(days=1)
        within = dt.datetime.combine(one_day, dt.time(hour=10), tzinfo=UTC)
        today_noon = dt.datetime.combine(today, dt.time(hour=12), tzinfo=UTC)
        too_old = dt.datetime.combine(today - dt.timedelta(days=8), dt.time(),
                                      tzinfo=UTC)
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("a", 100, "model-a", int(within.timestamp()),
                 int(now), "cli", "1"),
                ("b", 25, "model-b", int(within.timestamp()),
                 int(now), "cli", "1"),
                ("c", 50, "model-c", int(today_noon.timestamp()),
                 int(now), "cli", "1"),
                ("old", 999, "model-d", int(too_old.timestamp()),
                 int(now), "cli", "1"),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    codex = usage.codex_burn(7, codex_home)
    check("Codex burn buckets lifetime totals on thread creation day",
          codex == {one_day.isoformat(): 125, today.isoformat(): 50,
                    "total": 175})

    claude_home = root / "claude-home"
    project = claude_home / "projects" / "fixture-project"
    today_stamp = dt.datetime.combine(today, dt.time(hour=11), tzinfo=UTC)
    yesterday = today - dt.timedelta(days=1)
    yesterday_stamp = dt.datetime.combine(yesterday, dt.time(hour=23), tzinfo=UTC)

    main_file = project / "main.jsonl"
    write_lines(main_file, [
        "{broken",
        {"type": "user", "timestamp": today_stamp.isoformat(),
         "message": {"content": "must be ignored"}},
        {"type": "assistant", "timestamp": today_stamp.isoformat(),
         "sessionId": "main", "message": {"model": "claude-fixture",
         "content": [{"text": "must not escape"}], "usage": {
             "input_tokens": 10, "output_tokens": 5,
             "cache_creation_input_tokens": 3, "cache_read_input_tokens": 2}}},
    ])
    subagent_file = project / "session" / "subagents" / "agent.jsonl"
    write_lines(subagent_file, [
        {"type": "assistant", "timestamp": yesterday_stamp.isoformat(),
         "sessionId": "side", "isSidechain": True,
         "message": {"model": "claude-fixture", "usage": {
             "input_tokens": 7, "output_tokens": 4,
             "cache_creation_input_tokens": 1, "cache_read_input_tokens": 8}}},
    ])
    old_file = project / "old.jsonl"
    write_lines(old_file, [
        {"type": "assistant", "timestamp": today_stamp.isoformat(),
         "sessionId": "old", "message": {"model": "claude-fixture", "usage": {
             "input_tokens": 1000, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    ])
    old_mtime = (dt.datetime.combine(today - dt.timedelta(days=8), dt.time(),
                                    tzinfo=UTC).timestamp())
    os.utime(old_file, (old_mtime, old_mtime))

    claude = usage.claude_burn(7, claude_home)
    check("Claude burn preserves component splits per UTC day and context",
          claude == {"days": {
              yesterday.isoformat(): {
                  "main": {"fresh": 0, "cache": 0, "out": 0},
                  "subagents": {"fresh": 7, "cache": 9, "out": 4},
              },
              today.isoformat(): {
                  "main": {"fresh": 10, "cache": 5, "out": 5},
                  "subagents": {"fresh": 0, "cache": 0, "out": 0},
              },
          }, "totals": {"fresh": 17, "cache": 14, "out": 9}})
    check("Claude totals sum all days and contexts by component",
          claude["totals"] == {"fresh": 17, "cache": 14, "out": 9})
    check("Claude walk skips out-of-window files by mtime",
          claude["totals"]["fresh"] == 17)
    check("malformed transcript lines are ignored cleanly",
          claude["days"][today.isoformat()]["main"]
          == {"fresh": 10, "cache": 5, "out": 5})

    missing = root / "absent"
    check("absent Codex home has clean empty results",
          usage.codex_snapshot(missing) is None
          and usage.codex_burn(7, missing) == {"total": 0})
    check("absent Claude home has a clean empty result",
          usage.claude_burn(7, missing) == {
              "days": {},
              "totals": {"fresh": 0, "cache": 0, "out": 0},
          })


source = (HERE / "usage_monitor.py").read_text(encoding="utf-8").lower()
for forbidden in (
        "auth" + ".json",
        "cap" + "_sid",
        ".credentials" + ".json",
        "sandbox" + "-secrets",
):
    check("forbidden filename is absent from usage reader: " + forbidden,
          forbidden not in source)
