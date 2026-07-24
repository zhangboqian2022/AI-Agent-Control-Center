# Kimi Quota Monitor (Subsystem A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Kimi account quota monitoring (5h/weekly/booster balance) to AACC, authorized via the official Kimi Code Device Code Flow.

**Architecture:** Three new modules — `kimi_oauth.py` (RFC 8628 device flow, ported from the official `packages/oauth`), `kimi_quota.py` (`/coding/v1/usages` fetch + loose parser), `quota_service.py` (QObject worker-thread poller with Qt signals) — plus a `QuotaBar` widget at the top of the main panel and an OAuth dialog. Credentials live in `<config dir>/kimi-credentials.json` (0600), never in the YAML config, never touching `~/.kimi-code/credentials/`.

**Tech Stack:** Python 3.12, PySide6, httpx (already a dependency; tests use `httpx.MockTransport`), pytest + pytest-qt.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-three-in-one-integration-design.md`
- OAuth host `https://auth.kimi.com`, env overrides `KIMI_CODE_OAUTH_HOST` / `KIMI_OAUTH_HOST`; client_id `17e5f671-d194-4dfb-9706-5516cb48c098`; endpoints `/api/oauth/device_authorization` and `/api/oauth/token`.
- Quota URL base `https://api.kimi.com/coding/v1`, env override `KIMI_CODE_BASE_URL`; endpoint `/usages`.
- Device headers: `X-Msh-Platform: kimi_code_cli` + `X-Msh-Version/Device-Id/Device-Name/Device-Model/Os-Version`.
- Never read or write `~/.kimi-code/credentials/` (refresh-token rotation would kick the CLI offline).
- Quality gates after every task: `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests`, `.venv/bin/mypy src/aacc` (strict) — all green.
- Line length 100; commit messages `feat: ...` / `fix: ...` / `docs: ...` in English.
- TDD: failing test first, minimal implementation, commit per task.
- Branch: `feat/kimi-quota-integration` (already created; design doc committed there).

---

### Task 1: `kimi_oauth.py` — token model and Device Code Flow

**Files:**
- Create: `src/aacc/kimi_oauth.py`
- Test: `tests/test_kimi_oauth.py`

**Interfaces:**
- Consumes: only `httpx` and stdlib.
- Produces: `KimiOAuthError`, `KimiOAuthUnauthorizedError`, `KimiOAuthDeniedError`, `KimiOAuthCancelledError`, `DeviceAuthorization(user_code, device_code, verification_uri_complete, interval_seconds, expires_in_seconds)`, `KimiOAuthToken(access_token, refresh_token, expires_at, scope="", token_type="Bearer")` with `.is_valid()`, `.needs_refresh(now=None)`, `.to_dict()`, `.from_dict(raw)`, `oauth_host() -> str`, `request_device_authorization(client, *, version, device_id) -> DeviceAuthorization`, `poll_device_token(client, authorization, *, version, device_id, sleep, now, is_cancelled) -> KimiOAuthToken`, `refresh_access_token(client, token, *, version, device_id) -> KimiOAuthToken`. Constants: `CLIENT_ID`, `REFRESH_MARGIN_SECONDS=300`, `HTTP_TIMEOUT_SECONDS=30.0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kimi_oauth.py
from __future__ import annotations

import httpx
import pytest

from aacc.kimi_oauth import (
    CLIENT_ID,
    DeviceAuthorization,
    KimiOAuthDeniedError,
    KimiOAuthError,
    KimiOAuthToken,
    KimiOAuthUnauthorizedError,
    oauth_host,
    poll_device_token,
    refresh_access_token,
    request_device_authorization,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kimi_oauth.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aacc.kimi_oauth'`

- [ ] **Step 3: Write the implementation**

