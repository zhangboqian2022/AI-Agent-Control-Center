from __future__ import annotations

from aacc.kimi_web_session import (
    KIMI_MEMBERSHIP_URL,
    KimiWebSession,
    membership_fetch_script,
)


def test_membership_script_reads_both_connect_services_without_credentials():
    script = membership_fetch_script()

    assert "GetSubscriptionStats" in script
    assert "GetSubscription" in script
    assert "credentials: 'include'" in script
    assert "Authorization" not in script
    assert "access_token" not in script
    assert KIMI_MEMBERSHIP_URL.startswith("https://www.kimi.com/")


def test_web_session_uses_native_system_webview(qapp, tmp_path):
    session = KimiWebSession(tmp_path)

    assert type(session.view).__name__ == "QWebView"
    assert session.storage_path == tmp_path / "kimi-web-session"
    assert session.storage_path.is_dir()

    session.close()
