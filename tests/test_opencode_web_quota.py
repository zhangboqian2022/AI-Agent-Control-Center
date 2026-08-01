from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aacc.kimi_quota import QuotaStatus
from aacc.opencode_web_quota import parse_opencode_quota


def _now() -> datetime:
    return datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _payload(node: object) -> object:
    return {"raw": {"subscription": node}}


def test_parse_full_payload_ok() -> None:
    now = _now()
    quota = parse_opencode_quota(
        _payload(
            {
                "rollingUsage": {"usagePercent": 0, "resetInSec": 17760},
                "weeklyUsage": {"usagePercent": 42.5, "resetInSec": 226800},
                "monthlyUsage": {"usagePercent": 100, "resetInSec": 2674800},
            }
        ),
        now=now,
    )
    assert quota.status is QuotaStatus.OK
    assert quota.rolling is not None
    assert quota.rolling.percentage == 0
    assert quota.rolling.reset_seconds == 17760
    assert quota.rolling.reset_at == now + timedelta(seconds=17760)
    assert quota.weekly is not None and quota.weekly.percentage == 43
    assert quota.monthly is not None and quota.monthly.percentage == 100


def test_parse_fraction_percent_scaled() -> None:
    quota = parse_opencode_quota(
        {"subscription": {"rollingUsage": {"usagePercent": 0.42, "resetInSec": 60}}},
        now=_now(),
    )
    assert quota.rolling is not None and quota.rolling.percentage == 42


def test_parse_partial_when_window_missing() -> None:
    quota = parse_opencode_quota(
        {"subscription": {"rollingUsage": {"usagePercent": 10, "resetInSec": 60}}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.rolling is not None
    assert quota.weekly is None and quota.monthly is None


def test_parse_unknown_when_no_subscription() -> None:
    quota = parse_opencode_quota({"unrelated": {}}, now=_now())
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.rolling is None and quota.weekly is None and quota.monthly is None


def test_parse_direct_subscription_node() -> None:
    quota = parse_opencode_quota(
        {"rollingUsage": {"usagePercent": 5, "resetInSec": 3600}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.rolling is not None and quota.rolling.percentage == 5


def test_parse_invalid_values_ignored() -> None:
    quota = parse_opencode_quota(
        {
            "subscription": {
                "rollingUsage": {"usagePercent": 101, "resetInSec": -1},
                "weeklyUsage": {"usagePercent": "abc", "resetInSec": "not-a-number"},
                "monthlyUsage": {"usagePercent": True, "resetInSec": None},
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.rolling is None and quota.weekly is None and quota.monthly is None