```python
# src/aacc/kimi_oauth.py
"""Kimi Code OAuth Device Code Flow (RFC 8628).

Ported from MoonshotAI/kimi-code `packages/oauth` (MIT License,
Copyright (c) MoonshotAI) — see NOTICE. Credential storage is isolated
from the CLI on purpose: a third-party app that refreshed the CLI's own
refresh_token would rotate it server-side and kick the CLI offline
(lesson shared by KimiCodeBar and kimi-code-monitor).
"""
from __future__ import annotations

import json
import os
import platform
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_OAUTH_HOST = "https://auth.kimi.com"
CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_MARGIN_SECONDS = 300
POLL_TIMEOUT_SECONDS = 15 * 60
SLOW_DOWN_STEP_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 30.0
CREDENTIALS_FILE_NAME = "kimi-credentials.json"
DEVICE_ID_FILE_NAME = "device_id"


class KimiOAuthError(RuntimeError):
    """OAuth flow failed (network, HTTP status, or malformed response)."""


class KimiOAuthUnauthorizedError(KimiOAuthError):
    """Stored credentials were rejected; the user must authorize again."""


class KimiOAuthDeniedError(KimiOAuthError):
    """The user denied the request or let the device code expire."""


class KimiOAuthCancelledError(KimiOAuthError):
    """The local caller cancelled the flow."""


@dataclass(frozen=True)
class DeviceAuthorization:
    user_code: str
    device_code: str
    verification_uri_complete: str
    interval_seconds: float
    expires_in_seconds: int


@dataclass(frozen=True)
class KimiOAuthToken:
    access_token: str
    refresh_token: str
    expires_at: int
    scope: str = ""
    token_type: str = "Bearer"

    def is_valid(self) -> bool:
        return bool(self.access_token) and bool(self.refresh_token) and self.expires_at > 0

    def needs_refresh(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return current >= self.expires_at - REFRESH_MARGIN_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, raw: object) -> KimiOAuthToken | None:
        if not isinstance(raw, dict):
            return None
        access_token = raw.get("access_token")
        refresh_token = raw.get("refresh_token")
        expires_at = raw.get("expires_at")
        if not isinstance(access_token, str) or not access_token:
            return None
        if not isinstance(refresh_token, str) or not refresh_token:
            return None
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
            return None
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=int(expires_at),
            scope=raw.get("scope") if isinstance(raw.get("scope"), str) else "",
            token_type=raw.get("token_type")
            if isinstance(raw.get("token_type"), str)
            else "Bearer",
        )


def oauth_host() -> str:
    host = (
        os.environ.get("KIMI_CODE_OAUTH_HOST")
        or os.environ.get("KIMI_OAUTH_HOST")
        or DEFAULT_OAUTH_HOST
    )
    return host.rstrip("/")


def device_headers(version: str, device_id: str) -> dict[str, str]:
    mac_version = platform.mac_ver()[0] or "unknown"
    return {
        "X-Msh-Platform": "kimi_code_cli",
        "X-Msh-Version": version,
        "X-Msh-Device-Id": device_id,
        "X-Msh-Device-Name": platform.node() or "unknown",
        "X-Msh-Device-Model": f"macOS {mac_version} {platform.machine()}",
        "X-Msh-Os-Version": mac_version,
    }


def _error_detail(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    if isinstance(error, str) and error:
        return error
    for key in ("error_description", "message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _post_form(
    client: httpx.Client, url: str, params: dict[str, str], headers: dict[str, str]
) -> tuple[int, dict[str, Any]]:
    try:
        response = client.post(
            url,
            data=params,
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        raise KimiOAuthError(f"OAuth request to {url} failed: {error}") from error
    try:
        parsed: Any = response.json()
    except json.JSONDecodeError:
        parsed = {}
    return response.status_code, parsed if isinstance(parsed, dict) else {}


def _token_from_response(
    data: dict[str, Any], *, fallback_refresh_token: str = "", now: float | None = None
) -> KimiOAuthToken:
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise KimiOAuthError("OAuth response missing access_token")
    refresh_token = data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = fallback_refresh_token
    if not refresh_token:
        raise KimiOAuthError("OAuth response missing refresh_token")
    try:
        expires_in = float(data.get("expires_in"))
    except (TypeError, ValueError):
        raise KimiOAuthError("OAuth response missing or invalid expires_in") from None
    if expires_in <= 0:
        raise KimiOAuthError("OAuth response missing or invalid expires_in")
    current = time.time() if now is None else now
    scope = data.get("scope")
    token_type = data.get("token_type")
    return KimiOAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=int(current) + int(expires_in),
        scope=scope if isinstance(scope, str) else "",
        token_type=token_type if isinstance(token_type, str) else "Bearer",
    )


def request_device_authorization(
    client: httpx.Client, *, version: str, device_id: str
) -> DeviceAuthorization:
    status, data = _post_form(
        client,
        f"{oauth_host()}/api/oauth/device_authorization",
        {"client_id": CLIENT_ID},
        device_headers(version, device_id),
    )
    if status != 200:
        raise KimiOAuthError(f"Device authorization failed (HTTP {status}): {_error_detail(data)}")
    user_code = data.get("user_code")
    device_code = data.get("device_code")
    verification = data.get("verification_uri_complete")
    if not isinstance(user_code, str) or not user_code:
        raise KimiOAuthError("Device authorization response missing user_code")
    if not isinstance(device_code, str) or not device_code:
        raise KimiOAuthError("Device authorization response missing device_code")
    if not isinstance(verification, str) or not verification:
        raise KimiOAuthError("Device authorization response missing verification_uri_complete")
    try:
        interval = float(data.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 5.0
    try:
        expires_in = int(data.get("expires_in") or 900)
    except (TypeError, ValueError):
        expires_in = 900
    return DeviceAuthorization(
        user_code=user_code,
        device_code=device_code,
        verification_uri_complete=verification,
        interval_seconds=interval,
        expires_in_seconds=expires_in,
    )


def poll_device_token(
    client: httpx.Client,
    authorization: DeviceAuthorization,
    *,
    version: str,
    device_id: str,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> KimiOAuthToken:
    deadline = now() + POLL_TIMEOUT_SECONDS
    interval = max(1.0, authorization.interval_seconds)
    while now() < deadline:
        if is_cancelled():
            raise KimiOAuthCancelledError("OAuth flow cancelled")
        status, data = _post_form(
            client,
            f"{oauth_host()}/api/oauth/token",
            {
                "client_id": CLIENT_ID,
                "device_code": authorization.device_code,
                "grant_type": DEVICE_GRANT_TYPE,
            },
            device_headers(version, device_id),
        )
        if status == 200:
            return _token_from_response(data, now=now())
        error = data.get("error")
        if error == "authorization_pending":
            pass
        elif error == "slow_down":
            interval += SLOW_DOWN_STEP_SECONDS
        elif error == "expired_token":
            raise KimiOAuthDeniedError("设备授权已过期，请重新发起授权")
        elif error == "access_denied":
            raise KimiOAuthDeniedError("授权被拒绝")
        else:
            raise KimiOAuthError(
                f"Token polling failed (HTTP {status}): {_error_detail(data)}"
            )
        sleep(interval)
    raise KimiOAuthError("设备授权轮询超时 (timeout)")


def refresh_access_token(
    client: httpx.Client,
    token: KimiOAuthToken,
    *,
    version: str,
    device_id: str,
) -> KimiOAuthToken:
    status, data = _post_form(
        client,
        f"{oauth_host()}/api/oauth/token",
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        },
        device_headers(version, device_id),
    )
    if status in (401, 403):
        raise KimiOAuthUnauthorizedError(f"Token refresh rejected (HTTP {status})")
    if status != 200:
        if data.get("error") == "invalid_grant":
            raise KimiOAuthUnauthorizedError("Token refresh rejected: invalid_grant")
        raise KimiOAuthError(f"Token refresh failed (HTTP {status}): {_error_detail(data)}")
    return _token_from_response(data, fallback_refresh_token=token.refresh_token)


# ---------- credential & device-id persistence (AACC-owned, never the CLI's) ----------


def credentials_path(config_dir: Path) -> Path:
    return config_dir / CREDENTIALS_FILE_NAME


def load_credentials(config_dir: Path) -> dict[str, Any] | None:
    try:
        raw: Any = json.loads(credentials_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def save_credentials(config_dir: Path, data: dict[str, Any]) -> None:
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config_dir, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{CREDENTIALS_FILE_NAME}.", dir=config_dir
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, credentials_path(config_dir))
        os.chmod(credentials_path(config_dir), 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def clear_credentials(config_dir: Path) -> None:
    credentials_path(config_dir).unlink(missing_ok=True)


def load_or_create_device_id(config_dir: Path) -> str:
    path = config_dir / DEVICE_ID_FILE_NAME
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    device_id = str(uuid.uuid4())
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(device_id, encoding="utf-8")
    os.chmod(path, 0o600)
    return device_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kimi_oauth.py -q`
Expected: 13 passed

- [ ] **Step 5: Add persistence tests, then verify**

Append to `tests/test_kimi_oauth.py`:

```python
import os

from aacc.kimi_oauth import (
    clear_credentials,
    credentials_path,
    load_credentials,
    load_or_create_device_id,
    save_credentials,
)


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
```

Run: `.venv/bin/python -m pytest tests/test_kimi_oauth.py -q`
Expected: 16 passed

- [ ] **Step 6: Lint, type-check, commit**

```bash
.venv/bin/ruff check src/aacc/kimi_oauth.py tests/test_kimi_oauth.py
.venv/bin/mypy src/aacc
git add src/aacc/kimi_oauth.py tests/test_kimi_oauth.py
git commit -m "feat: add Kimi OAuth device flow module ported from official packages/oauth"
```

---

### Task 2: `kimi_quota.py` — usages fetch, parser, display formatting

