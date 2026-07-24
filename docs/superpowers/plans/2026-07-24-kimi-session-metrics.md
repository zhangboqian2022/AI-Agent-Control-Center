# Kimi Session Metrics (Subsystem B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show per-session token metrics (input/output/cache hit %/generation speed) on Kimi Code task cards, computed by incrementally tailing each session's `wire.jsonl`.

**Architecture:** `kimi_metrics.py` ports kimi-code-monitor's `metrics.js` (usage normalization, cache-hit %, median speed window). `kimi_wire_usage.py` incrementally tails wire files (byte-offset per session, complete-line discipline, reset on truncation) and accumulates `usage.record` (scope=turn) events. `KimiLocalDiscovery.discover()` attaches the accumulated usage to `TaskState.metadata["usage"]`; `TaskCard` renders a metrics row for `kimi_code` cards.

**Tech Stack:** Python 3.12, PySide6, pytest + pytest-qt. No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-three-in-one-integration-design.md`
- Branch: `feat/kimi-quota-integration`. Subsystem A (quota) plan lives at `docs/superpowers/plans/2026-07-24-kimi-quota-monitor.md`; this plan is independent of it — either may land first.
- Privacy boundary (unchanged): wire files are scanned for event type / usage fields only; prompt and response content is never stored, displayed, or logged. Lines over 64 KiB are skipped unparsed.
- Quality gates after every task: `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests`, `.venv/bin/mypy src/aacc` — all green.
- TDD: failing test first, minimal implementation, commit per task. Line length 100.

---

### Task 1: `kimi_metrics.py` — usage normalization and speed math

**Files:**
- Create: `src/aacc/kimi_metrics.py`
- Test: `tests/test_kimi_metrics.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `NormalizedUsage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0)` with `.total_input -> int` and `.cache_read_pct -> int | None`; `normalize_usage(raw: object) -> NormalizedUsage`; `decode_speed(output_tokens: int, duration_ms: object) -> int | None`; `SpeedTracker` with `.samples: list[int]`, `.append(speed: int | None) -> None`, `.median -> int`; `format_token_count(value: int) -> str`; `format_usage_line(usage: Mapping[str, Any]) -> str`. Constants `SPEED_SAMPLE_WINDOW=5`, `MIN_SPEED_DURATION_MS=100`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kimi_metrics.py
from __future__ import annotations

from aacc.kimi_metrics import (
    SpeedTracker,
    decode_speed,
    format_token_count,
    format_usage_line,
    normalize_usage,
)


def test_normalize_usage_cli_wire_field_names():
    usage = normalize_usage(
        {"inputOther": 100, "output": 20, "inputCacheRead": 300, "inputCacheCreation": 50}
    )
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.cache_read_tokens == 300
    assert usage.cache_creation_tokens == 50
    assert usage.total_input == 450
    assert usage.cache_read_pct == 67


def test_normalize_usage_alias_field_names():
    usage = normalize_usage(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 5,
        }
    )
    assert usage.total_input == 45
    usage2 = normalize_usage({"prompt_tokens": 7, "completion_tokens": 3})
    assert usage2.input_tokens == 7
    assert usage2.output_tokens == 3


def test_normalize_usage_junk_becomes_zero():
    usage = normalize_usage(None)
    assert usage.total_input == 0
    assert usage.cache_read_pct is None
    usage = normalize_usage({"inputOther": -5, "output": "x", "inputCacheRead": True})
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0


def test_normalize_usage_floats_are_floored():
    usage = normalize_usage({"inputOther": 10.9})
    assert usage.input_tokens == 10


def test_decode_speed_thresholds():
    assert decode_speed(200, 4000) == 50
    assert decode_speed(0, 4000) is None
    assert decode_speed(200, 50) is None  # below MIN_SPEED_DURATION_MS
    assert decode_speed(200, "junk") is None
    assert decode_speed(200, None) is None


def test_speed_tracker_median_and_window():
    tracker = SpeedTracker()
    assert tracker.median == 0
    for speed in (10, 30, 20):
        tracker.append(speed)
    assert tracker.samples == [10, 30, 20]
    assert tracker.median == 20
    for speed in (40, 50, 60):
        tracker.append(speed)
    assert tracker.samples == [30, 20, 40, 50, 60]  # window keeps last 5
    assert tracker.median == 40
    tracker.append(None)  # invalid sample only trims, never enters
    assert tracker.samples == [30, 20, 40, 50, 60]


