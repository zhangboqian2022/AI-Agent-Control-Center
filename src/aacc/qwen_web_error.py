"""Normalize Bailian (Qwen Code) token-plan quota errors for display."""

from __future__ import annotations

from enum import StrEnum

from aacc.i18n import LanguageManager


class QwenQuotaErrorCategory(StrEnum):
    UNAUTHORIZED = "unauthorized"
    REFRESH_TIMEOUT = "refresh_timeout"
    REFRESH_FAILED = "refresh_failed"
    PARSE_FAILED = "parse_failed"


_ERROR_KEYS: dict[QwenQuotaErrorCategory, str] = {
    QwenQuotaErrorCategory.UNAUTHORIZED: "qwen.web_unauthorized",
    QwenQuotaErrorCategory.REFRESH_TIMEOUT: "qwen.web_refresh_timeout",
    QwenQuotaErrorCategory.REFRESH_FAILED: "qwen.web_refresh_failed",
    QwenQuotaErrorCategory.PARSE_FAILED: "qwen.web_parse_failed",
}


def normalize_qwen_quota_error_category(value: object) -> QwenQuotaErrorCategory:
    if isinstance(value, QwenQuotaErrorCategory):
        return value
    if isinstance(value, str):
        try:
            return QwenQuotaErrorCategory(value)
        except ValueError:
            pass
    return QwenQuotaErrorCategory.REFRESH_FAILED


def qwen_quota_error_text(category: object, language_manager: LanguageManager) -> str:
    normalized = normalize_qwen_quota_error_category(category)
    return language_manager.text(_ERROR_KEYS[normalized])