**Files:**
- Create: `src/aacc/kimi_quota.py`
- Test: `tests/test_kimi_quota.py`

**Interfaces:**
- Consumes: `httpx`.
- Produces: `KimiQuotaError`, `KimiQuotaUnauthorizedError`, `QuotaDetail(used, limit, remaining, reset_at, percentage)`, `BoosterWallet(status, is_enabled, balance_yuan)`, `KimiQuota(weekly, five_hour, total_quota, membership_level, booster)`, `usages_url() -> str`, `parse_quota(data: object) -> KimiQuota`, `fetch_quota(client: httpx.Client, access_token: str) -> KimiQuota`, `format_reset_countdown(reset_at: datetime | None, *, now: datetime | None = None) -> str`, `format_balance(yuan: float | None) -> str`. Constant `HTTP_TIMEOUT_SECONDS=30.0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kimi_quota.py
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from aacc.kimi_quota import (
    KimiQuotaError,
    KimiQuotaUnauthorizedError,
    fetch_quota,
    format_balance,
    format_reset_countdown,
    parse_quota,
    usages_url,
)


def full_payload() -> dict:
    return {
        "usage": {"limit": "1000", "used": "420", "resetTime": "2026-07-31T16:00:00.000Z"},
        "limits": [
            {
                "window": {"duration": 10080},
                "detail": {"limit": "999", "used": "1"},
            },
            {
                "window": {"duration": 300},
                "detail": {"limit": "100", "used": "10", "resetTime": "2026-07-24T20:00:00Z"},
            },
        ],
        "totalQuota": {"limit": "5000", "remaining": "3000"},
        "user": {"membership": {"level": "PRO"}},
        "boosterWallet": {
            "status": "STATUS_ACTIVE",
            "balance": {"amount": "1000000000", "amountLeft": "315250700"},
        },
    }


def test_parse_full_payload():
    quota = parse_quota(full_payload())
    assert quota.weekly.used == 420
    assert quota.weekly.limit == 1000
    assert quota.weekly.percentage == 42
    assert quota.weekly.reset_at == datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    assert quota.five_hour.used == 10
    assert quota.five_hour.limit == 100
    assert quota.five_hour.percentage == 10
    assert quota.total_quota.remaining == 3000
    assert quota.total_quota.used == 2000
    assert quota.membership_level == "PRO"
    assert quota.booster is not None
    assert quota.booster.is_enabled
    assert quota.booster.balance_yuan == pytest.approx(3.152507)


def test_parse_booster_disabled_shows_zero_balance():
    payload = full_payload()
    payload["boosterWallet"]["status"] = "STATUS_DISABLED"
    quota = parse_quota(payload)
    assert quota.booster is not None
    assert not quota.booster.is_enabled
    assert quota.booster.balance_yuan == 0.0


def test_parse_missing_sections_yield_zero_details():
    quota = parse_quota({})
    assert quota.weekly.used == 0
    assert quota.weekly.limit == 0
    assert quota.weekly.percentage == 0
    assert quota.weekly.reset_at is None
    assert quota.five_hour.limit == 0
    assert quota.membership_level is None
    assert quota.booster is None


def test_parse_used_derived_from_remaining():
    quota = parse_quota({"usage": {"limit": 100, "remaining": 30}})
    assert quota.weekly.used == 70
    assert quota.weekly.remaining == 30


def test_parse_numeric_fields_not_strings():
    quota = parse_quota({"usage": {"limit": 200, "used": 50.0}})
    assert quota.weekly.used == 50
    assert quota.weekly.percentage == 25


def test_parse_garbage_is_safe():
    quota = parse_quota("garbage")
    assert quota.weekly.limit == 0
    quota = parse_quota({"usage": {"limit": "abc", "used": [1]}})
    assert quota.weekly.limit == 0
    assert quota.weekly.used == 0


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_quota_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=full_payload())

    quota = fetch_quota(make_client(handler), "tok")
    assert quota.weekly.percentage == 42


def test_fetch_quota_unauthorized_and_http_error():
    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(KimiQuotaUnauthorizedError):
        fetch_quota(make_client(handler_401), "tok")

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(KimiQuotaError, match="HTTP 500"):
        fetch_quota(make_client(handler_500), "tok")


def test_usages_url_env_override(monkeypatch):
    assert usages_url() == "https://api.kimi.com/coding/v1/usages"
    monkeypatch.setenv("KIMI_CODE_BASE_URL", "https://api.example.com/coding/v1/")
    assert usages_url() == "https://api.example.com/coding/v1/usages"


def test_format_reset_countdown():
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    assert format_reset_countdown(None, now=now) == "未知"
    assert (
        format_reset_countdown(datetime(2026, 7, 27, 14, 0, tzinfo=UTC), now=now)
        == "3天2小时后重置"
    )
    assert (
        format_reset_countdown(datetime(2026, 7, 24, 14, 30, tzinfo=UTC), now=now)
        == "2小时30分钟后重置"
    )
    assert (
        format_reset_countdown(datetime(2026, 7, 24, 12, 45, tzinfo=UTC), now=now)
        == "45分钟后重置"
    )
    assert (
        format_reset_countdown(datetime(2026, 7, 24, 11, 0, tzinfo=UTC), now=now) == "即将重置"
    )


def test_format_balance():
    assert format_balance(None) == ""
    assert format_balance(3.152507) == "¥3.15"
    assert format_balance(0.0) == "¥0.00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kimi_quota.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aacc.kimi_quota'`

- [ ] **Step 3: Write the implementation**

