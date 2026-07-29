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


def test_each_bilingual_product_document_keeps_the_live_language_contract() -> None:
    english_terms = (
        "Chinese/English",
        "immediately",
        "Compact mode remains in Settings and the tray menu",
        "does not refresh quotas or change monitored tasks or login state",
        "candidate",
    )
    chinese_terms = (
        "中英文",
        "即时切换",
        "紧凑模式保留在设置和托盘菜单",
        "不会刷新额度，也不会改变监控任务或登录状态",
        "候选",
    )

    for name in ("README.md", "docs/user-guide.en.md", "CHANGELOG.md"):
        text = _read(name)
        for term in english_terms:
            assert term.casefold() in text.casefold(), f"{name} must describe {term!r}"

    for name in ("README.zh-CN.md", "docs/user-guide.md", "CHANGELOG.zh-CN.md"):
        text = _read(name)
        for term in chinese_terms:
            assert term.casefold() in text.casefold(), f"{name} must describe {term!r}"


def test_screenshot_uses_explicit_synthetic_chinese_locale() -> None:
    script = _read("scripts/capture_panel_screenshot.py")

    assert "LanguageManager(ZH_CN" in script
    assert "language_manager=language_manager" in script
    assert 'findChild(QPushButton, "languageButton")' in script


def test_bilingual_manual_gates_cover_repeated_live_switching() -> None:
    english_checklist = _read("docs/windows-verification-checklist.en.md")
    chinese_checklist = _read("docs/windows-verification-checklist.zh-CN.md")
    notes = _read("docs/release-notes-1.4.2.md")

    assert re.search(
        r"- \[ \].*repeated.*language.*real tasks.*quota.*before and after Kimi login",
        english_checklist,
        re.DOTALL | re.IGNORECASE,
    )
    assert re.search(
        r"- \[ \].*Kimi 登录前后.*反复.*语言.*真实任务.*额度",
        chinese_checklist,
        re.DOTALL,
    )
    assert re.search(
        r"- \[ \].*macOS.*Chinese/English.*Kimi login",
        notes,
        re.DOTALL | re.IGNORECASE,
    )
    assert re.search(
        r"- \[ \].*macOS.*中英文.*Kimi 登录",
        notes,
        re.DOTALL,
    )


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


def test_windows_docs_cover_dynamic_codex_refresh_and_explicit_quit() -> None:
    english = "\n".join(
        _read(name)
        for name in (
            "README.md",
            "docs/user-guide.en.md",
            "docs/release-notes-1.4.2.md",
            "docs/windows-verification-checklist.en.md",
        )
    )
    chinese = "\n".join(
        _read(name)
        for name in (
            "README.zh-CN.md",
            "docs/user-guide.md",
            "docs/release-notes-1.4.2.md",
            "docs/windows-verification-checklist.zh-CN.md",
        )
    )

    for term in (
        "every 60 seconds",
        "opened or restarted after AACC",
        "right-click",
        "power button",
        "Quit AACC",
    ):
        assert term in english
    for term in (
        "每 60 秒",
        "在 AACC 之后打开或重启",
        "右键",
        "电源按钮",
        "退出 AACC",
    ):
        assert term in chinese


def test_kimi_session_docs_describe_platform_store_and_reuse_gate_honestly() -> None:
    windows_english_names = (
        "README.md",
        "docs/user-guide.en.md",
        "docs/release-notes-1.4.2.md",
        "docs/windows-verification-checklist.en.md",
    )
    windows_chinese_names = (
        "README.zh-CN.md",
        "docs/user-guide.md",
        "docs/release-notes-1.4.2.md",
        "docs/windows-verification-checklist.zh-CN.md",
    )
    english = "\n".join(_read(name) for name in windows_english_names)
    chinese = "\n".join(_read(name) for name in windows_chinese_names)

    for term in (
        "AACC-owned Edge profile",
        "%LOCALAPPDATA%\\AACC\\kimi-edge-profile",
        "normal Edge profile",
        "until you sign out",
        "protected reuse decision",
        "five-minute",
        "no generation tokens",
        "trustworthy reset",
        "macOS and Windows manual sign-off",
    ):
        assert term in english
    for term in (
        "AACC 专用 Edge 配置目录",
        "%LOCALAPPDATA%\\AACC\\kimi-edge-profile",
        "日常 Edge 配置",
        "直到手动退出",
        "受保护的复用决定",
        "五分钟",
        "不消耗生成 Token",
        "可信重置时间",
        "macOS 与 Windows 人工签字",
    ):
        assert term in chinese

    for text in (english, chinese):
        assert "%APPDATA%\\AACC" in text
        assert "cookie" in text.casefold()

    mac_design = _read("docs/superpowers/specs/2026-07-27-kimi-web-quota-readable-bars-design.md")
    assert "native per-application WebView store" in mac_design
    assert "WebView2" not in english
    assert "WebView2" not in chinese

    for name in windows_english_names:
        text = _read(name)
        assert re.search(r"website\s+bearer token", text, re.IGNORECASE)
        assert "Kimi Code OAuth" in text
    for name in windows_chinese_names:
        text = _read(name)
        assert re.search(r"网页\s+Bearer Token", text)
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


def test_superseded_kimi_plan_and_windows_setup_design_use_correct_session_boundary() -> None:
    plan = _read("docs/superpowers/plans/2026-07-27-kimi-web-quota-readable-bars.md")
    windows_design = _read("docs/superpowers/specs/2026-07-27-windows-stable-setup-design.md")

    assert "Status: Superseded by" in plan
    assert "2026-07-27-kimi-web-session-correction.md" in plan
    assert "Do not execute this plan" in plan
    assert "profile path is protected" not in plan
    assert "clears cookies, HTTP cache, and persistent storage" not in plan
    assert re.search(r"does not expose a configurable\s+profile path", plan)

    normalized_design = " ".join(windows_design.split())
    for term in (
        "AACC-owned",
        "protected reuse decision",
        "Kimi Code OAuth credentials",
        "native WebView store",
        "no claim that Setup preserves or removes",
    ):
        assert term in normalized_design
    assert "the cached Kimi login survive" not in normalized_design

    readme = _read("README.md")
    assert "Kimi Code OAuth credentials are stored separately" in readme
    assert "Kimi Code OAuth credentials are unaffected" not in readme
