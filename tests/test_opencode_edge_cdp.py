from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from aacc.opencode_edge_cdp import (
    ManagedOpenCodeEdgeOperation,
    OpenCodeEdgeCancelledError,
    OpenCodeEdgeQuotaError,
    OpenCodeEdgeUnauthorizedError,
    build_opencode_edge_launch,
    clear_owned_opencode_profile,
    opencode_dom_extract_expression,
    opencode_edge_profile_path,
    parse_opencode_edge_payload,
    select_opencode_target,
    validate_owned_opencode_profile,
)

WORKSPACE_URL = "https://opencode.ai/workspace/wrk_123/go"


def test_profile_path_is_separate_from_kimi(tmp_path: Path) -> None:
    assert opencode_edge_profile_path(tmp_path) == tmp_path / "AACC" / "opencode-edge-profile"


def test_launch_spec_is_shell_free_and_uses_workspace_url(tmp_path: Path) -> None:
    executable = Path("msedge.exe")
    spec = build_opencode_edge_launch(executable, tmp_path, WORKSPACE_URL, visible=False)

    assert spec.executable == executable
    assert f"--user-data-dir={tmp_path}" in spec.arguments
    assert "--remote-debugging-address=127.0.0.1" in spec.arguments
    assert "--headless=new" in spec.arguments
    assert f"--app={WORKSPACE_URL}" in spec.arguments


def test_dom_expression_extracts_only_allowed_quota_shape() -> None:
    expression = opencode_dom_extract_expression(WORKSPACE_URL)

    assert "document.body" in expression
    assert "usagePercent" in expression
    assert "resetInSec" in expression
    assert "kind: 'unauthorized'" in expression
    assert "document.cookie" not in expression
    assert WORKSPACE_URL not in expression


def test_select_target_accepts_loopback_workspace_page() -> None:
    target = select_opencode_target(
        [
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
            }
        ],
        expected_port=9222,
        expected_workspace_url=WORKSPACE_URL,
    )

    assert target.endswith("/devtools/page/abc")


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
    with pytest.raises(OpenCodeEdgeQuotaError):
        select_opencode_target([target], expected_port=9222, expected_workspace_url=WORKSPACE_URL)


def test_parse_edge_payload_allowlists_windows() -> None:
    result = parse_opencode_edge_payload(
        {
            "kind": "quota",
            "raw": {
                "subscription": {
                    "rollingUsage": {"usagePercent": 0, "resetInSec": 10},
                    "weeklyUsage": {"usagePercent": 42, "resetInSec": 20},
                    "monthlyUsage": {"usagePercent": 100, "resetInSec": None},
                }
            },
        }
    )

    assert result["subscription"]["rollingUsage"]["usagePercent"] == 0
    assert result["subscription"]["monthlyUsage"]["resetInSec"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "error", "message": "DOM_TIMEOUT"},
        {"kind": "quota", "raw": {"subscription": {"rollingUsage": {"usagePercent": 101}}}},
        {"kind": "quota", "raw": {"subscription": {"rollingUsage": {"usagePercent": True}}}},
        {"kind": "quota", "raw": {"subscription": {"rollingUsage": {"resetInSec": -1}}}},
        {"kind": "quota", "raw": {"subscription": {"secret": {"token": "x"}}}},
        {"kind": "quota", "raw": {"subscription": {}}},
    ],
)
def test_parse_edge_payload_rejects_untrusted_values(payload: object) -> None:
    with pytest.raises((OpenCodeEdgeQuotaError, OpenCodeEdgeUnauthorizedError)):
        parse_opencode_edge_payload(payload)


def test_parse_edge_payload_maps_expired_session_to_unauthorized() -> None:
    with pytest.raises(OpenCodeEdgeUnauthorizedError):
        parse_opencode_edge_payload({"kind": "unauthorized"})


def test_cancelled_event_is_observable_as_a_standard_event() -> None:
    assert Event().is_set() is False


def test_target_must_match_the_configured_workspace_and_url_has_no_suffixes() -> None:
    other_workspace = {
        "type": "page",
        "url": "https://opencode.ai/workspace/other/go",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
    }
    with pytest.raises(OpenCodeEdgeUnauthorizedError):
        select_opencode_target(
            [other_workspace], expected_port=9222, expected_workspace_url=WORKSPACE_URL
        )
    with pytest.raises(ValueError):
        opencode_dom_extract_expression(f"{WORKSPACE_URL}?unexpected=1")
    with pytest.raises(ValueError):
        opencode_dom_extract_expression(f"{WORKSPACE_URL};unexpected")


