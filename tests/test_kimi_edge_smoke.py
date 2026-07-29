from __future__ import annotations

from pathlib import Path

from aacc.kimi_edge_cdp import EdgeQuotaResult, EdgeSessionError
from aacc.kimi_edge_smoke import run_edge_cdp_smoke
from aacc.kimi_web_error import KimiWebErrorCategory


class SuccessfulOperation:
    def run(self, *, visible: bool, cancel: object) -> EdgeQuotaResult:
        assert visible is False
        assert hasattr(cancel, "is_set")
        return EdgeQuotaResult(stats={}, subscription={})


class FailingOperation:
    def run(self, *, visible: bool, cancel: object) -> EdgeQuotaResult:
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)


def test_edge_cdp_smoke_writes_fixed_success_evidence(tmp_path: Path) -> None:
    result_path = tmp_path / "result.txt"

    exit_code = run_edge_cdp_smoke(
        tmp_path,
        result_path,
        operation_factory=lambda **_kwargs: SuccessfulOperation(),
    )

    assert exit_code == 0
    assert result_path.read_bytes() == b"AACC_EDGE_CDP_SMOKE category=success\n"


def test_edge_cdp_smoke_writes_only_sanitized_failure_category(tmp_path: Path) -> None:
    result_path = tmp_path / "result.txt"

    exit_code = run_edge_cdp_smoke(
        tmp_path,
        result_path,
        operation_factory=lambda **_kwargs: FailingOperation(),
    )

    assert exit_code == 1
    assert result_path.read_bytes() == b"AACC_EDGE_CDP_SMOKE category=load_failed\n"
