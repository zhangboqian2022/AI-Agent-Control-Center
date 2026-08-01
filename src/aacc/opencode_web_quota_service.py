"""Qt-timer orchestration for the cached opencode.ai web session."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from aacc.i18n import ZH_CN, LanguageManager
from aacc.kimi_quota import QuotaStatus
from aacc.opencode_web_error import (
    OpenCodeQuotaErrorCategory,
    normalize_opencode_quota_error_category,
)
from aacc.opencode_web_quota import OpenCodeQuota, parse_opencode_quota

OPENCODE_WEB_QUOTA_INTERVAL_MS = 300_000


class _WebSessionLike(Protocol):
    login_state_changed: Any
    quota_received: Any
    error_occurred: Any

    def refresh(self) -> None: ...
    def open_login(self, parent: QWidget | None = None) -> None: ...
    def logout(self) -> bool | None: ...
    def close(self) -> None: ...
    def retranslate_ui(self) -> None: ...
    def set_workspace_url(self, url: str) -> None: ...


def _create_native_web_session(
    config_dir: Path,
    parent: QObject,
    *,
    language_manager: LanguageManager,
) -> _WebSessionLike:
    session_type: Any = import_module("aacc.opencode_web_session").OpenCodeWebSession
    return cast(
        _WebSessionLike,
        session_type(config_dir, parent, language_manager=language_manager),
    )


class OpenCodeWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(
        self,
        config_dir: Path,
        *,
        session: _WebSessionLike | None = None,
        language_manager: LanguageManager | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_dir = config_dir
        self._session: _WebSessionLike | None = session
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self._now = now
        self.last_quota: OpenCodeQuota | None = None
        self.workspace_url = ""
        self.timer = QTimer(self)
        self.timer.setInterval(OPENCODE_WEB_QUOTA_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh_now)
        if self._session is not None:
            self._connect_session(self._session)
        self._stopped = False

    def set_workspace_url(self, url: str) -> None:
        self.workspace_url = url.strip()

    def start(self) -> None:
        self._ensure_session().set_workspace_url(self.workspace_url)
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
        if not self.workspace_url:
            return
        self._ensure_session().refresh()

    def open_login(self, parent: QWidget | None = None) -> None:
        self._ensure_session().open_login(parent)

    def logout(self) -> bool:
        result: bool | None = True
        try:
            if self._session is not None:
                result = self._session.logout()
        finally:
            self.last_quota = None
        return result is not False

    def _on_quota_received(self, raw: object) -> None:
        quota = parse_opencode_quota(raw, now=self._now())
        self.last_quota = quota
        self.quota_updated.emit(quota)
        if quota.status is QuotaStatus.UNKNOWN:
            self.error_occurred.emit(OpenCodeQuotaErrorCategory.PARSE_FAILED.value)

    def _on_error(self, category: object) -> None:
        normalized = normalize_opencode_quota_error_category(category)
        self.error_occurred.emit(normalized.value)

    def _on_login_state_changed(self, authorized: bool) -> None:
        if not authorized:
            self.last_quota = None
        self.login_state_changed.emit(authorized)

    def _ensure_session(self) -> _WebSessionLike:
        if self._session is None:
            if sys.platform == "win32":
                session_type: Any = import_module("aacc.opencode_edge_session").OpenCodeEdgeSession
                self._session = cast(
                    _WebSessionLike,
                    session_type(self._config_dir, self, language_manager=self.language_manager),
                )
            else:
                self._session = _create_native_web_session(
                    self._config_dir, self, language_manager=self.language_manager
                )
            self._connect_session(self._session)
        return self._session

    def _connect_session(self, session: _WebSessionLike) -> None:
        session.login_state_changed.connect(self._on_login_state_changed)
        session.quota_received.connect(self._on_quota_received)
        session.error_occurred.connect(self._on_error)
