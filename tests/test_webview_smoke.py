from __future__ import annotations

from pathlib import Path


def test_smoke_result_uses_platform_independent_lf_evidence(monkeypatch, tmp_path) -> None:
    from aacc.webview_smoke import (  # noqa: PLC0415 - module is the behavior under test
        SMOKE_RESULT_PATH_ENV,
        _record_result,
    )

    result_path = tmp_path / "native-webview-result.txt"
    monkeypatch.setenv(SMOKE_RESULT_PATH_ENV, str(result_path))
    open_calls: list[dict[str, object]] = []
    original_open = type(result_path).open

    def recording_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        open_calls.append(dict(kwargs))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(result_path), "open", recording_open)

    assert _record_result("success") is True
    assert open_calls[-1]["newline"] == "\n"
    assert result_path.read_bytes() == b"AACC_WEBVIEW_SMOKE category=success\n"


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


def test_smoke_reuses_production_native_webview_initialization(monkeypatch) -> None:
    import aacc.webview_smoke as webview_smoke

    calls: list[tuple[str, Path] | str] = []
    data_dir = Path("C:/isolated/AACC")
    monkeypatch.setattr(
        webview_smoke,
        "initialize_native_webview",
        lambda path: calls.append(("initialize", path)),
    )

    class FakeApplication:
        def __init__(self, _argv):
            calls.append("application")

    class FakeSmoke:
        def __init__(self, _app):
            calls.append("smoke")

        def run(self) -> int:
            return 0

    monkeypatch.setattr(webview_smoke, "QApplication", FakeApplication)
    monkeypatch.setattr(webview_smoke, "NativeWebViewSmoke", FakeSmoke)

    assert webview_smoke.run_native_webview_smoke(data_dir) == 0
    assert calls == [
        ("initialize", data_dir),
        "application",
        "smoke",
    ]


def test_smoke_storage_failure_returns_fixed_sanitized_evidence(
    monkeypatch, tmp_path, capsys
) -> None:
    import aacc.webview_smoke as webview_smoke
    from aacc.file_security import FileProtectionError

    result_path = tmp_path / "native-webview-result.txt"
    monkeypatch.setenv(webview_smoke.SMOKE_RESULT_PATH_ENV, str(result_path))
    monkeypatch.setattr(
        webview_smoke,
        "initialize_native_webview",
        lambda _path: (_ for _ in ()).throw(FileProtectionError(r"C:\Users\private token=secret")),
    )

    assert webview_smoke.run_native_webview_smoke(tmp_path) == webview_smoke.EXIT_FAILURE
    captured = capsys.readouterr()
    assert captured.err == "AACC_WEBVIEW_SMOKE category=storage-protection-failed\n"
    assert "private" not in captured.err
    assert result_path.read_text(encoding="utf-8") == (
        "AACC_WEBVIEW_SMOKE category=storage-protection-failed\n"
    )
