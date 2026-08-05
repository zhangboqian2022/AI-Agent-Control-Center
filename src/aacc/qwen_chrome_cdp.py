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
# The extraction spans two SPA views (personal windows, then the team hash);
# a cold headless render of the Bailian console needs more than the 15 s the
# single-page Edge login path budgets, and the in-page wait itself can run to
# ~70 s, so the budget must exceed the worst-case single evaluate call.
QWEN_STARTUP_TIMEOUT_SECONDS = 90.0
# The extraction expression waits inside the page until both views render
# (tens of seconds on a cold load). The shared 5 s transport timeout would
# truncate the evaluate call mid-flight and restart it repeatedly, racing
# overlapping in-page waits; give the page socket room to finish in one call.
QWEN_PAGE_SOCKET_TIMEOUT_SECONDS = 90.0
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
    """Promise-based DOM capture evaluated through ``Runtime.evaluate``.

    The token-plan console renders two plans. The personal plan (the launch
    URL) exposes a 5-hour and a 7-day window as ``X%已用`` lines; the team
    plan lives behind the ``enterprise`` hash and renders one total-quota
    gauge. Gauge tick ladders (``0% 50% 90% 100%``) follow every value and
    are excluded later by the Python parser. The personal value marker
    (``%已用``) doubles as the logged-in readiness gate: the skeleton view
    shows the labels with ``-`` placeholders, and the anonymous marketing
    copy never renders it.

    An expired Aliyun session does not redirect away from the workspace
    origin; the console stays on the same URL and renders an inline login
    banner. That banner is classified as ``unauthorized`` so the session
    prompts for a visible re-login instead of looping on DOM timeouts.
    """

    return r"""
(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const FIVE = /^5\s*小时/;
  const SEVEN = /^7\s*天/;
  const USED = /\d{1,3}(?:\.\d+)?\s*%\s*(?:已用|used)/i;
  const LOGGED_OUT = /您当前处于未登录状态|登录以使用/;
  const TEAM_READY = /重置时间\s*\d{4}-/;
  const grab = () => (document.body ? document.body.innerText : '');
  const split = (text) => text.split('\n').map((line) => line.trim()).filter(Boolean);
  const sliceFrom = (arr, idx, stop) => {
    const out = [arr[idx]];
    for (let i = idx + 1; i < arr.length && out.length < 14; i += 1) {
      if (stop.test(arr[i])) break;
      out.push(arr[i]);
    }
    return out.join('\n');
  };

  let text = '';
  for (let attempt = 0; attempt <= 44; attempt += 1) {
    text = grab();
    if (USED.test(text)) break;
    if (LOGGED_OUT.test(text)) return {kind: 'unauthorized'};
    await wait(1000);
  }
  const personalLines = split(text);
  const fiveIdx = personalLines.findIndex((line) => FIVE.test(line));
  const sevenIdx = personalLines.findIndex((line) => SEVEN.test(line));
  const personalFiveHourText = fiveIdx >= 0
    ? sliceFrom(personalLines, fiveIdx, sevenIdx >= 0 ? SEVEN : /额度补充|额外用量包|套餐专属/)
    : null;
  const personalWeeklyText = sevenIdx >= 0
    ? sliceFrom(personalLines, sevenIdx, /额度补充|额外用量包|套餐专属/)
    : null;
  const hasPersonalValue =
    USED.test(personalFiveHourText || '') || USED.test(personalWeeklyText || '');
  if (!hasPersonalValue) {
    if (fiveIdx >= 0 || sevenIdx >= 0) return {kind: 'unauthorized'};
    return {kind: 'error', message: 'DOM_TIMEOUT'};
  }

  let teamTotalText = null;
  try { location.hash = '#/efm/subscription/token-plan/enterprise'; } catch (err) {}
  let teamText = '';
  let clicked = false;
  for (let attempt = 0; attempt <= 24; attempt += 1) {
    teamText = grab();
    if (TEAM_READY.test(teamText)) break;
    if (!clicked && attempt === 5) {
      const els = [...document.querySelectorAll('*')].filter(
        (el) => el.childElementCount === 0 && (el.textContent || '').trim() === '团队版'
      );
      if (els.length > 0) { try { els[0].click(); } catch (err) {} }
      clicked = true;
    }
    await wait(1000);
  }
  const teamLines = split(teamText);
  const totalIdx = teamLines.findIndex((line) => /^总额度/.test(line));
  if (totalIdx >= 0) {
    const candidate = sliceFrom(
      teamLines, totalIdx, /团队座席|座席加购|共享用量包|订阅明细|套餐专属/
    );
    if (/\d{1,3}(?:\.\d+)?\s*%/.test(candidate)) teamTotalText = candidate;
  }
  return {
    kind: 'quota',
    raw: {
      personalFiveHourText: personalFiveHourText,
      personalWeeklyText: personalWeeklyText,
      teamTotalText: teamTotalText,
    },
  };
})()
"""


def _safe_snippet(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > _MAX_SNIPPET_CHARS:
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    return value


_QWEN_PAYLOAD_KEYS = {"personalFiveHourText", "personalWeeklyText", "teamTotalText"}


def parse_qwen_chrome_payload(payload: object) -> dict[str, object]:
    """Reduce the untrusted page result to the rendered plan text snippets."""

    if not isinstance(payload, dict):
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    if payload.get("kind") == "unauthorized":
        raise QwenChromeUnauthorizedError
    if payload.get("kind") != "quota":
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    raw = payload.get("raw")
    if not isinstance(raw, dict) or set(raw) != _QWEN_PAYLOAD_KEYS:
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    five_hour_text = _safe_snippet(raw.get("personalFiveHourText"))
    weekly_text = _safe_snippet(raw.get("personalWeeklyText"))
    team_text = _safe_snippet(raw.get("teamTotalText"))
    if five_hour_text is None and weekly_text is None and team_text is None:
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    return {
        "personalFiveHourText": five_hour_text,
        "personalWeeklyText": weekly_text,
        "teamTotalText": team_text,
    }


def _protect_profile(profile: Path) -> None:
    protect_directory(profile)


def _open_qwen_page_socket(url: str) -> object:
    return _open_socket(url, timeout=QWEN_PAGE_SOCKET_TIMEOUT_SECONDS)


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
        socket_factory: Callable[[str], object] = _open_qwen_page_socket,
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
            startup_deadline = self._monotonic() + QWEN_STARTUP_TIMEOUT_SECONDS
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
        deadline = self._monotonic() + QWEN_STARTUP_TIMEOUT_SECONDS
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
