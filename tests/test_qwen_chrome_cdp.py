import json
import re
import sys
from pathlib import Path
from threading import Event

import psutil
import pytest

from aacc.qwen_chrome_cdp import (
    QWEN_SESSION_ORIGIN_DAILY_RECOPY,
    QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
    ManagedQwenChromeOperation,
    QwenChromeCancelledError,
    QwenChromeLoginCancelledError,
    QwenChromeMissingError,
    QwenChromeQuotaError,
    QwenChromeUnauthorizedError,
    _DetachedQwenChromeHandle,
    _find_qwen_chrome_processes_for_profile,
    _launch_hidden_qwen_chrome_with_workspace,
    build_qwen_chrome_launch,
    centered_window_bounds,
    clear_owned_qwen_chrome_profile,
    count_qwen_page_targets,
    daily_chrome_session_source,
    find_qwen_chrome_executable,
    install_qwen_hidden_page_stealth,
    install_qwen_visible_login_page,
    parse_qwen_chrome_payload,
    qwen_chrome_profile_path,
    qwen_dom_extract_expression,
    qwen_hidden_page_stealth_script,
    recopy_qwen_daily_chrome_session,
    select_qwen_page_sockets,
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


def test_hidden_launch_spec_uses_hidden_launchservices_instance(tmp_path: Path) -> None:
    # Aliyun's baxia risk control voids session tickets shown by headless
    # browsers, so the hidden refresh must launch a real headed Chrome. The
    # macOS process boundary uses NSWorkspace below; the spec itself remains
    # a normal Chrome argv list so the LaunchServices call can pass the exact
    # isolated profile and stealth flags without invoking `/usr/bin/open`.
    spec = build_qwen_chrome_launch(
        Path("chrome"), tmp_path, WORKSPACE_URL, visible=False, platform_name="darwin"
    )

    assert spec.executable == Path("chrome")
    chrome_flags = spec.arguments
    assert "--headless=new" not in spec.arguments
    assert "--disable-gpu" not in spec.arguments
    assert "--headless=new" not in chrome_flags
    assert "--disable-gpu" not in chrome_flags
    assert f"--user-data-dir={tmp_path}" in chrome_flags
    assert "--remote-debugging-address=127.0.0.1" in chrome_flags
    assert "--remote-debugging-port=0" in chrome_flags
    assert "--disable-extensions" in chrome_flags
    assert "--disable-background-timer-throttling" in chrome_flags
    assert "--disable-renderer-backgrounding" in chrome_flags
    assert "--disable-backgrounding-occluded-windows" in chrome_flags
    assert "--no-startup-window" in chrome_flags
    # The quota page is opened through CDP Target.createTarget, not a launch
    # URL, so the hidden instance never activates at startup.
    assert WORKSPACE_URL not in chrome_flags


def test_hidden_launchservices_options_do_not_add_chrome_to_recents() -> None:
    launched: dict[str, object] = {}

    class FakeURL:
        @staticmethod
        def fileURLWithPath_(path: str) -> tuple[str, str]:
            return ("url", path)

    class FakeArray:
        @staticmethod
        def arrayWithArray_(values: list[str]) -> tuple[str, list[str]]:
            return ("array", values)

    class FakeConfiguration:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        def setAddsToRecentItems_(self, value: bool) -> None:
            self.values["addsToRecentItems"] = value

        def setActivates_(self, value: bool) -> None:
            self.values["activates"] = value

        def setCreatesNewApplicationInstance_(self, value: bool) -> None:
            self.values["createsNewApplicationInstance"] = value

        def setHides_(self, value: bool) -> None:
            self.values["hides"] = value

        def setArguments_(self, value: object) -> None:
            self.values["arguments"] = value

    class FakeConfigurationType:
        configuration_instance = FakeConfiguration()

        @classmethod
        def configuration(cls) -> FakeConfiguration:
            return cls.configuration_instance

    class FakeRunningApplication:
        def processIdentifier(self) -> int:
            return 4321

        def isTerminated(self) -> bool:
            return False

        def terminate(self) -> bool:
            return True

    class FakeWorkspace:
        def openApplicationAtURL_configuration_completionHandler_(
            self, url: object, configuration: FakeConfiguration, completion: object
        ) -> None:
            launched.update(url=url, configuration=configuration)
            completion(FakeRunningApplication(), None)  # type: ignore[operator]

    process = _launch_hidden_qwen_chrome_with_workspace(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "--user-data-dir=/tmp/aacc-qwen-profile",
            "--no-startup-window",
        ],
        workspace=FakeWorkspace(),
        url_type=FakeURL,
        array_type=FakeArray,
        configuration_type=FakeConfigurationType,
    )

    assert process.pid == 4321
    assert launched["url"] == (
        "url",
        str(Path("/Applications/Google Chrome.app")),
    )
    assert FakeConfigurationType.configuration_instance.values == {
        "addsToRecentItems": False,
        "activates": False,
        "createsNewApplicationInstance": True,
        "hides": True,
        "arguments": (
            "array",
            ["--user-data-dir=/tmp/aacc-qwen-profile", "--no-startup-window"],
        ),
    }


