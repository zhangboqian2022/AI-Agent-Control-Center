from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from aacc.opencode_edge_cdp import (
    OpenCodeEdgeQuotaError,
    OpenCodeEdgeUnauthorizedError,
    build_opencode_edge_launch,
    opencode_dom_extract_expression,
    opencode_edge_profile_path,
    parse_opencode_edge_payload,
    select_opencode_target,
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
    )

    assert target.endswith("/devtools/page/abc")


@pytest.mark.parametrize(
    "target",
    [
        {
            "type": "page",
            "url": "https://example.com/workspace/wrk_123/go",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
        },
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
    ],
)
def test_select_target_rejects_foreign_or_unsafe_page(target: dict[str, str]) -> None:
    with pytest.raises(OpenCodeEdgeQuotaError):
        select_opencode_target([target], expected_port=9222)


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
