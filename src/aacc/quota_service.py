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
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, Signal

from aacc.credential_store import CredentialSnapshot, CredentialStore
from aacc.kimi_oauth import (
    KimiOAuthCancelledError,
    KimiOAuthError,
    KimiOAuthToken,
    KimiOAuthUnauthorizedError,
    load_or_create_device_id,
    poll_device_token,
    refresh_access_token,
    request_device_authorization,
)
from aacc.kimi_quota import (
    KimiQuotaError,
    KimiQuotaUnauthorizedError,
    fetch_quota,
)
from aacc.security import redact

STATE_UNAUTHORIZED = "unauthorized"
STATE_PENDING = "pending"
STATE_AUTHORIZED = "authorized"

CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class AccessGrant:
    token: str
    snapshot: CredentialSnapshot


class _StaleCredentialOperation(RuntimeError):
    pass


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
        interval_seconds: float = 300.0,
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
        self._credentials = CredentialStore(config_dir)
        self._state = self._state_for_credentials(self._credentials.snapshot().credentials)
        self._active_flow_id: str | None = None
        self._oauth_cancel_event: threading.Event | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._refresh_lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._last_fetch_monotonic = 0.0
        self._logger = logging.getLogger("aacc.quota")
        self._thread = threading.Thread(target=self._run, name="aacc-kimi-quota", daemon=True)

    # ---------- public API (any thread) ----------

    def state(self) -> str:
        with self._state_lock:
            return self._state

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._state_lock:
            if self._oauth_cancel_event is not None:
                self._oauth_cancel_event.set()
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
            self._credentials.invalidate()
            snapshot = self._credentials.snapshot()
            flow_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            self._active_flow_id = flow_id
            self._oauth_cancel_event = cancel_event
            changed = self._set_state_locked(STATE_PENDING)
        if changed:
            self.auth_state_changed.emit(STATE_PENDING)
        threading.Thread(
            target=self._oauth_flow,
            args=(flow_id, snapshot, cancel_event),
            name="aacc-kimi-oauth",
            daemon=True,
        ).start()

    def cancel_oauth(self) -> None:
        with self._state_lock:
            if self._oauth_cancel_event is not None:
                self._oauth_cancel_event.set()

    def set_api_key(self, key: str) -> None:
        trimmed = key.strip()
        if not trimmed:
            raise ValueError("API Key 不能为空")
        with self._state_lock:
            cancelled_flow = self._invalidate_active_flow_locked()
            self._credentials.invalidate()
            self._credentials.replace({"auth_method": "api_key", "api_key": trimmed})
            self._last_fetch_monotonic = 0.0
            changed = self._set_state_locked(STATE_AUTHORIZED)
        if changed:
            self.auth_state_changed.emit(STATE_AUTHORIZED)
        if cancelled_flow:
            self.oauth_finished.emit(False, "授权已取消")
        self.refresh_now()

    def logout(self) -> None:
        with self._state_lock:
            cancelled_flow = self._invalidate_active_flow_locked()
            self._credentials.invalidate()
            snapshot = self._credentials.snapshot()
            self._credentials.clear_if_current(snapshot)
            changed = self._set_state_locked(STATE_UNAUTHORIZED)
        if changed:
            self.auth_state_changed.emit(STATE_UNAUTHORIZED)
        if cancelled_flow:
            self.oauth_finished.emit(False, "授权已取消")

    # ---------- internals (worker thread) ----------

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            changed = self._set_state_locked(state)
        if changed:
            self.auth_state_changed.emit(state)

    def _set_state_locked(self, state: str) -> bool:
        changed = state != self._state
        self._state = state
        return changed

    def _invalidate_active_flow_locked(self) -> bool:
        active = self._active_flow_id is not None
        if self._oauth_cancel_event is not None:
            self._oauth_cancel_event.set()
        self._active_flow_id = None
        self._oauth_cancel_event = None
        return active

    def _poll_guarded(self) -> None:
        try:
            self._poll_once()
        except Exception as error:  # polling must never kill the thread
            message = self._safe_error(error)
            self._logger.warning("Kimi quota poll failed: %s", message)
            try:
                self.error_occurred.emit(message)
            except RuntimeError:
                return  # application shutting down

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll_guarded()
            self._wake.wait(self._interval)
            self._wake.clear()

    def _poll_once(self) -> None:
        with self._state_lock:
            if self._state == STATE_PENDING:
                return
        if time.monotonic() - self._last_fetch_monotonic < CACHE_TTL_SECONDS:
            return
        if not self._poll_lock.acquire(blocking=False):
            return
        try:
            with self._state_lock:
                if self._state == STATE_PENDING:
                    return
                snapshot = self._credentials.snapshot()
            with closing(self._client_factory()) as client:
                self._poll_with_client(client, snapshot)
        finally:
            self._poll_lock.release()

    def _poll_with_client(
        self,
        client: httpx.Client,
        snapshot: CredentialSnapshot,
    ) -> None:
        try:
            grant = self._access_token(client, snapshot)
        except _StaleCredentialOperation:
            self._reconcile_state_from_credentials()
            return
        except KimiOAuthUnauthorizedError:
            if not self._clear_credentials_if_current(snapshot):
                self._reconcile_state_from_credentials()
            return
        except KimiOAuthError as error:
            self.error_occurred.emit(self._safe_error(error))
            return
        if grant is None:
            self._set_state_if_current(snapshot, STATE_UNAUTHORIZED)
            return
        try:
            quota = fetch_quota(client, grant.token)
        except KimiQuotaUnauthorizedError:
            if not self._clear_credentials_if_current(grant.snapshot):
                self._reconcile_state_from_credentials()
            return
        except (KimiQuotaError, httpx.HTTPError) as error:
            self.error_occurred.emit(self._safe_error(error))
            return
        if not self._set_state_if_current(grant.snapshot, STATE_AUTHORIZED):
            self._reconcile_state_from_credentials()
            return
        with self._state_lock:
            if self._state == STATE_PENDING or not self._credentials.is_current(grant.snapshot):
                return
            self._last_fetch_monotonic = time.monotonic()
        self.quota_updated.emit(quota)

    def _access_token(
        self,
        client: httpx.Client,
        snapshot: CredentialSnapshot,
    ) -> AccessGrant | None:
        credentials = snapshot.credentials
        if not credentials:
            return None
        if credentials.get("auth_method") == "api_key":
            key = credentials.get("api_key")
            return AccessGrant(key, snapshot) if isinstance(key, str) and key else None
        token = KimiOAuthToken.from_dict(credentials.get("token"))
        if token is None or not token.is_valid():
            return None
        if not token.needs_refresh():
            return AccessGrant(token.access_token, snapshot)
        with self._refresh_lock:
            with self._state_lock:
                if self._state == STATE_PENDING or not self._credentials.is_current(snapshot):
                    raise _StaleCredentialOperation
                current = self._credentials.snapshot()
            credentials = current.credentials or {}
            token = KimiOAuthToken.from_dict(credentials.get("token"))
            if token is None or not token.is_valid():
                return None
            if not token.needs_refresh():
                return AccessGrant(token.access_token, current)
            refreshed = refresh_access_token(
                client, token, version=self._version, device_id=self._device_id
            )
            with self._state_lock:
                if self._state == STATE_PENDING:
                    raise _StaleCredentialOperation
                committed = self._credentials.replace_if_current(
                    current,
                    {"auth_method": "oauth", "token": refreshed.to_dict()},
                )
            if committed is None:
                raise _StaleCredentialOperation
            return AccessGrant(refreshed.access_token, committed)

    def _set_state_if_current(
        self,
        snapshot: CredentialSnapshot,
        state: str,
    ) -> bool:
        with self._state_lock:
            if self._state == STATE_PENDING or not self._credentials.is_current(snapshot):
                return False
            changed = self._set_state_locked(state)
        if changed:
            self.auth_state_changed.emit(state)
        return True

    def _clear_credentials_if_current(
        self,
        snapshot: CredentialSnapshot,
    ) -> bool:
        with self._state_lock:
            if self._state == STATE_PENDING:
                return False
            if not self._credentials.clear_if_current(snapshot):
                return False
            changed = self._set_state_locked(STATE_UNAUTHORIZED)
        if changed:
            self.auth_state_changed.emit(STATE_UNAUTHORIZED)
        return True

    def _reconcile_state_from_credentials(self) -> None:
        with self._state_lock:
            if self._state == STATE_PENDING:
                return
            state = self._state_for_credentials(self._credentials.snapshot().credentials)
            changed = self._set_state_locked(state)
        if changed:
            self.auth_state_changed.emit(state)

    def _interruptible_sleep(
        self,
        seconds: float,
        cancel_event: threading.Event,
    ) -> None:
        deadline = time.monotonic() + seconds
        while not cancel_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            cancel_event.wait(min(remaining, 0.5))

    def _oauth_flow(
        self,
        flow_id: str,
        snapshot: CredentialSnapshot,
        cancel_event: threading.Event,
    ) -> None:
        try:
            if cancel_event.is_set():
                raise KimiOAuthCancelledError("OAuth flow cancelled")
            with closing(self._client_factory()) as client:
                authorization = request_device_authorization(
                    client, version=self._version, device_id=self._device_id
                )
                self.oauth_code_ready.emit(
                    authorization.user_code,
                    authorization.verification_uri_complete,
                )
                token = poll_device_token(
                    client,
                    authorization,
                    version=self._version,
                    device_id=self._device_id,
                    sleep=lambda seconds: self._interruptible_sleep(seconds, cancel_event),
                    is_cancelled=cancel_event.is_set,
                )
            if cancel_event.is_set():
                raise KimiOAuthCancelledError("OAuth flow cancelled")
            credential_conflict = False
            with self._state_lock:
                if self._active_flow_id != flow_id:
                    return
                committed = self._credentials.replace_if_current(
                    snapshot,
                    {"auth_method": "oauth", "token": token.to_dict()},
                )
                if committed is None:
                    credential_conflict = True
                else:
                    self._active_flow_id = None
                    self._oauth_cancel_event = None
                    self._last_fetch_monotonic = 0.0
                    changed = self._set_state_locked(STATE_AUTHORIZED)
            if credential_conflict:
                self._finish_oauth_failure(flow_id, "凭据已被更新，已忽略过期授权结果")
                return
            if changed:
                self.auth_state_changed.emit(STATE_AUTHORIZED)
            self.oauth_finished.emit(True, "")
            self.refresh_now()
        except KimiOAuthCancelledError:
            self._finish_oauth_failure(flow_id, "授权已取消")
        except (KimiOAuthError, httpx.HTTPError) as error:
            self._finish_oauth_failure(flow_id, self._safe_error(error))
        except Exception as error:
            message = self._safe_error(error)
            self._logger.warning("Unexpected Kimi OAuth failure: %s", message)
            self._finish_oauth_failure(flow_id, message)

    def _finish_oauth_failure(self, flow_id: str, message: str) -> None:
        with self._state_lock:
            if self._active_flow_id != flow_id:
                return
            self._active_flow_id = None
            self._oauth_cancel_event = None
            state = self._state_for_credentials(self._credentials.snapshot().credentials)
            changed = self._set_state_locked(state)
        if changed:
            self.auth_state_changed.emit(state)
        self.oauth_finished.emit(False, message)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return redact(str(error) or type(error).__name__)[:160]

    @staticmethod
    def _state_for_credentials(credentials: object) -> str:
        if not isinstance(credentials, dict):
            return STATE_UNAUTHORIZED
        if credentials.get("auth_method") == "api_key":
            key = credentials.get("api_key")
            return (
                STATE_AUTHORIZED
                if isinstance(key, str) and bool(key.strip())
                else STATE_UNAUTHORIZED
            )
        if credentials.get("auth_method") == "oauth":
            token = KimiOAuthToken.from_dict(credentials.get("token"))
            if token is not None and token.is_valid():
                return STATE_AUTHORIZED
        return STATE_UNAUTHORIZED