```python
# src/aacc/kimi_quota.py
"""Kimi managed-platform quota fetch + parse.

Parsing follows the intentionally loose rules of MoonshotAI/kimi-code
`packages/oauth` `managed-usage.ts` (MIT License, Copyright (c) MoonshotAI)
plus edge-case fixes from KimiCodeBar (MIT License, Copyright (c) xifandev):
the booster balance is `balance.amountLeft` in units of 1e-8 yuan and is
only meaningful while the wallet status is ACTIVE/ENABLED — otherwise the
API returns a monthly-cap-derived number that must be shown as 0.
See NOTICE.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
HTTP_TIMEOUT_SECONDS = 30.0
FIVE_HOUR_WINDOW_MINUTES = 300
BOOSTER_ENABLED_STATUSES = {"STATUS_ACTIVE", "STATUS_ENABLED"}


class KimiQuotaError(RuntimeError):
    """Quota fetch or parse failed."""


class KimiQuotaUnauthorizedError(KimiQuotaError):
    """The access token was rejected; re-authorization is required."""


@dataclass(frozen=True)
class QuotaDetail:
    used: int
    limit: int
    remaining: int
    reset_at: datetime | None
    percentage: int


@dataclass(frozen=True)
class BoosterWallet:
    status: str
    is_enabled: bool
    balance_yuan: float


@dataclass(frozen=True)
class KimiQuota:
    weekly: QuotaDetail
    five_hour: QuotaDetail
    total_quota: QuotaDetail
    membership_level: str | None
    booster: BoosterWallet | None


def usages_url() -> str:
    base = os.environ.get("KIMI_CODE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/usages"


def _to_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return int(float(value))
            except ValueError:
                return None
    return None


def _parse_reset(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _make_detail(raw: object) -> QuotaDetail:
    section: dict[str, Any] = raw if isinstance(raw, dict) else {}
    limit = _to_int(section.get("limit")) or 0
    used = _to_int(section.get("used"))
    remaining = _to_int(section.get("remaining"))
    if used is None:
        used = max(0, limit - remaining) if remaining is not None else 0
    if remaining is None:
        remaining = max(0, limit - used)
    percentage = round(used / limit * 100) if limit > 0 else 0
    reset_at = _parse_reset(
        section.get("resetTime") or section.get("reset_at") or section.get("resetAt")
    )
    return QuotaDetail(
        used=used, limit=limit, remaining=remaining, reset_at=reset_at, percentage=percentage
    )


def _is_five_hour_window(window: object) -> bool:
    if not isinstance(window, dict):
        return False
    duration = _to_int(window.get("duration"))
    if duration != FIVE_HOUR_WINDOW_MINUTES:
        return False
    unit = window.get("timeUnit") or window.get("unit") or ""
    return not isinstance(unit, str) or not unit or unit.lower().startswith("m")


def _parse_booster(raw: object) -> BoosterWallet | None:
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    status_text = status if isinstance(status, str) and status else "STATUS_UNKNOWN"
    is_enabled = status_text.upper() in BOOSTER_ENABLED_STATUSES
    balance = raw.get("balance")
    amount_left = _to_int(balance.get("amountLeft")) if isinstance(balance, dict) else None
    balance_yuan = (
        max(0.0, amount_left / 100_000_000) if is_enabled and amount_left is not None else 0.0
    )
    return BoosterWallet(status=status_text, is_enabled=is_enabled, balance_yuan=balance_yuan)


def parse_quota(data: object) -> KimiQuota:
    root: dict[str, Any] = data if isinstance(data, dict) else {}
    weekly = _make_detail(root.get("usage"))
    five_hour = QuotaDetail(used=0, limit=0, remaining=0, reset_at=None, percentage=0)
    limits = root.get("limits")
    if isinstance(limits, list):
        for item in limits:
            if isinstance(item, dict) and _is_five_hour_window(item.get("window")):
                five_hour = _make_detail(item.get("detail"))
                break
    total_quota = _make_detail(root.get("totalQuota"))
    membership_level: str | None = None
    user = root.get("user")
    if isinstance(user, dict):
        membership = user.get("membership")
        if isinstance(membership, dict):
            level = membership.get("level")
            if isinstance(level, str) and level:
                membership_level = level
    return KimiQuota(
        weekly=weekly,
        five_hour=five_hour,
        total_quota=total_quota,
        membership_level=membership_level,
        booster=_parse_booster(root.get("boosterWallet")),
    )


def _error_detail(data: object) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            return error
        for key in ("message", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return "unknown"


def fetch_quota(client: httpx.Client, access_token: str) -> KimiQuota:
    try:
        response = client.get(
            usages_url(),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        raise KimiQuotaError(f"Quota request failed: {error}") from error
    if response.status_code in (401, 403):
        raise KimiQuotaUnauthorizedError(
            f"Quota request rejected (HTTP {response.status_code})"
        )
    if response.status_code != 200:
        try:
            detail = _error_detail(response.json())
        except ValueError:
            detail = "unknown"
        raise KimiQuotaError(f"Quota request failed (HTTP {response.status_code}): {detail}")
    try:
        payload: object = response.json()
    except ValueError:
        raise KimiQuotaError("Quota response is not valid JSON") from None
    return parse_quota(payload)


def format_reset_countdown(reset_at: datetime | None, *, now: datetime | None = None) -> str:
    if reset_at is None:
        return "未知"
    current = now or datetime.now(UTC)
    seconds = (reset_at - current).total_seconds()
    if seconds <= 0:
        return "即将重置"
    days = int(seconds // 86_400)
    hours = int((seconds % 86_400) // 3_600)
    minutes = int((seconds % 3_600) // 60)
    if days > 0:
        return f"{days}天{hours}小时后重置"
    if hours > 0:
        return f"{hours}小时{minutes}分钟后重置"
    if minutes > 0:
        return f"{minutes}分钟后重置"
    return "即将重置"


def format_balance(yuan: float | None) -> str:
    if yuan is None:
        return ""
    return f"¥{yuan:.2f}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kimi_quota.py -q`
Expected: 13 passed

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/ruff check src/aacc/kimi_quota.py tests/test_kimi_quota.py
.venv/bin/mypy src/aacc
git add src/aacc/kimi_quota.py tests/test_kimi_quota.py
git commit -m "feat: add Kimi quota usages client with loose parser and display formatting"
```

---

### Task 3: `quota_service.py` — QObject polling service

**Files:**
- Create: `src/aacc/quota_service.py`
- Test: `tests/test_quota_service.py`

**Interfaces:**
- Consumes: `aacc.kimi_oauth` (`KimiOAuthToken`, `KimiOAuthError`, `KimiOAuthUnauthorizedError`, `load_credentials`, `save_credentials`, `clear_credentials`, `load_or_create_device_id`, `request_device_authorization`, `poll_device_token`, `refresh_access_token`), `aacc.kimi_quota` (`KimiQuota`, `KimiQuotaError`, `KimiQuotaUnauthorizedError`, `fetch_quota`, `HTTP_TIMEOUT_SECONDS`).
- Produces: `QuotaService(config_dir, *, version, interval_seconds=60.0, client_factory=httpx.Client, parent=None)` with signals `quota_updated(object)`, `auth_state_changed(str)`, `oauth_code_ready(str, str)`, `oauth_finished(bool, str)`, `error_occurred(str)`; methods `start()`, `stop()`, `state() -> str`, `refresh_now()`, `begin_oauth()`, `cancel_oauth()`, `set_api_key(key: str)`, `logout()`. State constants `STATE_UNAUTHORIZED="unauthorized"`, `STATE_PENDING="pending"`, `STATE_AUTHORIZED="authorized"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quota_service.py
from __future__ import annotations

import threading
import time

import httpx
import pytest

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
        assert received.wait(timeout=5.0)
    finally:
        service.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_quota_service.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aacc.quota_service'`

- [ ] **Step 3: Write the implementation**

```python
# src/aacc/quota_service.py
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
    HTTP_TIMEOUT_SECONDS,
    KimiQuota,
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

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as error:  # polling must never kill the thread
                self._logger.warning("Kimi quota poll failed: %s", error)
                try:
                    self.error_occurred.emit(str(error))
                except RuntimeError:
                    return  # application shutting down
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
```

