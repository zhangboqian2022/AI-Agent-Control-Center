"""Incremental per-session token usage from Kimi wire files.

Each session's `agents/main/wire.jsonl` is tailed by byte offset: every
poll reads only appended bytes, consumes complete newline-terminated
lines, and keeps any trailing partial line for the next round. A file
that shrank (rotation/truncation) restarts accumulation from zero.

Privacy boundary matches kimi_discovery: only `usage.record` event usage
fields and durations are read; prompt/response content is never touched,
and lines over 64 KiB are skipped unparsed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aacc.kimi_metrics import SpeedTracker, decode_speed, normalize_usage

_WIRE_MAX_LINE_BYTES = 65_536  # same bound as kimi_discovery


@dataclass
class SessionUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    speed: SpeedTracker = field(default_factory=SpeedTracker)
    last_duration_ms: float = 0.0

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    @property
    def cache_read_pct(self) -> int | None:
        if self.total_input_tokens <= 0:
            return None
        return round(self.cache_read_tokens / self.total_input_tokens * 100)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_input_tokens": self.total_input_tokens,
            "cache_read_pct": self.cache_read_pct,
            "speed_tps": self.speed.median,
            "last_duration_ms": self.last_duration_ms,
        }


def _duration_of(item: dict[str, Any]) -> object:
    for key in ("durationMs", "duration_ms", "streamDuration"):
        value = item.get(key)
        if value is not None:
            return value
    return None


class WireUsageTracker:
    def __init__(self) -> None:
        self._offsets: dict[Path, int] = {}
        self._sessions: dict[Path, SessionUsage] = {}

    def poll(self, session_dir: Path) -> SessionUsage | None:
        wire = session_dir / "agents" / "main" / "wire.jsonl"
        try:
            size = wire.stat().st_size
        except OSError:
            return None
        offset = self._offsets.get(wire, 0)
        if size < offset:
            offset = 0
            self._sessions[wire] = SessionUsage()
        if size == offset:
            return self._sessions.setdefault(wire, SessionUsage())
        try:
            with wire.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(size - offset)
        except OSError:
            return None
        segments = data.split(b"\n")
        if data.endswith(b"\n"):
            complete_lines = segments[:-1]
            consumed = size
        else:
            complete_lines = segments[:-1]
            consumed = size - len(segments[-1])
        self._offsets[wire] = consumed
        session = self._sessions.setdefault(wire, SessionUsage())
        for line in complete_lines:
            self._consume(session, line)
        return session

    def _consume(self, session: SessionUsage, line: bytes) -> None:
        if not line or len(line) > _WIRE_MAX_LINE_BYTES or b'"usage.record"' not in line:
            return
        try:
            item: Any = json.loads(line.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return
        if not isinstance(item, dict):
            return
        if item.get("type") != "usage.record" or item.get("usageScope") != "turn":
            return
        usage = normalize_usage(item.get("usage") or item.get("token_usage"))
        session.input_tokens += usage.input_tokens
        session.output_tokens += usage.output_tokens
        session.cache_read_tokens += usage.cache_read_tokens
        session.cache_creation_tokens += usage.cache_creation_tokens
        duration = _duration_of(item)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            session.last_duration_ms = float(duration)
        session.speed.append(decode_speed(usage.output_tokens, duration))
