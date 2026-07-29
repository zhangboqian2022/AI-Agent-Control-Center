from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

import pytest

from aacc.kimi_web_error import KimiWebErrorCategory


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


class FakeProcess:
    def __init__(self) -> None:
        self.return_code: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.return_code = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 1


def test_edge_profile_is_isolated_under_local_app_data(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import edge_profile_path

    assert edge_profile_path(tmp_path) == tmp_path / "AACC" / "kimi-edge-profile"


def test_edge_discovery_accepts_existing_explicit_program_files_path(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import find_edge_executable

    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")

    found = find_edge_executable(
        environ={"PROGRAMFILES(X86)": str(tmp_path)},
        registry_reader=lambda _key, _name: None,
    )

    assert found == edge


def test_edge_discovery_rejects_reparse_point(tmp_path: Path, monkeypatch) -> None:
    import aacc.kimi_edge_cdp as edge_cdp

    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")
    monkeypatch.setattr(edge_cdp, "_is_reparse_point", lambda candidate: candidate == edge)

    with pytest.raises(edge_cdp.EdgeSessionError) as raised:
        edge_cdp.find_edge_executable(
            environ={"PROGRAMFILES(X86)": str(tmp_path)},
            registry_reader=lambda _key, _name: None,
        )

    assert raised.value.category is KimiWebErrorCategory.LOAD_FAILED
    assert str(edge) not in str(raised.value)


def test_profile_validation_rejects_reparse_point(tmp_path: Path, monkeypatch) -> None:
    import aacc.kimi_edge_cdp as edge_cdp

    profile = edge_cdp.edge_profile_path(tmp_path)
    profile.mkdir(parents=True)
    monkeypatch.setattr(edge_cdp, "_is_reparse_point", lambda candidate: candidate == profile)

    with pytest.raises(edge_cdp.EdgeSessionError) as raised:
        edge_cdp.validate_owned_profile(profile, tmp_path)

    assert raised.value.category is KimiWebErrorCategory.LOAD_FAILED


def test_background_launch_uses_random_loopback_cdp_and_dedicated_profile(
    tmp_path: Path,
) -> None:
    from aacc.kimi_edge_cdp import KIMI_MEMBERSHIP_URL, build_edge_launch

    spec = build_edge_launch(Path("C:/Edge/msedge.exe"), tmp_path, visible=False)

    assert "--remote-debugging-address=127.0.0.1" in spec.arguments
    assert "--remote-debugging-port=0" in spec.arguments
    assert "--headless=new" in spec.arguments
    assert all("Default" not in argument for argument in spec.arguments)
    assert spec.arguments[-1] == f"--app={KIMI_MEMBERSHIP_URL}"


def test_visible_launch_does_not_request_headless_mode(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import build_edge_launch

    spec = build_edge_launch(Path("C:/Edge/msedge.exe"), tmp_path, visible=True)

    assert "--headless=new" not in spec.arguments
    assert "--disable-gpu" not in spec.arguments


def test_devtools_endpoint_uses_active_port_and_loopback(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import read_devtools_endpoint

    (tmp_path / "DevToolsActivePort").write_text(
        "43127\n/devtools/browser/browser-id\n",
        encoding="utf-8",
    )

    endpoint = read_devtools_endpoint(tmp_path)

    assert endpoint.http_origin == "http://127.0.0.1:43127"
    assert (
        endpoint.browser_websocket
        == "ws://127.0.0.1:43127/devtools/browser/browser-id"
    )


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "0\n/devtools/browser/id\n",
        "65536\n/devtools/browser/id\n",
        "43127\nhttp://remote.invalid/devtools/browser/id\n",
        "43127\n/devtools/page/id\n",
    ],
)
def test_devtools_endpoint_rejects_untrusted_contents(tmp_path: Path, contents: str) -> None:
    from aacc.kimi_edge_cdp import EdgeSessionError, read_devtools_endpoint

    (tmp_path / "DevToolsActivePort").write_text(contents, encoding="utf-8")

    with pytest.raises(EdgeSessionError) as raised:
        read_devtools_endpoint(tmp_path)

    assert raised.value.category is KimiWebErrorCategory.LOAD_FAILED


def test_cdp_evaluate_ignores_events_and_matches_request_id() -> None:
    from aacc.kimi_edge_cdp import CdpConnection

    socket = FakeSocket(
        [
            '{"method":"Runtime.consoleAPICalled","params":{}}',
            '{"id":1,"result":{"result":{"type":"object","value":{"kind":"quota"}}}}',
        ]
    )
    connection = CdpConnection(socket)

    result = connection.evaluate("Promise.resolve({kind: 'quota'})")

    assert result == {"kind": "quota"}
    sent: dict[str, Any] = __import__("json").loads(socket.sent[0])
    assert sent["id"] == 1
    assert sent["method"] == "Runtime.evaluate"
    assert sent["params"]["awaitPromise"] is True
    assert sent["params"]["returnByValue"] is True


def test_cdp_rejects_oversized_message_without_echoing_contents() -> None:
    from aacc.kimi_edge_cdp import MAX_CDP_MESSAGE_BYTES, CdpConnection, EdgeSessionError

    secret = "access_token=should-never-escape"
    socket = FakeSocket(["x" * MAX_CDP_MESSAGE_BYTES + secret])
    connection = CdpConnection(socket)

    with pytest.raises(EdgeSessionError) as raised:
        connection.evaluate("1")

    assert raised.value.category is KimiWebErrorCategory.REFRESH_FAILED
    assert secret not in str(raised.value)


def test_select_kimi_target_accepts_only_loopback_websocket() -> None:
    from aacc.kimi_edge_cdp import select_kimi_target

    target = select_kimi_target(
        [
            {
                "type": "page",
                "url": "https://www.kimi.com/membership/subscription",
                "webSocketDebuggerUrl": "ws://127.0.0.1:43127/devtools/page/id",
            }
        ],
        expected_port=43127,
    )

    assert target == "ws://127.0.0.1:43127/devtools/page/id"


@pytest.mark.parametrize(
    "websocket_url",
    [
        "ws://remote.invalid:43127/devtools/page/id",
        "ws://127.0.0.1:9/devtools/page/id",
        "wss://127.0.0.1:43127/devtools/page/id",
    ],
)
def test_select_kimi_target_rejects_nonlocal_endpoint(websocket_url: str) -> None:
    from aacc.kimi_edge_cdp import EdgeSessionError, select_kimi_target

    with pytest.raises(EdgeSessionError):
        select_kimi_target(
            [
                {
                    "type": "page",
                    "url": "https://www.kimi.com/membership/subscription",
                    "webSocketDebuggerUrl": websocket_url,
                }
            ],
            expected_port=43127,
        )


def test_parse_quota_payload_returns_only_two_membership_documents() -> None:
    from aacc.kimi_edge_cdp import EdgeQuotaResult, parse_quota_payload

    stats = {"ratelimitCode5h": 0.2}
    subscription = {"plan": "max"}

    assert parse_quota_payload(
        {"kind": "quota", "stats": stats, "subscription": subscription}
    ) == EdgeQuotaResult(stats, subscription)


def test_parse_unauthorized_payload_uses_internal_exception() -> None:
    from aacc.kimi_edge_cdp import EdgeUnauthorizedError, parse_quota_payload

    with pytest.raises(EdgeUnauthorizedError):
        parse_quota_payload({"kind": "unauthorized"})


def test_managed_edge_operation_returns_quota_and_closes_browser(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import ManagedEdgeOperation

    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"MZ")
    process = FakeProcess()
    launched: list[list[str]] = []
    page_socket = FakeSocket(
        [
            (
                '{"id":1,"result":{"result":{"type":"object","value":'
                '{"kind":"quota","stats":{"five":5},"subscription":{"month":30}}}}}'
            )
        ]
    )
    browser_socket = FakeSocket(['{"id":1,"result":{}}'])

    def protect(profile: Path) -> None:
        profile.mkdir(parents=True, exist_ok=True)

    def start(command: list[str]) -> FakeProcess:
        launched.append(command)
        profile = tmp_path / "AACC" / "kimi-edge-profile"
        (profile / "DevToolsActivePort").write_text(
            "43127\n/devtools/browser/browser-id\n",
            encoding="ascii",
        )
        return process

    def open_socket(url: str) -> FakeSocket:
        return browser_socket if "/browser/" in url else page_socket

    operation = ManagedEdgeOperation(
        local_app_data=tmp_path,
        executable=edge,
        protector=protect,
        process_factory=start,
        target_loader=lambda _origin: [
            {
                "type": "page",
                "url": "https://www.kimi.com/membership/subscription",
                "webSocketDebuggerUrl": "ws://127.0.0.1:43127/devtools/page/page-id",
            }
        ],
        socket_factory=open_socket,
        sleep=lambda _seconds: None,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result.stats == {"five": 5}
    assert result.subscription == {"month": 30}
    assert launched[0][0] == str(edge)
    assert "--headless=new" in launched[0]
    assert browser_socket.sent
    assert '"method":"Browser.close"' in browser_socket.sent[0]


def test_managed_edge_operation_honors_cancellation_before_launch(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import EdgeCancelledError, ManagedEdgeOperation

    cancelled = Event()
    cancelled.set()
    operation = ManagedEdgeOperation(
        local_app_data=tmp_path,
        executable=tmp_path / "msedge.exe",
    )

    with pytest.raises(EdgeCancelledError):
        operation.run(visible=True, cancel=cancelled)
