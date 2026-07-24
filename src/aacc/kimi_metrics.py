"""Per-session token usage metrics.

Ported from kimi-code-monitor `metrics.js` (MIT License,
Copyright (c) 2026 十叶) — usage field normalization across the CLI's
wire naming and API naming, cache-hit percentage, and a robust median
generation speed over a sliding sample window. See NOTICE.
"""
from __future__ import annotations

import math
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
    if not math.isfinite(number) or number <= 0:  # NaN/inf or non-positive
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
    if not math.isfinite(duration) or duration < MIN_SPEED_DURATION_MS or output == 0:
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
    tokens_part = f"↑{format_token_count(total_input)} ↓{format_token_count(output)}"
    details: list[str] = []
    if isinstance(cache_pct, int) and not isinstance(cache_pct, bool):
        details.append(f"缓存{cache_pct}%")
    if speed > 0:
        details.append(f"{speed} tok/s")
    if not details:
        return tokens_part
    if len(details) > 1:
        return f"{tokens_part} {' '.join(details[:-1])} · {details[-1]}"
    return f"{tokens_part} · {details[0]}"