Note: `test_oauth_cancel` uses interval 60 — `poll_device_token`'s `sleep` is `time.sleep`, so the worker would block in `sleep(60)` after the first pending response. To keep cancellation responsive, `poll_device_token` sleeps in 0.5s slices when `is_cancelled` is provided... simpler: in `QuotaService._oauth_flow`, pass a custom sleep:

```python
            token = poll_device_token(
                client,
                authorization,
                version=self._version,
                device_id=self._device_id,
                sleep=self._interruptible_sleep,
                is_cancelled=self._cancel_oauth.is_set,
            )
```

with:

```python
    def _interruptible_sleep(self, seconds: float) -> None:
        self._cancel_oauth.wait(min(seconds, 0.5))
```

Wait — that caps each slice at 0.5s but only sleeps 0.5s total per poll interval, making the poll hammer the server every 0.5s. Correct version sleeps the full interval in slices:

```python
    def _interruptible_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._cancel_oauth.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._cancel_oauth.wait(min(remaining, 0.5))
```

Use this version in `_oauth_flow`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_quota_service.py -q`
Expected: 11 passed (all within ~30s; oauth cancel must finish fast)

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/ruff check src/aacc/quota_service.py tests/test_quota_service.py
.venv/bin/mypy src/aacc
.venv/bin/python -m pytest -q
git add src/aacc/quota_service.py tests/test_quota_service.py
git commit -m "feat: add Kimi quota polling service with device OAuth flow"
```

---

### Task 4: Config field + `QuotaBar` widget + styles

**Files:**
- Modify: `src/aacc/models.py` (`AppSettings`, after line 60 `api: APIConfig...`)
- Modify: `src/aacc/gui.py` (new `QuotaBar` class after `ElidedLabel`, i.e. before line 148 `class TaskCard`)
- Modify: `src/aacc/styles.qss` (after line 27 `#messageLabel` rule)
- Test: `tests/test_quota_bar.py`

**Interfaces:**
- Consumes: `aacc.kimi_quota.KimiQuota`, `format_reset_countdown`, `format_balance`; `aacc.quota_service` state constants.
- Produces: `QuotaBar(QFrame)` with signal `clicked()`, methods `show_unauthorized()`, `show_pending()`, `show_quota(quota: KimiQuota)`, `show_error(message: str)`; `AppSettings.kimi_quota_enabled: bool = True`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quota_bar.py
from __future__ import annotations

from datetime import UTC, datetime

from aacc.gui import QuotaBar
from aacc.kimi_quota import BoosterWallet, KimiQuota, QuotaDetail


def make_quota() -> KimiQuota:
    return KimiQuota(
        weekly=QuotaDetail(
            used=420,
            limit=1000,
            remaining=580,
            reset_at=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
            percentage=42,
        ),
        five_hour=QuotaDetail(
            used=10,
            limit=100,
            remaining=90,
            reset_at=datetime(2026, 7, 24, 20, 0, tzinfo=UTC),
            percentage=10,
        ),
        total_quota=QuotaDetail(used=0, limit=0, remaining=0, reset_at=None, percentage=0),
        membership_level="PRO",
        booster=BoosterWallet(status="STATUS_ACTIVE", is_enabled=True, balance_yuan=3.15),
    )


def test_unauthorized_state(qapp):
    bar = QuotaBar()
    bar.show_unauthorized()
    assert "授权" in bar.summary_label.text()
    assert bar.weekly_bar.value() == 0


def test_pending_state(qapp):
    bar = QuotaBar()
    bar.show_pending()
    assert "授权中" in bar.summary_label.text()


def test_show_quota(qapp):
    bar = QuotaBar()
    bar.show_quota(make_quota())
    assert bar.weekly_bar.value() == 42
    assert bar.five_hour_bar.value() == 10
    assert "42%" in bar.weekly_label.text()
    assert "10%" in bar.five_hour_label.text()
    assert "¥3.15" in bar.balance_label.text()
    assert "PRO" in bar.toolTip()


def test_show_quota_without_booster_hides_balance(qapp):
    quota = make_quota()
    bar = QuotaBar()
    bar.show_quota(
        KimiQuota(
            weekly=quota.weekly,
            five_hour=quota.five_hour,
            total_quota=quota.total_quota,
            membership_level=None,
            booster=None,
        )
    )
    assert bar.balance_label.text() == ""


