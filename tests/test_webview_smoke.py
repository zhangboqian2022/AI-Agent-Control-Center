from __future__ import annotations


def test_late_javascript_callback_cannot_override_a_finished_failure() -> None:
    from aacc.webview_smoke import (  # noqa: PLC0415 - module is the behavior under test
        EXIT_FAILURE,
        EXPECTED_JAVASCRIPT_RESULT,
        NativeWebViewSmoke,
    )

    smoke = object.__new__(NativeWebViewSmoke)
    smoke._finished = True
    smoke._exit_code = EXIT_FAILURE

    smoke._javascript_finished(EXPECTED_JAVASCRIPT_RESULT)

    assert smoke._exit_code == EXIT_FAILURE
