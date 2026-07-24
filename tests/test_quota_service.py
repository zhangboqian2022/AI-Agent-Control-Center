from __future__ import annotations

import threading
import time
from contextlib import suppress

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from aacc.kimi_oauth import clear_credentials, load_credentials, save_credentials
from aacc.quota_service import (
    STATE_AUTHORIZED,
    STATE_PENDING,
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


class TrackingClient(httpx.Client):
    def __init__(self, handler) -> None:
        super().__init__(transport=httpx.MockTransport(handler))
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


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


def test_poll_redacts_server_secret_from_error_signal(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-kimi-x"})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"message": "token=private-token-sentinel"},
        )

    service = make_service(tmp_path, handler)
    errors: list[str] = []
    service.error_occurred.connect(errors.append)

    service.refresh_now()

    assert wait_for(lambda: len(errors) == 1)
    assert "private-token-sentinel" not in errors[0]
    assert "[REDACTED]" in errors[0]


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


def test_poll_skips_while_oauth_is_pending(tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-old"})
    calls: list[str] = []
    service = make_service(tmp_path, quota_handler(calls))
    service._state = STATE_PENDING

    service._poll_once()

    assert calls == []


def test_delayed_refresh_cannot_overwrite_new_api_key(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "oauth", "token": EXPIRED_TOKEN})
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/token":
            refresh_started.set()
            assert release_refresh.wait(5)
            return httpx.Response(
                200,
                json={
                    "access_token": "late-refresh",
                    "refresh_token": "late-refresh-token",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(200, json=QUOTA_PAYLOAD)

    service = make_service(tmp_path, handler)
    service.refresh_now()
    assert refresh_started.wait(5)

    service.set_api_key("sk-new")
    release_refresh.set()

    assert wait_for(lambda: (load_credentials(tmp_path) or {}).get("api_key") == "sk-new")
    time.sleep(0.1)
    assert load_credentials(tmp_path) == {
        "auth_method": "api_key",
        "api_key": "sk-new",
    }


def test_external_credential_removal_during_refresh_reconciles_state(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "oauth", "token": EXPIRED_TOKEN})
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/token":
            refresh_started.set()
            assert release_refresh.wait(5)
            return httpx.Response(
                200,
                json={
                    "access_token": "late-refresh",
                    "refresh_token": "late-refresh-token",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(200, json=QUOTA_PAYLOAD)

    service = make_service(tmp_path, handler)
    service.refresh_now()
    assert refresh_started.wait(5)

    clear_credentials(tmp_path)
    release_refresh.set()

    assert wait_for(lambda: not service._poll_lock.locked())
    assert wait_for(lambda: service.state() == STATE_UNAUTHORIZED)
    assert load_credentials(tmp_path) is None


def test_delayed_401_cannot_clear_new_api_key(qapp, tmp_path):
    save_credentials(tmp_path, {"auth_method": "oauth", "token": VALID_TOKEN})
    quota_started = threading.Event()
    release_quota = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        quota_started.set()
        assert release_quota.wait(5)
        return httpx.Response(401, json={})

    service = make_service(tmp_path, handler)
    service.refresh_now()
    assert quota_started.wait(5)

    service.set_api_key("sk-new")
    release_quota.set()

    assert wait_for(lambda: not service._poll_lock.locked())
    assert load_credentials(tmp_path) == {
        "auth_method": "api_key",
        "api_key": "sk-new",
    }
    assert service.state() == STATE_AUTHORIZED


def test_api_key_wins_over_late_oauth(qapp, tmp_path):
    token_started = threading.Event()
    release_token = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/device_authorization":
            return httpx.Response(
                200,
                json={
                    "user_code": "CODE",
                    "device_code": "device",
                    "verification_uri_complete": "https://example.com",
                    "interval": 1,
                    "expires_in": 900,
                },
            )
        if request.url.path == "/api/oauth/token":
            token_started.set()
            assert release_token.wait(5)
            return httpx.Response(
                200,
                json={
                    "access_token": "late-oauth",
                    "refresh_token": "late-refresh",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(200, json=QUOTA_PAYLOAD)

    service = make_service(tmp_path, handler)
    service.begin_oauth()
    assert token_started.wait(5)

    service.set_api_key("sk-new")
    release_token.set()

    assert wait_for(lambda: service.state() != STATE_PENDING)
    time.sleep(0.1)
    assert load_credentials(tmp_path) == {
        "auth_method": "api_key",
        "api_key": "sk-new",
    }


def test_logout_wins_over_late_oauth(qapp, tmp_path):
    token_started = threading.Event()
    release_token = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/device_authorization":
            return httpx.Response(
                200,
                json={
                    "user_code": "CODE",
                    "device_code": "device",
                    "verification_uri_complete": "https://example.com",
                    "interval": 1,
                    "expires_in": 900,
                },
            )
        token_started.set()
        assert release_token.wait(5)
        return httpx.Response(
            200,
            json={
                "access_token": "late-oauth",
                "refresh_token": "late-refresh",
                "expires_in": 3600,
            },
        )

    service = make_service(tmp_path, handler)
    service.begin_oauth()
    assert token_started.wait(5)

    service.logout()
    release_token.set()

    assert wait_for(lambda: service.state() == STATE_UNAUTHORIZED)
    time.sleep(0.1)
    assert load_credentials(tmp_path) is None


def test_external_credential_change_during_oauth_exits_pending(qapp, tmp_path):
    token_started = threading.Event()
    release_token = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/device_authorization":
            return httpx.Response(
                200,
                json={
                    "user_code": "CODE",
                    "device_code": "device",
                    "verification_uri_complete": "https://example.com",
                    "interval": 1,
                    "expires_in": 900,
                },
            )
        token_started.set()
        assert release_token.wait(5)
        return httpx.Response(
            200,
            json={
                "access_token": "late-oauth",
                "refresh_token": "late-refresh",
                "expires_in": 3600,
            },
        )

    service = make_service(tmp_path, handler)
    finished: list[tuple[bool, str]] = []
    service.oauth_finished.connect(lambda success, message: finished.append((success, message)))

    service.begin_oauth()
    assert token_started.wait(5)
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "external-key"})
    release_token.set()

    assert wait_for(lambda: service.state() != STATE_PENDING)
    assert service.state() == STATE_AUTHORIZED
    assert load_credentials(tmp_path) == {
        "auth_method": "api_key",
        "api_key": "external-key",
    }
    assert wait_for(lambda: len(finished) == 1)
    assert finished[0][0] is False


def test_two_threads_can_start_only_one_oauth_flow(tmp_path):
    service = make_service(tmp_path, quota_handler([]))
    pending_barrier = threading.Barrier(2)
    original_set_state = service._set_state
    flow_started = 0
    flow_lock = threading.Lock()

    def synchronize_pending(state: str) -> None:
        if state == STATE_PENDING:
            with suppress(threading.BrokenBarrierError):
                pending_barrier.wait(timeout=1)
        original_set_state(state)

    def count_flow(*_args: object) -> None:
        nonlocal flow_started
        with flow_lock:
            flow_started += 1

    service._set_state = synchronize_pending  # type: ignore[method-assign]
    service._oauth_flow = count_flow  # type: ignore[method-assign]
    callers = [threading.Thread(target=service.begin_oauth) for _ in range(2)]

    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=3)

    assert flow_started == 1
    assert service.state() == STATE_PENDING


def test_poll_closes_created_http_client(tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-key"})
    clients: list[TrackingClient] = []

    def factory() -> httpx.Client:
        client = TrackingClient(quota_handler([]))
        clients.append(client)
        return client

    service = QuotaService(tmp_path, version="test", client_factory=factory)

    service._poll_once()

    assert len(clients) == 1
    assert clients[0].close_calls == 1


def test_no_fd_leak_over_poll_cycles(tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-key"})
    clients: list[TrackingClient] = []

    def server_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "unavailable"})

    def factory() -> httpx.Client:
        client = TrackingClient(server_error)
        clients.append(client)
        return client

    service = QuotaService(tmp_path, version="test", client_factory=factory)

    for _ in range(200):
        service._poll_once()

    assert len(clients) == 200
    assert all(client.close_calls == 1 for client in clients)


def test_oauth_closes_created_http_client(qapp, tmp_path):
    clients: list[TrackingClient] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/device_authorization":
            return httpx.Response(
                200,
                json={
                    "user_code": "CODE",
                    "device_code": "device",
                    "verification_uri_complete": "https://example.com",
                    "interval": 1,
                    "expires_in": 900,
                },
            )
        if request.url.path == "/api/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "oauth-access",
                    "refresh_token": "oauth-refresh",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(200, json=QUOTA_PAYLOAD)

    def factory() -> httpx.Client:
        client = TrackingClient(handler)
        clients.append(client)
        return client

    service = QuotaService(tmp_path, version="test", client_factory=factory)
    finished: list[bool] = []
    service.oauth_finished.connect(lambda success, _message: finished.append(success))

    service.begin_oauth()

    assert wait_for(lambda: finished == [True])
    assert wait_for(lambda: bool(clients) and clients[0].close_calls == 1)


def test_oauth_save_oserror_exits_pending_and_finishes_once(qapp, tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/device_authorization":
            return httpx.Response(
                200,
                json={
                    "user_code": "CODE",
                    "device_code": "device",
                    "verification_uri_complete": "https://example.com",
                    "interval": 1,
                    "expires_in": 900,
                },
            )
        return httpx.Response(
            200,
            json={
                "access_token": "oauth-access",
                "refresh_token": "oauth-refresh",
                "expires_in": 3600,
            },
        )

    service = make_service(tmp_path, handler)
    finished: list[tuple[bool, str]] = []
    service.oauth_finished.connect(lambda success, message: finished.append((success, message)))

    def fail_save(_config_dir, _data) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr("aacc.credential_store.save_credentials", fail_save)
    service.begin_oauth()

    assert wait_for(lambda: len(finished) == 1, timeout=2)
    assert finished[0][0] is False
    assert "disk unavailable" in finished[0][1]
    assert service.state() == STATE_UNAUTHORIZED