def test_format_token_count():
    assert format_token_count(999) == "999"
    assert format_token_count(1234) == "1.2k"
    assert format_token_count(1000) == "1k"
    assert format_token_count(1_500_000) == "1.5M"
    assert format_token_count(2_000_000) == "2M"


def test_format_usage_line_full_and_sparse():
    line = format_usage_line(
        {
            "total_input_tokens": 12_300,
            "output_tokens": 1_200,
            "cache_read_pct": 68,
            "speed_tps": 42,
        }
    )
    assert line == "↑12.3k ↓1.2k 缓存68% · 42 tok/s"
    sparse = format_usage_line({"total_input_tokens": 0, "output_tokens": 0})
    assert sparse == "↑0 ↓0"
    no_cache = format_usage_line(
        {"total_input_tokens": 100, "output_tokens": 50, "cache_read_pct": None}
    )
    assert "缓存" not in no_cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kimi_metrics.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aacc.kimi_metrics'`

- [ ] **Step 3: Write the implementation**

```python
# src/aacc/kimi_metrics.py
"""Per-session token usage metrics.

Ported from kimi-code-monitor `metrics.js` (MIT License,
Copyright (c) 2026 十叶) — usage field normalization across the CLI's
wire naming and API naming, cache-hit percentage, and a robust median
generation speed over a sliding sample window. See NOTICE.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SPEED_SAMPLE_WINDOW = 5
MIN_SPEED_DURATION_MS = 100


def _to_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    number: float
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return 0
    else:
        return 0
    if number != number or number <= 0:  # NaN or non-positive
        return 0
    return int(number)


def _first_defined(source: Mapping[str, Any], keys: tuple[str, ...]) -> object:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return 0


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    @property
    def cache_read_pct(self) -> int | None:
        total = self.total_input
        if total <= 0:
            return None
        return round(self.cache_read_tokens / total * 100)


def normalize_usage(raw: object) -> NormalizedUsage:
    usage: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    return NormalizedUsage(
        input_tokens=_to_non_negative_int(
            _first_defined(usage, ("inputOther", "input_tokens", "prompt_tokens"))
        ),
        output_tokens=_to_non_negative_int(
            _first_defined(usage, ("output", "output_tokens", "completion_tokens"))
        ),
        cache_read_tokens=_to_non_negative_int(
            _first_defined(
                usage, ("inputCacheRead", "cache_read_input_tokens", "cache_read_tokens")
            )
        ),
        cache_creation_tokens=_to_non_negative_int(
            _first_defined(
                usage,
                ("inputCacheCreation", "cache_creation_input_tokens", "cache_creation_tokens"),
            )
        ),
    )


def decode_speed(output_tokens: int, duration_ms: object) -> int | None:
    output = _to_non_negative_int(output_tokens)
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return None
    duration = float(duration_ms)
    if duration < MIN_SPEED_DURATION_MS or output == 0:
        return None
    return round(output / (duration / 1000))


@dataclass
class SpeedTracker:
    samples: list[int] = field(default_factory=list)

    def append(self, speed: int | None) -> None:
        if speed is not None and speed > 0:
            self.samples.append(speed)
        del self.samples[:-SPEED_SAMPLE_WINDOW]

    @property
    def median(self) -> int:
        if not self.samples:
            return 0
        ordered = sorted(self.samples)
        middle = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[middle]
        return round((ordered[middle - 1] + ordered[middle]) / 2)


def format_token_count(value: int) -> str:
    number = int(value)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}".replace(".0", "") + "M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}".replace(".0", "") + "k"
    return str(number)


def format_usage_line(usage: Mapping[str, Any]) -> str:
    total_input = _to_non_negative_int(usage.get("total_input_tokens"))
    output = _to_non_negative_int(usage.get("output_tokens"))
    cache_pct = usage.get("cache_read_pct")
    speed = _to_non_negative_int(usage.get("speed_tps"))
    parts = [f"↑{format_token_count(total_input)} ↓{format_token_count(output)}"]
    if isinstance(cache_pct, int) and not isinstance(cache_pct, bool):
        parts.append(f"缓存{cache_pct}%")
    if speed > 0:
        parts.append(f"{speed} tok/s")
    return " ".join(parts[:1]) + " · " + " ".join(parts[1:]) if len(parts) > 1 else parts[0]
```

