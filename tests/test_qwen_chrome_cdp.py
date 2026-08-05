import json
import re
from pathlib import Path
from threading import Event

import psutil
import pytest

from aacc.qwen_chrome_cdp import (
    ManagedQwenChromeOperation,
    QwenChromeCancelledError,
    QwenChromeMissingError,
    QwenChromeQuotaError,
    QwenChromeUnauthorizedError,
    _DetachedQwenChromeHandle,
    _find_qwen_chrome_processes_for_profile,
    build_qwen_chrome_launch,
    clear_owned_qwen_chrome_profile,
    find_qwen_chrome_executable,
    install_qwen_hidden_page_stealth,
    parse_qwen_chrome_payload,
    qwen_chrome_profile_path,
    qwen_dom_extract_expression,
    qwen_hidden_page_stealth_script,
    select_qwen_target,
    terminate_qwen_chrome_profile_processes,
    validate_owned_qwen_chrome_profile,
)

WORKSPACE_URL = (
    "https://bailian.console.aliyun.com/cn-beijing?tab=plan#/efm/subscription/token-plan/personal"
)


def test_profile_path_is_aacc_owned(tmp_path: Path) -> None:
    assert qwen_chrome_profile_path(tmp_path) == tmp_path / "qwen-chrome-profile"


def test_find_chrome_executable_prefers_installed_candidate(tmp_path: Path) -> None:
    chrome = tmp_path / "Google Chrome"
    chrome.write_text("binary", encoding="utf-8")
    missing = tmp_path / "missing"
    found = find_qwen_chrome_executable(candidates=(missing, chrome))
    assert found == chrome


def test_find_chrome_executable_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(QwenChromeMissingError):
        find_qwen_chrome_executable(candidates=(tmp_path / "missing",))


def test_find_chrome_executable_default_darwin_candidates(tmp_path: Path) -> None:
    from aacc.qwen_chrome_cdp import _default_chrome_candidates

    home = tmp_path / "home"
    candidates = _default_chrome_candidates("darwin", home)
    assert Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") in candidates
    assert (
        home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    ) in candidates
    assert _default_chrome_candidates("win32", home) == ()


def test_find_chrome_executable_no_candidates_on_windows() -> None:
    with pytest.raises(QwenChromeMissingError):
        find_qwen_chrome_executable(platform_name="win32")


def test_hidden_launch_spec_wraps_open_and_drops_headless(tmp_path: Path) -> None:
    # Aliyun's baxia risk control voids session tickets shown by headless
    # browsers, so the hidden refresh must launch a real headed Chrome via
    # `open -g -n` (no focus steal) instead of --headless=new.
    spec = build_qwen_chrome_launch(
        Path("chrome"), tmp_path, WORKSPACE_URL, visible=False, platform_name="darwin"
    )

    assert spec.executable == Path("/usr/bin/open")
    assert spec.arguments[:5] == ("-g", "-n", "-b", "com.google.Chrome", "--args")
    chrome_flags = spec.arguments[5:]
    assert "--headless=new" not in chrome_flags
    assert "--disable-gpu" not in chrome_flags
    assert f"--user-data-dir={tmp_path}" in chrome_flags
    assert "--remote-debugging-address=127.0.0.1" in chrome_flags
    assert "--remote-debugging-port=0" in chrome_flags
    assert "--disable-extensions" in chrome_flags
    assert "--disable-background-timer-throttling" in chrome_flags
    assert "--disable-renderer-backgrounding" in chrome_flags
    assert "--disable-backgrounding-occluded-windows" in chrome_flags
    assert "--window-position=0,0" in chrome_flags
    assert "--window-size=1100,700" in chrome_flags
    assert chrome_flags[-1] == WORKSPACE_URL


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_hidden_launch_spec_rejected_off_darwin(tmp_path: Path, platform_name: str) -> None:
    with pytest.raises(QwenChromeQuotaError):
        build_qwen_chrome_launch(
            Path("chrome"), tmp_path, WORKSPACE_URL, visible=False, platform_name=platform_name
        )


