"""Parse opencode.ai workspace usage payloads into the shared quota model.

The workspace page renders Go-plan usage through the same-origin ``/_server``
RPC ``subscription.get``; this module converts captured payloads into a
normalized model without coupling network or browser state to the parser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from aacc.kimi_quota import QuotaStatus


@dataclass(frozen=True)
class OpenCodeUsage:
    percentage: int | None
    reset_seconds: int | None
    reset_at: datetime | None


@dataclass(frozen=True)
class OpenCodeQuota:
    rolling: OpenCodeUsage | None
    weekly: OpenCodeUsage | None
    monthly: OpenCodeUsage | None
    status: QuotaStatus
    fetched_at: datetime


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
    if number > 100:
        return None
    return math.floor(number + 0.5)


def _usage(value: object, *, now: datetime) -> OpenCodeUsage | None:
    if not isinstance(value, dict):
        return None
    percentage = _percentage(value.get("usagePercent"))
    reset = _number(value.get("resetInSec"))
    reset_seconds = int(reset) if reset is not None and reset >= 0 else None
    if percentage is None and reset_seconds is None:
        return None
    reset_at = now + timedelta(seconds=reset_seconds) if reset_seconds is not None else None
    return OpenCodeUsage(percentage, reset_seconds, reset_at)


def parse_opencode_quota(payload: object, *, now: datetime) -> OpenCodeQuota:
    """Convert a captured opencode.ai usage payload into ``OpenCodeQuota``."""

    raw: object = payload.get("raw") if isinstance(payload, dict) and "raw" in payload else payload
    node: object = None
    if isinstance(raw, dict):
        subscription = raw.get("subscription")
        if isinstance(subscription, dict):
            node = subscription
        elif isinstance(raw.get("rollingUsage"), dict):
            node = raw
    if not isinstance(node, dict):
        return OpenCodeQuota(None, None, None, QuotaStatus.UNKNOWN, now)
    rolling = _usage(node.get("rollingUsage"), now=now)
    weekly = _usage(node.get("weeklyUsage"), now=now)
    monthly = _usage(node.get("monthlyUsage"), now=now)
    known = sum(item is not None for item in (rolling, weekly, monthly))
    status = QuotaStatus.OK if known == 3 else QuotaStatus.PARTIAL if known else QuotaStatus.UNKNOWN
    return OpenCodeQuota(rolling, weekly, monthly, status, now)