def test_hidden_launchservices_late_completion_after_cancel_is_terminated() -> None:
    completion_holder: dict[str, object] = {}

    class FakeURL:
        @staticmethod
        def fileURLWithPath_(path: str) -> tuple[str, str]:
            return ("url", path)

    class FakeArray:
        @staticmethod
        def arrayWithArray_(values: list[str]) -> tuple[str, list[str]]:
            return ("array", values)

    class FakeConfiguration:
        @classmethod
        def configuration(cls) -> "FakeConfiguration":
            return cls()

        def setAddsToRecentItems_(self, _value: bool) -> None:
            pass

        def setActivates_(self, _value: bool) -> None:
            pass

        def setCreatesNewApplicationInstance_(self, _value: bool) -> None:
            pass

        def setHides_(self, _value: bool) -> None:
            pass

        def setArguments_(self, _value: object) -> None:
            pass

    class FakeRunningApplication:
        def __init__(self) -> None:
            self.terminated = False

        def processIdentifier(self) -> int:
            return 9876

        def isTerminated(self) -> bool:
            return self.terminated

        def terminate(self) -> bool:
            self.terminated = True
            return True

    class DelayedWorkspace:
        def openApplicationAtURL_configuration_completionHandler_(
            self, _url: object, _configuration: object, completion: object
        ) -> None:
            completion_holder["completion"] = completion

    process = _launch_hidden_qwen_chrome_with_workspace(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "--user-data-dir=/tmp/aacc-qwen-profile",
            "--no-startup-window",
        ],
        workspace=DelayedWorkspace(),
        url_type=FakeURL,
        array_type=FakeArray,
        configuration_type=FakeConfiguration,
    )

    assert process.is_completion_pending()
    process.cancel_pending()
    application = FakeRunningApplication()
    completion_holder["completion"](application, None)  # type: ignore[operator]

    assert application.terminated
    assert process.pid == 9876
    assert process.poll() == 0


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


def test_detached_handle_does_not_treat_successful_open_as_chrome_exit(
    tmp_path: Path,
) -> None:
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

    assert handle.poll() is None
    assert handle.wait(timeout=5.0) == 0
    assert events == ["find"]


