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


def test_kimi_web_session_docs_describe_native_store_and_reuse_gate_honestly() -> None:
    english_names = (
        "README.md",
        "docs/user-guide.en.md",
        "docs/release-notes-1.4.2.md",
        "docs/windows-verification-checklist.en.md",
        "docs/superpowers/specs/2026-07-27-kimi-web-quota-readable-bars-design.md",
    )
    chinese_names = (
        "README.zh-CN.md",
        "docs/user-guide.md",
        "docs/release-notes-1.4.2.md",
        "docs/windows-verification-checklist.zh-CN.md",
    )
    english = "\n".join(_read(name) for name in english_names)
    chinese = "\n".join(_read(name) for name in chinese_names)

    for term in (
        "native per-application WebView store",
        "protected reuse decision",
        "synchronously disables reuse",
        "bounded native site-data cleanup",
        "same five-minute cycle",
        "no generation tokens",
        "trustworthy reset",
        "macOS and Windows manual sign-off",
    ):
        assert term in english
    for term in (
        "原生的每应用 WebView 存储",
        "受保护的复用决定",
        "同步关闭复用",
        "有界的原生站点数据清理",
        "同一个五分钟周期",
        "不消耗生成 Token",
        "可信重置时间",
        "macOS 与 Windows 人工签字",
    ):
        assert term in chinese

    for text in (english, chinese):
        assert "%APPDATA%\\AACC" in text
        assert "cookie" in text.casefold()

    for name in english_names:
        text = _read(name)
        assert "website bearer token" in text.casefold()
        assert "Kimi Code OAuth" in text
    for name in chinese_names:
        text = _read(name)
        assert "网页 Bearer Token" in text
        assert "Kimi Code OAuth" in text

    assert "including settings, history, and the cached Kimi web session" not in english
    assert (
        "including configuration, task history, database, and the cached Kimi membership session"
        not in english
    )
    assert "其中包括设置、历史和缓存的 Kimi 网页会话" not in chinese
    assert "其中包括配置、任务历史、数据库与缓存的 Kimi 会员会话" not in chinese


def test_old_kimi_web_quota_design_is_explicitly_superseded() -> None:
    design = _read("docs/superpowers/specs/2026-07-27-kimi-web-quota-readable-bars-design.md")

    assert "Status: Superseded" in design
    assert "2026-07-27-kimi-web-session-correction-design.md" in design
    assert "nextBillingTime" not in design
    assert "native per-application WebView store" in design
    assert re.search(r"protected\s+reuse\s+decision", design)
