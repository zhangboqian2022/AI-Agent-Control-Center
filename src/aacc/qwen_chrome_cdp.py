"""Isolated Google Chrome/CDP primitives for Qwen Code token-plan quota.

The Aliyun Bailian login (password / QR / RAM) is a multi-origin flow with
new-window requests that the lightweight native web view cannot navigate.
Following the Windows Edge-CDP paradigm, this module drives a real Google
Chrome instance through the DevTools Protocol: one visible window for the
initial login, headless runs for the 5-minute refreshes. Cookies stay in an
AACC-owned Chrome profile directory; AACC never sees the account password.

Chrome ships its own debugging endpoint files and accepts CDP commands the
same way Edge does, so the transport primitives are reused from
``kimi_edge_cdp`` and only the browser discovery, profile ownership and the
token-plan extraction contract are Qwen-specific here.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Sequence
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
    read_devtools_endpoint,
)
from aacc.qwen_web_error import QwenQuotaErrorCategory

QWEN_CHROME_PROFILE_NAME = "qwen-chrome-profile"
_QUARANTINE_PREFIX = f".{QWEN_CHROME_PROFILE_NAME}.logout-"
_PAGE_PATH = re.compile(r"^/devtools/page/[A-Za-z0-9_-]+$")
_MAX_SNIPPET_CHARS = 20_000
_logger = logging.getLogger("aacc.qwen_chrome_cdp")


class QwenChromeQuotaError(RuntimeError):
    """Sanitized Qwen Chrome failure."""

    def __init__(self, category: QwenQuotaErrorCategory) -> None:
        self.category = category
        super().__init__(category.value)


class QwenChromeUnauthorizedError(RuntimeError):
    """The owned Qwen Chrome session is not signed in to Bailian."""


class QwenChromeCancelledError(RuntimeError):
    """The owning Qt session cancelled the Chrome operation."""


class QwenChromeMissingError(RuntimeError):
    """Google Chrome is not installed; fall back to the native web view."""


@dataclass(frozen=True)
class QwenChromeLaunchSpec:
    executable: Path
    arguments: tuple[str, ...]
    profile: Path


def qwen_chrome_profile_path(config_dir: Path) -> Path:
    return config_dir / QWEN_CHROME_PROFILE_NAME


def _default_chrome_candidates(platform_name: str, home: Path) -> tuple[Path, ...]:
    if platform_name == "darwin":
        return (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        )
    return ()


def find_qwen_chrome_executable(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    candidates: Sequence[Path] | None = None,
) -> Path:
    """Locate an installed Chrome binary without invoking a shell or PATH."""

    resolved_platform = sys.platform if platform_name is None else platform_name
    resolved_home = Path.home() if home is None else home
    resolved_candidates = (
        tuple(candidates)
        if candidates is not None
        else _default_chrome_candidates(resolved_platform, resolved_home)
    )
    for candidate in resolved_candidates:
        if not candidate.is_file() or _is_reparse_point(candidate):
            continue
        return candidate
    raise QwenChromeMissingError()


def validate_owned_qwen_chrome_profile(profile: Path, config_dir: Path) -> None:
    """Reject a profile that is not the exact AACC-owned path."""

    expected = qwen_chrome_profile_path(config_dir)
    if profile != expected or _is_reparse_point(profile):
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    if _is_reparse_point(config_dir) or _is_reparse_point(expected.parent):
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    if profile.exists() and not profile.is_dir():
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)


def clear_owned_qwen_chrome_profile(profile: Path, config_dir: Path) -> None:
    """Remove only AACC's exact Chrome profile, never a user browser profile."""

    validate_owned_qwen_chrome_profile(profile, config_dir)
    if not profile.parent.exists():
        return
    try:
        if profile.exists():
            quarantine = profile.parent / f"{_QUARANTINE_PREFIX}{uuid4().hex}"
            os.replace(profile, quarantine)
        for candidate in profile.parent.iterdir():
            if not candidate.name.startswith(_QUARANTINE_PREFIX):
                continue
            if _is_reparse_point(candidate) or not candidate.is_dir():
                raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
            shutil.rmtree(candidate)
    except QwenChromeQuotaError:
        raise
    except OSError as error:
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from error


def _validate_workspace_url(workspace_url: str) -> None:
    parsed = urlparse(workspace_url)
    if parsed.scheme != "https" or parsed.netloc != "bailian.console.aliyun.com":
        raise ValueError("invalid Qwen workspace URL")


