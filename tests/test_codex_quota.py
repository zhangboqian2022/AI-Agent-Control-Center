from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from aacc.codex_quota import (
    CodexQuotaReader,
    CodexQuotaStatus,
    parse_rate_limits,
)

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)


def token_count(
    *,
    primary: tuple[float, int] | None,
    secondary: tuple[float, int] | None,
    timestamp: datetime = NOW,
) -> dict[str, object]:
    def window(value: tuple[float, int] | None) -> dict[str, object] | None:
        if value is None:
            return None
        used_percent, minutes = value
        return {
            "used_percent": used_percent,
            "window_minutes": minutes,
            "resets_at": int((NOW + timedelta(days=2)).timestamp()),
        }

    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "private_prompt": "private-prompt-sentinel",
            "rate_limits": {
                "limit_id": "codex",
                "primary": window(primary),
                "secondary": window(secondary),
                "plan_type": "prolite",
            },
        },
    }


def test_parses_current_weekly_window_from_primary():
    snapshot = parse_rate_limits(
        token_count(primary=(9.0, 10080), secondary=None),
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 9
    assert snapshot.weekly.window_minutes == 10080
    assert snapshot.plan_type == "prolite"


def test_weekly_in_secondary_parses_and_legacy_short_window_is_ignored():
    snapshot = parse_rate_limits(
        token_count(primary=(81.0, 300), secondary=(27.0, 10080)),
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 27
    assert not hasattr(snapshot, "five_hour")


def test_legacy_short_window_alone_is_unknown():
    snapshot = parse_rate_limits(
        token_count(primary=(81.0, 300), secondary=None),
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert snapshot.weekly is None


def test_invalid_or_expired_weekly_window_is_unknown():
    invalid = token_count(primary=(101.0, 10080), secondary=None)
    assert (
        parse_rate_limits(invalid, now=NOW).status
        is CodexQuotaStatus.UNKNOWN
    )

    expired = token_count(primary=(9.0, 10080), secondary=None)
    rate_limits = expired["payload"]["rate_limits"]  # type: ignore[index]
    rate_limits["primary"]["resets_at"] = int((NOW - timedelta(seconds=1)).timestamp())  # type: ignore[index]
    assert (
        parse_rate_limits(expired, now=NOW).status
        is CodexQuotaStatus.UNKNOWN
    )


def test_parser_retains_no_private_event_content():
    snapshot = parse_rate_limits(
        token_count(primary=(9.0, 10080), secondary=None),
        now=NOW,
    )

    assert "private-prompt-sentinel" not in repr(snapshot)


def test_reader_uses_bounded_tail_and_ignores_incomplete_last_line(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "rollout-current.jsonl"
    valid = json.dumps(
        token_count(primary=(9.0, 10080), secondary=None)
    ).encode()
    with path.open("wb") as handle:
        handle.write(b"private-prefix-sentinel" * 20_000)
        handle.write(b"\n")
        handle.write(valid)
        handle.write(b"\n")
        handle.write(b'{"private_response":"incomplete')

    snapshot = CodexQuotaReader(sessions, now=lambda: NOW).read_latest()

    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 9
    assert "private-prefix-sentinel" not in repr(snapshot)


def test_reader_falls_back_when_newest_session_has_no_rate_limits(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    older = sessions / "rollout-older.jsonl"
    older.write_text(
        json.dumps(token_count(primary=(17.0, 10080), secondary=None)) + "\n",
        encoding="utf-8",
    )
    newer = sessions / "rollout-newer.jsonl"
    newer.write_text(
        json.dumps(
            {
                "timestamp": NOW.isoformat(),
                "type": "event_msg",
                "payload": {"type": "task_started"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    newer_mtime = older.stat().st_mtime + 10
    os.utime(newer, (newer_mtime, newer_mtime))

    snapshot = CodexQuotaReader(sessions, now=lambda: NOW).read_latest()

    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 17


def test_reader_without_valid_metadata_returns_unknown(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "rollout.jsonl").write_text(
        "not-json\n"
        + json.dumps(token_count(primary=(80.0, 300), secondary=None))
        + "\n",
        encoding="utf-8",
    )

    snapshot = CodexQuotaReader(sessions, now=lambda: NOW).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert snapshot.weekly is None
