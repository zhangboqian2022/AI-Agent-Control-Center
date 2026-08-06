"""Isolated Google Chrome/CDP primitives for Qwen Code token-plan quota.

The Aliyun Bailian login (password / QR / RAM) is a multi-origin flow with
new-window requests that the lightweight native web view cannot navigate.
Following the Windows Edge-CDP paradigm, this module drives a real Google
Chrome instance through the DevTools Protocol: one visible window for the
initial login, and background refreshes inside a headed-but-hidden window.
Aliyun's risk control fingerprints headless browsers and voids session
tickets presented by them, so refreshes launch a real headed Chrome binary
directly with ``--no-startup-window`` and open the refresh page in a
background window through ``Target.createTarget``, pushing it off-screen
through CDP instead of passing ``--headless``. Bypassing LaunchServices
(the ``open`` command) keeps the instance out of the Dock's recent items
and avoids the second-instance Dock tile that ``open -n`` creates, so a
finished refresh leaves neither an icon nor a process behind. Cookies stay
in an AACC-owned Chrome profile directory; AACC never sees the account
password.

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
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import psutil

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
# a cold hidden render of the Bailian console needs more than the 15 s the
# single-page Edge login path budgets, and the in-page wait itself can run to
# ~70 s, so the budget must exceed the worst-case single evaluate call.
QWEN_STARTUP_TIMEOUT_SECONDS = 90.0
# The extraction expression waits inside the page until both views render
# (tens of seconds on a cold load). The shared 5 s transport timeout would
# truncate the evaluate call mid-flight and restart it repeatedly, racing
# overlapping in-page waits; give the page socket room to finish in one call.
QWEN_PAGE_SOCKET_TIMEOUT_SECONDS = 90.0
# The hidden refresh reuses the bounded auth grace window defined for the
# Kimi Edge headless path; the shared constant name belongs to that module,
# so alias it locally instead of renaming across modules.
_QWEN_REFRESH_AUTH_GRACE_SECONDS = EDGE_HEADLESS_AUTH_GRACE_SECONDS
_QUARANTINE_PREFIX = f".{QWEN_CHROME_PROFILE_NAME}.logout-"
_QWEN_RECOPY_QUARANTINE_PREFIX = f".{QWEN_CHROME_PROFILE_NAME}.pre-dailycopy-"
_QWEN_RECOPY_KEEP = 3
_PAGE_PATH = re.compile(r"^/devtools/page/[A-Za-z0-9_-]+$")
_MAX_SNIPPET_CHARS = 20_000
_QWEN_HIDDEN_WINDOW_OFFSET = -32000
_QWEN_HIDDEN_WINDOW_WIDTH = 1100
_QWEN_HIDDEN_WINDOW_HEIGHT = 700
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


def daily_chrome_session_source(
    *, platform_name: str | None = None, home: Path | None = None
) -> Path | None:
    """Locate the daily Chrome user-data root when its Default profile exists.

    Only the default profile location is considered: the automatic recopy
    must never guess at foreign browser profiles.
    """

    resolved_platform = sys.platform if platform_name is None else platform_name
    resolved_home = Path.home() if home is None else home
    if resolved_platform != "darwin":
        return None
    root = resolved_home / "Library" / "Application Support" / "Google" / "Chrome"
    if _is_reparse_point(root) or not (root / "Default").is_dir():
        return None
    return root


def _backup_sqlite_database(source: Path, destination: Path) -> None:
    """Online-backup a live database (the daily Chrome holds the lock)."""

    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(destination)
        try:
            with target:
                connection.backup(target)
        finally:
            target.close()
    finally:
        connection.close()


def _prune_qwen_recopy_quarantines(config_dir: Path) -> None:
    quarantines = sorted(
        (
            entry
            for entry in config_dir.iterdir()
            if entry.name.startswith(_QWEN_RECOPY_QUARANTINE_PREFIX)
            and entry.is_dir()
            and not _is_reparse_point(entry)
        ),
        key=lambda entry: entry.name,
    )
    for entry in quarantines[:-_QWEN_RECOPY_KEEP]:
        shutil.rmtree(entry, ignore_errors=True)


def recopy_qwen_daily_chrome_session(
    config_dir: Path,
    *,
    source_root: Path | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
) -> None:
    """Rebuild the managed profile from the daily Chrome session subset.

    Aliyun expires copied sessions server-side after roughly five and a half
    hours while the daily browser keeps a live session through real use, so
    re-copying the minimal session set restores hidden refreshes without any
    login. Login Data (saved passwords) is never copied.
    """

    root = (
        source_root
        if source_root is not None
        else daily_chrome_session_source(platform_name=platform_name, home=home)
    )
    if root is None or _is_reparse_point(root):
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    source_default = root / "Default"
    required = (
        root / "Local State",
        source_default / "Cookies",
        source_default / "Preferences",
        source_default / "Secure Preferences",
    )
    if not source_default.is_dir() or any(
        _is_reparse_point(path) or not path.is_file() for path in required
    ):
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    profile = qwen_chrome_profile_path(config_dir)
    validate_owned_qwen_chrome_profile(profile, config_dir)
    terminate_qwen_chrome_profile_processes(profile)
    try:
        if profile.exists():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            os.replace(profile, config_dir / f"{_QWEN_RECOPY_QUARANTINE_PREFIX}{timestamp}")
        _prune_qwen_recopy_quarantines(config_dir)
        profile.mkdir(parents=True)
        default = profile / "Default"
        default.mkdir()
        shutil.copy2(root / "Local State", profile / "Local State")
        _backup_sqlite_database(source_default / "Cookies", default / "Cookies")
        for name in ("Preferences", "Secure Preferences"):
            shutil.copy2(source_default / name, default / name)
        for name in ("Local Storage", "Session Storage"):
            storage = source_default / name
            if storage.is_dir() and not _is_reparse_point(storage):
                shutil.copytree(storage, default / name)
        protect_directory(profile)
    except QwenChromeQuotaError:
        raise
    except Exception as error:
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
    platform_name: str | None = None,
) -> QwenChromeLaunchSpec:
    """Build the launch specification for one owned Chrome run.

    Hidden refreshes need a real headed browser (Aliyun's risk control voids
    tickets presented by headless fingerprints). The hidden path execs the
    Chrome binary directly with ``--no-startup-window`` — bypassing
    LaunchServices keeps the instance out of the Dock's recent items and
    avoids the second-instance Dock tile ``open -n`` would create — and the
    quota page is opened later in a background window through CDP, so the
    launch URL is deliberately absent from the arguments. Hidden mode is
    macOS-only and fails closed elsewhere instead of falling back to
    headless.
    """

    _validate_workspace_url(workspace_url)
    if visible:
        return QwenChromeLaunchSpec(
            executable=executable,
            profile=profile,
            arguments=(
                f"--user-data-dir={profile}",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "--no-first-run",
                "--no-default-browser-check",
                workspace_url,
            ),
        )
    resolved_platform = sys.platform if platform_name is None else platform_name
    if resolved_platform != "darwin":
        raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
    return QwenChromeLaunchSpec(
        executable=executable,
        profile=profile,
        arguments=(
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            # The copied daily profile's Preferences would otherwise make
            # Chrome re-download the user's full extension set (~241 MB) into
            # the hidden instance.
            "--disable-extensions",
            # Keep setTimeout at full speed while the window sits occluded
            # off-screen (A/B verified on Chrome 150).
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            # Start windowless: the refresh page is created through CDP
            # Target.createTarget in a background window, so the instance
            # never activates, never steals focus, and exits cleanly through
            # Browser.close (A/B verified on Chrome 151).
            "--no-startup-window",
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


def qwen_hidden_page_stealth_script() -> str:
    """Fingerprint masking injected before the first baxia read of the page.

    A CDP-attached Chrome reports ``navigator.webdriver`` as true; the
    off-screen window also exposes negative screen coordinates. Both are
    strong automation signals, so mask them before any page script runs.
    """

    return r"""
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
  } catch (err) {}
  const maskCoordinate = (prop) => {
    try {
      const descriptor = Object.getOwnPropertyDescriptor(window, prop)
        || Object.getOwnPropertyDescriptor(Window.prototype, prop);
      if (!descriptor || typeof descriptor.get !== 'function') return;
      const original = descriptor.get;
      Object.defineProperty(window, prop, {
        configurable: true,
        get() {
          const value = original.call(this);
          return typeof value === 'number' && value < 0 ? 0 : value;
        },
      });
    } catch (err) {}
  };
  ['screenX', 'screenY', 'screenLeft', 'screenTop'].forEach(maskCoordinate);
})();
"""


def install_qwen_hidden_page_stealth(page: CdpConnection) -> None:
    """Mask automation fingerprints and push the refresh window off-screen.

    Stealth is an optimization: sessions can still succeed without it, so any
    CDP failure is swallowed and the caller continues on the plain path.
    ``Page.addScriptToEvaluateOnNewDocument`` only applies to documents
    created after registration, so the page is reloaded before extraction to
    make the very first fingerprint read see the masked values.
    """

    try:
        page.send_command("Page.enable", {})
        page.send_command(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": qwen_hidden_page_stealth_script()},
        )
        # The Bailian console serves a mobile interstitial below desktop
        # viewport widths; pin desktop metrics so the reloaded document
        # renders the quota view even though the window is a thin strip.
        page.send_command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1100, "height": 700, "deviceScaleFactor": 1, "mobile": False},
        )
        page.send_command("Page.reload", {})
        response = page.send_command("Browser.getWindowForTarget", {})
        result = response.get("result")
        window_id = result.get("windowId") if isinstance(result, dict) else None
        if window_id is None:
            return
        page.send_command(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": _QWEN_HIDDEN_WINDOW_OFFSET,
                    "top": _QWEN_HIDDEN_WINDOW_OFFSET,
                    # The CDP-created background window starts at Chrome's
                    # default size; the Bailian console serves a mobile
                    # interstitial below desktop viewport widths, so size it
                    # explicitly alongside the device-metrics override.
                    "width": _QWEN_HIDDEN_WINDOW_WIDTH,
                    "height": _QWEN_HIDDEN_WINDOW_HEIGHT,
                },
            },
        )
    except Exception:
        _logger.warning("Qwen hidden-page stealth installation failed", exc_info=True)


def _find_qwen_chrome_processes_for_profile(
    profile: Path,
    *,
    process_iter: Callable[[tuple[str, ...]], Iterable[Any]] = psutil.process_iter,
) -> list[Any]:
    """Locate Chrome processes bound to the exact AACC-owned profile.

    Matching requires an exact ``--user-data-dir=<profile>`` argv element;
    substring matching could reach into foreign profiles. The name filter
    drops the ``open`` launcher, whose argv carries the same flags after
    ``--args``. Per-process read failures skip the process and overall
    failure yields no matches (fail closed: never kill what was not proven).
    """

    flag = f"--user-data-dir={profile}"
    matches: list[Any] = []
    try:
        for process in process_iter(("name", "cmdline")):
            try:
                info = process.info
                name = info.get("name")
                cmdline = info.get("cmdline")
            except (AttributeError, KeyError, psutil.Error, OSError):
                continue
            if not isinstance(name, str) or "chrome" not in name.casefold():
                continue
            if not isinstance(cmdline, (list, tuple)) or flag not in cmdline:
                continue
            matches.append(process)
    except (psutil.Error, OSError):
        return []
    return matches


def terminate_qwen_chrome_profile_processes(
    profile: Path,
    *,
    process_finder: Callable[[Path], Iterable[Any]] = _find_qwen_chrome_processes_for_profile,
    process_waiter: Callable[
        [Sequence[Any], float], tuple[Iterable[Any], Iterable[Any]]
    ] = psutil.wait_procs,
) -> None:
    """Terminate every Chrome process bound to the owned profile.

    Chrome helpers inherit ``--user-data-dir`` in their argv, so the finder
    already returns the whole tree; terminate all matches, then escalate
    survivors to kill.
    """

    processes = list(process_finder(profile))
    for process in processes:
        with suppress(Exception):
            process.terminate()
    try:
        _gone, alive = process_waiter(processes, 2.0)
    except (psutil.Error, OSError):
        alive = processes
    for process in alive:
        with suppress(Exception):
            process.kill()


class ManagedQwenChromeOperation:
    """Run one visible login or hidden headed refresh against the owned profile."""

    def __init__(
        self,
        workspace_url: str,
        *,
        config_dir: Path,
        executable: Path | None = None,
        platform_name: str | None = None,
        protector: Callable[[Path], None] = _protect_profile,
        process_factory: Callable[[list[str]], _ProcessLike] = _start_process,
        target_loader: Callable[[str], object] = _load_targets,
        socket_factory: Callable[[str], object] = _open_qwen_page_socket,
        process_tree_terminator: Callable[[_ProcessLike], None] = _terminate_process_tree,
        chrome_process_finder: Callable[[Path], Iterable[Any]] | None = None,
        expression_factory: Callable[[], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        session_recopy: Callable[[Path], None] | None = None,
    ) -> None:
        _validate_workspace_url(workspace_url)
        self.workspace_url = workspace_url
        self.config_dir = config_dir
        self.profile = qwen_chrome_profile_path(config_dir)
        self.executable = executable
        self._platform_name = sys.platform if platform_name is None else platform_name
        self._protector = protector
        self._process_factory = process_factory
        self._target_loader = target_loader
        self._socket_factory = socket_factory
        self._process_tree_terminator = process_tree_terminator
        self._chrome_process_finder = (
            chrome_process_finder
            if chrome_process_finder is not None
            else _find_qwen_chrome_processes_for_profile
        )
        self._expression_factory = expression_factory or qwen_dom_extract_expression
        self._sleep = sleep
        self._monotonic = monotonic
        self._session_recopy = session_recopy

    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]:
        """Run one operation, transparently recopying an expired session.

        Aliyun expires copied sessions server-side (~5.5 h), after which the
        console renders an inline login banner. When a recopy callback is
        configured, a hidden refresh that hits that banner rebuilds the
        profile from the daily Chrome session and retries once instead of
        surfacing the logout; the retry keeps the full grace behaviour.
        """

        if cancel.is_set():
            raise QwenChromeCancelledError
        fail_fast_unauthorized = not visible and self._session_recopy is not None
        try:
            return self._run_once(
                visible=visible,
                cancel=cancel,
                fail_fast_unauthorized=fail_fast_unauthorized,
            )
        except QwenChromeUnauthorizedError:
            if visible or self._session_recopy is None:
                raise
            _logger.warning(
                "Qwen hidden refresh found an expired session; "
                "recopying the daily Chrome session before retrying"
            )
            try:
                self._session_recopy(self.config_dir)
            except Exception:
                _logger.warning("Qwen daily Chrome session recopy failed", exc_info=True)
                # Surface the original logout: the retry never happened, so
                # the session layer must prompt for a visible re-login.
                raise QwenChromeUnauthorizedError from None
            return self._run_once(visible=visible, cancel=cancel, fail_fast_unauthorized=False)

    def _run_once(
        self, *, visible: bool, cancel: Event, fail_fast_unauthorized: bool
    ) -> dict[str, object]:
        validate_owned_qwen_chrome_profile(self.profile, self.config_dir)
        try:
            self._protector(self.profile)
            validate_owned_qwen_chrome_profile(self.profile, self.config_dir)
        except QwenChromeQuotaError:
            raise
        except Exception as error:
            raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from error
        self._remove_stale_active_port()
        self._cleanup_lingering_profile_processes()
        try:
            executable = self.executable or find_qwen_chrome_executable()
            spec = build_qwen_chrome_launch(
                executable,
                self.profile,
                self.workspace_url,
                visible=visible,
                platform_name=self._platform_name,
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
            refresh_auth_deadline = self._monotonic() + _QWEN_REFRESH_AUTH_GRACE_SECONDS
            target_requested = visible
            while True:
                if cancel.is_set():
                    raise QwenChromeCancelledError
                if process.poll() is not None:
                    raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED)
                try:
                    if not target_requested:
                        # The hidden launch starts windowless; open the quota
                        # page in a background window so the instance never
                        # activates (verified on Chrome 151: no focus steal,
                        # no Dock tile, clean Browser.close exit). Requested
                        # once per run; a failed send leaves the flag clear
                        # and the retry below issues it again.
                        browser.send_command(
                            "Target.createTarget",
                            {
                                "url": self.workspace_url,
                                "newWindow": True,
                                "background": True,
                            },
                        )
                        target_requested = True
                    targets = self._target_loader(endpoint.http_origin)
                    page_url = select_qwen_target(targets, expected_port=port)
                    page = CdpConnection(self._socket_factory(page_url))  # type: ignore[arg-type]
                    try:
                        if not visible:
                            install_qwen_hidden_page_stealth(page)
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
                    elif fail_fast_unauthorized or self._monotonic() >= refresh_auth_deadline:
                        # Fail fast hands the expired session to the outer
                        # recopy path without burning the grace window on a
                        # banner that cannot change by itself.
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

    def _cleanup_lingering_profile_processes(self) -> None:
        """Kill zombie Chrome instances holding the profile before a new launch.

        Fire-and-forget: a leftover instance from an earlier interrupted run
        would otherwise lock the profile and the failure would only surface
        as an endpoint timeout.
        """

        try:
            terminate_qwen_chrome_profile_processes(
                self.profile, process_finder=self._chrome_process_finder
            )
        except Exception:
            _logger.warning("Qwen Chrome pre-launch cleanup failed", exc_info=True)

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
