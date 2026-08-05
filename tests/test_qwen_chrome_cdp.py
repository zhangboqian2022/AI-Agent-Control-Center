from pathlib import Path
from threading import Event

import pytest

from aacc.qwen_chrome_cdp import (
    ManagedQwenChromeOperation,
    QwenChromeCancelledError,
    QwenChromeMissingError,
    QwenChromeQuotaError,
    QwenChromeUnauthorizedError,
    build_qwen_chrome_launch,
    clear_owned_qwen_chrome_profile,
    find_qwen_chrome_executable,
    parse_qwen_chrome_payload,
    qwen_chrome_profile_path,
    qwen_dom_extract_expression,
    select_qwen_target,
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


def test_launch_spec_is_shell_free_and_uses_workspace_url(tmp_path: Path) -> None:
    executable = Path("chrome")
    spec = build_qwen_chrome_launch(executable, tmp_path, WORKSPACE_URL, visible=False)

    assert spec.executable == executable
    assert f"--user-data-dir={tmp_path}" in spec.arguments
    assert "--remote-debugging-address=127.0.0.1" in spec.arguments
    assert "--headless=new" in spec.arguments
    assert WORKSPACE_URL in spec.arguments

    visible_spec = build_qwen_chrome_launch(executable, tmp_path, WORKSPACE_URL, visible=True)
    assert "--headless=new" not in visible_spec.arguments


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
        monotonic=lambda: 0.0,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result == {
        "personalFiveHourText": "5小时限额\n0.04%已用",
        "personalWeeklyText": "7天限额\n65%已用",
        "teamTotalText": None,
    }
    assert process.waits == 1


def test_managed_operation_headless_unauthorized_is_bounded(
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
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 61.0])
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        sleep=lambda _seconds: None,
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