def test_visible_launch_spec_keeps_direct_executable(tmp_path: Path) -> None:
    spec = build_qwen_chrome_launch(
        Path("chrome"), tmp_path, WORKSPACE_URL, visible=True, platform_name="darwin"
    )

    assert spec.executable == Path("chrome")
    assert "--headless=new" not in spec.arguments
    assert f"--user-data-dir={tmp_path}" in spec.arguments
    assert WORKSPACE_URL in spec.arguments


def test_launch_spec_rejects_non_bailian_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_qwen_chrome_launch(Path("chrome"), tmp_path, "https://example.com/", visible=False)


def test_dom_expression_extracts_only_allowed_quota_shape() -> None:
    expression = qwen_dom_extract_expression()

    assert "document.body" in expression
    assert "personalFiveHourText" in expression
    assert "personalWeeklyText" in expression
    assert "teamTotalText" in expression
    assert "token-plan/enterprise" in expression
    assert "kind: 'unauthorized'" in expression
    assert "document.cookie" not in expression


def test_dom_expression_flags_logged_out_console_as_unauthorized() -> None:
    # The logged-out Bailian console stays on the workspace origin and renders
    # an inline login banner instead of redirecting, so target selection cannot
    # see it. The wait loop must classify the banner as unauthorized; otherwise
    # every refresh burns the full startup budget on DOM_TIMEOUT retries while
    # the GUI keeps showing the stale last-known quota ("数据过期") with no way
    # back into the login flow.
    expression = qwen_dom_extract_expression()

    marker = re.search(r"LOGGED_OUT = /(.+)/;", expression)
    assert marker is not None
    logged_out = re.compile(marker.group(1))

    observed_logged_out_text = (
        "登录\n概览\n我的订阅\n登录以使用\n您当前处于未登录状态，登录后可使用完整服务\n立即登录\n"
    )
    assert logged_out.search(observed_logged_out_text) is not None

    observed_logged_in_text = (
        "5小时限额\n0.04%已用\n将于 2026-08-05 18:23:45 重置刷新\n"
        "0%\n50%\n90%\n100%\n7天限额\n65%已用\n"
    )
    assert logged_out.search(observed_logged_in_text) is None


def test_page_socket_uses_extended_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # The extraction expression waits for the SPA to render (tens of seconds);
    # the 5 s transport default truncates the evaluate call mid-flight.
    import aacc.qwen_chrome_cdp as module

    captured: dict[str, object] = {}

    def fake_open_socket(url: str, *, timeout: float) -> object:
        captured["url"] = url
        captured["timeout"] = timeout
        return object()

    monkeypatch.setattr(module, "_open_socket", fake_open_socket)
    module._open_qwen_page_socket("ws://127.0.0.1:9222/devtools/page/abc")

    assert captured["url"] == "ws://127.0.0.1:9222/devtools/page/abc"
    assert captured["timeout"] == module.QWEN_PAGE_SOCKET_TIMEOUT_SECONDS
    assert captured["timeout"] >= 90.0


def test_select_target_accepts_loopback_bailian_page() -> None:
    target = select_qwen_target(
        [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
            }
        ],
        expected_port=9222,
    )

    assert target.endswith("/devtools/page/abc")


def test_select_target_skips_foreign_pages_before_bailian() -> None:
    target = select_qwen_target(
        [
            {
                "type": "page",
                "url": "https://signin.aliyun.com/login.htm",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/foreign",
            },
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/owned",
            },
        ],
        expected_port=9222,
    )

    assert target.endswith("/devtools/page/owned")


