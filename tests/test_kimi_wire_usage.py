from __future__ import annotations

import json
from pathlib import Path

from aacc.kimi_wire_usage import WireUsageTracker


def wire_path(session_dir: Path) -> Path:
    path = session_dir / "agents" / "main" / "wire.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def usage_record(**overrides) -> dict:
    record = {
        "type": "usage.record",
        "usageScope": "turn",
        "usage": {
            "inputOther": 100,
            "output": 200,
            "inputCacheRead": 300,
            "inputCacheCreation": 0,
        },
        "durationMs": 4000,
    }
    record.update(overrides)
    return record


def write_lines(path: Path, records: list[dict], mode: str = "w") -> None:
    with path.open(mode, encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def test_missing_wire_returns_none(tmp_path):
    tracker = WireUsageTracker()
    assert tracker.poll(tmp_path / "nope") is None


def test_accumulates_turn_usage_records(tmp_path):
    session = tmp_path / "s1"
    path = wire_path(session)
    write_lines(
        path,
        [
            {"type": "turn.prompt", "content": "secret prompt ignored"},
            usage_record(),
            {"type": "usage.record", "usageScope": "session", "usage": {"output": 999}},
        ],
    )
    tracker = WireUsageTracker()
    usage = tracker.poll(session)
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 200  # session-scoped record ignored
    assert usage.cache_read_tokens == 300
    assert usage.speed.median == 50  # 200 tokens / 4s
    assert usage.last_duration_ms == 4000
    metadata = usage.to_metadata()
    assert metadata["total_input_tokens"] == 400
    assert metadata["cache_read_pct"] == 75
    assert metadata["speed_tps"] == 50


def test_incremental_poll_reads_only_appended_lines(tmp_path):
    session = tmp_path / "s1"
    path = wire_path(session)
    write_lines(path, [usage_record()])
    tracker = WireUsageTracker()
    first = tracker.poll(session)
    assert first is not None and first.output_tokens == 200
    write_lines(path, [usage_record()], mode="a")
    second = tracker.poll(session)
    assert second is not None
    assert second.output_tokens == 400  # cumulative across polls


def test_truncation_resets_accumulation(tmp_path):
    session = tmp_path / "s1"
    path = wire_path(session)
    write_lines(path, [usage_record(), usage_record()])
    tracker = WireUsageTracker()
    assert tracker.poll(session).output_tokens == 400  # type: ignore[union-attr]
    write_lines(path, [usage_record()])  # smaller file: rotated/truncated
    usage = tracker.poll(session)
    assert usage is not None
    assert usage.output_tokens == 200


def test_partial_trailing_line_consumed_next_round(tmp_path):
    session = tmp_path / "s1"
    path = wire_path(session)
    complete = json.dumps(usage_record()) + "\n"
    partial = json.dumps({**usage_record(), "usage": {"output": 999}})
    path.write_bytes(complete.encode() + partial.encode()[:20])
    tracker = WireUsageTracker()
    usage = tracker.poll(session)
    assert usage is not None and usage.output_tokens == 200
    with path.open("a", encoding="utf-8") as handle:
        handle.write(partial[20:] + "\n")
    usage = tracker.poll(session)
    assert usage is not None and usage.output_tokens == 200 + 999


def test_oversized_and_malformed_lines_skipped(tmp_path):
    session = tmp_path / "s1"
    path = wire_path(session)
    big = json.dumps({"type": "usage.record", "usageScope": "turn", "usage": {"output": 1},
                      "pad": "x" * 70_000})
    path.write_text(big + "\n" + "not json\n" + json.dumps(usage_record()) + "\n")
    tracker = WireUsageTracker()
    usage = tracker.poll(session)
    assert usage is not None
    assert usage.output_tokens == 200


def test_speed_uses_duration_alias_fields(tmp_path):
    session = tmp_path / "s1"
    path = wire_path(session)
    write_lines(path, [usage_record(durationMs=None, streamDuration=2000)])
    tracker = WireUsageTracker()
    usage = tracker.poll(session)
    assert usage is not None
    assert usage.speed.median == 100  # 200 tokens / 2s