def build_qwen_chrome_launch(
    executable: Path,
    profile: Path,
    workspace_url: str,
    *,
    visible: bool,
) -> QwenChromeLaunchSpec:
    _validate_workspace_url(workspace_url)
    mode_arguments: tuple[str, ...] = () if visible else ("--headless=new", "--disable-gpu")
    return QwenChromeLaunchSpec(
        executable=executable,
        profile=profile,
        arguments=(
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            *mode_arguments,
            workspace_url,
        ),
    )


def select_qwen_target(targets: object, *, expected_port: int) -> str:
    """Select the Bailian page while rejecting externally supplied CDP URLs."""

    if not isinstance(targets, list):
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    for candidate in targets:
        if not isinstance(candidate, dict):
            continue
        page_url = candidate.get("url")
        websocket_url = candidate.get("webSocketDebuggerUrl")
        parsed_page = urlparse(page_url) if isinstance(page_url, str) else None
        if candidate.get("type") != "page" or parsed_page is None:
            continue
        if parsed_page.scheme != "https" or parsed_page.netloc != "bailian.console.aliyun.com":
            continue
        if not isinstance(websocket_url, str):
            raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
        parsed_socket = urlparse(websocket_url)
        try:
            socket_port = parsed_socket.port
        except ValueError as error:
            raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from error
        if (
            parsed_socket.scheme == "ws"
            and parsed_socket.hostname == "127.0.0.1"
            and socket_port == expected_port
            and _PAGE_PATH.fullmatch(parsed_socket.path) is not None
            and not parsed_socket.query
            and not parsed_socket.fragment
        ):
            return websocket_url
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    raise QwenChromeUnauthorizedError


def qwen_dom_extract_expression() -> str:
    """Promise-based DOM capture evaluated through ``Runtime.evaluate``."""

    return r"""
(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const FIVE = /5\s*小时|5\s*h|5h/i;
  const SEVEN = /7\s*天|7\s*d|7d/i;
  const PCT = /(\d{1,3}(?:\.\d+)?)\s*%/;
  const sliceWindow = (lines, idx, stop) => {
    const out = [lines[idx]];
    for (let i = idx + 1; i < lines.length && out.length < 12; i += 1) {
      if (stop.test(lines[i])) break;
      out.push(lines[i]);
    }
    return out.join('\n');
  };
  for (let attempt = 0; attempt <= 11; attempt += 1) {
    const text = document.body ? document.body.innerText : '';
    if (text) {
      const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
      const fiveIdx = lines.findIndex((line) => FIVE.test(line));
      const sevenIdx = lines.findIndex((line) => SEVEN.test(line));
      if (fiveIdx >= 0 || sevenIdx >= 0) {
        const fiveText = fiveIdx >= 0 ? sliceWindow(lines, fiveIdx, SEVEN) : null;
        const weeklyText = sevenIdx >= 0 ? sliceWindow(lines, sevenIdx, FIVE) : null;
        if (PCT.test(fiveText || '') || PCT.test(weeklyText || '')) {
          return {
            kind: 'quota',
            raw: {fiveHourText: fiveText, weeklyText: weeklyText},
          };
        }
        return {kind: 'unauthorized'};
      }
    }
    await wait(1000);
  }
  return {kind: 'error', message: 'DOM_TIMEOUT'};
})()
"""


