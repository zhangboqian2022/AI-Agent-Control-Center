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

_FIVE_HOUR_KEYS = ("personalFiveHourText", "fiveHourText", "five_hour_text")
_WEEKLY_KEYS = ("personalWeeklyText", "weeklyText", "sevenDayText", "weekly_text")
_TEAM_KEYS = ("teamTotalText", "team_total_text")
_PERCENTAGE_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_USED_PERCENTAGE_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:已用|used)", re.IGNORECASE)
_GAUGE_TICKS = ("0%", "50%", "90%", "100%")
_RESET_MARKERS = re.compile(r"重置|reset|resets|后", re.IGNORECASE)
_ABSOLUTE_RESET_PATTERN = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?"
)
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
    team_total: QuotaDetail | None = None


def _window_text(node: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _before_gauge_ticks(text: str) -> str:
    """Drop the gauge tick ladder (0%/50%/90%/100%) and everything after it.

    The console renders each quota gauge as ``value`` followed by the tick
    ladder, so everything before the ladder is signal and the ladder itself
    (plus the marketing copy below it) is noise. Snippets without a ladder
    are returned unchanged.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index in range(len(lines) - len(_GAUGE_TICKS) + 1):
        if tuple(lines[index : index + len(_GAUGE_TICKS)]) == _GAUGE_TICKS:
            return "\n".join(lines[:index])
    return "\n".join(lines)


def _percentage(text: str) -> float | None:
    searchable = _before_gauge_ticks(text)
    match = _USED_PERCENTAGE_PATTERN.search(searchable) or _PERCENTAGE_PATTERN.search(searchable)
    if match is None:
        return None
    value = float(match.group(1))
    return value if 0 <= value <= 100 else None


def _reset_at(lines: list[str], *, now: datetime) -> datetime | None:
    candidates = lines[1:] if len(lines) > 1 else []
    marked = [line for line in candidates if _RESET_MARKERS.search(line)]
    ordered = marked if marked else candidates
    for line in ordered:
        match = _ABSOLUTE_RESET_PATTERN.search(line)
        if match is None:
            continue
        year, month, day, hour, minute, second = (int(group or 0) for group in match.groups())
        try:
            local = datetime(year, month, day, hour, minute, second)
        except ValueError:
            continue
        # The console renders the instant in the local timezone.
        return local.astimezone()
    seconds = 0
    for pattern, unit in _RESET_UNITS:
        for match in pattern.finditer("\n".join(ordered)):
            seconds += int(match.group(1)) * unit
    return now + timedelta(seconds=seconds) if seconds > 0 else None


def _parse_window(text: str | None, *, now: datetime) -> QuotaDetail | None:
    if not text:
        return None
    percentage = _percentage(text)
    if percentage is None:
        return None
    reset_at = _reset_at(text.splitlines(), now=now)
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
    team_total = _parse_window(_window_text(node, _TEAM_KEYS), now=now)
    known = sum(item is not None for item in (five_hour, weekly))
    status = (
        QuotaStatus.OK
        if known == 2
        else QuotaStatus.PARTIAL
        if known == 1
        else (QuotaStatus.PARTIAL if team_total is not None else QuotaStatus.UNKNOWN)
    )
    return QwenQuota(
        five_hour=five_hour,
        weekly=weekly,
        status=status,
        fetched_at=now,
        team_total=team_total,
    )
