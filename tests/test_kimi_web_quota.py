from __future__ import annotations

from datetime import UTC, datetime

from aacc.kimi_quota import KimiQuota, QuotaDetail, QuotaStatus
from aacc.kimi_web_quota import merge_kimi_quota, parse_membership_quota

NOW = datetime(2026, 7, 27, 5, 0, tzinfo=UTC)


def detail(percentage: int, *, reset: datetime | None = None) -> QuotaDetail:
    return QuotaDetail(
        used=percentage,
        limit=100,
        remaining=100 - percentage,
        reset_at=reset,
        percentage=percentage,
    )


def quota(
    *,
    five_hour: QuotaDetail | None,
    weekly: QuotaDetail | None,
    monthly: QuotaDetail | None,
    fetched_at: datetime | None = NOW,
) -> KimiQuota:
    known = sum(item is not None for item in (five_hour, weekly, monthly))
    return KimiQuota(
        five_hour=five_hour,
        weekly=weekly,
        monthly=monthly,
        membership_level=None,
        booster=None,
        status=QuotaStatus.OK if known == 3 else QuotaStatus.PARTIAL,
        fetched_at=fetched_at,
    )


def test_parse_membership_quota_reads_all_three_windows_and_resets():
    stats = {
        "subscriptionBalance": {
            "amountUsedRatio": 0.3103,
            "expireTime": "2026-08-20T02:30:00Z",
        },
        "ratelimitCode5h": {
            "usedRatio": 0,
            "resetTime": "2026-07-27T13:28:47Z",
        },
        "ratelimitCode7d": {
            "usedRatio": 0.7201,
            "resetTime": "2026-08-03T13:28:47Z",
        },
    }
    subscription = {
        "subscriptions": [
            {
                "status": "SUBSCRIPTION_STATUS_ACTIVE",
                "nextBillingTime": "2026-08-20T02:30:00Z",
                "level": "ALLEGRO",
            }
        ]
    }

    result = parse_membership_quota(stats, subscription, now=NOW)

    assert result.status is QuotaStatus.OK
    assert result.five_hour is not None
    assert result.five_hour.percentage == 0
    assert result.weekly is not None
    assert result.weekly.percentage == 72
    assert result.monthly is not None
    assert result.monthly.percentage == 31
    assert result.monthly.reset_at == datetime(2026, 8, 20, 2, 30, tzinfo=UTC)
    assert result.membership_level == "ALLEGRO"
    assert result.fetched_at == NOW


def test_parse_membership_quota_accepts_top_level_numeric_rate_limits():
    stats = {
        "subscriptionBalance": {"amountUsedRatio": 31.03},
        "ratelimitCode5h": 0.1,
        "ratelimitCode7d": 72.01,
        "expireTime": 1_785_610_800,
    }

    result = parse_membership_quota(stats, {}, now=NOW)

    assert result.five_hour is not None
    assert result.five_hour.percentage == 10
    assert result.weekly is not None
    assert result.weekly.percentage == 72
    assert result.monthly is not None
    assert result.monthly.percentage == 31


def test_parse_membership_quota_rejects_invalid_values_without_fabricating_zero():
    result = parse_membership_quota(
        {
            "subscriptionBalance": {"amountUsedRatio": "secret"},
            "ratelimitCode5h": float("nan"),
            "ratelimitCode7d": 101,
        },
        {},
        now=NOW,
    )

    assert result.status is QuotaStatus.UNKNOWN
    assert result.five_hour is None
    assert result.weekly is None
    assert result.monthly is None


def test_merge_web_quota_wins_and_code_only_fills_missing_windows():
    web = quota(
        five_hour=detail(1),
        weekly=None,
        monthly=detail(31),
        fetched_at=datetime(2026, 7, 27, 5, 5, tzinfo=UTC),
    )
    code = quota(
        five_hour=detail(2),
        weekly=detail(72),
        monthly=detail(99),
        fetched_at=NOW,
    )

    result = merge_kimi_quota(web, code)

    assert result.five_hour is web.five_hour
    assert result.weekly is code.weekly
    assert result.monthly is web.monthly
    assert result.fetched_at == web.fetched_at
    assert result.status is QuotaStatus.OK


def test_merge_never_uses_kimi_code_monthly_value():
    code = quota(
        five_hour=detail(2),
        weekly=detail(72),
        monthly=detail(99),
    )

    result = merge_kimi_quota(None, code)

    assert result.five_hour is code.five_hour
    assert result.weekly is code.weekly
    assert result.monthly is None
    assert result.status is QuotaStatus.PARTIAL
