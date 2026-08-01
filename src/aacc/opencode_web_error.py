"""Normalize opencode.ai workspace usage errors for display."""

from __future__ import annotations

from enum import StrEnum

from aacc.i18n import LanguageManager


class OpenCodeQuotaErrorCategory(StrEnum):
    UNAUTHORIZED = "unauthorized"
    REFRESH_TIMEOUT = "refresh_timeout"
    REFRESH_FAILED = "refresh_failed"
    PARSE_FAILED = "parse_failed"


_ERROR_KEYS: dict[OpenCodeQuotaErrorCategory, str] = {
    OpenCodeQuotaErrorCategory.UNAUTHORIZED: "opencode.web_unauthorized",
    OpenCodeQuotaErrorCategory.REFRESH_TIMEOUT: "opencode.web_refresh_timeout",
    OpenCodeQuotaErrorCategory.REFRESH_FAILED: "opencode.web_refresh_failed",
    OpenCodeQuotaErrorCategory.PARSE_FAILED: "opencode.web_parse_failed",
}


def normalize_opencode_quota_error_category(value: object) -> OpenCodeQuotaErrorCategory:
    if isinstance(value, OpenCodeQuotaErrorCategory):
        return value
    if isinstance(value, str):
        try:
            return OpenCodeQuotaErrorCategory(value)
        except ValueError:
            pass
    return OpenCodeQuotaErrorCategory.REFRESH_FAILED


def opencode_quota_error_text(category: object, language_manager: LanguageManager) -> str:
    normalized = normalize_opencode_quota_error_category(category)
    return language_manager.text(_ERROR_KEYS[normalized])