@pytest.mark.parametrize(
    "target",
    [
        {
            "type": "page",
            "url": WORKSPACE_URL,
            "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/abc",
        },
        {
            "type": "page",
            "url": WORKSPACE_URL,
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc?evil=1",
        },
        {
            "type": "page",
            "url": WORKSPACE_URL,
            "webSocketDebuggerUrl": "ws://127.0.0.1:not-a-port/devtools/page/abc",
        },
    ],
)
def test_select_target_rejects_foreign_or_unsafe_page(target: dict[str, str]) -> None:
    with pytest.raises(QwenChromeQuotaError):
        select_qwen_target([target], expected_port=9222)


@pytest.mark.parametrize("targets", [None, {}, ["invalid"], [{"type": "other"}]])
def test_target_selection_rejects_malformed_target_lists(targets: object) -> None:
    with pytest.raises((QwenChromeQuotaError, QwenChromeUnauthorizedError)):
        select_qwen_target(targets, expected_port=9222)


def test_target_selection_without_bailian_page_is_unauthorized() -> None:
    with pytest.raises(QwenChromeUnauthorizedError):
        select_qwen_target(
            [
                {
                    "type": "page",
                    "url": "https://signin.aliyun.com/login.htm",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
                }
            ],
            expected_port=9222,
        )


def test_select_target_rejects_non_string_websocket_url() -> None:
    with pytest.raises(QwenChromeQuotaError):
        select_qwen_target(
            [{"type": "page", "url": WORKSPACE_URL, "webSocketDebuggerUrl": 5}],
            expected_port=9222,
        )


def test_parse_payload_allowlists_text_snippets() -> None:
    result = parse_qwen_chrome_payload(
        {
            "kind": "quota",
            "raw": {
                "personalFiveHourText": "5小时限额\n3.02%已用",
                "personalWeeklyText": "7天限额\n1.38%已用",
                "teamTotalText": "总额度\n92.82%",
            },
        }
    )

    assert result["personalFiveHourText"] == "5小时限额\n3.02%已用"
    assert result["personalWeeklyText"] == "7天限额\n1.38%已用"
    assert result["teamTotalText"] == "总额度\n92.82%"


def test_parse_payload_accepts_partial_window() -> None:
    result = parse_qwen_chrome_payload(
        {
            "kind": "quota",
            "raw": {
                "personalFiveHourText": "5小时限额\n0.04%已用",
                "personalWeeklyText": None,
                "teamTotalText": None,
            },
        }
    )
    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert result["personalWeeklyText"] is None
    assert result["teamTotalText"] is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"kind": "error", "message": "DOM_TIMEOUT"},
        {"kind": "quota", "raw": None},
        {"kind": "quota", "raw": {"personalFiveHourText": "x"}},
        {
            "kind": "quota",
            "raw": {
                "personalFiveHourText": None,
                "personalWeeklyText": None,
                "teamTotalText": None,
            },
        },
        {
            "kind": "quota",
            "raw": {
                "personalFiveHourText": 5,
                "personalWeeklyText": None,
                "teamTotalText": None,
            },
        },
        {
            "kind": "quota",
            "raw": {
                "personalFiveHourText": "x",
                "personalWeeklyText": None,
                "teamTotalText": None,
                "evil": 1,
            },
        },
    ],
)
def test_parse_payload_rejects_untrusted_values(payload: object) -> None:
    with pytest.raises((QwenChromeQuotaError, QwenChromeUnauthorizedError)):
        parse_qwen_chrome_payload(payload)


def test_parse_payload_rejects_oversized_snippet() -> None:
    huge = "5小时限额\n" + ("x" * 30_000)
    with pytest.raises(QwenChromeQuotaError):
        parse_qwen_chrome_payload(
            {
                "kind": "quota",
                "raw": {
                    "personalFiveHourText": huge,
                    "personalWeeklyText": None,
                    "teamTotalText": None,
                },
            }
        )


def test_parse_payload_maps_expired_session_to_unauthorized() -> None:
    with pytest.raises(QwenChromeUnauthorizedError):
        parse_qwen_chrome_payload({"kind": "unauthorized"})


class FakeProcess:
    pid = 123

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.waits = 0

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.waits += 1
        return 0

    def terminate(self) -> None:
        pass


