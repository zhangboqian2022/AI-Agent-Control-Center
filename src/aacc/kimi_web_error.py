"""Stable, language-neutral error categories for Kimi quota presentation."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from aacc.i18n import LanguageManager


class KimiWebErrorCategory(StrEnum):
    """Allowlisted categories that may cross the Kimi session/service boundary."""

    LOAD_FAILED = "load_failed"
    REFRESH_TIMEOUT = "refresh_timeout"
    REFRESH_FAILED = "refresh_failed"
    STATE_SAVE_FAILED = "state_save_failed"
    CODE_FALLBACK_REFRESH_FAILED = "code_fallback_refresh_failed"
    LOGOUT_PARTIAL = "logout_partial"


class KimiCodeQuotaErrorCategory(StrEnum):
    """Allowlisted Kimi Code/OAuth/API errors exposed to presentation."""

    REFRESH_FAILED = "code_refresh_failed"
    OAUTH_FAILED = "code_oauth_failed"
    OAUTH_CANCELLED = "code_oauth_cancelled"
    OAUTH_CONFLICT = "code_oauth_conflict"


class KimiWebQuotaErrorCategory(StrEnum):
    """Allowlisted membership-WebView errors exposed to presentation."""

    LOAD_FAILED = "web_load_failed"
    REFRESH_TIMEOUT = "web_refresh_timeout"
    REFRESH_FAILED = "web_refresh_failed"
    STATE_SAVE_FAILED = "web_state_save_failed"
    LOGOUT_PARTIAL = "web_logout_partial"


_WEB_ERROR_TEXT_KEYS: Final[dict[KimiWebQuotaErrorCategory, str]] = {
    KimiWebQuotaErrorCategory.LOAD_FAILED: "kimi.web_load_failed",
    KimiWebQuotaErrorCategory.REFRESH_TIMEOUT: "kimi.web_refresh_timeout",
    KimiWebQuotaErrorCategory.REFRESH_FAILED: "kimi.web_refresh_failed",
    KimiWebQuotaErrorCategory.STATE_SAVE_FAILED: "kimi.web_state_save_failed",
    KimiWebQuotaErrorCategory.LOGOUT_PARTIAL: "kimi.logout_partial",
}

_CODE_ERROR_TEXT_KEYS: Final[dict[KimiCodeQuotaErrorCategory, str]] = {
    KimiCodeQuotaErrorCategory.REFRESH_FAILED: "kimi.code_refresh_failed",
    KimiCodeQuotaErrorCategory.OAUTH_FAILED: "kimi.code_oauth_failed",
    KimiCodeQuotaErrorCategory.OAUTH_CANCELLED: "kimi.code_oauth_failed",
    KimiCodeQuotaErrorCategory.OAUTH_CONFLICT: "kimi.code_oauth_failed",
}

_SESSION_TO_WEB_QUOTA: Final[dict[KimiWebErrorCategory, KimiWebQuotaErrorCategory]] = {
    KimiWebErrorCategory.LOAD_FAILED: KimiWebQuotaErrorCategory.LOAD_FAILED,
    KimiWebErrorCategory.REFRESH_TIMEOUT: KimiWebQuotaErrorCategory.REFRESH_TIMEOUT,
    KimiWebErrorCategory.REFRESH_FAILED: KimiWebQuotaErrorCategory.REFRESH_FAILED,
    KimiWebErrorCategory.STATE_SAVE_FAILED: KimiWebQuotaErrorCategory.STATE_SAVE_FAILED,
    KimiWebErrorCategory.LOGOUT_PARTIAL: KimiWebQuotaErrorCategory.LOGOUT_PARTIAL,
}


def normalize_kimi_web_error_category(value: object) -> KimiWebErrorCategory:
    """Return an allowlisted category without retaining untrusted input."""

    if not isinstance(value, str):
        return KimiWebErrorCategory.REFRESH_FAILED
    try:
        return KimiWebErrorCategory(value)
    except ValueError:
        return KimiWebErrorCategory.REFRESH_FAILED


def kimi_web_error_text(category: object, language_manager: LanguageManager) -> str:
    """Translate a category only at the current presentation boundary."""

    normalized = normalize_kimi_web_quota_error_category(category)
    return language_manager.text(_WEB_ERROR_TEXT_KEYS[normalized])


def normalize_kimi_code_quota_error_category(
    value: object,
) -> KimiCodeQuotaErrorCategory:
    """Fold unknown Kimi Code errors without retaining their source text."""

    if isinstance(value, KimiCodeQuotaErrorCategory):
        return value
    if isinstance(value, str):
        try:
            return KimiCodeQuotaErrorCategory(value)
        except ValueError:
            pass
    return KimiCodeQuotaErrorCategory.REFRESH_FAILED


def normalize_kimi_web_quota_error_category(
    value: object,
) -> KimiWebQuotaErrorCategory:
    """Fold unknown web errors without retaining their source text."""

    if isinstance(value, KimiWebQuotaErrorCategory):
        return value
    if isinstance(value, str):
        try:
            return KimiWebQuotaErrorCategory(value)
        except ValueError:
            pass
        try:
            session_category = KimiWebErrorCategory(value)
        except ValueError:
            return KimiWebQuotaErrorCategory.REFRESH_FAILED
        return _SESSION_TO_WEB_QUOTA.get(
            session_category,
            KimiWebQuotaErrorCategory.REFRESH_FAILED,
        )
    return KimiWebQuotaErrorCategory.REFRESH_FAILED


def kimi_code_quota_error_text(
    category: object,
    language_manager: LanguageManager,
) -> str:
    """Translate a safe Kimi Code category at the presentation boundary."""

    normalized = normalize_kimi_code_quota_error_category(category)
    return language_manager.text(_CODE_ERROR_TEXT_KEYS[normalized])
