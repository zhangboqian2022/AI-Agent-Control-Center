"""Parse Bailian (Qwen Code) token-plan quota payloads into the shared model.

The Bailian personal token-plan page renders a 5-hour and a 7-day window as a
micro-frontend (``bailian-tokenplan``). The in-page extraction script reads the
rendered DOM and emits normalized values; this module converts those captured
values into ``QwenQuota`` without coupling the parser to the browser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from aacc.kimi_quota import QuotaDetail, QuotaStatus


@dataclass(frozen=True)
class QwenQuota:
    five_hour: QuotaDetail | None
    weekly: QuotaDetail | None
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
    if number is None or number < 0 or number > 100:
        return None
    return round(number)


def _usage(node: object, *, now: datetime) -> QuotaDetail | None:
    if not isinstance(node, dict):
        return None
    percentage = _percentage(node.get("percentage", node.get("usagePercent")))
    reset = _number(node.get("resetSeconds", node.get("resetInSec")))
    reset_seconds = int(reset) if reset is not None and reset >= 0 else None
    if percentage is None and reset_seconds is None:
        return None
    reset_at = now + timedelta(seconds=reset_seconds) if reset_seconds is not None else None
    return QuotaDetail(
        used=percentage if percentage is not None else 0,
        limit=100,
        remaining=100 - percentage if percentage is not None else 100,
        reset_at=reset_at,
        percentage=percentage if percentage is not None else 0,
    )


def parse_qwen_quota(payload: object, *, now: datetime) -> QwenQuota:
    """Convert a captured Bailian token-plan payload into ``QwenQuota``."""

    raw: object = payload.get("raw") if isinstance(payload, dict) and "raw" in payload else payload
    node: object = raw if isinstance(raw, dict) else None
    if not isinstance(node, dict):
        return QwenQuota(None, None, QuotaStatus.UNKNOWN, now)
    five_hour = _usage(node.get("fiveHour", node.get("five_hour")), now=now)
    weekly = _usage(
        node.get("weekly")
        if "weekly" in node
        else node.get("sevenDay", node.get("7d") if "7d" in node else None),
        now=now,
    )
    known = sum(item is not None for item in (five_hour, weekly))
    status = (
        QuotaStatus.OK if known == 2 else QuotaStatus.PARTIAL if known == 1 else QuotaStatus.UNKNOWN
    )
    return QwenQuota(five_hour=five_hour, weekly=weekly, status=status, fetched_at=now)