def test_detached_handle_surfaces_open_failure(tmp_path: Path) -> None:
    handle = _DetachedQwenChromeHandle(
        ImmediateExitOpener(3),
        profile=tmp_path,
        process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    assert handle.poll() == 3
    assert handle.wait(timeout=5.0) == 3


def test_detached_handle_surfaces_real_launchservices_exit(tmp_path: Path) -> None:
    handle = _DetachedQwenChromeHandle(
        ImmediateExitOpener(0),
        profile=tmp_path,
        process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
        launcher_handoff=False,
    )

    assert handle.poll() == 0


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

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

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


def test_login_evaluates_every_bailian_target_until_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    quota_payload = {
        "kind": "quota",
        "raw": {
            "personalFiveHourText": "5小时限额\n12.5%已用",
            "personalWeeklyText": "7天限额\n65%已用",
            "teamTotalText": None,
        },
    }
    stale_payload = {"kind": "unauthorized"}
    payloads: dict[str, object] = {
        "ws://127.0.0.1:9222/devtools/page/A": stale_payload,
        "ws://127.0.0.1:9222/devtools/page/B": quota_payload,
    }
    evaluated: list[str] = []

    class FakeCdp:
        def __init__(self, socket: object) -> None:
            self._socket = socket

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            evaluated.append(str(self._socket))
            return payloads[str(self._socket)]

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    # The console can hold a stale logged-out Bailian tab next to the live
    # one (a redirect left it behind during login), and /json ordering does
    # not promise which comes first. A visible login that only evaluates
    # the first target starves on the stale tab until the deadline even
    # though the live tab renders the quota.
    ticks = iter([0.0] * 60 + [2000.0])
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
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/A",
            },
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/B",
            },
        ],
        socket_factory=lambda url: url,
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(ticks, 2000.0),
    )

    result = operation.run(visible=True, cancel=Event())

    assert "ws://127.0.0.1:9222/devtools/page/A" in evaluated
    assert "ws://127.0.0.1:9222/devtools/page/B" in evaluated
    assert result == {
        "personalFiveHourText": "5小时限额\n12.5%已用",
        "personalWeeklyText": "7天限额\n65%已用",
        "teamTotalText": None,
    }


def test_hidden_refresh_all_candidates_failing_raises_refresh_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()

    class FakeCdp:
        def __init__(self, socket: object) -> None:
            self._socket = socket

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            if str(self._socket).endswith("/A"):
                raise RuntimeError("page transport died")
            return {"kind": "error", "generation": 1, "message": "DOM_TIMEOUT"}

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    ticks = iter([0.0] * 80 + [200.0])
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
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/A",
            },
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/B",
            },
        ],
        socket_factory=lambda url: url,
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(ticks, 200.0),
    )

    with pytest.raises(QwenChromeQuotaError):
        operation.run(visible=False, cancel=Event())


def test_select_page_sockets_returns_every_valid_bailian_target() -> None:
    sockets = select_qwen_page_sockets(
        [
            {
                "type": "page",
                "url": "https://www.google.com/",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/foreign",
            },
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/A",
            },
            {
                "type": "page",
                "url": WORKSPACE_URL + "-extra",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/B",
            },
        ],
        expected_port=9222,
    )

    assert sockets == [
        "ws://127.0.0.1:9222/devtools/page/A",
        "ws://127.0.0.1:9222/devtools/page/B",
    ]


def test_select_page_sockets_without_bailian_page_is_unauthorized() -> None:
    with pytest.raises(QwenChromeUnauthorizedError):
        select_qwen_page_sockets(
            [
                {
                    "type": "page",
                    "url": "https://www.google.com/",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/x",
                }
            ],
            expected_port=9222,
        )


def test_hidden_refresh_missing_page_never_triggers_recopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    # A missing Bailian page is a startup race, not an expired session: the
    # loop must burn the full startup deadline as a plain refresh failure and
    # must never hand it to the recopy path (a recopy would overwrite a
    # healthy profile with whatever the daily Chrome currently holds).
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 91.0])
    recopied: list[Path] = []
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
        monotonic=lambda: next(ticks, 91.0),
        session_recopy=lambda config_dir: recopied.append(config_dir),
    )

    with pytest.raises(QwenChromeQuotaError):
        operation.run(visible=False, cancel=Event())
    assert recopied == []


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
    """A process that has already exited (e.g. Chrome crashing at startup)."""

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


def test_find_chrome_processes_matches_exact_user_data_dir(tmp_path: Path) -> None:
    profile = tmp_path / "qwen-chrome-profile"
    flag = f"--user-data-dir={profile}"
    chrome_main = FakeIterProcess("Google Chrome", ["/Applications/chrome", flag])
    chrome_helper = FakeIterProcess("Google Chrome Helper", ["helper", flag])
    substring_profile = FakeIterProcess("Google Chrome", ["chrome", f"{flag}-evil"])
    # A foreign launcher could carry the same flag in its argv; the name
    # filter must keep non-Chrome processes out of the kill set.
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

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            calls.append("create_target")
            return {"result": {}}

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

    assert calls == ["create_target", "stealth", "evaluate"]


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