def _make_chrome_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "config" / "qwen-chrome-profile"
    profile.mkdir(parents=True)
    (profile / "DevToolsActivePort").write_text(
        "9222\n/devtools/browser/browser-id\n", encoding="ascii"
    )
    return profile


def _fake_process_factory(profile: Path, process: FakeProcess):
    def start(command: list[str]) -> FakeProcess:
        process.commands.append(command)
        (profile / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/browser-id\n", encoding="ascii"
        )
        return process

    return start


def test_managed_operation_returns_sanitized_quota_and_closes_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    payload = {
        "kind": "quota",
        "raw": {
            "personalFiveHourText": "5小时限额\n0.04%已用",
            "personalWeeklyText": "7天限额\n65%已用",
            "teamTotalText": None,
        },
    }

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def evaluate(self, _expression: str) -> object:
            return payload

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
            }
        ],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: 0.0,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result == {
        "personalFiveHourText": "5小时限额\n0.04%已用",
        "personalWeeklyText": "7天限额\n65%已用",
        "teamTotalText": None,
    }


def test_managed_operation_hidden_unauthorized_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 61.0])
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        sleep=lambda _seconds: None,
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: next(ticks, 61.0),
    )

    with pytest.raises(QwenChromeUnauthorizedError):
        operation.run(visible=False, cancel=Event())


def test_managed_operation_propagates_missing_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    _make_chrome_profile(tmp_path)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
    )

    def _missing() -> Path:
        raise QwenChromeMissingError

    monkeypatch.setattr(module, "find_qwen_chrome_executable", _missing)
    with pytest.raises(QwenChromeMissingError):
        operation.run(visible=False, cancel=Event())


def test_managed_operation_cancel_before_start_raises(tmp_path: Path) -> None:
    _make_chrome_profile(tmp_path)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
    )
    cancel = Event()
    cancel.set()
    with pytest.raises(QwenChromeCancelledError):
        operation.run(visible=False, cancel=cancel)


def test_managed_operation_rejects_unsafe_profile(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    expected = config_dir / "qwen-chrome-profile"
    expected.parent.mkdir(parents=True)
    expected.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=config_dir,
        executable=Path("chrome"),
    )

    with pytest.raises(QwenChromeQuotaError):
        operation.run(visible=False, cancel=Event())


def test_profile_validation_and_logout_are_owned_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    config_dir = tmp_path / "config"
    profile = qwen_chrome_profile_path(config_dir)
    profile.mkdir(parents=True)
    (profile / "cookie").write_text("private", encoding="utf-8")
    clear_owned_qwen_chrome_profile(profile, config_dir)
    assert not profile.exists()

    profile.write_text("not a directory", encoding="utf-8")
    with pytest.raises(QwenChromeQuotaError):
        validate_owned_qwen_chrome_profile(profile, config_dir)

    profile.unlink()
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))
    profile.mkdir(parents=True)
    with pytest.raises(QwenChromeQuotaError):
        clear_owned_qwen_chrome_profile(profile, config_dir)

    missing_config = tmp_path / "missing"
    clear_owned_qwen_chrome_profile(qwen_chrome_profile_path(missing_config), missing_config)


def test_profile_logout_retries_quarantined_directory_after_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    config_dir = tmp_path / "config"
    profile = qwen_chrome_profile_path(config_dir)
    profile.mkdir(parents=True)
    (profile / "cookie").write_text("private", encoding="utf-8")
    real_rmtree = module.shutil.rmtree
    failed = False

    def fail_once(path: Path) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("profile is still locked")
        real_rmtree(path)

    monkeypatch.setattr(module.shutil, "rmtree", fail_once)
    with pytest.raises(QwenChromeQuotaError):
        clear_owned_qwen_chrome_profile(profile, config_dir)
    assert not profile.exists()
    assert list(profile.parent.glob(".qwen-chrome-profile.logout-*"))

    monkeypatch.setattr(module.shutil, "rmtree", real_rmtree)
    clear_owned_qwen_chrome_profile(profile, config_dir)
    assert not list(profile.parent.glob(".qwen-chrome-profile.logout-*"))


