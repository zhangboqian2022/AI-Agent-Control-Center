from __future__ import annotations

from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.qwen_web_error import (
    QwenQuotaErrorCategory,
    normalize_qwen_quota_error_category,
    qwen_quota_error_text,
)


def test_normalize_known_category_passthrough() -> None:
    assert (
        normalize_qwen_quota_error_category(QwenQuotaErrorCategory.UNAUTHORIZED)
        is QwenQuotaErrorCategory.UNAUTHORIZED
    )


def test_normalize_known_string() -> None:
    assert (
        normalize_qwen_quota_error_category("refresh_timeout")
        is QwenQuotaErrorCategory.REFRESH_TIMEOUT
    )


def test_normalize_unknown_falls_back_to_refresh_failed() -> None:
    assert normalize_qwen_quota_error_category("bogus") is QwenQuotaErrorCategory.REFRESH_FAILED
    assert normalize_qwen_quota_error_category(None) is QwenQuotaErrorCategory.REFRESH_FAILED


def test_error_text_bilingual() -> None:
    zh = LanguageManager(ZH_CN)
    en = LanguageManager(EN_US)
    assert qwen_quota_error_text("unauthorized", zh) == "Qwen Code 登录已过期，请重新授权"
    assert qwen_quota_error_text("unauthorized", en) == (
        "Qwen Code sign-in expired. Please authorize again"
    )
    assert qwen_quota_error_text("refresh_timeout", zh) == "Qwen Code 额度刷新超时"