Wait — the expected format is `↑12.3k ↓1.2k 缓存68% · 42 tok/s` for full and `↑0 ↓0` for sparse. Simplify `format_usage_line` to match exactly:

```python
def format_usage_line(usage: Mapping[str, Any]) -> str:
    total_input = _to_non_negative_int(usage.get("total_input_tokens"))
    output = _to_non_negative_int(usage.get("output_tokens"))
    cache_pct = usage.get("cache_read_pct")
    speed = _to_non_negative_int(usage.get("speed_tps"))
    tokens_part = f"↑{format_token_count(total_input)} ↓{format_token_count(output)}"
    details: list[str] = []
    if isinstance(cache_pct, int) and not isinstance(cache_pct, bool):
        details.append(f"缓存{cache_pct}%")
    if speed > 0:
        details.append(f"{speed} tok/s")
    if not details:
        return tokens_part
    return f"{tokens_part} {' '.join(details[:-1])} · {details[-1]}" if len(details) > 1 else (
        f"{tokens_part} · {details[0]}"
    )
```

Check against expectations: full → details=["缓存68%","42 tok/s"] → "↑12.3k ↓1.2k 缓存68% · 42 tok/s" ✓. sparse → "↑0 ↓0" ✓. no_cache → details=[] → "↑100 ↓50", test asserts "缓存" not in ✓. Use this simpler final version in the plan and drop the first draft.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kimi_metrics.py -q`
Expected: 8 passed

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/ruff check src/aacc/kimi_metrics.py tests/test_kimi_metrics.py
.venv/bin/mypy src/aacc
git add src/aacc/kimi_metrics.py tests/test_kimi_metrics.py
git commit -m "feat: add Kimi per-session token metrics ported from kimi-code-monitor"
```

---

### Task 2: `kimi_wire_usage.py` — incremental wire tailing

**Files:**
- Create: `src/aacc/kimi_wire_usage.py`
- Test: `tests/test_kimi_wire_usage.py`

**Interfaces:**
- Consumes: `aacc.kimi_metrics` (`SpeedTracker`, `decode_speed`, `normalize_usage`).
- Produces: `SessionUsage` dataclass (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `speed: SpeedTracker`, `last_duration_ms: float`) with `.to_metadata() -> dict[str, Any]`; `WireUsageTracker` with `.poll(session_dir: Path) -> SessionUsage | None`.

Metadata dict keys (consumed by `format_usage_line` in Task 1): `total_input_tokens`, `output_tokens`, `cache_read_pct` (int | None), `speed_tps` (int).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kimi_wire_usage.py
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
    partial = json.dumps(usage_record(output=999))
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kimi_wire_usage.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aacc.kimi_wire_usage'`

- [ ] **Step 3: Write the implementation**

```python
# src/aacc/kimi_wire_usage.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kimi_wire_usage.py -q`
Expected: 7 passed

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/ruff check src/aacc/kimi_wire_usage.py tests/test_kimi_wire_usage.py
.venv/bin/mypy src/aacc
git add src/aacc/kimi_wire_usage.py tests/test_kimi_wire_usage.py
git commit -m "feat: add incremental wire usage tracker for Kimi sessions"
```

---

### Task 3: Attach usage metadata in `KimiLocalDiscovery.discover()`

**Files:**
- Modify: `src/aacc/kimi_discovery.py` — `KimiLocalDiscovery.__init__` (lines 203-232), `discover()` metadata block (lines 290-297)
- Test: `tests/test_kimi_wire_usage.py` (append integration test)

**Interfaces:**
- Consumes: `WireUsageTracker` (Task 2).
- Produces: `KimiLocalDiscovery(..., usage_tracker: WireUsageTracker | None = None)`; discovered `TaskState.metadata["usage"]` = `SessionUsage.to_metadata()` when a wire file exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kimi_wire_usage.py` (reuse the local helpers). Look at how existing `tests/test_kimi_discovery.py` builds sessions (session_index.jsonl + session dirs); mirror that pattern with tmp_path:

