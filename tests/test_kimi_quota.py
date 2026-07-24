from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from aacc.kimi_quota import (
    KimiQuotaError,
    KimiQuotaUnauthorizedError,
    fetch_quota,
    format_balance,
    format_reset_countdown,
    parse_quota,
    usages_url,
)


def full_payload() -> dict:
    return {
        "usage": {"limit": "1000", "used": "420", "resetTime": "2026-07-31T16:00:00.000Z"},
        "limits": [
            {
                "window": {"duration": 10080},
                "detail": {"limit": "999", "used": "1"},
            },
            {
                "window": {"duration": 300},
                "detail": {"limit": "100", "used": "10", "resetTime": "2026-07-24T20:00:00Z"},
            },
        ],
        "totalQuota": {"limit": "5000", "remaining": "3000"},
        "user": {"membership": {"level": "PRO"}},
        "boosterWallet": {
            "status": "STATUS_ACTIVE",
            "balance": {"amount": "1000000000", "amountLeft": "315250700"},
        },
    }


def test_parse_full_payload():
    quota = parse_quota(full_payload())
    assert quota.weekly.used == 420
    assert quota.weekly.limit == 1000
    assert quota.weekly.percentage == 42
    assert quota.weekly.reset_at == datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    assert quota.five_hour.used == 10
    assert quota.five_hour.limit == 100
    assert quota.five_hour.percentage == 10
    assert quota.total_quota.remaining == 3000
    assert quota.total_quota.used == 2000
    assert quota.membership_level == "PRO"
    assert quota.booster is not None
    assert quota.booster.is_enabled
    assert quota.booster.balance_yuan == pytest.approx(3.152507)


def test_parse_booster_disabled_shows_zero_balance():
    payload = full_payload()
    payload["boosterWallet"]["status"] = "STATUS_DISABLED"
    quota = parse_quota(payload)
    assert quota.booster is not None
    assert not quota.booster.is_enabled
    assert quota.booster.balance_yuan == 0.0


def test_parse_missing_sections_yield_zero_details():
    quota = parse_quota({})
    assert quota.weekly.used == 0
    assert quota.weekly.limit == 0
    assert quota.weekly.percentage == 0
    assert quota.weekly.reset_at is None
    assert quota.five_hour.limit == 0
    assert quota.membership_level is None
    assert quota.booster is None


def test_parse_used_derived_from_remaining():
    quota = parse_quota({"usage": {"limit": 100, "remaining": 30}})
    assert quota.weekly.used == 70
    assert quota.weekly.remaining == 30


def test_parse_numeric_fields_not_strings():
    quota = parse_quota({"usage": {"limit": 200, "used": 50.0}})
    assert quota.weekly.used == 50
    assert quota.weekly.percentage == 25


def test_parse_garbage_is_safe():
    quota = parse_quota("garbage")
    assert quota.weekly.limit == 0
    quota = parse_quota({"usage": {"limit": "abc", "used": [1]}})
    assert quota.weekly.limit == 0
    assert quota.weekly.used == 0


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_quota_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=full_payload())

    quota = fetch_quota(make_client(handler), "tok")
    assert quota.weekly.percentage == 42


def test_fetch_quota_unauthorized_and_http_error():
    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(KimiQuotaUnauthorizedError):
        fetch_quota(make_client(handler_401), "tok")

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(KimiQuotaError, match="HTTP 500"):
        fetch_quota(make_client(handler_500), "tok")


def test_usages_url_env_override(monkeypatch):
    assert usages_url() == "https://api.kimi.com/coding/v1/usages"
    monkeypatch.setenv("KIMI_CODE_BASE_URL", "https://api.example.com/coding/v1/")
    assert usages_url() == "https://api.example.com/coding/v1/usages"


def test_format_reset_countdown():
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    assert format_reset_countdown(None, now=now) == "未知"
    assert (
        format_reset_countdown(datetime(2026, 7, 27, 14, 0, tzinfo=UTC), now=now)
        == "3天2小时后重置"
    )
    assert (
        format_reset_countdown(datetime(2026, 7, 24, 14, 30, tzinfo=UTC), now=now)
        == "2小时30分钟后重置"
    )
    assert (
        format_reset_countdown(datetime(2026, 7, 24, 12, 45, tzinfo=UTC), now=now)
        == "45分钟后重置"
    )
    assert (
        format_reset_countdown(datetime(2026, 7, 24, 11, 0, tzinfo=UTC), now=now) == "即将重置"
    )


def test_format_balance():
    assert format_balance(None) == ""
    assert format_balance(3.152507) == "¥3.15"
    assert format_balance(0.0) == "¥0.00"


def test_to_int_non_finite_is_safe():
    quota = parse_quota({"usage": {"limit": float("inf"), "used": float("nan")}})
    assert quota.weekly.limit == 0
    assert quota.weekly.used == 0
    quota = parse_quota({"usage": {"limit": "inf", "used": "nan"}})
    assert quota.weekly.limit == 0
    assert quota.weekly.used == 0
