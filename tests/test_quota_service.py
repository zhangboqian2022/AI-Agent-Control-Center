from __future__ import annotations

import threading
import time

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from aacc.kimi_oauth import load_credentials, save_credentials
from aacc.quota_service import (
    STATE_AUTHORIZED,
    STATE_UNAUTHORIZED,
    QuotaService,
)

VALID_TOKEN = {
    "access_token": "at",
    "refresh_token": "rt",
    "expires_at": int(time.time()) + 3600,
}
EXPIRED_TOKEN = {
    "access_token": "old-at",
    "refresh_token": "rt",
    "expires_at": int(time.time()) - 10,
}
QUOTA_PAYLOAD = {"usage": {"limit": "100", "used": "42"}}


def quota_handler(calls: list[str], payload: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=payload if payload is not None else QUOTA_PAYLOAD)

    return handler


def make_service(tmp_path, handler, **kwargs) -> QuotaService:
    transport = httpx.MockTransport(handler)
    return QuotaService(
        tmp_path,
        version="test",
        client_factory=lambda: httpx.Client(transport=transport),
        **kwargs,
    )


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Signals are emitted from worker threads and queued to the GUI
        # thread, so pump the event loop while waiting.
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_state_unauthorized_without_credentials(tmp_path):
    service = make_service(tmp_path, quota_handler([]))
    assert service.state() == STATE_UNAUTHORIZED


def test_existing_credentials_restore_authorized_state(tmp_path):
    save_credentials(tmp_path, {"auth_method": "oauth", "token": VALID_TOKEN})
    service = make_service(tmp_path, quota_handler([]))
    assert service.state() == STATE_AUTHORIZED


def test_poll_emits_quota_with_api_key(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-kimi-x"})
    calls: list[str] = []
    service = make_service(tmp_path, quota_handler(calls))
    received: list[object] = []
    service.quota_updated.connect(received.append)
    service.refresh_now()
    assert wait_for(lambda: len(received) == 1)
    assert received[0].weekly.percentage == 42
    assert "/coding/v1/usages" in calls


def test_poll_401_clears_credentials(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "oauth", "token": VALID_TOKEN})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    service = make_service(tmp_path, handler)
    states: list[str] = []
    service.auth_state_changed.connect(states.append)
    service.refresh_now()
    assert wait_for(lambda: service.state() == STATE_UNAUTHORIZED)
    assert load_credentials(tmp_path) is None
    assert STATE_UNAUTHORIZED in states


def test_poll_refreshes_expired_oauth_token(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "oauth", "token": EXPIRED_TOKEN})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/token":
            return httpx.Response(
                200,
                json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
            )
        assert request.headers["Authorization"] == "Bearer new-at"
        return httpx.Response(200, json=QUOTA_PAYLOAD)

    service = make_service(tmp_path, handler)
    received: list[object] = []
    service.quota_updated.connect(received.append)
    service.refresh_now()
    assert wait_for(lambda: len(received) == 1)
    saved = load_credentials(tmp_path)
    assert saved is not None
    assert saved["token"]["access_token"] == "new-at"


def test_refresh_now_respects_cache_ttl(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-kimi-x"})
    calls: list[str] = []
    service = make_service(tmp_path, quota_handler(calls))
    received: list[object] = []
    service.quota_updated.connect(received.append)
    service.refresh_now()
    assert wait_for(lambda: len(received) == 1)
    service.refresh_now()
    time.sleep(0.3)
    assert calls.count("/coding/v1/usages") == 1


def test_set_api_key_and_logout(qapp, tmp_path):
    service = make_service(tmp_path, quota_handler([]))
    service.set_api_key("sk-kimi-abc")
    assert load_credentials(tmp_path) == {"auth_method": "api_key", "api_key": "sk-kimi-abc"}
    assert service.state() == STATE_AUTHORIZED
    service.logout()
    assert service.state() == STATE_UNAUTHORIZED
    assert load_credentials(tmp_path) is None


def test_set_api_key_rejects_blank(qapp, tmp_path):
    service = make_service(tmp_path, quota_handler([]))
    with pytest.raises(ValueError):
        service.set_api_key("   ")


def test_oauth_flow_end_to_end(qapp, tmp_path):
    token_holder: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/device_authorization":
            return httpx.Response(
                200,
                json={
                    "user_code": "ABCD-EFGH",
                    "device_code": "dc",
                    "verification_uri_complete": "https://auth.kimi.com/device",
                    "interval": 1,
                    "expires_in": 900,
                },
            )
        if request.url.path == "/api/oauth/token":
            return httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
            )
        token_holder["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json=QUOTA_PAYLOAD)

    service = make_service(tmp_path, handler)
    codes: list[tuple[str, str]] = []
    finished: list[tuple[bool, str]] = []
    service.oauth_code_ready.connect(lambda code, url: codes.append((code, url)))
    service.oauth_finished.connect(lambda ok, msg: finished.append((ok, msg)))
    service.begin_oauth()
    assert wait_for(lambda: len(finished) == 1, timeout=10.0)
    assert codes == [("ABCD-EFGH", "https://auth.kimi.com/device")]
    assert finished[0][0] is True
    assert service.state() == STATE_AUTHORIZED
    saved = load_credentials(tmp_path)
    assert saved is not None and saved["auth_method"] == "oauth"


def test_oauth_cancel(qapp, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/device_authorization":
            return httpx.Response(
                200,
                json={
                    "user_code": "X",
                    "device_code": "dc",
                    "verification_uri_complete": "https://example.com",
                    "interval": 60,
                    "expires_in": 900,
                },
            )
        return httpx.Response(400, json={"error": "authorization_pending"})

    service = make_service(tmp_path, handler)
    finished: list[bool] = []
    service.oauth_finished.connect(lambda ok, _msg: finished.append(ok))
    service.begin_oauth()
    assert wait_for(lambda: service.state() == "pending")
    service.cancel_oauth()
    assert wait_for(lambda: len(finished) == 1, timeout=10.0)
    assert finished[0] is False
    assert service.state() == STATE_UNAUTHORIZED


def test_start_and_stop_polling_thread(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-kimi-x"})
    calls: list[str] = []
    service = make_service(tmp_path, quota_handler(calls), interval_seconds=0.2)
    received: threading.Event = threading.Event()
    service.quota_updated.connect(lambda _q: received.set())
    service.start()
    try:
        # The signal is queued to the GUI thread; pump events while waiting.
        deadline = time.monotonic() + 5.0
        while not received.is_set() and time.monotonic() < deadline:
            QApplication.processEvents()
            received.wait(0.02)
        assert received.is_set()
    finally:
        service.stop()