def test_hidden_refresh_opens_quota_page_in_background_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    launched: list[list[str]] = []
    cdp_commands: list[tuple[str, object]] = []

    def start(command: list[str]) -> FakeProcess:
        launched.append(command)
        (profile / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/browser-id\n", encoding="ascii"
        )
        return FakeProcess()

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, method: str, params: object) -> dict[str, object]:
            cdp_commands.append((method, params))
            return {"result": {}}

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
    # The hidden path uses LaunchServices' hidden-instance mode and starts
    # windowless; the page is opened in a background window through CDP so
    # the instance never activates or leaves a Dock tile behind.
    assert launched[0][0] == "chrome"
    assert "--no-startup-window" in launched[0]
    create_target = [params for method, params in cdp_commands if method == "Target.createTarget"]
    assert create_target == [{"url": WORKSPACE_URL, "newWindow": True, "background": True}]


def test_hidden_refresh_fails_fast_when_chrome_exits_at_startup(tmp_path: Path) -> None:
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

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

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

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

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
    terminated: list[int] = []
    profile_alive = False
    clock = [0.0]

    class StubbornProcess:
        """Chrome that ignores Browser.close past the shutdown window."""

        pid = 777

        def __init__(self) -> None:
            self.waits = 0

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.waits += 1
            if self.waits == 1:
                raise TimeoutError
            return 0

        def terminate(self) -> None:
            pass

    def start(_command: list[str]) -> StubbornProcess:
        nonlocal profile_alive
        (profile / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/browser-id\n", encoding="ascii"
        )
        profile_alive = True
        return StubbornProcess()

    def finder(_profile: Path) -> list[object]:
        return [object()] if profile_alive else []

    def terminate(owned: object) -> None:
        nonlocal profile_alive
        terminated.append(owned.pid)  # type: ignore[attr-defined]
        profile_alive = False

    def advance_clock(_seconds: float) -> None:
        clock[0] = 6.0

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

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
        process_tree_terminator=terminate,
        chrome_process_finder=finder,
        sleep=advance_clock,
        monotonic=lambda: clock[0],
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert terminated == [777]


def _make_daily_chrome_source(root: Path) -> Path:
    default = root / "Default"
    default.mkdir(parents=True)
    (root / "Local State").write_text('{"os_crypt":{"encrypted_key":"k"}}', encoding="utf-8")
    import sqlite3

    connection = sqlite3.connect(default / "Cookies")
    with connection:
        connection.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
        connection.execute("INSERT INTO cookies VALUES ('.aliyun.com', 'cna')")
    connection.close()
    (default / "Preferences").write_text("{}", encoding="utf-8")
    (default / "Secure Preferences").write_text("{}", encoding="utf-8")
    (default / "Local Storage" / "leveldb").mkdir(parents=True)
    (default / "Local Storage" / "leveldb" / "000001.log").write_text("ls", encoding="utf-8")
    (default / "Session Storage").mkdir()
    (default / "Session Storage" / "CURRENT").write_text("ss", encoding="utf-8")
    (default / "Login Data").write_text("secret", encoding="utf-8")
    return root


