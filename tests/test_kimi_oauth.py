from __future__ import annotations

import os

import httpx
import pytest

from aacc.kimi_oauth import (
    CLIENT_ID,
    DeviceAuthorization,
    KimiOAuthDeniedError,
    KimiOAuthError,
    KimiOAuthToken,
    KimiOAuthUnauthorizedError,
    clear_credentials,
    credentials_path,
    load_credentials,
    load_or_create_device_id,
    oauth_host,
    poll_device_token,
    refresh_access_token,
    request_device_authorization,
    save_credentials,
)


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_oauth_host_default_and_env_override(monkeypatch):
    assert oauth_host() == "https://auth.kimi.com"
    monkeypatch.setenv("KIMI_CODE_OAUTH_HOST", "https://auth.example.com/")
    assert oauth_host() == "https://auth.example.com"
    monkeypatch.delenv("KIMI_CODE_OAUTH_HOST")
    monkeypatch.setenv("KIMI_OAUTH_HOST", "https://fallback.example.com")
    assert oauth_host() == "https://fallback.example.com"


def test_token_validity_and_refresh_margin():
    token = KimiOAuthToken(access_token="a", refresh_token="r", expires_at=1000)
    assert token.is_valid()
    assert token.needs_refresh(now=1000 - 299)
    assert not token.needs_refresh(now=1000 - 301)
    assert not KimiOAuthToken(access_token="", refresh_token="r", expires_at=1).is_valid()


def test_token_dict_roundtrip_and_from_dict_rejects_junk():
    token = KimiOAuthToken(access_token="a", refresh_token="r", expires_at=1000, scope="s")
    assert KimiOAuthToken.from_dict(token.to_dict()) == token
    assert KimiOAuthToken.from_dict({"access_token": "a"}) is None
    assert KimiOAuthToken.from_dict("nope") is None


def test_request_device_authorization_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/oauth/device_authorization"
        assert request.headers["X-Msh-Platform"] == "kimi_code_cli"
        assert request.headers["X-Msh-Device-Id"] == "dev-1"
        assert b"client_id=" in request.content
        return httpx.Response(
            200,
            json={
                "user_code": "ABCD-EFGH",
                "device_code": "dc-1",
                "verification_uri_complete": "https://auth.kimi.com/device?code=ABCD",
                "interval": 5,
                "expires_in": 900,
            },
        )

    auth = request_device_authorization(make_client(handler), version="1.4.0", device_id="dev-1")
    assert auth.user_code == "ABCD-EFGH"
    assert auth.device_code == "dc-1"
    assert auth.interval_seconds == 5
    assert auth.expires_in_seconds == 900


def test_request_device_authorization_http_error_and_missing_fields():
    def bad_status(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(KimiOAuthError, match="HTTP 500"):
        request_device_authorization(make_client(bad_status), version="1", device_id="d")

    def missing_fields(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user_code": "x"})

    with pytest.raises(KimiOAuthError, match="missing"):
        request_device_authorization(make_client(missing_fields), version="1", device_id="d")


def _auth(interval: float = 5) -> DeviceAuthorization:
    return DeviceAuthorization(
        user_code="u",
        device_code="dc",
        verification_uri_complete="https://example.com",
        interval_seconds=interval,
        expires_in_seconds=900,
    )


def test_poll_device_token_pending_then_success():
    responses = iter(
        [
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code" in body
        assert "device_code=dc" in body
        return next(responses)

    token = poll_device_token(
        make_client(handler), _auth(), version="1", device_id="d", sleep=lambda _: None
    )
    assert token.access_token == "at"
    assert token.refresh_token == "rt"
    assert token.expires_at > 0


def test_poll_device_token_slow_down_increases_interval():
    responses = iter(
        [
            httpx.Response(400, json={"error": "slow_down"}),
            httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 60}
            ),
        ]
    )
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    poll_device_token(
        make_client(handler),
        _auth(interval=5),
        version="1",
        device_id="d",
        sleep=slept.append,
    )
    assert slept == [10.0]


def test_poll_device_token_expired_and_denied():
    def expired(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "expired_token"})

    with pytest.raises(KimiOAuthDeniedError):
        poll_device_token(
            make_client(expired), _auth(), version="1", device_id="d", sleep=lambda _: None
        )

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "access_denied"})

    with pytest.raises(KimiOAuthDeniedError):
        poll_device_token(
            make_client(denied), _auth(), version="1", device_id="d", sleep=lambda _: None
        )


def test_poll_device_token_cancelled():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "authorization_pending"})

    from aacc.kimi_oauth import KimiOAuthCancelledError

    with pytest.raises(KimiOAuthCancelledError):
        poll_device_token(
            make_client(handler),
            _auth(),
            version="1",
            device_id="d",
            sleep=lambda _: None,
            is_cancelled=lambda: True,
        )


def test_poll_device_token_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "authorization_pending"})

    clock = {"t": 0.0}

    def now() -> float:
        clock["t"] += 1000.0
        return clock["t"]

    with pytest.raises(KimiOAuthError, match="timeout|超时"):
        poll_device_token(
            make_client(handler),
            _auth(),
            version="1",
            device_id="d",
            sleep=lambda _: None,
            now=now,
        )


def test_refresh_access_token_keeps_old_refresh_token_when_omitted():
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"grant_type=refresh_token" in request.content
        assert b"refresh_token=old-rt" in request.content
        return httpx.Response(200, json={"access_token": "new-at", "expires_in": 3600})

    old = KimiOAuthToken(access_token="a", refresh_token="old-rt", expires_at=1)
    refreshed = refresh_access_token(make_client(handler), old, version="1", device_id="d")
    assert refreshed.access_token == "new-at"
    assert refreshed.refresh_token == "old-rt"


def test_refresh_access_token_unauthorized():
    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    old = KimiOAuthToken(access_token="a", refresh_token="rt", expires_at=1)
    with pytest.raises(KimiOAuthUnauthorizedError):
        refresh_access_token(make_client(handler_401), old, version="1", device_id="d")

    def handler_invalid_grant(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(KimiOAuthUnauthorizedError):
        refresh_access_token(make_client(handler_invalid_grant), old, version="1", device_id="d")

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error_description": "server"})

    with pytest.raises(KimiOAuthError) as exc_info:
        refresh_access_token(make_client(handler_500), old, version="1", device_id="d")
    assert not isinstance(exc_info.value, KimiOAuthUnauthorizedError)


def test_client_id_is_official_kimi_code_client():
    assert CLIENT_ID == "17e5f671-d194-4dfb-9706-5516cb48c098"


def test_credentials_roundtrip_permissions_and_clear(tmp_path):
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "sk-kimi-x"})
    assert load_credentials(tmp_path) == {"auth_method": "api_key", "api_key": "sk-kimi-x"}
    mode = os.stat(credentials_path(tmp_path)).st_mode & 0o777
    assert mode == 0o600
    clear_credentials(tmp_path)
    assert load_credentials(tmp_path) is None


def test_load_credentials_tolerates_junk(tmp_path):
    assert load_credentials(tmp_path) is None
    credentials_path(tmp_path).write_text("not json", encoding="utf-8")
    assert load_credentials(tmp_path) is None
    credentials_path(tmp_path).write_text("[1, 2]", encoding="utf-8")
    assert load_credentials(tmp_path) is None


def test_device_id_created_once_with_permissions(tmp_path):
    first = load_or_create_device_id(tmp_path)
    assert first
    assert load_or_create_device_id(tmp_path) == first
    mode = os.stat(tmp_path / "device_id").st_mode & 0o777
    assert mode == 0o600
