from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aacc.kimi_quota import QuotaStatus
from aacc.qwen_web_quota import parse_qwen_quota


def _now() -> datetime:
    return datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def test_parse_full_payload_ok() -> None:
    now = _now()
    quota = parse_qwen_quota(
        {
            "raw": {
                "fiveHour": {"percentage": 30, "resetSeconds": 18000},
                "sevenDay": {"percentage": 65, "resetSeconds": 604800},
            }
        },
        now=now,
    )
    assert quota.status is QuotaStatus.OK
    assert quota.five_hour is not None
    assert quota.five_hour.percentage == 30
    assert quota.five_hour.reset_at == now + timedelta(seconds=18000)
    assert quota.weekly is not None and quota.weekly.percentage == 65


def test_parse_uses_usage_percent_alias() -> None:
    quota = parse_qwen_quota(
        {
            "fiveHour": {"usagePercent": 12, "resetInSec": 3600},
            "weekly": {"usagePercent": 88, "resetInSec": 86400},
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.OK
    assert quota.five_hour is not None and quota.five_hour.percentage == 12
    assert quota.weekly is not None and quota.weekly.percentage == 88


def test_parse_partial_when_one_window_missing() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHour": {"percentage": 30, "resetSeconds": 18000}}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.five_hour is not None
    assert quota.weekly is None


def test_parse_unknown_when_no_window() -> None:
    quota = parse_qwen_quota({"unrelated": {}}, now=_now())
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.five_hour is None and quota.weekly is None


def test_parse_invalid_values_ignored() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "fiveHour": {"percentage": 101, "resetSeconds": -1},
                "sevenDay": {"percentage": "abc", "resetSeconds": "not-a-number"},
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.five_hour is None and quota.weekly is None


def test_parse_accepts_7d_alias_key() -> None:
    quota = parse_qwen_quota(
        {"fiveHour": {"percentage": 0}, "7d": {"percentage": 100}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.OK
    assert quota.five_hour is not None and quota.five_hour.percentage == 0
    assert quota.weekly is not None and quota.weekly.percentage == 100


def test_parse_percentage_only_without_reset() -> None:
    quota = parse_qwen_quota(
        {"fiveHour": {"percentage": 42}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.five_hour is not None
    assert quota.five_hour.reset_at is None