def test_daily_chrome_session_source_requires_default_profile(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert daily_chrome_session_source(platform_name="darwin", home=home) is None
    root = home / "Library" / "Application Support" / "Google" / "Chrome"
    (root / "Default").mkdir(parents=True)
    assert daily_chrome_session_source(platform_name="darwin", home=home) == root
    assert daily_chrome_session_source(platform_name="win32", home=home) is None


def test_recopy_daily_session_copies_minimal_set(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    profile = config_dir / "qwen-chrome-profile"
    profile.mkdir()
    (profile / "stale.txt").write_text("old", encoding="utf-8")
    source = _make_daily_chrome_source(tmp_path / "daily")

    recopy_qwen_daily_chrome_session(config_dir, source_root=source)

    default = profile / "Default"
    assert not (profile / "stale.txt").exists()
    assert (profile / "Local State").read_text(encoding="utf-8") == (
        '{"os_crypt":{"encrypted_key":"k"}}'
    )
    assert (default / "Preferences").exists()
    assert (default / "Secure Preferences").exists()
    assert (default / "Local Storage" / "leveldb" / "000001.log").exists()
    assert (default / "Session Storage" / "CURRENT").exists()
    assert not (default / "Login Data").exists()
    import sqlite3

    connection = sqlite3.connect(default / "Cookies")
    rows = connection.execute("SELECT host_key, name FROM cookies").fetchall()
    connection.close()
    assert rows == [(".aliyun.com", "cna")]
    quarantines = [
        entry
        for entry in config_dir.iterdir()
        if entry.name.startswith(".qwen-chrome-profile.pre-dailycopy-")
    ]
    assert len(quarantines) == 1
    assert (quarantines[0] / "stale.txt").exists()
    if sys.platform != "win32":
        # Windows protection is a native DACL, which stat() cannot see.
        assert profile.stat().st_mode & 0o777 == 0o700


def test_recopy_daily_session_prunes_old_quarantines(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    profile = config_dir / "qwen-chrome-profile"
    profile.mkdir()
    stamps = ("20260801-000000", "20260802-000000", "20260803-000000", "20260804-000000")
    for stamp in stamps:
        (config_dir / f".qwen-chrome-profile.pre-dailycopy-{stamp}").mkdir()
    source = _make_daily_chrome_source(tmp_path / "daily")

    recopy_qwen_daily_chrome_session(config_dir, source_root=source)

    remaining = sorted(
        entry.name
        for entry in config_dir.iterdir()
        if entry.name.startswith(".qwen-chrome-profile.pre-dailycopy-")
    )
    assert len(remaining) == 3
    assert ".qwen-chrome-profile.pre-dailycopy-20260801-000000" not in remaining
    assert ".qwen-chrome-profile.pre-dailycopy-20260802-000000" not in remaining


def test_recopy_daily_session_missing_source_raises(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with pytest.raises(QwenChromeQuotaError):
        recopy_qwen_daily_chrome_session(
            config_dir, platform_name="darwin", home=tmp_path / "empty-home"
        )


def test_recopy_daily_session_missing_required_files_raises(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    source = _make_daily_chrome_source(tmp_path / "daily")
    (source / "Local State").unlink()
    with pytest.raises(QwenChromeQuotaError):
        recopy_qwen_daily_chrome_session(config_dir, source_root=source)


def test_recopy_daily_session_rejects_symlink_source(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    real = _make_daily_chrome_source(tmp_path / "daily")
    link = tmp_path / "daily-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(QwenChromeQuotaError):
        recopy_qwen_daily_chrome_session(config_dir, source_root=link)


def _quota_page_target() -> dict[str, str]:
    return {
        "type": "page",
        "url": WORKSPACE_URL,
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-id",
    }


def test_hidden_refresh_recopies_and_retries_after_unauthorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    attempts = {"count": 0}
    evaluations = {"count": 0}

    def target_loader(_origin: str) -> list[object]:
        attempts["count"] += 1
        return [_quota_page_target()]

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            # The rendered login banner is the only legitimate recopy
            # trigger: the first attempt sees it, the retry after recopy
            # reads the rebuilt session's quota normally.
            evaluations["count"] += 1
            if evaluations["count"] == 1:
                return {"kind": "unauthorized"}
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    recopied: list[Path] = []
    clock = iter([0.0] * 16)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=target_loader,
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
        session_recopy=lambda config_dir: recopied.append(config_dir),
    )

    with caplog.at_level(logging.WARNING, logger="aacc.qwen_chrome_cdp"):
        result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == [tmp_path / "config"]
    assert attempts["count"] == 2
    assert evaluations["count"] == 2
    assert any("recopy" in record.message.casefold() for record in caplog.records)


def test_hidden_refresh_recopy_failure_surfaces_unauthorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    attempts = {"count": 0}
    evaluations = {"count": 0}

    def target_loader(_origin: str) -> list[object]:
        attempts["count"] += 1
        return [_quota_page_target()]

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            evaluations["count"] += 1
            return {"kind": "unauthorized"}

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)

    def broken_recopy(_config_dir: Path) -> None:
        raise OSError("daily profile unreadable")

    clock = iter([0.0] * 16)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=target_loader,
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        sleep=lambda _seconds: None,
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: next(clock, 61.0),
        session_recopy=broken_recopy,
    )

    with pytest.raises(QwenChromeUnauthorizedError):
        operation.run(visible=False, cancel=Event())
    assert attempts["count"] == 1
    assert evaluations["count"] == 1


def test_visible_login_never_triggers_session_recopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    recopied: list[Path] = []
    # Keep every deadline computation at t=0, then jump far past the visible
    # login deadline so the bounded unauthorized loop terminates. A Bailian
    # target that never appears means the user closed the login window, which
    # ends the operation as a user cancel long before that deadline.
    clock = iter([0.0] * 6)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        sleep=lambda _seconds: None,
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: next(clock, 999_999.0),
        session_recopy=lambda config_dir: recopied.append(config_dir),
    )

    with pytest.raises(QwenChromeLoginCancelledError):
        operation.run(visible=True, cancel=Event())
    assert recopied == []


def test_visible_login_window_close_cancels_cleanly_without_recopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Closing the login window is the user's way to abandon the flow: a page
    # target exists first, then every page target disappears for good. The
    # loop must detect a sustained absence (3 consecutive 2 s polls with
    # zero page targets of any origin) and end as a user cancel — a clean
    # Browser.close shutdown with no recopy and no unauthorized outcome —
    # instead of holding a windowless Chrome in the Dock until the 15-minute
    # login deadline.
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    browser_closes: list[str] = []
    sleeps: list[float] = []
    window_open = {"flag": True}

    def target_loader(_origin: str) -> list[object]:
        if window_open["flag"]:
            window_open["flag"] = False
            return [_quota_page_target()]
        return []  # the user closed the window: zero page targets

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            # The window is closed while the user is still logging in.
            return {"kind": "unauthorized"}

        def close_browser(self) -> None:
            browser_closes.append("close_browser")

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    recopied: list[Path] = []
    # Advance the fake clock well past every deadline so the previous
    # behaviour (retry until the login deadline) terminates this test as a
    # plain quota error instead of hanging.
    clock = iter(float(tick) for tick in range(0, 100_000, 10))
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=target_loader,
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        sleep=sleeps.append,
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: next(clock, 100_000.0),
        session_recopy=lambda config_dir: recopied.append(config_dir),
    )

    with pytest.raises(QwenChromeLoginCancelledError):
        operation.run(visible=True, cancel=Event())

    # One unauthorized poll (page still open), then three zero-target polls;
    # the third raises before sleeping.
    assert sleeps == [2.0, 2.0, 2.0]
    assert recopied == []
    assert browser_closes == ["close_browser"]
    assert process.waits >= 1


def test_login_window_cancel_is_a_cancel_outcome() -> None:
    # The session layer must treat a closed login window like a user cancel
    # (silent, no recopy, no logout state change), never as an expired
    # session.
    assert issubclass(QwenChromeLoginCancelledError, QwenChromeCancelledError)
    assert not issubclass(QwenChromeLoginCancelledError, QwenChromeUnauthorizedError)


def test_refresh_teardown_closes_browser_and_process_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    browser_closes: list[str] = []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            browser_closes.append("close_browser")

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
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [_quota_page_target()],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: 0.0,
    )

    operation.run(visible=False, cancel=Event())

    assert browser_closes == ["close_browser"]
    assert process.waits >= 1