def test_profile_validation_rejects_reparse_parent_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    config_dir = tmp_path / "config"
    profile = qwen_chrome_profile_path(config_dir)
    profile.mkdir(parents=True)
    monkeypatch.setattr(module, "_is_reparse_point", lambda path: path in {config_dir, profile})

    with pytest.raises(QwenChromeQuotaError):
        validate_owned_qwen_chrome_profile(profile, config_dir)


def test_profile_validation_rejects_reparse_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    config_dir = tmp_path / "config"
    profile = qwen_chrome_profile_path(config_dir)
    profile.mkdir(parents=True)
    monkeypatch.setattr(module, "_is_reparse_point", lambda path: path == config_dir)

    with pytest.raises(QwenChromeQuotaError):
        validate_owned_qwen_chrome_profile(profile, config_dir)


def test_clear_profile_skips_foreign_entries_and_rejects_unsafe_quarantine(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    profile = qwen_chrome_profile_path(config_dir)
    profile.mkdir(parents=True)
    foreign = config_dir / "unrelated-directory"
    foreign.mkdir()
    quarantine = config_dir / ".qwen-chrome-profile.logout-abc"
    quarantine.mkdir()

    clear_owned_qwen_chrome_profile(profile, config_dir)
    assert not profile.exists()
    assert not quarantine.exists()
    assert foreign.exists()

    profile.mkdir()
    external = tmp_path / "external-target"
    external.mkdir()
    unsafe_quarantine = config_dir / ".qwen-chrome-profile.logout-evil"
    unsafe_quarantine.symlink_to(external, target_is_directory=True)

    with pytest.raises(QwenChromeQuotaError):
        clear_owned_qwen_chrome_profile(profile, config_dir)
    assert external.exists()


class ImmediateExitOpener:
    """The ``open -g -n`` launcher hands off to LaunchServices and exits at once."""

    pid = 777

    def __init__(self, return_code: int | None) -> None:
        self.return_code = return_code
        self.terminated = False

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.return_code is None:
            raise TimeoutError
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True


class FakeSocket:
    def __init__(self, incoming: list[str]) -> None:
        self.incoming = incoming
        self.sent: list[str] = []

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def recv(self) -> str:
        return self.incoming.pop(0)

    def close(self) -> None:
        pass


class FakeIterProcess:
    def __init__(self, name: object, cmdline: object) -> None:
        self.info = {"name": name, "cmdline": cmdline}


class FakeChromeProcess:
    def __init__(self, *, stubborn: bool, pid: int = 4242) -> None:
        self.pid = pid
        self.alive = True
        self.terminated = 0
        self.killed = 0
        self._stubborn = stubborn

    def terminate(self) -> None:
        self.terminated += 1
        if not self._stubborn:
            self.alive = False

    def kill(self) -> None:
        self.killed += 1
        self.alive = False


def _quota_payload() -> dict[str, object]:
    return {
        "kind": "quota",
        "raw": {
            "personalFiveHourText": "5小时限额\n0.04%已用",
            "personalWeeklyText": "7天限额\n65%已用",
            "teamTotalText": None,
        },
    }


def test_detached_handle_masks_zero_exit_of_open_launcher(tmp_path: Path) -> None:
    events: list[str] = []

    def finder(_profile: Path) -> list[object]:
        events.append("find")
        return []

    handle = _DetachedQwenChromeHandle(
        ImmediateExitOpener(0),
        profile=tmp_path,
        process_finder=finder,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    # Chrome liveness is probed through the DevTools endpoint and the
    # per-profile process finder, so a zero exit of the `open` launcher must
    # not read as a dead browser.
    assert handle.pid == 777
    assert handle.poll() is None
    assert handle.wait(timeout=5.0) == 0
    assert events == ["find"]


def test_detached_handle_surfaces_non_zero_open_exit(tmp_path: Path) -> None:
    handle = _DetachedQwenChromeHandle(
        ImmediateExitOpener(3),
        profile=tmp_path,
        process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    assert handle.poll() == 3
    assert handle.wait(timeout=5.0) == 3


def test_detached_handle_wait_times_out_while_chrome_alive(tmp_path: Path) -> None:
    clock = iter([0.0, 4.9, 5.1])
    handle = _DetachedQwenChromeHandle(
        ImmediateExitOpener(0),
        profile=tmp_path,
        process_finder=lambda _profile: [FakeChromeProcess(stubborn=True)],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 99.0),
    )

    with pytest.raises(TimeoutError):
        handle.wait(timeout=5.0)


def test_detached_handle_terminate_delegates_to_profile_killer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    seen: list[tuple[Path, object]] = []

    def record(profile: Path, *, process_finder: object = None) -> None:
        seen.append((profile, process_finder))

    def finder(_profile: Path) -> list[object]:
        return []

    monkeypatch.setattr(module, "terminate_qwen_chrome_profile_processes", record)
    handle = _DetachedQwenChromeHandle(
        ImmediateExitOpener(0),
        profile=tmp_path,
        process_finder=finder,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    handle.terminate()

    assert seen == [(tmp_path, finder)]


def test_find_chrome_processes_matches_exact_user_data_dir(tmp_path: Path) -> None:
    profile = tmp_path / "qwen-chrome-profile"
    flag = f"--user-data-dir={profile}"
    chrome_main = FakeIterProcess("Google Chrome", ["/Applications/chrome", flag])
    chrome_helper = FakeIterProcess("Google Chrome Helper", ["helper", flag])
    substring_profile = FakeIterProcess("Google Chrome", ["chrome", f"{flag}-evil"])
    # The `open` launcher carries the same flag after --args; the name filter
    # must keep it out of the kill set.
    open_launcher = FakeIterProcess(
        "open", ["/usr/bin/open", "-g", "-n", "-b", "com.google.Chrome", "--args", flag]
    )
    foreign_profile = FakeIterProcess("Google Chrome", ["chrome", "--user-data-dir=/elsewhere"])

    def process_iter(_attrs: tuple[str, ...]) -> list[FakeIterProcess]:
        return [chrome_main, chrome_helper, substring_profile, open_launcher, foreign_profile]

    found = _find_qwen_chrome_processes_for_profile(profile, process_iter=process_iter)

    assert found == [chrome_main, chrome_helper]


def test_find_chrome_processes_skips_unreadable_and_fails_closed(tmp_path: Path) -> None:
    profile = tmp_path / "qwen-chrome-profile"
    flag = f"--user-data-dir={profile}"
    good = FakeIterProcess("Google Chrome", ["chrome", flag])

    class Exploding:
        @property
        def info(self) -> dict[str, object]:
            raise AttributeError("process vanished")

    found = _find_qwen_chrome_processes_for_profile(
        profile, process_iter=lambda _attrs: [Exploding(), good]
    )
    assert found == [good]

    def broken_iter(_attrs: tuple[str, ...]) -> list[object]:
        raise psutil.Error()

    assert _find_qwen_chrome_processes_for_profile(profile, process_iter=broken_iter) == []


def test_terminate_profile_processes_escalates_for_stubborn_chrome(tmp_path: Path) -> None:
    stubborn = FakeChromeProcess(stubborn=True, pid=1)
    well_behaved = FakeChromeProcess(stubborn=False, pid=2)

    def waiter(
        processes: list[FakeChromeProcess], timeout: float
    ) -> tuple[list[FakeChromeProcess], list[FakeChromeProcess]]:
        del timeout
        return ([], [process for process in processes if process.alive])

    terminate_qwen_chrome_profile_processes(
        tmp_path,
        process_finder=lambda _profile: [stubborn, well_behaved],
        process_waiter=waiter,
    )

    assert (stubborn.terminated, stubborn.killed) == (1, 1)
    assert (well_behaved.terminated, well_behaved.killed) == (1, 0)


def test_terminate_profile_processes_kills_everything_when_waiter_fails(
    tmp_path: Path,
) -> None:
    chrome = FakeChromeProcess(stubborn=False)

    def waiter(processes: list[FakeChromeProcess], timeout: float) -> object:
        del processes, timeout
        raise psutil.Error()

    terminate_qwen_chrome_profile_processes(
        tmp_path,
        process_finder=lambda _profile: [chrome],
        process_waiter=waiter,
    )

    assert (chrome.terminated, chrome.killed) == (1, 1)


def test_stealth_script_masks_webdriver_and_negative_coordinates() -> None:
    script = qwen_hidden_page_stealth_script()

    assert "webdriver" in script
    assert "screenX" in script
    assert "screenY" in script


def test_stealth_install_sends_expected_cdp_sequence() -> None:
    from aacc.kimi_edge_cdp import CdpConnection

    socket = FakeSocket(
        [
            '{"id":1,"result":{}}',
            '{"method":"Page.loadEventFired","params":{}}',
            '{"id":2,"result":{"identifier":"script-1"}}',
            '{"id":3,"result":{}}',
            '{"id":4,"result":{}}',
            '{"id":5,"result":{"windowId":7,"bounds":{"left":0,"top":30}}}',
            '{"id":6,"result":{}}',
            '{"id":7,"result":{"result":{"value":{"kind":"quota"}}}}',
        ]
    )
    page = CdpConnection(socket)

    install_qwen_hidden_page_stealth(page)
    payload = page.evaluate("1 + 1")

    methods = [json.loads(frame)["method"] for frame in socket.sent]
    assert methods == [
        "Page.enable",
        "Page.addScriptToEvaluateOnNewDocument",
        "Emulation.setDeviceMetricsOverride",
        "Page.reload",
        "Browser.getWindowForTarget",
        "Browser.setWindowBounds",
        "Runtime.evaluate",
    ]
    emulation_params = json.loads(socket.sent[2])["params"]
    assert emulation_params["width"] >= 1024
    assert emulation_params["mobile"] is False
    bounds_params = json.loads(socket.sent[5])["params"]
    assert bounds_params["windowId"] == 7
    assert bounds_params["bounds"]["left"] == -32000
    assert bounds_params["bounds"]["top"] == -32000
    assert payload == {"kind": "quota"}


def test_stealth_install_swallows_cdp_errors() -> None:
    from aacc.kimi_edge_cdp import CdpConnection

    socket = FakeSocket(
        [
            '{"id":1,"result":{}}',
            '{"id":2,"error":{"code":-32000,"message":"denied"}}',
        ]
    )
    page = CdpConnection(socket)

    install_qwen_hidden_page_stealth(page)

    methods = [json.loads(frame)["method"] for frame in socket.sent]
    assert methods == ["Page.enable", "Page.addScriptToEvaluateOnNewDocument"]


def test_stealth_install_skips_window_move_without_window_id() -> None:
    from aacc.kimi_edge_cdp import CdpConnection

    socket = FakeSocket(
        [
            '{"id":1,"result":{}}',
            '{"id":2,"result":{"identifier":"script-1"}}',
            '{"id":3,"result":{}}',
            '{"id":4,"result":{}}',
            '{"id":5,"result":{"bounds":{}}}',
        ]
    )
    page = CdpConnection(socket)

    install_qwen_hidden_page_stealth(page)

    methods = [json.loads(frame)["method"] for frame in socket.sent]
    assert methods == [
        "Page.enable",
        "Page.addScriptToEvaluateOnNewDocument",
        "Emulation.setDeviceMetricsOverride",
        "Page.reload",
        "Browser.getWindowForTarget",
    ]


def test_hidden_refresh_installs_stealth_before_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    calls: list[str] = []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def evaluate(self, _expression: str) -> object:
            calls.append("evaluate")
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_stealth(_page: object) -> None:
        calls.append("stealth")

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    monkeypatch.setattr(module, "install_qwen_hidden_page_stealth", fake_stealth)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
            }
        ],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: 0.0,
    )

    operation.run(visible=False, cancel=Event())

    assert calls == ["stealth", "evaluate"]


