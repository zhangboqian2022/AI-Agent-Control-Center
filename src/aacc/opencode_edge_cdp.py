"""Isolated Microsoft Edge/CDP primitives for OpenCode quota on Windows."""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.parse import urlparse
from uuid import uuid4

from aacc.file_security import protect_directory
from aacc.kimi_edge_cdp import (
    EDGE_HEADLESS_AUTH_GRACE_SECONDS,
    EDGE_LOGIN_TIMEOUT_SECONDS,
    EDGE_SHUTDOWN_TIMEOUT_SECONDS,
    EDGE_STARTUP_TIMEOUT_SECONDS,
    CdpConnection,
    DevToolsEndpoint,
    _is_reparse_point,
    _load_targets,
    _open_socket,
    _ProcessLike,
    _start_process,
    _terminate_process_tree,
    find_edge_executable,
    read_devtools_endpoint,
)
from aacc.opencode_web_error import OpenCodeQuotaErrorCategory

OPENCODE_EDGE_PROFILE_NAME = "opencode-edge-profile"
_QUARANTINE_PREFIX = f".{OPENCODE_EDGE_PROFILE_NAME}.logout-"
_WINDOW_KEYS = frozenset({"usagePercent", "resetInSec"})
_WORKSPACE_PATH = re.compile(r"^/workspace/[A-Za-z0-9_-]+(?:/go)?/?$")
_PAGE_PATH = re.compile(r"^/devtools/page/[A-Za-z0-9_-]+$")
_logger = logging.getLogger("aacc.opencode_edge_cdp")


class OpenCodeEdgeQuotaError(RuntimeError):
    """Sanitized OpenCode Edge failure."""

    def __init__(self, category: OpenCodeQuotaErrorCategory) -> None:
        self.category = category
        super().__init__(category.value)


class OpenCodeEdgeUnauthorizedError(RuntimeError):
    """The owned OpenCode web session needs visible authorization."""


class OpenCodeEdgeCancelledError(RuntimeError):
    """The owning Qt session cancelled the Edge operation."""


@dataclass(frozen=True)
class OpenCodeEdgeLaunchSpec:
    executable: Path
    arguments: tuple[str, ...]
    profile: Path


def opencode_edge_profile_path(local_app_data: Path) -> Path:
    return local_app_data / "AACC" / OPENCODE_EDGE_PROFILE_NAME


def validate_owned_opencode_profile(profile: Path, local_app_data: Path) -> None:
    expected = opencode_edge_profile_path(local_app_data)
    if profile != expected or _is_reparse_point(profile):
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    if _is_reparse_point(local_app_data) or _is_reparse_point(expected.parent):
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    if profile.exists() and not profile.is_dir():
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)


def clear_owned_opencode_profile(profile: Path, local_app_data: Path) -> None:
    validate_owned_opencode_profile(profile, local_app_data)
    if not profile.parent.exists():
        return
    try:
        if profile.exists():
            quarantine = profile.parent / f"{_QUARANTINE_PREFIX}{uuid4().hex}"
            os.replace(profile, quarantine)
        # A failed recursive delete must be retryable even though the atomic
        # rename already removed the live profile path.
        for candidate in profile.parent.iterdir():
            if not candidate.name.startswith(_QUARANTINE_PREFIX):
                continue
            if _is_reparse_point(candidate) or not candidate.is_dir():
                raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
            shutil.rmtree(candidate)
    except OpenCodeEdgeQuotaError:
        raise
    except OSError as error:
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED) from error


def _validate_workspace_url(workspace_url: str) -> None:
    parsed = urlparse(workspace_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "opencode.ai"
        or _WORKSPACE_PATH.fullmatch(parsed.path) is None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid OpenCode workspace URL")


def build_opencode_edge_launch(
    executable: Path,
    profile: Path,
    workspace_url: str,
    *,
    visible: bool,
) -> OpenCodeEdgeLaunchSpec:
    _validate_workspace_url(workspace_url)
    mode_arguments: tuple[str, ...] = () if visible else ("--headless=new", "--disable-gpu")
    return OpenCodeEdgeLaunchSpec(
        executable=executable,
        profile=profile,
        arguments=(
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            *mode_arguments,
            f"--app={workspace_url}",
        ),
    )


def select_opencode_target(
    targets: object,
    *,
    expected_port: int,
    expected_workspace_url: str,
) -> str:
    _validate_workspace_url(expected_workspace_url)
    expected_path = urlparse(expected_workspace_url).path.rstrip("/")
    if not isinstance(targets, list):
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    for candidate in targets:
        if not isinstance(candidate, dict):
            continue
        page_url = candidate.get("url")
        websocket_url = candidate.get("webSocketDebuggerUrl")
        parsed_page = urlparse(page_url) if isinstance(page_url, str) else None
        if candidate.get("type") != "page" or parsed_page is None:
            continue
        if (
            parsed_page.scheme != "https"
            or parsed_page.netloc != "opencode.ai"
            or parsed_page.path.rstrip("/") != expected_path
            or parsed_page.params
            or parsed_page.query
            or parsed_page.fragment
        ):
            continue
        if not isinstance(websocket_url, str):
            raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
        parsed_socket = urlparse(websocket_url)
        try:
            socket_port = parsed_socket.port
        except ValueError as error:
            raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED) from error
        if (
            parsed_socket.scheme == "ws"
            and parsed_socket.hostname == "127.0.0.1"
            and socket_port == expected_port
            and _PAGE_PATH.fullmatch(parsed_socket.path) is not None
            and not parsed_socket.query
            and not parsed_socket.fragment
        ):
            return websocket_url
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    raise OpenCodeEdgeUnauthorizedError


