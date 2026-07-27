from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

MAX_CODEX_QUOTA_TAIL_BYTES = 262_144
MAX_CODEX_QUOTA_LINE_BYTES = 65_536
MAX_CODEX_QUOTA_FILES = 20
WEEKLY_WINDOW_MINUTES = 10_080


class CodexQuotaStatus(StrEnum):
    OK = "ok"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CodexQuotaWindow:
    used_percent: int
    window_minutes: int
    resets_at: datetime


@dataclass(frozen=True)
class CodexQuotaSnapshot:
    weekly: CodexQuotaWindow | None
    observed_at: datetime | None
    status: CodexQuotaStatus
    plan_type: str | None = None


class CodexQuotaReaderLike(Protocol):
    def read_latest(self) -> CodexQuotaSnapshot: ...


def _unknown() -> CodexQuotaSnapshot:
    return CodexQuotaSnapshot(
        weekly=None,
        observed_at=None,
        status=CodexQuotaStatus.UNKNOWN,
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _timestamp(value: object) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _iso_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _weekly_window(value: object, *, now: datetime) -> CodexQuotaWindow | None:
    if not isinstance(value, dict):
        return None
    minutes = _number(value.get("window_minutes"))
    if minutes != WEEKLY_WINDOW_MINUTES:
        return None
    used = _number(value.get("used_percent"))
    if used is None or used < 0 or used > 100:
        return None
    resets_at = _timestamp(value.get("resets_at"))
    if resets_at is None or resets_at <= now:
        return None
    return CodexQuotaWindow(
        used_percent=round(used),
        window_minutes=WEEKLY_WINDOW_MINUTES,
        resets_at=resets_at,
    )


def parse_rate_limits(
    item: object,
    *,
    now: datetime,
) -> CodexQuotaSnapshot:
    if not isinstance(item, dict) or item.get("type") != "event_msg":
        return _unknown()
    payload = item.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return _unknown()
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return _unknown()
    weekly = next(
        (
            parsed
            for key in ("primary", "secondary")
            if (
                parsed := _weekly_window(
                    rate_limits.get(key),
                    now=now,
                )
            )
            is not None
        ),
        None,
    )
    if weekly is None:
        return _unknown()
    observed_at = _iso_time(item.get("timestamp"))
    if observed_at is None:
        return _unknown()
    plan = rate_limits.get("plan_type")
    plan_type = plan[:32] if isinstance(plan, str) and plan else None
    return CodexQuotaSnapshot(
        weekly=weekly,
        observed_at=observed_at,
        status=CodexQuotaStatus.OK,
        plan_type=plan_type,
    )


def _app_server_weekly_window(value: object, *, now: datetime) -> CodexQuotaWindow | None:
    if not isinstance(value, dict):
        return None
    minutes = _number(value.get("windowDurationMins"))
    if minutes != WEEKLY_WINDOW_MINUTES:
        return None
    used = _number(value.get("usedPercent"))
    if used is None or used < 0 or used > 100:
        return None
    resets_at = _timestamp(value.get("resetsAt"))
    if resets_at is None or resets_at <= now:
        return None
    return CodexQuotaWindow(
        used_percent=round(used),
        window_minutes=WEEKLY_WINDOW_MINUTES,
        resets_at=resets_at,
    )


def parse_app_server_rate_limits(
    data: object,
    *,
    now: datetime,
) -> CodexQuotaSnapshot:
    """Parse the read-only account/rateLimits/read result."""

    if not isinstance(data, dict):
        return _unknown()
    rate_limits: object = data.get("rateLimits")
    by_limit_id = data.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict) and isinstance(by_limit_id.get("codex"), dict):
        rate_limits = by_limit_id["codex"]
    if not isinstance(rate_limits, dict):
        return _unknown()
    weekly = next(
        (
            parsed
            for key in ("primary", "secondary")
            if (
                parsed := _app_server_weekly_window(
                    rate_limits.get(key),
                    now=now,
                )
            )
            is not None
        ),
        None,
    )
    if weekly is None:
        return _unknown()
    plan = rate_limits.get("planType")
    plan_type = plan[:32] if isinstance(plan, str) and plan else None
    return CodexQuotaSnapshot(
        weekly=weekly,
        observed_at=now,
        status=CodexQuotaStatus.OK,
        plan_type=plan_type,
    )


class CodexQuotaReader:
    """Reads only bounded, structured rate-limit metadata from Codex sessions."""

    def __init__(
        self,
        session_directory: Path,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_directory = session_directory
        self._now = now

    def read_latest(self) -> CodexQuotaSnapshot:
        now = self._now()
        for path in self._candidate_paths():
            for item in self._tail_items(path):
                snapshot = parse_rate_limits(item, now=now)
                if snapshot.status is CodexQuotaStatus.OK:
                    return snapshot
        return _unknown()

    def _candidate_paths(self) -> list[Path]:
        try:
            paths = list(self._session_directory.rglob("*.jsonl"))
        except OSError:
            return []

        def modified(path: Path) -> int:
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return -1

        paths.sort(key=modified, reverse=True)
        return paths[:MAX_CODEX_QUOTA_FILES]

    @staticmethod
    def _tail_items(path: Path) -> list[object]:
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                offset = max(0, size - MAX_CODEX_QUOTA_TAIL_BYTES)
                handle.seek(offset)
                data = handle.read(MAX_CODEX_QUOTA_TAIL_BYTES)
        except OSError:
            return []
        if offset > 0:
            separator = data.find(b"\n")
            data = data[separator + 1 :] if separator >= 0 else b""
        if data and not data.endswith(b"\n"):
            separator = data.rfind(b"\n")
            data = data[: separator + 1] if separator >= 0 else b""
        items: list[object] = []
        for line in reversed(data.splitlines()):
            if not line or len(line) > MAX_CODEX_QUOTA_LINE_BYTES:
                continue
            try:
                items.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return items


class CompositeCodexQuotaReader:
    """Prefer live account quota and retain bounded local metadata as fallback."""

    def __init__(
        self,
        primary: CodexQuotaReaderLike | None,
        fallback: CodexQuotaReaderLike,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def read_latest(self) -> CodexQuotaSnapshot:
        if self._primary is not None:
            live = self._primary.read_latest()
            if live.status is CodexQuotaStatus.OK:
                return live
        return self._fallback.read_latest()