```python
def test_discovery_attaches_usage_metadata(tmp_path):
    from aacc.kimi_discovery import KimiLocalDiscovery

    home = tmp_path / "home"
    session_dir = home / "sessions" / "wd_x" / "s1"
    path = wire_path(session_dir)
    write_lines(path, [usage_record()])
    (session_dir / "state.json").write_text(
        json.dumps({"title": "demo", "updatedAt": "2026-07-24T00:00:00Z"}),
        encoding="utf-8",
    )
    index = home / "session_index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        json.dumps(
            {
                "sessionId": "s1",
                "sessionDir": str(session_dir),
                "workDir": "/tmp/work",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    discovery = KimiLocalDiscovery(
        kimi_home=home,
        agent_process_alive=lambda: False,
    )
    tasks = discovery.discover({"s1"})
    assert len(tasks) == 1
    usage = tasks[0].state.metadata.get("usage")
    assert usage is not None
    assert usage["output_tokens"] == 200
    assert usage["total_input_tokens"] == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kimi_wire_usage.py::test_discovery_attaches_usage_metadata -q`
Expected: FAIL — `usage is None` (metadata not attached yet)

- [ ] **Step 3: Implement**

In `src/aacc/kimi_discovery.py`:

1. Import: `from aacc.kimi_wire_usage import WireUsageTracker`.

2. `KimiLocalDiscovery.__init__` signature: add keyword `usage_tracker: WireUsageTracker | None = None` (after `max_tasks`), and in the body:

```python
        self.usage_tracker = usage_tracker or WireUsageTracker()
```

3. In `discover()`, replace the `metadata={...}` block (lines 290-297) with:

```python
                        metadata={
                            "discovered": True,
                            **(
                                {"work_dir": session["work_dir"]}
                                if session["work_dir"]
                                else {}
                            ),
                            **(
                                {"usage": usage.to_metadata()}
                                if (
                                    usage := self.usage_tracker.poll(
                                        session["session_dir"]
                                    )
                                )
                                else {}
                            ),
                        },
```