def test_refresh_teardown_closes_browser_and_process_on_deadline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    browser_closes: list[str] = []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def close_browser(self) -> None:
            browser_closes.append("close_browser")

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    # The Bailian page never appears; the loop burns the startup deadline and
    # fails — the teardown must still close the browser and reap the process.
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 91.0])
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
        monotonic=lambda: next(ticks, 91.0),
    )

    with pytest.raises(QwenChromeQuotaError):
        operation.run(visible=False, cancel=Event())

    assert browser_closes == ["close_browser"]
    assert process.waits >= 1


def test_refresh_teardown_closes_browser_and_process_on_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    process = FakeProcess()
    browser_closes: list[str] = []
    cancel = Event()

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def close_browser(self) -> None:
            browser_closes.append("close_browser")

        def close(self) -> None:
            pass

    def cancel_on_sleep(_seconds: float) -> None:
        cancel.set()

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        sleep=cancel_on_sleep,
        chrome_process_finder=lambda _profile: [],
        monotonic=lambda: 0.0,
    )

    with pytest.raises(QwenChromeCancelledError):
        operation.run(visible=False, cancel=cancel)

    assert browser_closes == ["close_browser"]
    assert process.waits >= 1


def _make_recovery_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    evaluations: list[object],
    session_origin: str,
    recopied: list[Path],
    recopy_raises: bool = False,
    session_recopy=True,
    recheck_delay_seconds: float = 0.0,
    eager_recopy: bool = False,
):
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    cursor = iter(evaluations)

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return next(cursor)

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)

    def recopy(config_dir: Path) -> None:
        if recopy_raises:
            raise OSError("daily profile unreadable")
        recopied.append(config_dir)

    clock = iter([0.0] * 64)
    return ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=lambda _origin: [_quota_page_target()],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
        session_recopy=recopy if session_recopy else None,
        session_origin=session_origin,
        recheck_delay_seconds=recheck_delay_seconds,
        eager_recopy=eager_recopy,
    )


