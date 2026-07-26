from pathlib import Path

from aacc.constants import default_app_support_dir


def test_app_support_dir_macos() -> None:
    assert default_app_support_dir("darwin", None) == (
        Path.home() / "Library" / "Application Support" / "AACC"
    )


def test_app_support_dir_windows_prefers_appdata() -> None:
    assert default_app_support_dir("win32", r"C:\Users\u\AppData\Roaming") == (
        Path(r"C:\Users\u\AppData\Roaming") / "AACC"
    )


def test_app_support_dir_windows_falls_back_to_home() -> None:
    assert default_app_support_dir("win32", None) == (
        Path.home() / "AppData" / "Roaming" / "AACC"
    )