def test_visible_login_skips_stealth_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    calls: list[str] = []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def evaluate(self, _expression: str) -> object:
            calls.append("evaluate")
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_stealth(_page: object) -> None:
        calls.append("stealth")

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    monkeypatch.setattr(module, "install_qwen_hidden_page_stealth", fake_stealth)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
            }
        ],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: 0.0,
    )

    operation.run(visible=True, cancel=Event())

    assert calls == ["evaluate"]


def test_hidden_refresh_succeeds_after_fire_and_forget_open_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    launched: list[list[str]] = []

    def start(command: list[str]) -> ImmediateExitOpener:
        launched.append(command)
        (profile / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/browser-id\n", encoding="ascii"
        )
        return ImmediateExitOpener(0)

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    monkeypatch.setattr(module, "install_qwen_hidden_page_stealth", lambda _page: None)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=start,
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
            }
        ],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: 0.0,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert launched[0][0] == "/usr/bin/open"
    assert "-g" in launched[0]
    assert "-n" in launched[0]


def test_hidden_refresh_fails_fast_when_open_launcher_fails(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    qwen_chrome_profile_path(config_dir).mkdir(parents=True)

    def start(_command: list[str]) -> ImmediateExitOpener:
        return ImmediateExitOpener(3)

    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=config_dir,
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=start,
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(QwenChromeQuotaError):
        operation.run(visible=False, cancel=Event())


def test_hidden_refresh_cleans_profile_processes_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    events: list[str] = []

    def finder(_profile: Path) -> list[object]:
        events.append("find")
        return []

    def start(_command: list[str]) -> FakeProcess:
        events.append("launch")
        (profile / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/browser-id\n", encoding="ascii"
        )
        return FakeProcess()

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    monkeypatch.setattr(module, "install_qwen_hidden_page_stealth", lambda _page: None)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=start,
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
            }
        ],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=finder,
        monotonic=lambda: 0.0,
    )

    operation.run(visible=False, cancel=Event())

    assert events[0] == "find"
    assert events.index("find") < events.index("launch")