def test_manual_origin_banner_rechecks_before_recopy_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}, _quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
        recopied=recopied,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == []  # the recheck recovered; nothing was overwritten
    assert operation.recopy_performed is False


def test_manual_origin_confirmed_dead_falls_through_to_recopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[
            {"kind": "unauthorized"},
            {"kind": "unauthorized"},
            _quota_payload(),
        ],
        session_origin=QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
        recopied=recopied,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == [tmp_path / "config"]
    assert operation.recopy_performed is True


def test_manual_origin_without_recopy_surfaces_unauthorized_after_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}, {"kind": "unauthorized"}],
        session_origin=QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
        recopied=[],
        session_recopy=False,
    )

    with pytest.raises(QwenChromeUnauthorizedError):
        operation.run(visible=False, cancel=Event())


def test_daily_origin_banner_recopies_immediately_without_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}, _quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=recopied,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == [tmp_path / "config"]


def test_eager_recopy_copies_before_refresh_and_reports_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[_quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=recopied,
        eager_recopy=True,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == [tmp_path / "config"]
    assert operation.recopy_performed is True


def test_eager_recopy_failure_propagates_for_visible_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[_quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=[],
        recopy_raises=True,
        eager_recopy=True,
    )

    with pytest.raises(QwenChromeQuotaError):
        operation.run(visible=False, cancel=Event())


def test_eager_recopy_unauthorized_does_not_recopy_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=recopied,
        eager_recopy=True,
    )

    with pytest.raises(QwenChromeUnauthorizedError):
        operation.run(visible=False, cancel=Event())
    assert recopied == [tmp_path / "config"]  # exactly once


def test_count_page_targets_counts_any_origin() -> None:
    targets = [
        {"type": "page", "url": "https://signin.aliyun.com/login"},
        {"type": "page", "url": WORKSPACE_URL},
        {"type": "iframe", "url": "https://example.com"},
        "garbage",
    ]
    assert count_qwen_page_targets(targets) == 2
    assert count_qwen_page_targets(None) == 0
    assert count_qwen_page_targets("not a list") == 0


def test_visible_login_survives_cross_origin_redirect_without_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    listings = iter(
        [
            # Poll 1..2: the page sits on the SMS origin — not Bailian, but a
            # page target exists, so this must never count as a user cancel.
            [
                {
                    "type": "page",
                    "url": "https://signin.aliyun.com/sms",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/sms",
                }
            ],
            [
                {
                    "type": "page",
                    "url": "https://signin.aliyun.com/sms",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/sms",
                }
            ],
            # Poll 3: back on Bailian with the quota rendered.
            [_quota_page_target()],
        ]
    )
    evaluations = {"count": 0}

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            evaluations["count"] += 1
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=lambda _origin: next(listings),
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
    )

    result = operation.run(visible=True, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert evaluations["count"] == 1


def test_visible_login_cancels_when_window_truly_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    seen_page = {"flag": False}

    def target_loader(_origin: str) -> list[object]:
        if not seen_page["flag"]:
            seen_page["flag"] = True
            return [_quota_page_target()]
        return []  # the user closed the window: zero page targets

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return {"kind": "unauthorized"}  # keep polling: not yet logged in

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=target_loader,
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
    )

    with pytest.raises(QwenChromeLoginCancelledError):
        operation.run(visible=True, cancel=Event())


