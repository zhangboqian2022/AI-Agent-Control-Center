"""Qt-timer orchestration for the cached Bailian (Qwen Code) web session."""

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
from aacc.qwen_chrome_cdp import QwenChromeMissingError, find_qwen_chrome_executable
from aacc.qwen_web_error import (
    QwenQuotaErrorCategory,
    normalize_qwen_quota_error_category,
)
from aacc.qwen_web_quota import QwenQuota, parse_qwen_quota

QWEN_WEB_QUOTA_INTERVAL_MS = 300_000


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
    session_type: Any = import_module("aacc.qwen_web_session").QwenWebSession
    return cast(
        _WebSessionLike,
        session_type(config_dir, parent, language_manager=language_manager),
    )


def _create_chrome_web_session(
    config_dir: Path,
    parent: QObject,
    *,
    language_manager: LanguageManager,
) -> _WebSessionLike:
    session_type: Any = import_module("aacc.qwen_chrome_session").QwenChromeSession
    return cast(
        _WebSessionLike,
        session_type(config_dir, parent, language_manager=language_manager),
    )


def _create_platform_web_session(
    config_dir: Path,
    parent: QObject,
    *,
    language_manager: LanguageManager,
) -> _WebSessionLike:
    """Prefer the Chrome-CDP session on macOS when Chrome is installed.

    The Aliyun login flow (RAM entry and friends) needs a full browser
    engine; the lightweight native web view is the fallback when Chrome is
    missing and the Windows path until a dedicated Edge-CDP session exists.
    """

    if sys.platform == "darwin":
        try:
            find_qwen_chrome_executable()
        except QwenChromeMissingError:
            pass
        else:
            return _create_chrome_web_session(config_dir, parent, language_manager=language_manager)
    return _create_native_web_session(config_dir, parent, language_manager=language_manager)


class QwenWebQuotaService(QObject):
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
        self.last_quota: QwenQuota | None = None
        self.workspace_url = ""
        self.timer = QTimer(self)
        self.timer.setInterval(QWEN_WEB_QUOTA_INTERVAL_MS)
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
            result = self._ensure_session().logout()
        except Exception:
            result = False
        finally:
            self.last_quota = None
        return result is not False

    def _on_quota_received(self, raw: object) -> None:
        quota = parse_qwen_quota(raw, now=self._now())
        self.last_quota = quota
        self.quota_updated.emit(quota)
        if quota.status is QuotaStatus.UNKNOWN:
            self.error_occurred.emit(QwenQuotaErrorCategory.PARSE_FAILED.value)

    def _on_error(self, category: object) -> None:
        normalized = normalize_qwen_quota_error_category(category)
        self.error_occurred.emit(normalized.value)

    def _on_login_state_changed(self, authorized: bool) -> None:
        if not authorized:
            self.last_quota = None
        self.login_state_changed.emit(authorized)

    def _ensure_session(self) -> _WebSessionLike:
        if self._session is None:
            self._session = _create_platform_web_session(
                self._config_dir, self, language_manager=self.language_manager
            )
            self._connect_session(self._session)
        return self._session

    def _connect_session(self, session: _WebSessionLike) -> None:
        session.login_state_changed.connect(self._on_login_state_changed)
        session.quota_received.connect(self._on_quota_received)
        session.error_occurred.connect(self._on_error)
