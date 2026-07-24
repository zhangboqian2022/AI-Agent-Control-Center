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


def test_normalize_usage_non_finite_becomes_zero():
    usage = normalize_usage({"output": float("inf"), "inputOther": float("nan")})
    assert usage.output_tokens == 0
    assert usage.input_tokens == 0


def test_decode_speed_thresholds():
    assert decode_speed(200, 4000) == 50
    assert decode_speed(0, 4000) is None
    assert decode_speed(200, 50) is None  # below MIN_SPEED_DURATION_MS
    assert decode_speed(200, "junk") is None
    assert decode_speed(200, None) is None


def test_decode_speed_non_finite_duration():
    assert decode_speed(200, float("nan")) is None
    assert decode_speed(200, float("inf")) is None


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