def test_centered_window_bounds_centers_on_screen() -> None:
    assert centered_window_bounds(2560, 1440, 1100, 700) == (730, 370, 1100, 700)
    # A screen smaller than the window clamps to the origin, never negative.
    assert centered_window_bounds(800, 600, 1100, 700) == (0, 0, 1100, 700)


def test_install_visible_login_page_masks_webdriver_and_centers() -> None:
    commands: list[tuple[str, object]] = []

    class Page:
        def send_command(self, method: str, params: object) -> dict[str, object]:
            commands.append((method, params))
            if method == "Browser.getWindowForTarget":
                return {"result": {"windowId": 7}}
            return {"result": {}}

    install_qwen_visible_login_page(Page(), (100, 50, 1100, 700))

    methods = [method for method, _ in commands]
    assert "Page.enable" in methods
    assert "Page.addScriptToEvaluateOnNewDocument" in methods
    assert "Page.reload" in methods
    bounds_commands = [params for method, params in commands if method == "Browser.setWindowBounds"]
    assert bounds_commands == [
        {"windowId": 7, "bounds": {"left": 100, "top": 50, "width": 1100, "height": 700}}
    ]
    script = dict(
        (method, params)
        for method, params in commands
        if method == "Page.addScriptToEvaluateOnNewDocument"
    )["Page.addScriptToEvaluateOnNewDocument"]
    assert "webdriver" in script["source"]


def test_visible_login_applies_bounds_and_stealth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    commands: list[str] = []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, method: str, _params: object) -> dict[str, object]:
            commands.append(method)
            if method == "Browser.getWindowForTarget":
                return {"result": {"windowId": 3}}
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=lambda _origin: [_quota_page_target()],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
        visible_window_bounds=(730, 370, 1100, 700),
    )

    result = operation.run(visible=True, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert "Page.addScriptToEvaluateOnNewDocument" in commands
    assert "Browser.setWindowBounds" in commands


def test_operation_force_kills_surviving_profile_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    survivor = FakeChromeProcess(stubborn=False, pid=9001)
    state = {"shutdown_done": False, "forced": False}

    def finder(_profile: Path) -> list[FakeChromeProcess]:
        # Graceful shutdown has exited but one process is still alive; it
        # disappears only once the forced-termination pass has run.
        if state["shutdown_done"] and not state["forced"]:
            return [survivor]
        return []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)

    def forced_terminator(_profile: Path, process_finder=None) -> None:
        del process_finder
        # The pre-launch cleanup also calls the terminator while the finder
        # is empty; only mark the forced pass once a survivor is actually
        # present (post-shutdown).
        if finder(_profile):
            state["forced"] = True

    monkeypatch.setattr(module, "terminate_qwen_chrome_profile_processes", forced_terminator)

    process = FakeProcess()

    def marking_wait(timeout: float | None = None) -> int:
        del timeout
        state["shutdown_done"] = True
        return 0

    process.wait = marking_wait  # graceful shutdown reports success
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [_quota_page_target()],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=finder,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
    )

    with caplog.at_level(logging.WARNING, logger="aacc.qwen_chrome_cdp"):
        operation.run(visible=True, cancel=Event())

    assert state["forced"] is True
    assert any("remained" in record.message.casefold() for record in caplog.records)


def test_verify_profile_processes_logs_error_for_stubborn_survivors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import aacc.qwen_chrome_cdp as module

    stubborn = FakeChromeProcess(stubborn=True, pid=1)

    def no_op_terminator(_profile: Path, process_finder=None) -> None:
        del process_finder

    monkeypatch.setattr(module, "terminate_qwen_chrome_profile_processes", no_op_terminator)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(_make_chrome_profile(tmp_path), FakeProcess()),
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        chrome_process_finder=lambda _profile: [stubborn],
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    with caplog.at_level(logging.ERROR, logger="aacc.qwen_chrome_cdp"):
        operation._verify_profile_processes_gone()

    assert any("survived" in record.message.casefold() for record in caplog.records)