(If the walrus inside a dict literal trips ruff/mypy, hoist it: compute `usage = self.usage_tracker.poll(session["session_dir"])` right after `session_id = session["id"]` and reference `usage` in the dict.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kimi_wire_usage.py tests/test_kimi_discovery.py -q`
Expected: all pass (existing discovery tests must stay green — metadata gains a key, nothing removed)

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
.venv/bin/python -m pytest -q
git add src/aacc/kimi_discovery.py tests/test_kimi_wire_usage.py
git commit -m "feat: attach session token usage metadata in Kimi discovery"
```

---

### Task 4: TaskCard usage row + styles

**Files:**
- Modify: `src/aacc/gui.py` — `TaskCard.__init__` (after line 223 `details_layout.addLayout(activity_row)`), `TaskCard.set_state` (after the workdir block, lines 261-267)
- Modify: `src/aacc/styles.qss` (after `#messageLabel` rule)
- Test: `tests/test_gui_usage.py`

**Interfaces:**
- Consumes: `aacc.kimi_metrics.format_usage_line`; `TaskState.metadata["usage"]` (Task 3).
- Produces: `TaskCard.usage_label: QLabel` — visible only for `kimi_code` cards with usage metadata.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_usage.py
from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")


def make_card(qtbot, agent_type: str, metadata: dict):
    from aacc.gui import TaskCard
    from aacc.models import AgentConfig, TaskConfig, TaskState

    task = TaskConfig(id="kimi:s1", slot=1, name="demo", agent=AgentConfig(type=agent_type))
    state = TaskState.new("kimi:s1", "RUNNING", metadata=metadata)
    card = TaskCard(task, state)
    qtbot.addWidget(card)
    return card


USAGE = {
    "total_input_tokens": 12_300,
    "output_tokens": 1_200,
    "cache_read_pct": 68,
    "speed_tps": 42,
}


def test_kimi_card_shows_usage_row(qtbot):
    card = make_card(qtbot, "kimi_code", {"usage": USAGE})
    assert card.usage_label.isVisible() or not card.usage_label.isHidden()
    assert card.usage_label.text() == "↑12.3k ↓1.2k 缓存68% · 42 tok/s"


def test_card_hides_usage_row_without_metadata(qtbot):
    card = make_card(qtbot, "kimi_code", {})
    assert card.usage_label.isHidden()


def test_non_kimi_card_hides_usage_row(qtbot):
    card = make_card(qtbot, "codex_cli", {"usage": USAGE})
    assert card.usage_label.isHidden()


def test_usage_row_updates_on_set_state(qtbot):
    from aacc.models import TaskState

    card = make_card(qtbot, "kimi_code", {})
    updated = TaskState.new("kimi:s1", "RUNNING", metadata={"usage": USAGE})
    card.set_state(updated)
    assert not card.usage_label.isHidden()
    assert "42 tok/s" in card.usage_label.text()
```

Note: widgets not added to a shown window report `isVisible() == False`; assert on `isHidden()` instead (hidden flag is what `set_state` controls). Adjust assertions to `assert not card.usage_label.isHidden()` / `assert card.usage_label.isHidden()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_usage.py -q`
Expected: FAIL — `AttributeError: 'TaskCard' object has no attribute 'usage_label'`

- [ ] **Step 3: Implement**

In `src/aacc/gui.py`:

1. Import: `from aacc.kimi_metrics import format_usage_line`.

2. `TaskCard.__init__`, after `details_layout.addLayout(activity_row)` (line 223), add:

```python
        self.usage_label = QLabel()
        self.usage_label.setObjectName("usageLabel")
        self.usage_label.hide()
        details_layout.addWidget(self.usage_label)
```

3. `TaskCard.set_state`, after the workdir if/else block (lines 261-267), add:

```python
        usage = state.metadata.get("usage")
        if self.task.agent.type == "kimi_code" and isinstance(usage, dict):
            self.usage_label.setText(format_usage_line(usage))
            self.usage_label.show()
        else:
            self.usage_label.hide()
```

4. `src/aacc/styles.qss`, after the `#messageLabel` rule:

```css
#usageLabel { color: #6b7c93; font-family: Menlo; font-size: 10px; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_usage.py -q`
Expected: 4 passed

- [ ] **Step 5: Lint, type-check, full suite, commit**

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
.venv/bin/python -m pytest -q
git add src/aacc/gui.py src/aacc/styles.qss tests/test_gui_usage.py
git commit -m "feat: render token usage row on Kimi task cards"
```

---

### Task 5: Attribution + CHANGELOG + final gate

**Files:**
- Modify: `NOTICE` (if it doesn't exist yet — i.e. this plan landed before subsystem A — create it with the content from the subsystem A plan, Task 7; the 十叶 / kimi-code-monitor entry is required by this plan regardless)
- Modify: `CHANGELOG.md`, `CHANGELOG.zh-CN.md`

- [ ] **Step 1: Verify NOTICE contains the kimi-code-monitor entry**

```bash
grep -n "kimi-code-monitor" NOTICE || echo MISSING
```

If MISSING, create `NOTICE` with the full content from `docs/superpowers/plans/2026-07-24-kimi-quota-monitor.md` Task 7 Step 1 (all three entries), and add the README attribution section from that task too.

- [ ] **Step 2: CHANGELOG**

Add under `## Unreleased` / `## 未发布`:

```markdown
- Kimi Code 任务卡片新增 token 用量行：累计输入/输出、缓存命中率与中位生成速度（增量读取 wire 文件）。
```

English: `Kimi Code task cards now show a token usage row: cumulative input/output, cache hit rate, and median generation speed (incremental wire tailing).`

- [ ] **Step 3: Final gate + commit**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
git add NOTICE README.md README.zh-CN.md CHANGELOG.md CHANGELOG.zh-CN.md
git commit -m "docs: attribute kimi-code-monitor metrics port and changelog usage row"
```

---

## Self-Review Notes

- Spec coverage: metrics port (Task 1), incremental wire tailing with truncation/partial-line discipline and 64 KiB bound (Task 2), discovery metadata (Task 3), card rendering kimi_code-only (Task 4), attribution (Task 5).
- Type consistency: `SessionUsage.to_metadata()` keys (`total_input_tokens`, `output_tokens`, `cache_read_pct`, `speed_tps`) exactly match what `format_usage_line` reads.
- Non-goal check: no WS, no kimi web, no quota — those are subsystems C and A.