def test_select_target_skips_foreign_pages_before_configured_workspace() -> None:
    target = select_opencode_target(
        [
            {
                "type": "page",
                "url": "https://example.com/",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/foreign",
            },
            {
                "type": "page",
                "url": WORKSPACE_URL,
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/owned",
            },
        ],
        expected_port=9222,
        expected_workspace_url=WORKSPACE_URL,
    )

    assert target.endswith("/devtools/page/owned")


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


def _make_edge_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "local" / "AACC" / "opencode-edge-profile"
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
    import aacc.opencode_edge_cdp as module

    profile = _make_edge_profile(tmp_path)
    process = FakeProcess()
    payload = {
        "kind": "quota",
        "raw": {
            "subscription": {
                "rollingUsage": {"usagePercent": 1},
                "weeklyUsage": {"usagePercent": 2},
                "monthlyUsage": {"usagePercent": 3},
            }
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
    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
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
        "subscription": {
            "rollingUsage": {"usagePercent": 1.0, "resetInSec": None},
            "weeklyUsage": {"usagePercent": 2.0, "resetInSec": None},
            "monthlyUsage": {"usagePercent": 3.0, "resetInSec": None},
        }
    }
    assert process.waits == 1


def test_managed_operation_headless_unauthorized_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.opencode_edge_cdp as module

    profile = _make_edge_profile(tmp_path)
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
    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        sleep=lambda _seconds: None,
        monotonic=lambda: next(ticks, 61.0),
    )

    with pytest.raises(OpenCodeEdgeUnauthorizedError):
        operation.run(visible=False, cancel=Event())


def test_managed_operation_rejects_unsafe_profile(tmp_path: Path) -> None:
    local = tmp_path / "local"
    expected = local / "AACC" / "opencode-edge-profile"
    expected.parent.mkdir(parents=True)
    expected.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=local,
        executable=Path("msedge.exe"),
    )

    with pytest.raises(OpenCodeEdgeQuotaError):
        operation.run(visible=False, cancel=Event())


def test_profile_validation_and_logout_are_owned_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.opencode_edge_cdp as module

    local = tmp_path / "local"
    profile = opencode_edge_profile_path(local)
    profile.mkdir(parents=True)
    (profile / "cookie").write_text("private", encoding="utf-8")
    clear_owned_opencode_profile(profile, local)
    assert not profile.exists()

    profile.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OpenCodeEdgeQuotaError):
        validate_owned_opencode_profile(profile, local)

    profile.unlink()
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))
    profile.mkdir(parents=True)
    with pytest.raises(OpenCodeEdgeQuotaError):
        clear_owned_opencode_profile(profile, local)

    missing_local = tmp_path / "missing"
    clear_owned_opencode_profile(opencode_edge_profile_path(missing_local), missing_local)


def test_profile_logout_retries_quarantined_directory_after_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.opencode_edge_cdp as module

    local = tmp_path / "local"
    profile = opencode_edge_profile_path(local)
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
    with pytest.raises(OpenCodeEdgeQuotaError):
        clear_owned_opencode_profile(profile, local)
    assert not profile.exists()
    assert list(profile.parent.glob(".opencode-edge-profile.logout-*"))

    monkeypatch.setattr(module.shutil, "rmtree", real_rmtree)
    clear_owned_opencode_profile(profile, local)
    assert not list(profile.parent.glob(".opencode-edge-profile.logout-*"))


def test_profile_validation_rejects_reparse_parent_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.opencode_edge_cdp as module

    local = tmp_path / "local"
    profile = opencode_edge_profile_path(local)
    profile.mkdir(parents=True)
    monkeypatch.setattr(module, "_is_reparse_point", lambda path: path in {local, profile})

    with pytest.raises(OpenCodeEdgeQuotaError):
        validate_owned_opencode_profile(profile, local)


@pytest.mark.parametrize("targets", [None, {}, ["invalid"], [{"type": "other"}]])
def test_target_selection_rejects_malformed_target_lists(targets: object) -> None:
    with pytest.raises((OpenCodeEdgeQuotaError, OpenCodeEdgeUnauthorizedError)):
        select_opencode_target(
            targets,
            expected_port=9222,
            expected_workspace_url=WORKSPACE_URL,
        )


