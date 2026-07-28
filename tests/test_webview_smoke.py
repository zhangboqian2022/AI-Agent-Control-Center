from __future__ import annotations


def test_smoke_result_is_persisted_as_a_fixed_sanitized_category(monkeypatch, tmp_path) -> None:
    from aacc.webview_smoke import (  # noqa: PLC0415 - module is the behavior under test
        SMOKE_RESULT_PATH_ENV,
        _record_result,
    )

    result_path = tmp_path / "native-webview-result.txt"
    monkeypatch.setenv(SMOKE_RESULT_PATH_ENV, str(result_path))

    assert _record_result("load-failed") is True
    assert result_path.read_text(encoding="utf-8") == ("AACC_WEBVIEW_SMOKE category=load-failed\n")

    assert _record_result("secret-from-remote") is True
    assert result_path.read_text(encoding="utf-8") == (
        "AACC_WEBVIEW_SMOKE category=invalid-category\n"
    )


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