def opencode_dom_extract_expression(workspace_url: str) -> str:
    _validate_workspace_url(workspace_url)
    return r"""
(async () => {
  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const parseResetSeconds = (text) => {
    let seconds = 0;
    const days = text.match(/(\d+)\s*(?:天|days?|day)/i);
    const hours = text.match(/(\d+)\s*(?:小时|hours?|hour)/i);
    const minutes = text.match(/(\d+)\s*(?:分钟|minutes?|minute)/i);
    if (days) seconds += parseInt(days[1], 10) * 86400;
    if (hours) seconds += parseInt(hours[1], 10) * 3600;
    if (minutes) seconds += parseInt(minutes[1], 10) * 60;
    return seconds > 0 ? seconds : null;
  };
  for (let attempt = 0; attempt <= 7; attempt += 1) {
    const text = document.body ? document.body.innerText : '';
    const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
    if (/(?:sign in|log in|continue with (?:github|google)|登录)/i.test(text)) {
      return {kind: 'unauthorized'};
    }
    const percentages = [];
    const resets = [];
    for (const line of lines) {
      const percent = line.match(/^(\d{1,3})\s*%$/);
      if (percent) percentages.push(parseInt(percent[1], 10));
      if (/重置|reset|Resets/i.test(line)) resets.push(parseResetSeconds(line));
    }
    if (percentages.length >= 3) {
      const take = (values, index) => index < values.length ? values[index] : null;
      return {
        kind: 'quota',
        raw: {subscription: {
          rollingUsage: {usagePercent: percentages[0], resetInSec: take(resets, 0)},
          weeklyUsage: {usagePercent: percentages[1], resetInSec: take(resets, 1)},
          monthlyUsage: {usagePercent: percentages[2], resetInSec: take(resets, 2)}
        }}
      };
    }
    await wait(500);
  }
  return {kind: 'error', message: 'DOM_TIMEOUT'};
})()
"""


def _safe_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _safe_window(value: object) -> dict[str, int | float | None]:
    if not isinstance(value, dict) or not set(value).issubset(_WINDOW_KEYS):
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    percentage = _safe_number(value.get("usagePercent"))
    if percentage is None or not 0 <= percentage <= 100:
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    reset = value.get("resetInSec")
    if reset is not None:
        reset_number = _safe_number(reset)
        if reset_number is None or reset_number < 0:
            raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
        reset = int(reset_number)
    return {"usagePercent": percentage, "resetInSec": reset}


def parse_opencode_edge_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    if payload.get("kind") == "unauthorized":
        raise OpenCodeEdgeUnauthorizedError
    if payload.get("kind") != "quota":
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    raw = payload.get("raw")
    subscription = raw.get("subscription") if isinstance(raw, dict) else None
    if not isinstance(subscription, dict) or set(subscription) != {
        "rollingUsage",
        "weeklyUsage",
        "monthlyUsage",
    }:
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
    safe_subscription: dict[str, object] = {}
    for key, value in subscription.items():
        safe_subscription[key] = _safe_window(value)
    return {"subscription": safe_subscription}


def _protect_profile(profile: Path) -> None:
    protect_directory(profile, platform="win32")


