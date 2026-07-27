"""Qt-timer orchestration for the cached Kimi web membership session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from aacc.kimi_quota import KimiQuota
from aacc.kimi_web_quota import parse_membership_quota
from aacc.kimi_web_session import KimiWebSession
from aacc.security import redact

WEB_QUOTA_INTERVAL_MS = 300_000


class _WebSessionLike(Protocol):
    login_state_changed: Any
    quota_received: Any
    error_occurred: Any

    def refresh(self) -> None: ...

    def open_login(self, parent: QWidget | None = None) -> None: ...

    def logout(self) -> None: ...

    def close(self) -> None: ...


class KimiWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(
        self,
        config_dir: Path,
        *,
        session: _WebSessionLike | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_dir = config_dir
        self._session: _WebSessionLike | None = session
        self._now = now
        self.last_quota: KimiQuota | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(WEB_QUOTA_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh_now)
        if self._session is not None:
            self._connect_session(self._session)
        self._fallback_refresh: Callable[[], None] | None = None
        self._stopped = False

    def start(self) -> None:
        self._ensure_session()
        if not self.timer.isActive():
            self.timer.start()
        self.refresh_now()

    def stop(self) -> None:
        self.timer.stop()
        if self._stopped:
            return
        self._stopped = True
        if self._session is not None:
            self._session.close()

    def refresh_now(self) -> None:
        if self._fallback_refresh is not None:
            try:
                self._fallback_refresh()
            except Exception:  # noqa: BLE001 - one failed source must not skip the other
                self._on_error("Kimi Code 备用额度刷新失败")
        self._ensure_session().refresh()

    def set_fallback_refresh(self, callback: Callable[[], None]) -> None:
        self._fallback_refresh = callback

    def open_login(self, parent: QWidget | None = None) -> None:
        self._ensure_session().open_login(parent)

    def logout(self) -> None:
        if self._session is not None:
            self._session.logout()
        self.last_quota = None

    def _on_quota_received(self, stats: object, subscription: object) -> None:
        quota = parse_membership_quota(stats, subscription, now=self._now())
        self.last_quota = quota
        self.quota_updated.emit(quota)

    def _on_error(self, message: str) -> None:
        self.error_occurred.emit(redact(message)[:160])

    def _ensure_session(self) -> _WebSessionLike:
        if self._session is None:
            self._session = KimiWebSession(self._config_dir, self)
            self._connect_session(self._session)
        return self._session

    def _connect_session(self, session: _WebSessionLike) -> None:
        session.login_state_changed.connect(self.login_state_changed.emit)
        session.quota_received.connect(self._on_quota_received)
        session.error_occurred.connect(self._on_error)
