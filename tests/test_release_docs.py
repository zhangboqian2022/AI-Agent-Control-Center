from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "docs" / "images" / "panel-overview.png"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert header[12:16] == b"IHDR"
    return struct.unpack(">II", header[16:24])


def _png_chunk_types(path: Path) -> list[bytes]:
    data = path.read_bytes()
    chunks: list[bytes] = []
    offset = 8
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunks.append(chunk_type)
        offset += 12 + length
    assert offset == len(data)
    return chunks


def test_screenshot_fixture_is_fixed_and_privacy_safe() -> None:
    script = _read("scripts/capture_panel_screenshot.py")

    assert "DEMO_NOW = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)" in script
    assert "CODEX_WEEK = 17" in script
    assert "KIMI_5H = 30" in script
    assert "KIMI_WEEK = 72" in script
    assert "KIMI_MONTH = 31" in script
    assert "window.resize(420," in script
    lowered = script.casefold()
    for forbidden in (
        "/users/",
        "c:\\users\\",
        "/home/",
        "zhangboqian",
        "sk-",
    ):
        assert forbidden not in lowered
    normalized_script = lowered.replace("\\", "/")
    normalized_home = str(Path.home()).casefold().replace("\\", "/")
    assert normalized_home not in normalized_script
    assert _png_size(SCREENSHOT) == (420, 577)
    assert set(_png_chunk_types(SCREENSHOT)) <= {b"IHDR", b"pHYs", b"IDAT", b"IEND"}


def test_readmes_caption_the_demo_immediately_and_make_setup_primary() -> None:
    cases = (
        (
            "README.md",
            "_Illustrative UI with synthetic demo data; no real account or task data._",
            ("per-user", "without administrator", "Start Menu", "SmartScreen"),
        ),
        (
            "README.zh-CN.md",
            "_使用合成演示数据生成的界面示意图，不含真实账户或任务数据。_",
            ("当前用户", "无需管理员", "开始菜单", "SmartScreen"),
        ),
    )

    for name, caption, required_terms in cases:
        text = _read(name)
        assert re.search(
            r"!\[[^\]]+\]\(docs/images/panel-overview\.png\)\n\n" + re.escape(caption),
            text,
        )
        assert "AACC-1.4.2-Setup.exe" in text
        assert "AACC-1.4.2-windows-x64.zip" not in text
        for term in required_terms:
            assert term in text


def test_release_docs_keep_windows_manual_gates_open() -> None:
    notes = _read("docs/release-notes-1.4.2.md")

    assert "AACC-1.4.2-Setup.exe" in notes
    assert "AACC-1.4.2.dmg" in notes
    assert "Windows 10" in notes and "Windows 11" in notes
    assert "Windows Server" in notes
    assert "native DACL" in notes
    assert "fixed-purpose broker" in notes
    assert "`v1.4.2` 尚未创建" in notes
    assert re.search(r"- \[ \].*Windows 10/11", notes, re.DOTALL)


def test_bilingual_guides_describe_setup_lifecycle_and_preserved_appdata() -> None:
    cases = (
        (
            "docs/user-guide.en.md",
            ("AACC-1.4.2-Setup.exe", "per-user", "%LocalAppData%", "%APPDATA%"),
        ),
        (
            "docs/user-guide.md",
            ("AACC-1.4.2-Setup.exe", "当前用户", "%LocalAppData%", "%APPDATA%"),
        ),
        (
            "docs/windows-verification-checklist.en.md",
            ("AACC-1.4.2-Setup.exe", "Windows 10", "Windows 11", "separate"),
        ),
        (
            "docs/windows-verification-checklist.zh-CN.md",
            ("AACC-1.4.2-Setup.exe", "Windows 10", "Windows 11", "另一"),
        ),
    )

    for name, required_terms in cases:
        text = _read(name)
        for term in required_terms:
            assert term in text


def test_bilingual_guides_make_desktop_controls_platform_specific() -> None:
    english = _read("docs/user-guide.en.md")
    chinese = _read("docs/user-guide.md")

    for term in ("Win+H", "system tray", "Accessibility", "no startup entry"):
        assert term in english
    for term in ("Win+H", "系统托盘", "辅助功能", "不添加开机启动项"):
        assert term in chinese