class ManagedOpenCodeEdgeOperation:
    """Run one visible login or headless refresh against the owned profile."""

    def __init__(
        self,
        workspace_url: str,
        *,
        local_app_data: Path,
        executable: Path | None = None,
        protector: Callable[[Path], None] = _protect_profile,
        process_factory: Callable[[list[str]], _ProcessLike] = _start_process,
        target_loader: Callable[[str], object] = _load_targets,
        socket_factory: Callable[[str], object] = _open_socket,
        process_tree_terminator: Callable[[_ProcessLike], None] = _terminate_process_tree,
        expression_factory: Callable[[], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate_workspace_url(workspace_url)
        self.workspace_url = workspace_url
        self.local_app_data = local_app_data
        self.profile = opencode_edge_profile_path(local_app_data)
        self.executable = executable
        self._protector = protector
        self._process_factory = process_factory
        self._target_loader = target_loader
        self._socket_factory = socket_factory
        self._process_tree_terminator = process_tree_terminator
        self._expression_factory = expression_factory or (
            lambda: opencode_dom_extract_expression(workspace_url)
        )
        self._sleep = sleep
        self._monotonic = monotonic

    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]:
        if cancel.is_set():
            raise OpenCodeEdgeCancelledError
        validate_owned_opencode_profile(self.profile, self.local_app_data)
        try:
            self._protector(self.profile)
            validate_owned_opencode_profile(self.profile, self.local_app_data)
        except OpenCodeEdgeQuotaError:
            raise
        except Exception as error:
            raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED) from error
        self._remove_stale_active_port()
        try:
            executable = self.executable or find_edge_executable()
            spec = build_opencode_edge_launch(
                executable, self.profile, self.workspace_url, visible=visible
            )
            process = self._process_factory([str(spec.executable), *spec.arguments])
        except OpenCodeEdgeQuotaError:
            raise
        except Exception as error:
            raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED) from error

        browser: CdpConnection | None = None
        try:
            endpoint = self._wait_for_endpoint(process, cancel)
            browser = CdpConnection(self._socket_factory(endpoint.browser_websocket))  # type: ignore[arg-type]
            port = urlparse(endpoint.http_origin).port
            if port is None:
                raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
            startup_deadline = self._monotonic() + EDGE_STARTUP_TIMEOUT_SECONDS
            login_deadline = self._monotonic() + EDGE_LOGIN_TIMEOUT_SECONDS
            headless_auth_deadline = self._monotonic() + EDGE_HEADLESS_AUTH_GRACE_SECONDS
            while True:
                if cancel.is_set():
                    raise OpenCodeEdgeCancelledError
                if process.poll() is not None:
                    raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
                try:
                    targets = self._target_loader(endpoint.http_origin)
                    page_url = select_opencode_target(
                        targets,
                        expected_port=port,
                        expected_workspace_url=self.workspace_url,
                    )
                    page = CdpConnection(self._socket_factory(page_url))  # type: ignore[arg-type]
                    try:
                        payload = page.evaluate(self._expression_factory())
                    finally:
                        page.close()
                    return parse_opencode_edge_payload(payload)
                except OpenCodeEdgeUnauthorizedError:
                    if visible:
                        if self._monotonic() >= login_deadline:
                            raise OpenCodeEdgeQuotaError(
                                OpenCodeQuotaErrorCategory.REFRESH_TIMEOUT
                            ) from None
                    elif self._monotonic() >= headless_auth_deadline:
                        raise
                    self._sleep(2.0)
                except OpenCodeEdgeCancelledError:
                    raise
                except OpenCodeEdgeQuotaError:
                    now = self._monotonic()
                    if now >= startup_deadline and (not visible or now >= login_deadline):
                        raise
                    self._sleep(0.1 if now < startup_deadline else 1.0)
                except Exception as error:
                    now = self._monotonic()
                    if now >= startup_deadline and (not visible or now >= login_deadline):
                        raise OpenCodeEdgeQuotaError(
                            OpenCodeQuotaErrorCategory.REFRESH_FAILED
                        ) from error
                    self._sleep(0.1 if now < startup_deadline else 1.0)
        finally:
            if browser is not None:
                with suppress(Exception):
                    browser.close_browser()
                browser.close()
            if not self._shutdown_process(process):
                _logger.error("OpenCode Edge process did not stop cleanly")

    def _wait_for_endpoint(
        self,
        process: _ProcessLike,
        cancel: Event,
    ) -> DevToolsEndpoint:
        deadline = self._monotonic() + EDGE_STARTUP_TIMEOUT_SECONDS
        while self._monotonic() < deadline:
            if cancel.is_set():
                raise OpenCodeEdgeCancelledError
            if process.poll() is not None:
                raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
            try:
                return read_devtools_endpoint(self.profile)
            except Exception:
                self._sleep(0.1)
        raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)

    def _remove_stale_active_port(self) -> None:
        active_port = self.profile / "DevToolsActivePort"
        if not active_port.exists():
            return
        if _is_reparse_point(active_port) or not active_port.is_file():
            raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED)
        try:
            active_port.unlink()
        except OSError as error:
            raise OpenCodeEdgeQuotaError(OpenCodeQuotaErrorCategory.REFRESH_FAILED) from error

    def _shutdown_process(self, process: _ProcessLike) -> bool:
        try:
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
            return True
        except Exception:
            pass
        try:
            self._process_tree_terminator(process)
        except Exception:
            _logger.error("OpenCode Edge process tree termination failed", exc_info=True)
            return False
        try:
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
            return True
        except Exception:
            _logger.error("OpenCode Edge process remained alive after termination", exc_info=True)
            return False
