"""Parse Bailian (Qwen Code) token-plan quota payloads into the shared model.

The Bailian personal token-plan page renders a 5-hour and a 7-day window as a
micro-frontend (``bailian-tokenplan``). The in-page extraction script emits the
rendered text snippets around each window label; this module converts those
captured snippets into ``QwenQuota`` without coupling the parser to the
browser.

Percentages keep their fractional part (the console renders values such as
``0.04%``). A window only counts as present when a percentage was rendered:
the anonymous/login view repeats the window labels in marketing copy without
usage numbers, and that copy must not be mistaken for quota data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from aacc.kimi_quota import QuotaDetail, QuotaStatus

_FIVE_HOUR_KEYS = ("fiveHourText", "five_hour_text")
_WEEKLY_KEYS = ("weeklyText", "sevenDayText", "weekly_text")
_PERCENTAGE_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_RESET_MARKERS = re.compile(r"重置|reset|resets|后", re.IGNORECASE)
_RESET_UNITS = (
    (re.compile(r"(\d+)\s*(?:天|days?|day)"), 86_400),
    (re.compile(r"(\d+)\s*(?:小时|hours?|hour)"), 3_600),
    (re.compile(r"(\d+)\s*(?:分钟|minutes?|minute|min)"), 60),
)


@dataclass(frozen=True)
class QwenQuota:
    five_hour: QuotaDetail | None
    weekly: QuotaDetail | None
    status: QuotaStatus
    fetched_at: datetime


def _window_text(node: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _percentage(text: str) -> float | None:
    match = _PERCENTAGE_PATTERN.search(text)
    if match is None:
        return None
    value = float(match.group(1))
    return value if 0 <= value <= 100 else None


def _reset_seconds(lines: list[str]) -> int | None:
    candidates = lines[1:] if len(lines) > 1 else []
    marked = [line for line in candidates if _RESET_MARKERS.search(line)]
    text = "\n".join(marked if marked else candidates)
    seconds = 0
    for pattern, unit in _RESET_UNITS:
        for match in pattern.finditer(text):
            seconds += int(match.group(1)) * unit
    return seconds if seconds > 0 else None


def _parse_window(text: str | None, *, now: datetime) -> QuotaDetail | None:
    if not text:
        return None
    percentage = _percentage(text)
    if percentage is None:
        return None
    reset_seconds = _reset_seconds(text.splitlines())
    reset_at = now + timedelta(seconds=reset_seconds) if reset_seconds else None
    used = int(percentage)
    return QuotaDetail(
        used=used,
        limit=100,
        remaining=100 - used,
        reset_at=reset_at,
        percentage=percentage,
    )


def parse_qwen_quota(payload: object, *, now: datetime) -> QwenQuota:
    """Convert a captured Bailian token-plan payload into ``QwenQuota``."""

    raw: object = payload.get("raw") if isinstance(payload, dict) and "raw" in payload else payload
    node: object = raw if isinstance(raw, dict) else None
    if not isinstance(node, dict):
        return QwenQuota(None, None, QuotaStatus.UNKNOWN, now)
    five_hour = _parse_window(_window_text(node, _FIVE_HOUR_KEYS), now=now)
    weekly = _parse_window(_window_text(node, _WEEKLY_KEYS), now=now)
    known = sum(item is not None for item in (five_hour, weekly))
    status = (
        QuotaStatus.OK if known == 2 else QuotaStatus.PARTIAL if known == 1 else QuotaStatus.UNKNOWN
    )
    return QwenQuota(five_hour=five_hour, weekly=weekly, status=status, fetched_at=now)
