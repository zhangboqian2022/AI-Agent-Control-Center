from __future__ import annotations

from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.opencode_web_error import (
    OpenCodeQuotaErrorCategory,
    normalize_opencode_quota_error_category,
    opencode_quota_error_text,
)


def test_normalize_accepts_enum_and_string() -> None:
    assert (
        normalize_opencode_quota_error_category(OpenCodeQuotaErrorCategory.UNAUTHORIZED)
        is OpenCodeQuotaErrorCategory.UNAUTHORIZED
    )
    assert (
        normalize_opencode_quota_error_category("refresh_timeout")
        is OpenCodeQuotaErrorCategory.REFRESH_TIMEOUT
    )


def test_normalize_unknown_falls_back_to_refresh_failed() -> None:
    assert (
        normalize_opencode_quota_error_category("bogus")
        is OpenCodeQuotaErrorCategory.REFRESH_FAILED
    )
    assert (
        normalize_opencode_quota_error_category(None) is OpenCodeQuotaErrorCategory.REFRESH_FAILED
    )


def test_error_text_maps_both_languages() -> None:
    zh = LanguageManager(ZH_CN)
    en = LanguageManager(EN_US)
    assert opencode_quota_error_text("unauthorized", zh) == "OpenCode 登录已过期，请重新授权"
    assert opencode_quota_error_text("unauthorized", en) == (
        "OpenCode sign-in expired. Please authorize again"
    )
    assert opencode_quota_error_text("refresh_timeout", zh) == "OpenCode 额度刷新超时"