def _safe_snippet(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > _MAX_SNIPPET_CHARS:
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    return value


def parse_qwen_chrome_payload(payload: object) -> dict[str, object]:
    """Reduce the untrusted page result to the two rendered text snippets."""

    if not isinstance(payload, dict):
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    if payload.get("kind") == "unauthorized":
        raise QwenChromeUnauthorizedError
    if payload.get("kind") != "quota":
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    raw = payload.get("raw")
    if not isinstance(raw, dict) or set(raw) != {"fiveHourText", "weeklyText"}:
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    five_hour_text = _safe_snippet(raw.get("fiveHourText"))
    weekly_text = _safe_snippet(raw.get("weeklyText"))
    if five_hour_text is None and weekly_text is None:
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    return {"fiveHourText": five_hour_text, "weeklyText": weekly_text}


def _protect_profile(profile: Path) -> None:
    protect_directory(profile)


class ManagedQwenChromeOperation:
    """Run one visible login or headless refresh against the owned profile."""

    def __init__(
        self,
        workspace_url: str,
        *,
        config_dir: Path,
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
        self.config_dir = config_dir
        self.profile = qwen_chrome_profile_path(config_dir)
        self.executable = executable
        self._protector = protector
        self._process_factory = process_factory
        self._target_loader = target_loader
        self._socket_factory = socket_factory
        self._process_tree_terminator = process_tree_terminator
        self._expression_factory = expression_factory or qwen_dom_extract_expression
        self._sleep = sleep
        self._monotonic = monotonic

    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]:
        if cancel.is_set():
            raise QwenChromeCancelledError
        validate_owned_qwen_chrome_profile(self.profile, self.config_dir)
        try:
            self._protector(self.profile)
            validate_owned_qwen_chrome_profile(self.profile, self.config_dir)
        except QwenChromeQuotaError:
            raise
        except Exception as error:
            raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from error
        self._remove_stale_active_port()
        try:
            executable = self.executable or find_qwen_chrome_executable()
            spec = build_qwen_chrome_launch(
                executable, self.profile, self.workspace_url, visible=visible
            )
            process = self._process_factory([str(spec.executable), *spec.arguments])
        except (QwenChromeMissingError, QwenChromeQuotaError):
            raise
        except Exception as error:
            raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from error

        browser: CdpConnection | None = None
        try:
            endpoint = self._wait_for_endpoint(process, cancel)
            browser = CdpConnection(self._socket_factory(endpoint.browser_websocket))  # type: ignore[arg-type]
            port = urlparse(endpoint.http_origin).port
            if port is None:
                raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
            startup_deadline = self._monotonic() + EDGE_STARTUP_TIMEOUT_SECONDS
            login_deadline = self._monotonic() + EDGE_LOGIN_TIMEOUT_SECONDS
            headless_auth_deadline = self._monotonic() + EDGE_HEADLESS_AUTH_GRACE_SECONDS
            while True:
                if cancel.is_set():
                    raise QwenChromeCancelledError
                if process.poll() is not None:
                    raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
                try:
                    targets = self._target_loader(endpoint.http_origin)
                    page_url = select_qwen_target(targets, expected_port=port)
                    page = CdpConnection(self._socket_factory(page_url))  # type: ignore[arg-type]
                    try:
                        payload = page.evaluate(self._expression_factory())
                    finally:
                        page.close()
                    return parse_qwen_chrome_payload(payload)
                except QwenChromeUnauthorizedError:
                    if visible:
                        if self._monotonic() >= login_deadline:
                            raise QwenChromeQuotaError(
                                QwenQuotaErrorCategory.REFRESH_TIMEOUT
                            ) from None
                    elif self._monotonic() >= headless_auth_deadline:
                        raise
                    self._sleep(2.0)
                except QwenChromeCancelledError:
                    raise
                except QwenChromeQuotaError:
                    now = self._monotonic()
                    if now >= startup_deadline and (not visible or now >= login_deadline):
                        raise
                    self._sleep(0.1 if now < startup_deadline else 1.0)
                except Exception as error:
                    now = self._monotonic()
                    if now >= startup_deadline and (not visible or now >= login_deadline):
                        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from error
                    self._sleep(0.1 if now < startup_deadline else 1.0)
        finally:
            if browser is not None:
                with suppress(Exception):
                    browser.close_browser()
                browser.close()
            if not self._shutdown_process(process):
                _logger.error("Qwen Chrome process did not stop cleanly")

    def _wait_for_endpoint(
        self,
        process: _ProcessLike,
        cancel: Event,
    ) -> DevToolsEndpoint:
        deadline = self._monotonic() + EDGE_STARTUP_TIMEOUT_SECONDS
        while self._monotonic() < deadline:
            if cancel.is_set():
                raise QwenChromeCancelledError
            if process.poll() is not None:
                raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
            try:
                return read_devtools_endpoint(self.profile)
            except Exception:
                self._sleep(0.1)
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)

    def _remove_stale_active_port(self) -> None:
        active_port = self.profile / "DevToolsActivePort"
        if not active_port.exists():
            return
        if _is_reparse_point(active_port) or not active_port.is_file():
            raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
        try:
            active_port.unlink()
        except OSError as error:
            raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from error

    def _shutdown_process(self, process: _ProcessLike) -> bool:
        try:
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
            return True
        except Exception:
            pass
        try:
            self._process_tree_terminator(process)
        except Exception:
            _logger.error("Qwen Chrome process tree termination failed", exc_info=True)
            return False
        try:
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
            return True
        except Exception:
            _logger.error("Qwen Chrome process remained alive after termination", exc_info=True)
            return False
