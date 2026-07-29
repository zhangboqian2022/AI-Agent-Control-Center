"""Parse and merge Kimi web-membership quota metadata.

The web membership service is a first-party but non-public Connect endpoint,
so this module deliberately accepts the small set of shapes observed in the
web client without coupling network or browser state to the parser.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from aacc.kimi_quota import KimiQuota, QuotaDetail, QuotaStatus


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _percentage(value: object) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    if number <= 1:
        number *= 100
    if number > 100:
        return None
    return round(number)


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("seconds")
    number = _number(value)
    if number is not None:
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _first(mapping: object, *keys: str) -> object | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            value: object = mapping[key]
            return value
    return None


def _ratio(value: object) -> int | None:
    if isinstance(value, dict):
        value = _first(
            value,
            "usedRatio",
            "amountUsedRatio",
            "usedPercent",
            "percentage",
            "ratio",
            "value",
        )
    return _percentage(value)


def _reset(value: object) -> datetime | None:
    return _timestamp(
        _first(
            value,
            "resetTime",
            "resetAt",
            "expireTime",
            "expiresAt",
            "nextResetTime",
        )
    )


def _detail(value: object) -> QuotaDetail | None:
    percentage = _ratio(value)
    if percentage is None:
        return None
    return QuotaDetail(
        used=percentage,
        limit=100,
        remaining=100 - percentage,
        reset_at=_reset(value),
        percentage=percentage,
    )


def _subscription_candidates(subscription: object) -> list[dict[str, Any]]:
    if not isinstance(subscription, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for key in ("subscription", "activeSubscription"):
        value = subscription.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for key in ("subscriptions", "items"):
        value = subscription.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    if any(
        key in subscription for key in ("nextBillingTime", "expireTime", "level", "membershipLevel")
    ):
        candidates.append(subscription)
    return candidates


def _active_subscription(subscription: object) -> dict[str, Any] | None:
    candidates = _subscription_candidates(subscription)
    for candidate in candidates:
        status = candidate.get("status")
        if isinstance(status, str) and "ACTIVE" in status.upper():
            return candidate
    return candidates[0] if candidates else None


def parse_membership_quota(
    stats: object,
    subscription: object,
    *,
    now: datetime,
) -> KimiQuota:
    """Convert Kimi web membership responses into the shared quota model."""

    root: dict[str, Any] = stats if isinstance(stats, dict) else {}
    balance = root.get("subscriptionBalance")
    monthly = _detail(balance)
    five_hour = _detail(root.get("ratelimitCode5h"))
    weekly = _detail(root.get("ratelimitCode7d"))
    active = _active_subscription(subscription)

    # Only the balance object describes the quota-window reset. Subscription
    # and root-level billing dates can instead be annual renewal dates.
    monthly_reset = None
    for reset_key in ("expireTime", "resetTime"):
        monthly_reset = _timestamp(_first(balance, reset_key))
        if monthly_reset is not None:
            break
    if monthly is not None:
        monthly = QuotaDetail(
            used=monthly.used,
            limit=monthly.limit,
            remaining=monthly.remaining,
            reset_at=monthly_reset,
            percentage=monthly.percentage,
        )

    known = sum(item is not None for item in (five_hour, weekly, monthly))
    status = QuotaStatus.OK if known == 3 else QuotaStatus.PARTIAL if known else QuotaStatus.UNKNOWN
    level = _first(active, "level", "membershipLevel", "planType")
    return KimiQuota(
        five_hour=five_hour,
        weekly=weekly,
        monthly=monthly,
        membership_level=level if isinstance(level, str) and level else None,
        booster=None,
        status=status,
        fetched_at=now,
    )


def merge_kimi_quota(
    web: KimiQuota | None,
    code: KimiQuota | None,
    *,
    now: datetime | None = None,
    fallback_max_age_seconds: float = 330.0,
) -> KimiQuota:
    """Prefer coherent web data and use Kimi Code only for its two windows."""

    reference_time = now or datetime.now(UTC)
    code_is_current = False
    code_is_fresh = False
    if code is not None and code.fetched_at is not None:
        age_seconds = (reference_time - code.fetched_at).total_seconds()
        code_is_current = age_seconds >= 0
        code_is_fresh = 0 <= age_seconds <= fallback_max_age_seconds
    # A temporary background refresh failure must not turn a previously known
    # 0% (or any other valid value) into "--". Keep verifiable last-known
    # values and label the merged snapshot stale until a poll succeeds.
    fallback = code if code_is_current else None
    five_hour = (
        web.five_hour
        if web and web.five_hour is not None
        else (fallback.five_hour if fallback else None)
    )
    weekly = (
        web.weekly if web and web.weekly is not None else (fallback.weekly if fallback else None)
    )
    monthly = web.monthly if web else None
    known = sum(item is not None for item in (five_hour, weekly, monthly))
    stale_fallback_used = (
        not code_is_fresh
        and fallback is not None
        and (
            (web is None or web.five_hour is None)
            and fallback.five_hour is not None
            or (web is None or web.weekly is None)
            and fallback.weekly is not None
        )
    )
    status = (
        QuotaStatus.STALE
        if known and stale_fallback_used
        else QuotaStatus.OK
        if known == 3
        else QuotaStatus.PARTIAL
        if known
        else QuotaStatus.UNKNOWN
    )
    fetched_candidates = [
        item.fetched_at
        for item in (web, fallback)
        if item is not None and item.fetched_at is not None
    ]
    fetched_at = max(fetched_candidates) if fetched_candidates else None
    membership_level = (
        web.membership_level
        if web is not None and web.membership_level
        else fallback.membership_level
        if fallback is not None
        else None
    )
    booster = (
        web.booster
        if web is not None and web.booster is not None
        else (fallback.booster if fallback is not None else None)
    )
    return KimiQuota(
        five_hour=five_hour,
        weekly=weekly,
        monthly=monthly,
        membership_level=membership_level,
        booster=booster,
        status=status,
        fetched_at=fetched_at,
    )
