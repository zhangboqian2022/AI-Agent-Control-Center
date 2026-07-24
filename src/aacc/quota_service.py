"""GUI-side Kimi quota polling service.

Runs network work on a daemon worker thread and reports back through Qt
signals (queued to the GUI thread automatically). Mirrors the discovery
services' discipline: polling never kills the thread, errors are logged
and surfaced as signals, and token refresh is single-flight.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, Signal

from aacc.kimi_oauth import (
    KimiOAuthCancelledError,
    KimiOAuthError,
    KimiOAuthToken,
    KimiOAuthUnauthorizedError,
    clear_credentials,
    load_credentials,
    load_or_create_device_id,
    poll_device_token,
    refresh_access_token,
    request_device_authorization,
    save_credentials,
)
from aacc.kimi_quota import (
    KimiQuotaError,
    KimiQuotaUnauthorizedError,
    fetch_quota,
)

STATE_UNAUTHORIZED = "unauthorized"
STATE_PENDING = "pending"
STATE_AUTHORIZED = "authorized"

CACHE_TTL_SECONDS = 30.0


class QuotaService(QObject):
    quota_updated = Signal(object)
    auth_state_changed = Signal(str)
    oauth_code_ready = Signal(str, str)
    oauth_finished = Signal(bool, str)
    error_occurred = Signal(str)

    def __init__(
        self,
        config_dir: Path,
        *,
        version: str,
        interval_seconds: float = 60.0,
        client_factory: Callable[[], httpx.Client] = httpx.Client,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_dir = config_dir
        self._version = version
        self._device_id = load_or_create_device_id(config_dir)
        self._interval = max(0.2, interval_seconds)
        self._client_factory = client_factory
        self._state_lock = threading.RLock()
        self._state = (
            STATE_AUTHORIZED if load_credentials(config_dir) else STATE_UNAUTHORIZED
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancel_oauth = threading.Event()
        self._refresh_lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._last_fetch_monotonic = 0.0
        self._logger = logging.getLogger("aacc.quota")
        self._thread = threading.Thread(
            target=self._run, name="aacc-kimi-quota", daemon=True
        )

    # ---------- public API (any thread) ----------

    def state(self) -> str:
        with self._state_lock:
            return self._state

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._cancel_oauth.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self._interval + 2)

    def refresh_now(self) -> None:
        self._wake.set()
        if not self._thread.is_alive():
            # start() was never called: run a one-shot poll so explicit
            # refreshes (login, settings changes) still take effect.
            threading.Thread(
                target=self._poll_guarded, name="aacc-kimi-quota-refresh", daemon=True
            ).start()

    def begin_oauth(self) -> None:
        with self._state_lock:
            if self._state == STATE_PENDING:
                return
        self._cancel_oauth.clear()
        self._set_state(STATE_PENDING)
        threading.Thread(
            target=self._oauth_flow, name="aacc-kimi-oauth", daemon=True
        ).start()

    def cancel_oauth(self) -> None:
        self._cancel_oauth.set()

    def set_api_key(self, key: str) -> None:
        trimmed = key.strip()
        if not trimmed:
            raise ValueError("API Key 不能为空")
        save_credentials(
            self._config_dir, {"auth_method": "api_key", "api_key": trimmed}
        )
        self._last_fetch_monotonic = 0.0
        self._set_state(STATE_AUTHORIZED)
        self.refresh_now()

    def logout(self) -> None:
        self._cancel_oauth.set()
        clear_credentials(self._config_dir)
        self._set_state(STATE_UNAUTHORIZED)

    # ---------- internals (worker thread) ----------

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            changed = state != self._state
            self._state = state
        if changed:
            self.auth_state_changed.emit(state)

    def _poll_guarded(self) -> None:
        try:
            self._poll_once()
        except Exception as error:  # polling must never kill the thread
            self._logger.warning("Kimi quota poll failed: %s", error)
            try:
                self.error_occurred.emit(str(error))
            except RuntimeError:
                return  # application shutting down

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll_guarded()
            self._wake.wait(self._interval)
            self._wake.clear()

    def _poll_once(self) -> None:
        if time.monotonic() - self._last_fetch_monotonic < CACHE_TTL_SECONDS:
            return
        if not self._poll_lock.acquire(blocking=False):
            return
        try:
            client = self._client_factory()
            try:
                token = self._access_token(client)
            except KimiOAuthUnauthorizedError:
                clear_credentials(self._config_dir)
                self._set_state(STATE_UNAUTHORIZED)
                return
            except KimiOAuthError as error:
                self.error_occurred.emit(str(error))
                return
            if token is None:
                self._set_state(STATE_UNAUTHORIZED)
                return
            self._set_state(STATE_AUTHORIZED)
            try:
                quota = fetch_quota(client, token)
            except KimiQuotaUnauthorizedError:
                clear_credentials(self._config_dir)
                self._set_state(STATE_UNAUTHORIZED)
                return
            except (KimiQuotaError, httpx.HTTPError) as error:
                self.error_occurred.emit(str(error))
                return
            self._last_fetch_monotonic = time.monotonic()
            self.quota_updated.emit(quota)
        finally:
            self._poll_lock.release()

    def _access_token(self, client: httpx.Client) -> str | None:
        credentials = load_credentials(self._config_dir)
        if not credentials:
            return None
        if credentials.get("auth_method") == "api_key":
            key = credentials.get("api_key")
            return key if isinstance(key, str) and key else None
        token = KimiOAuthToken.from_dict(credentials.get("token"))
        if token is None or not token.is_valid():
            return None
        if not token.needs_refresh():
            return token.access_token
        with self._refresh_lock:
            # Re-read after taking the lock: another thread may have refreshed.
            credentials = load_credentials(self._config_dir) or {}
            token = KimiOAuthToken.from_dict(credentials.get("token"))
            if token is None or not token.is_valid():
                return None
            if not token.needs_refresh():
                return token.access_token
            refreshed = refresh_access_token(
                client, token, version=self._version, device_id=self._device_id
            )
            save_credentials(
                self._config_dir,
                {"auth_method": "oauth", "token": refreshed.to_dict()},
            )
            return refreshed.access_token

    def _interruptible_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._cancel_oauth.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._cancel_oauth.wait(min(remaining, 0.5))

    def _oauth_flow(self) -> None:
        try:
            client = self._client_factory()
            authorization = request_device_authorization(
                client, version=self._version, device_id=self._device_id
            )
            self.oauth_code_ready.emit(
                authorization.user_code, authorization.verification_uri_complete
            )
            token = poll_device_token(
                client,
                authorization,
                version=self._version,
                device_id=self._device_id,
                sleep=self._interruptible_sleep,
                is_cancelled=self._cancel_oauth.is_set,
            )
            save_credentials(
                self._config_dir, {"auth_method": "oauth", "token": token.to_dict()}
            )
            self._last_fetch_monotonic = 0.0
            self._set_state(STATE_AUTHORIZED)
            self.oauth_finished.emit(True, "")
            self.refresh_now()
        except KimiOAuthCancelledError:
            self._set_state(STATE_UNAUTHORIZED)
            self.oauth_finished.emit(False, "授权已取消")
        except (KimiOAuthError, httpx.HTTPError) as error:
            self._set_state(STATE_UNAUTHORIZED)
            self.oauth_finished.emit(False, str(error))
