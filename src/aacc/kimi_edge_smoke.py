"""Sanitized product smoke for the frozen Windows Edge/CDP quota path."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Protocol

from aacc.kimi_edge_cdp import (
    EdgeCancelledError,
    EdgeSessionError,
    ManagedEdgeOperation,
)
from aacc.kimi_web_error import KimiWebErrorCategory

_SMOKE_EXPRESSION = "Promise.resolve({kind:'quota',stats:{},subscription:{}})"


class _Operation(Protocol):
    def run(self, *, visible: bool, cancel: Event) -> object: ...


def run_edge_cdp_smoke(
    local_app_data: Path,
    result_path: Path,
    *,
    operation_factory: Callable[..., _Operation] = ManagedEdgeOperation,
) -> int:
    """Exercise the packaged Edge transport and write fixed, non-secret evidence."""

    category = "success"
    exit_code = 0
    try:
        operation = operation_factory(
            local_app_data=local_app_data,
            expression_factory=lambda: _SMOKE_EXPRESSION,
        )
        operation.run(visible=False, cancel=Event())
    except EdgeSessionError as error:
        category = error.category.value
        exit_code = 1
    except EdgeCancelledError:
        category = KimiWebErrorCategory.REFRESH_FAILED.value
        exit_code = 1
    except Exception:  # noqa: BLE001 - product diagnostic must stay sanitized
        category = KimiWebErrorCategory.REFRESH_FAILED.value
        exit_code = 1
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(f"AACC_EDGE_CDP_SMOKE category={category}\n".encode("ascii"))
    return exit_code