def test_clicked_signal(qapp):
    bar = QuotaBar()
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPoint(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mouseReleaseEvent(event)
    assert clicks == [True]


def test_kimi_quota_enabled_default():
    from aacc.models import AppSettings

    assert AppSettings().kimi_quota_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_quota_bar.py -q`
Expected: FAIL with `ImportError: cannot import name 'QuotaBar' from 'aacc.gui'`

- [ ] **Step 3: Implement config field, QuotaBar, styles**

In `src/aacc/models.py`, add to `AppSettings` after the `api` field:

```python
    kimi_quota_enabled: bool = True
```

In `src/aacc/gui.py`, after the `ElidedLabel` class (before `class TaskCard`), add:

```python
class QuotaBar(QFrame):
    """Kimi account quota strip shown above the task list."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("quotaBar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(7)
        self.dot = QLabel("●")
        self.dot.setObjectName("quotaDot")
        layout.addWidget(self.dot)
        self.summary_label = QLabel("Kimi 额度")
        self.summary_label.setObjectName("quotaSummary")
        layout.addWidget(self.summary_label)
        layout.addSpacing(4)
        self.weekly_label = QLabel("周 --")
        self.weekly_label.setObjectName("quotaText")
        layout.addWidget(self.weekly_label)
        self.weekly_bar = QProgressBar()
        self.weekly_bar.setObjectName("quotaProgress")
        self.weekly_bar.setRange(0, 100)
        self.weekly_bar.setTextVisible(False)
        self.weekly_bar.setFixedSize(56, 5)
        layout.addWidget(self.weekly_bar)
        self.five_hour_label = QLabel("5h --")
        self.five_hour_label.setObjectName("quotaText")
        layout.addWidget(self.five_hour_label)
        self.five_hour_bar = QProgressBar()
        self.five_hour_bar.setObjectName("quotaProgress")
        self.five_hour_bar.setRange(0, 100)
        self.five_hour_bar.setTextVisible(False)
        self.five_hour_bar.setFixedSize(56, 5)
        layout.addWidget(self.five_hour_bar)
        layout.addStretch()
        self.balance_label = QLabel("")
        self.balance_label.setObjectName("quotaBalance")
        layout.addWidget(self.balance_label)
        self.show_unauthorized()

    def show_unauthorized(self) -> None:
        self.dot.setStyleSheet("color: #e06c75;")
        self.summary_label.setText("Kimi 额度 · 点击授权")
        self.weekly_label.setText("周 --")
        self.five_hour_label.setText("5h --")
        self.weekly_bar.setValue(0)
        self.five_hour_bar.setValue(0)
        self.balance_label.setText("")
        self.setToolTip("点击通过 Kimi 官方设备授权登录，查询账户额度")

    def show_pending(self) -> None:
        self.dot.setStyleSheet("color: #e5c07b;")
        self.summary_label.setText("Kimi 额度 · 授权中…")

    def show_quota(self, quota: KimiQuota) -> None:
        self.dot.setStyleSheet("color: #98c379;")
        self.summary_label.setText("Kimi 额度")
        self.weekly_label.setText(f"周 {quota.weekly.percentage}%")
        self.five_hour_label.setText(f"5h {quota.five_hour.percentage}%")
        self.weekly_bar.setValue(quota.weekly.percentage)
        self.five_hour_bar.setValue(quota.five_hour.percentage)
        balance = (
            format_balance(quota.booster.balance_yuan) if quota.booster is not None else ""
        )
        self.balance_label.setText(balance)
        tooltip_lines = [
            f"每周额度：{quota.weekly.percentage}%"
            f"（{format_reset_countdown(quota.weekly.reset_at)}）",
            f"5 小时额度：{quota.five_hour.percentage}%"
            f"（{format_reset_countdown(quota.five_hour.reset_at)}）",
        ]
        if quota.membership_level:
            tooltip_lines.append(f"会员等级：{quota.membership_level}")
        if balance:
            tooltip_lines.append(f"加油包余额：{balance}")
        tooltip_lines.append("点击刷新")
        self.setToolTip("\n".join(tooltip_lines))

    def show_error(self, message: str) -> None:
        self.dot.setStyleSheet("color: #8997aa;")
        self.setToolTip(f"额度刷新失败：{message}\n点击重试")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)
```

Add imports to gui.py: `QProgressBar` to the `PySide6.QtWidgets` import list, and
`from aacc.kimi_quota import KimiQuota, format_balance, format_reset_countdown`.

In `src/aacc/styles.qss`, after the `#messageLabel` rule (line 27), add:

```css
#quotaBar { background: rgba(255, 255, 255, 0.04); border-radius: 8px; }
#quotaSummary { color: #d9e2ef; font-size: 11px; font-weight: 700; }
#quotaText { color: #8997aa; font-size: 10px; }
#quotaBalance { color: #e5c07b; font-size: 10px; font-weight: 700; }
#quotaProgress { background: rgba(255, 255, 255, 0.08); border: none; border-radius: 2px; }
#quotaProgress::chunk { background: #4d9fff; border-radius: 2px; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_quota_bar.py -q`
Expected: 7 passed

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
.venv/bin/python -m pytest -q
git add src/aacc/models.py src/aacc/gui.py src/aacc/styles.qss tests/test_quota_bar.py
git commit -m "feat: add QuotaBar widget and kimi_quota_enabled config flag"
```

---

### Task 5: MainWindow wiring + OAuth dialog + settings entries

**Files:**
- Modify: `src/aacc/gui.py` — `MainWindow.__init__` (new kwargs, QuotaBar insertion after line 822 `layout.addLayout(header)`), new methods, `SettingsDialog.__init__` (after line 395 `layout.addWidget(rotate_credentials)`)
- Test: `tests/test_gui_quota_wiring.py`

**Interfaces:**
- Consumes: `QuotaService` (Task 3), `QuotaBar` (Task 4).
- Produces: `MainWindow(..., quota_service: QuotaService | None = None, open_url: Callable[[str], None] | None = None)`; attribute `window.quota_bar: QuotaBar | None`; methods `window.save_kimi_api_key(key: str)`, `window.kimi_logout()`. Default `open_url` opens the URL in the system browser.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_quota_wiring.py
from __future__ import annotations

import httpx
import pytest

from aacc.quota_service import STATE_AUTHORIZED, STATE_UNAUTHORIZED, QuotaService
from aacc.kimi_oauth import save_credentials

pytest.importorskip("pytestqt")


def make_window(qtbot, tmp_path, handler=None, with_service=True):
    from aacc.automation_executor import AutomationExecutor
    from aacc.automation import MacAutomation
    from aacc.config import default_config
    from aacc.gui import MainWindow
    from aacc.persistence import StateStore
    from aacc.task_manager import TaskManager

    config = default_config()
    store = StateStore(tmp_path / "state.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    automation = MacAutomation(config, accessibility_trusted=lambda: True)
    service = None
    if with_service:
        transport = httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json={"usage": {}}))
        )
        service = QuotaService(
            tmp_path / "cfg",
            version="test",
            client_factory=lambda: httpx.Client(transport=transport),
        )
    opened: list[str] = []
    window = MainWindow(
        manager,
        AutomationExecutor(automation),
        enable_tray=False,
        quota_service=service,
        open_url=opened.append,
    )
    qtbot.addWidget(window)
    return window, service, opened


def test_quota_bar_absent_without_service(qtbot, tmp_path):
    window, _, _ = make_window(qtbot, tmp_path, with_service=False)
    assert window.quota_bar is None


def test_quota_bar_present_and_click_triggers_refresh(qtbot, tmp_path):
    window, service, _ = make_window(qtbot, tmp_path)
    assert window.quota_bar is not None
    calls: list[bool] = []
    service.refresh_now = lambda: calls.append(True)  # type: ignore[method-assign]
    window._on_quota_bar_clicked()
    assert calls == []  # unauthorized state starts OAuth instead
    service._state = STATE_AUTHORIZED
    window._on_quota_bar_clicked()
    assert calls == [True]


def test_click_unauthorized_starts_oauth(qtbot, tmp_path):
    window, service, _ = make_window(qtbot, tmp_path)
    began: list[bool] = []
    service.begin_oauth = lambda: began.append(True)  # type: ignore[method-assign]
    assert service.state() == STATE_UNAUTHORIZED
    window._on_quota_bar_clicked()
    assert began == [True]


def test_oauth_code_ready_opens_dialog_and_url(qtbot, tmp_path):
    window, _, opened = make_window(qtbot, tmp_path)
    window._on_oauth_code_ready("ABCD-EFGH", "https://auth.kimi.com/device")
    assert opened == ["https://auth.kimi.com/device"]
    assert window._oauth_dialog is not None
    assert "ABCD-EFGH" in window._oauth_dialog.code_label.text()
    window._on_oauth_finished(True, "")
    assert window._oauth_dialog is None


def test_save_api_key_and_logout_delegate(qtbot, tmp_path):
    window, service, _ = make_window(qtbot, tmp_path)
    saved: list[str] = []
    service.set_api_key = saved.append  # type: ignore[method-assign]
    window.save_kimi_api_key(" sk-kimi-x ")
    assert saved == [" sk-kimi-x "]
    logged_out: list[bool] = []
    service.logout = lambda: logged_out.append(True)  # type: ignore[method-assign]
    window.kimi_logout()
    assert logged_out == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_quota_wiring.py -q`
Expected: FAIL — `MainWindow.__init__() got an unexpected keyword argument 'quota_service'`

- [ ] **Step 3: Implement MainWindow wiring**

In `src/aacc/gui.py`:

1. Imports: add `from aacc.quota_service import STATE_AUTHORIZED, STATE_PENDING, QuotaService` and `from PySide6.QtGui import QDesktopServices`, `from PySide6.QtCore import QUrl` (extend the existing QtGui/QtCore import lists), `from PySide6.QtWidgets import QDialogButtonBox, QLineEdit` (extend QtWidgets list).

2. `MainWindow.__init__` signature: add keyword params after `settings: QSettings | None = None`:

```python
        quota_service: QuotaService | None = None,
        open_url: Callable[[str], None] | None = None,
```

3. In `__init__` body, after the accessibility block (`self._open_accessibility_settings = ...` line 655), add:

```python
        self.quota_service = quota_service
        self._open_url = open_url or (
            lambda url: QDesktopServices.openUrl(QUrl(url))
        )
        self._oauth_dialog: KimiOAuthDialog | None = None
        self.quota_bar: QuotaBar | None = None
```

4. After `layout.addLayout(header)` (line 822), add:

```python
        if self.quota_service is not None:
            self.quota_bar = QuotaBar()
            self.quota_bar.clicked.connect(self._on_quota_bar_clicked)
            layout.addWidget(self.quota_bar)
            self.quota_service.quota_updated.connect(self._on_quota_updated)
            self.quota_service.auth_state_changed.connect(self._on_quota_auth_state)
            self.quota_service.oauth_code_ready.connect(self._on_oauth_code_ready)
            self.quota_service.oauth_finished.connect(self._on_oauth_finished)
            self.quota_service.error_occurred.connect(self._on_quota_error)
            self._on_quota_auth_state(self.quota_service.state())
```

5. New methods on `MainWindow` (place after `_apply_state`):

```python
    def _on_quota_bar_clicked(self) -> None:
        if self.quota_service is None:
            return
        if self.quota_service.state() == STATE_AUTHORIZED:
            self.quota_service.refresh_now()
        elif self.quota_service.state() != STATE_PENDING:
            self.quota_service.begin_oauth()

    def _on_quota_updated(self, quota: object) -> None:
        if self.quota_bar is not None and isinstance(quota, KimiQuota):
            self.quota_bar.show_quota(quota)

    def _on_quota_auth_state(self, state: str) -> None:
        if self.quota_bar is None:
            return
        if state == STATE_PENDING:
            self.quota_bar.show_pending()
        elif state != STATE_AUTHORIZED:
            self.quota_bar.show_unauthorized()

    def _on_quota_error(self, message: str) -> None:
        if self.quota_bar is not None:
            self.quota_bar.show_error(message)

    def _on_oauth_code_ready(self, user_code: str, url: str) -> None:
        if self._oauth_dialog is None:
            self._oauth_dialog = KimiOAuthDialog(self)
            self._oauth_dialog.cancelled.connect(self._on_oauth_cancelled)
        self._oauth_dialog.set_code(user_code)
        self._oauth_dialog.show()
        self._open_url(url)

    def _on_oauth_cancelled(self) -> None:
        if self.quota_service is not None:
            self.quota_service.cancel_oauth()

    def _on_oauth_finished(self, success: bool, message: str) -> None:
        if self._oauth_dialog is not None:
            self._oauth_dialog.close()
            self._oauth_dialog.deleteLater()
            self._oauth_dialog = None
        if not success and message:
            self.subtitle.setText(f"KIMI 授权失败：{message[:60]}")

    def save_kimi_api_key(self, key: str) -> None:
        if self.quota_service is None:
            return
        try:
            self.quota_service.set_api_key(key)
        except ValueError as error:
            self.subtitle.setText(str(error))

    def kimi_logout(self) -> None:
        if self.quota_service is not None:
            self.quota_service.logout()
```

6. New dialog class (place after `QuotaBar`):

```python
class KimiOAuthDialog(QDialog):
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kimi 授权")
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("浏览器已打开 Kimi 授权页面，请确认以下验证码："))
        self.code_label = QLabel("")
        self.code_label.setObjectName("oauthCode")
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.code_label)
        hint = QLabel("授权完成后此窗口会自动关闭")
        hint.setObjectName("quotaText")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        cancel = QPushButton("取消授权")
        cancel.clicked.connect(self._on_cancel)
        layout.addWidget(cancel)

    def set_code(self, user_code: str) -> None:
        self.code_label.setText(user_code)

    def _on_cancel(self) -> None:
        self.cancelled.emit()
        self.close()
```

Add qss rule after the quota rules:

```css
#oauthCode { color: #d9e2ef; font-family: Menlo; font-size: 20px; font-weight: 700; padding: 8px; }
```

7. `SettingsDialog.__init__`, after `layout.addWidget(rotate_credentials)` (line 395), add:

```python
        if window.quota_service is not None:
            layout.addWidget(QLabel("Kimi 额度（可用 API Key 替代 OAuth 授权）"))
            api_key = QLineEdit()
            api_key.setPlaceholderText("sk-kimi-…")
            api_key.setEchoMode(QLineEdit.EchoMode.Password)
            layout.addWidget(api_key)
            save_key = QPushButton("保存 Kimi API Key")
            save_key.clicked.connect(lambda: window.save_kimi_api_key(api_key.text()))
            layout.addWidget(save_key)
            kimi_logout = QPushButton("退出 Kimi 登录")
            kimi_logout.clicked.connect(window.kimi_logout)
            layout.addWidget(kimi_logout)
```

Note: `save_kimi_api_key` passes the raw text to the service (the service trims and validates), which is what the test asserts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_quota_wiring.py tests/test_quota_bar.py -q`
Expected: all pass

- [ ] **Step 5: Lint, type-check, full suite, commit**

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
.venv/bin/python -m pytest -q
git add src/aacc/gui.py src/aacc/styles.qss tests/test_gui_quota_wiring.py
git commit -m "feat: wire quota service into main window with OAuth dialog and settings"
```

---

### Task 6: app.py runtime wiring

**Files:**
- Modify: `src/aacc/app.py` — `Runtime` dataclass, `build_runtime`, `_run_application`
- Test: extend `tests/test_app.py`

**Interfaces:**
- Consumes: `QuotaService` (Task 3), `aacc.__version__`.
- Produces: `Runtime.quota_service: QuotaService | None`; `build_runtime(..., quota_service_factory: Callable[[Path], QuotaService | None] | None = None)` keyword for tests.

- [ ] **Step 1: Write the failing test**

Check the existing `tests/test_app.py` for how `build_runtime` is tested (fixtures for config/db paths). Append:

```python
def test_build_runtime_creates_quota_service_when_enabled(tmp_path, monkeypatch):
    import httpx

    from aacc.app import build_runtime
    from aacc.quota_service import QuotaService

    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    runtime = build_runtime(
        config_path,
        database_path,
        quota_service_factory=lambda config_dir: QuotaService(
            config_dir,
            version="test",
            client_factory=lambda: httpx.Client(transport=transport),
        ),
    )
    try:
        assert runtime.quota_service is not None
    finally:
        runtime.close()


def test_build_runtime_skips_quota_service_when_disabled(tmp_path):
    from aacc.app import build_runtime

    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    runtime = build_runtime(config_path, database_path, quota_service_factory=lambda _dir: None)
    try:
        assert runtime.quota_service is None
    finally:
        runtime.close()
```

(Adjust to the actual fixtures/imports already used in `tests/test_app.py`; `build_runtime` must default to the real factory when the kwarg is omitted, honoring `config.app.kimi_quota_enabled`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -q`
Expected: FAIL — `build_runtime() got an unexpected keyword argument 'quota_service_factory'`

- [ ] **Step 3: Implement**

In `src/aacc/app.py`:

1. Imports: add `import aacc` (or `from aacc import __version__`), `from aacc.quota_service import QuotaService`, `from collections.abc import Callable` (already imported).

2. `Runtime` dataclass: add field

```python
    quota_service: QuotaService | None = None
```

and in `close()`, before the other stops:

```python
        if self.quota_service is not None:
            self.quota_service.stop()
```

3. `build_runtime` signature + body:

```python
def _default_quota_service_factory(config_dir: Path, config: AppConfig) -> QuotaService | None:
    if not config.app.kimi_quota_enabled:
        return None
    return QuotaService(config_dir, version=__version__)


def build_runtime(
    config_path: Path,
    database_path: Path,
    *,
    accessibility_trusted: Callable[[], bool] = lambda: True,
    quota_service_factory: Callable[[Path], QuotaService | None] | None = None,
) -> Runtime:
    config = load_config(config_path)
    ...
    factory = quota_service_factory or (
        lambda config_dir: _default_quota_service_factory(config_dir, config)
    )
    return Runtime(
        ...,
        quota_service=factory(config_path.parent),
    )
```

4. `_run_application`: pass `quota_service=runtime.quota_service` to `MainWindow(...)`, and after `runtime.kimi_desktop_discovery.start()` add:

```python
    if runtime.quota_service is not None:
        runtime.quota_service.start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -q`
Expected: all pass

- [ ] **Step 5: Lint, type-check, full suite, commit**

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
.venv/bin/python -m pytest -q
git add src/aacc/app.py tests/test_app.py
git commit -m "feat: start Kimi quota service in application runtime"
```

---

### Task 7: NOTICE, README attribution, CHANGELOG, final gate

**Files:**
- Create: `NOTICE`
- Modify: `README.md`, `README.zh-CN.md` (致谢/Attribution section)
- Modify: `CHANGELOG.md`, `CHANGELOG.zh-CN.md` (Unreleased section)

- [ ] **Step 1: Write NOTICE**

```text
AACC (AI Agent Control Center)
Copyright (c) AACC contributors

This product includes logic ported or adapted from the following
MIT-licensed projects:

1. MoonshotAI/kimi-code — packages/oauth
   Copyright (c) MoonshotAI
   https://github.com/MoonshotAI/kimi-code
   Used for: the OAuth Device Code Flow parameters and token handling in
   src/aacc/kimi_oauth.py, and the loose usages payload parsing rules in
   src/aacc/kimi_quota.py.

2. KimiCodeBar — https://github.com/xifandev/KimiCodeBar
   Copyright (c) 2026 xifandev
   Used for: booster-wallet parsing edge cases (balance units, ACTIVE /
   ENABLED status gating) and the credential-storage isolation principle
   in src/aacc/kimi_oauth.py / src/aacc/kimi_quota.py.

3. kimi-code-monitor — https://github.com/bfjnbvf/kimi-code-monitor
   Copyright (c) 2026 十叶
   Used for: per-session token metric algorithms in src/aacc/kimi_metrics.py
   (usage field normalization, median speed window).

The MIT License text for each project is available in their respective
repositories and at https://opensource.org/license/mit
```

- [ ] **Step 2: README attribution**

Append to `README.zh-CN.md` (and the English equivalent in `README.md`) a short section:

```markdown
## 致谢

Kimi 额度监控与会话指标功能参考并移植了以下 MIT 开源项目的逻辑：
[MoonshotAI/kimi-code](https://github.com/MoonshotAI/kimi-code)（官方 OAuth 流程与额度接口约定）、
[KimiCodeBar](https://github.com/xifandev/KimiCodeBar)（加油包解析与凭据隔离设计）、
[kimi-code-monitor](https://github.com/bfjnbvf/kimi-code-monitor)（会话 token 指标算法）。
详见 [NOTICE](NOTICE)。
```

- [ ] **Step 3: CHANGELOG**

Add to both CHANGELOG files under a new `## Unreleased` (or `## 未发布`) heading:

```markdown
- 新增 Kimi 账户额度监控：面板顶部显示每周 / 5 小时额度与加油包余额，支持官方设备授权登录或 API Key。
```

English: `Add Kimi account quota monitoring: weekly / 5-hour quota and booster balance in the panel header, via official device authorization or API key.`

- [ ] **Step 4: Final quality gate + commit**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
git add NOTICE README.md README.zh-CN.md CHANGELOG.md CHANGELOG.zh-CN.md
git commit -m "docs: add NOTICE and attribution for ported MIT-licensed logic"
```

- [ ] **Step 5: Manual smoke test (requires real macOS desktop, mark clearly)**

Not runnable in CI: build the app (`scripts/build_app.sh`), launch it, click the quota bar, complete a real device authorization, confirm the quota values render. Record the result in the task notes. Do not commit anything for this step.

---

## Self-Review Notes

- Spec coverage: OAuth flow (Task 1), quota API + parser rules incl. booster edge cases (Task 2), polling service with state machine + cache TTL + single-flight refresh (Task 3), config flag + QuotaBar (Task 4), MainWindow + dialog + settings (Task 5), runtime wiring (Task 6), attribution (Task 7). API-key auth: Tasks 3 + 5. Env overrides: Tasks 1 + 2.
- `QuotaService._poll_once` honors `CACHE_TTL_SECONDS=30` on every path including `refresh_now` (TTL guard is the first statement).
- Settings dialog intentionally has no enable/disable checkbox (M1 scope cut): the bar appears iff `kimi_quota_enabled` in config.yaml; documented in the spec.
