"""Kimi managed-platform quota fetch + parse.

Parsing follows the intentionally loose rules of MoonshotAI/kimi-code
`packages/oauth` `managed-usage.ts` (MIT License, Copyright (c) MoonshotAI)
plus edge-case fixes from KimiCodeBar (MIT License, Copyright (c) xifandev):
the booster balance is `balance.amountLeft` in units of 1e-8 yuan and is
only meaningful while the wallet status is ACTIVE/ENABLED — otherwise the
API returns a monthly-cap-derived number that must be shown as 0.
See NOTICE.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
HTTP_TIMEOUT_SECONDS = 30.0
FIVE_HOUR_WINDOW_MINUTES = 300
BOOSTER_ENABLED_STATUSES = {"STATUS_ACTIVE", "STATUS_ENABLED"}


class KimiQuotaError(RuntimeError):
    """Quota fetch or parse failed."""


class KimiQuotaUnauthorizedError(KimiQuotaError):
    """The access token was rejected; re-authorization is required."""


@dataclass(frozen=True)
class QuotaDetail:
    used: int
    limit: int
    remaining: int
    reset_at: datetime | None
    percentage: int


@dataclass(frozen=True)
class BoosterWallet:
    status: str
    is_enabled: bool
    balance_yuan: float


@dataclass(frozen=True)
class KimiQuota:
    weekly: QuotaDetail
    five_hour: QuotaDetail
    total_quota: QuotaDetail
    membership_level: str | None
    booster: BoosterWallet | None


def usages_url() -> str:
    base = os.environ.get("KIMI_CODE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/usages"


def _to_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                number = float(value)
            except ValueError:
                return None
            return int(number) if math.isfinite(number) else None
    return None


def _parse_reset(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _make_detail(raw: object) -> QuotaDetail:
    section: dict[str, Any] = raw if isinstance(raw, dict) else {}
    limit = _to_int(section.get("limit")) or 0
    used = _to_int(section.get("used"))
    remaining = _to_int(section.get("remaining"))
    if used is None:
        used = max(0, limit - remaining) if remaining is not None else 0
    if remaining is None:
        remaining = max(0, limit - used)
    percentage = round(used / limit * 100) if limit > 0 else 0
    reset_at = _parse_reset(
        section.get("resetTime") or section.get("reset_at") or section.get("resetAt")
    )
    return QuotaDetail(
        used=used, limit=limit, remaining=remaining, reset_at=reset_at, percentage=percentage
    )


def _is_five_hour_window(window: object) -> bool:
    if not isinstance(window, dict):
        return False
    duration = _to_int(window.get("duration"))
    if duration != FIVE_HOUR_WINDOW_MINUTES:
        return False
    unit = window.get("timeUnit") or window.get("unit") or ""
    if not isinstance(unit, str) or not unit:
        return True
    # The live API spells the unit "TIME_UNIT_MINUTE"; older payloads used
    # short forms ("m", "min", "minute"). "month" must not match.
    normalized = unit.lower().removeprefix("time_unit_")
    return normalized == "m" or normalized.startswith("min")


def _parse_booster(raw: object) -> BoosterWallet | None:
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    status_text = status if isinstance(status, str) and status else "STATUS_UNKNOWN"
    is_enabled = status_text.upper() in BOOSTER_ENABLED_STATUSES
    balance = raw.get("balance")
    amount_left = _to_int(balance.get("amountLeft")) if isinstance(balance, dict) else None
    balance_yuan = (
        max(0.0, amount_left / 100_000_000) if is_enabled and amount_left is not None else 0.0
    )
    return BoosterWallet(status=status_text, is_enabled=is_enabled, balance_yuan=balance_yuan)


def parse_quota(data: object) -> KimiQuota:
    root: dict[str, Any] = data if isinstance(data, dict) else {}
    weekly = _make_detail(root.get("usage"))
    five_hour = QuotaDetail(used=0, limit=0, remaining=0, reset_at=None, percentage=0)
    limits = root.get("limits")
    if isinstance(limits, list):
        for item in limits:
            if isinstance(item, dict) and _is_five_hour_window(item.get("window")):
                five_hour = _make_detail(item.get("detail"))
                break
    total_quota = _make_detail(root.get("totalQuota"))
    membership_level: str | None = None
    user = root.get("user")
    if isinstance(user, dict):
        membership = user.get("membership")
        if isinstance(membership, dict):
            level = membership.get("level")
            if isinstance(level, str) and level:
                membership_level = level
    return KimiQuota(
        weekly=weekly,
        five_hour=five_hour,
        total_quota=total_quota,
        membership_level=membership_level,
        booster=_parse_booster(root.get("boosterWallet")),
    )


def _error_detail(data: object) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            return error
        for key in ("message", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return "unknown"


def fetch_quota(client: httpx.Client, access_token: str) -> KimiQuota:
    try:
        response = client.get(
            usages_url(),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        raise KimiQuotaError(f"Quota request failed: {error}") from error
    if response.status_code in (401, 403):
        raise KimiQuotaUnauthorizedError(
            f"Quota request rejected (HTTP {response.status_code})"
        )
    if response.status_code != 200:
        try:
            detail = _error_detail(response.json())
        except ValueError:
            detail = "unknown"
        raise KimiQuotaError(f"Quota request failed (HTTP {response.status_code}): {detail}")
    try:
        payload: object = response.json()
    except ValueError:
        raise KimiQuotaError("Quota response is not valid JSON") from None
    return parse_quota(payload)


def format_reset_countdown(reset_at: datetime | None, *, now: datetime | None = None) -> str:
    if reset_at is None:
        return "未知"
    current = now or datetime.now(UTC)
    seconds = (reset_at - current).total_seconds()
    if seconds <= 0:
        return "即将重置"
    days = int(seconds // 86_400)
    hours = int((seconds % 86_400) // 3_600)
    minutes = int((seconds % 3_600) // 60)
    if days > 0:
        return f"{days}天{hours}小时后重置"
    if hours > 0:
        return f"{hours}小时{minutes}分钟后重置"
    if minutes > 0:
        return f"{minutes}分钟后重置"
    return "即将重置"


def format_balance(yuan: float | None) -> str:
    if yuan is None:
        return ""
    return f"¥{yuan:.2f}"