def test_target_selection_rejects_matching_page_without_socket() -> None:
    with pytest.raises(OpenCodeEdgeQuotaError):
        select_opencode_target(
            [{"type": "page", "url": WORKSPACE_URL}],
            expected_port=9222,
            expected_workspace_url=WORKSPACE_URL,
        )


@pytest.mark.parametrize(
    "payload",
    [None, {"kind": "quota", "raw": None}, {"kind": "quota", "raw": {"subscription": None}}],
)
def test_payload_parser_rejects_missing_outer_shapes(payload: object) -> None:
    with pytest.raises(OpenCodeEdgeQuotaError):
        parse_opencode_edge_payload(payload)


def test_payload_parser_rejects_non_finite_and_invalid_reset_values() -> None:
    base = {
        "rollingUsage": {"usagePercent": float("nan")},
        "weeklyUsage": {"usagePercent": 1},
        "monthlyUsage": {"usagePercent": 2},
    }
    with pytest.raises(OpenCodeEdgeQuotaError):
        parse_opencode_edge_payload({"kind": "quota", "raw": {"subscription": base}})


def test_managed_operation_rejects_cancelled_or_failed_start(
    tmp_path: Path,
) -> None:
    profile = _make_edge_profile(tmp_path)
    cancelled = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        protector=lambda _profile: None,
    )
    cancel = Event()
    cancel.set()
    with pytest.raises(OpenCodeEdgeCancelledError):
        cancelled.run(visible=False, cancel=cancel)

    protected_failure = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        protector=lambda _profile: (_ for _ in ()).throw(RuntimeError("ACL")),
    )
    with pytest.raises(OpenCodeEdgeQuotaError):
        protected_failure.run(visible=False, cancel=Event())

    process_failure = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        protector=lambda _profile: None,
        process_factory=lambda _command: (_ for _ in ()).throw(RuntimeError("start")),
    )
    with pytest.raises(OpenCodeEdgeQuotaError):
        process_failure.run(visible=False, cancel=Event())
    assert profile.exists()


def test_managed_operation_rejects_dead_process_and_endpoint_without_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.opencode_edge_cdp as module

    profile = _make_edge_profile(tmp_path)

    class DeadProcess(FakeProcess):
        def poll(self) -> int | None:
            return 1

    dead = DeadProcess()
    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, dead),
        monotonic=lambda: 0.0,
    )
    with pytest.raises(OpenCodeEdgeQuotaError):
        operation.run(visible=False, cancel=Event())

    class EndpointWithoutPort:
        http_origin = "http://127.0.0.1"
        browser_websocket = "ws://127.0.0.1/devtools/browser/id"

    class FakeBrowser:
        def __init__(self, _socket: object) -> None:
            pass

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeBrowser)

    live = FakeProcess()
    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, live),
        socket_factory=lambda _url: object(),
    )
    operation._wait_for_endpoint = lambda _process, _cancel: EndpointWithoutPort()  # type: ignore[method-assign]
    with pytest.raises(OpenCodeEdgeQuotaError):
        operation.run(visible=False, cancel=Event())


def test_managed_operation_wait_and_shutdown_paths_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.opencode_edge_cdp as module

    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        sleep=lambda _seconds: None,
        monotonic=lambda: 16.0,
    )
    process = FakeProcess()
    cancel = Event()
    cancel.set()
    with pytest.raises(OpenCodeEdgeCancelledError):
        operation._wait_for_endpoint(process, cancel)

    class ErrorProcess(FakeProcess):
        def poll(self) -> int | None:
            return 1

    with pytest.raises(OpenCodeEdgeQuotaError):
        operation._wait_for_endpoint(ErrorProcess(), Event())

    ticks = iter([16.0, 16.0, 40.0])
    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        sleep=lambda _seconds: None,
        monotonic=lambda: next(ticks),
    )
    monkeypatch.setattr(
        module,
        "read_devtools_endpoint",
        lambda _profile: (_ for _ in ()).throw(RuntimeError()),
    )
    with pytest.raises(OpenCodeEdgeQuotaError):
        operation._wait_for_endpoint(process, Event())

    class StubbornProcess(FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            del timeout
            raise TimeoutError

    terminated: list[int] = []
    operation = ManagedOpenCodeEdgeOperation(
        WORKSPACE_URL,
        local_app_data=tmp_path / "local",
        executable=Path("msedge.exe"),
        process_tree_terminator=lambda proc: terminated.append(proc.pid),
    )
    operation._shutdown_process(StubbornProcess())
    assert terminated == [123]