def test_profile_cleanup_failure_does_not_block_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    state = {"raised": False}

    def finder(_profile: Path) -> list[object]:
        if not state["raised"]:
            state["raised"] = True
            raise OSError("psutil unavailable")
        return []

    def start(_command: list[str]) -> FakeProcess:
        (profile / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/browser-id\n", encoding="ascii"
        )
        return FakeProcess()

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    monkeypatch.setattr(module, "install_qwen_hidden_page_stealth", lambda _page: None)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=start,
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
            }
        ],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=finder,
        monotonic=lambda: 0.0,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"


def test_hidden_refresh_falls_back_to_terminator_when_chrome_outlives_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    stubborn = FakeChromeProcess(stubborn=True)
    # First finding is consumed by the pre-launch cleanup; the shutdown wait
    # then sees the stubborn process, times out, and the injected terminator
    # must kick in before the final wait sees an empty profile.
    findings = iter([[], [stubborn], []])
    clock = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 6.0])
    terminated: list[int] = []

    def finder(_profile: Path) -> list[object]:
        return next(findings, [])

    def start(_command: list[str]) -> ImmediateExitOpener:
        (profile / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/browser-id\n", encoding="ascii"
        )
        return ImmediateExitOpener(0)

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    monkeypatch.setattr(module, "install_qwen_hidden_page_stealth", lambda _page: None)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=start,
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
            }
        ],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        process_tree_terminator=lambda owned: terminated.append(owned.pid),
        chrome_process_finder=finder,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 6.0),
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert terminated == [777]
