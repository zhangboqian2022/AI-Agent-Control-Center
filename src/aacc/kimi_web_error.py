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


_ERROR_TEXT_KEYS: Final[dict[KimiWebErrorCategory, str]] = {
    KimiWebErrorCategory.LOAD_FAILED: "kimi.web_load_failed",
    KimiWebErrorCategory.REFRESH_TIMEOUT: "kimi.web_refresh_timeout",
    KimiWebErrorCategory.REFRESH_FAILED: "kimi.web_refresh_failed",
    KimiWebErrorCategory.STATE_SAVE_FAILED: "kimi.web_state_save_failed",
    KimiWebErrorCategory.CODE_FALLBACK_REFRESH_FAILED: ("kimi.code_fallback_refresh_failed"),
    KimiWebErrorCategory.LOGOUT_PARTIAL: "kimi.logout_partial",
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

    normalized = normalize_kimi_web_error_category(category)
    return language_manager.text(_ERROR_TEXT_KEYS[normalized])
