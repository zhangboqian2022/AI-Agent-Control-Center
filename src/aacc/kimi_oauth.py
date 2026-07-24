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
        scope = raw.get("scope")
        token_type = raw.get("token_type")
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=int(expires_at),
            scope=scope if isinstance(scope, str) else "",
            token_type=token_type if isinstance(token_type, str) else "Bearer",
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
    expires_in_raw = data.get("expires_in")
    if expires_in_raw is None:
        raise KimiOAuthError("OAuth response missing or invalid expires_in")
    try:
        expires_in = float(expires_in_raw)
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
