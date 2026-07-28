from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from aacc.i18n import (
    EN_US,
    ZH_CN,
    LanguageManager,
    catalog_keys,
    detect_system_language,
    load_language,
    placeholder_names,
)


def test_system_language_is_chinese_only_for_chinese_locales() -> None:
    assert detect_system_language("zh_CN") == ZH_CN
    assert detect_system_language("zh-Hant-TW") == ZH_CN
    assert detect_system_language("en_US") == EN_US
    assert detect_system_language("ja_JP") == EN_US


def test_persisted_language_is_strict_and_invalid_value_falls_back(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "i18n.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui_language", "en_US")
    assert load_language(settings, "zh_CN") == EN_US
    settings.setValue("ui_language", "EN")
    assert load_language(settings, "zh_CN") == ZH_CN


def test_catalogs_have_identical_keys_and_placeholders() -> None:
    assert catalog_keys(ZH_CN) == catalog_keys(EN_US)
    for key in catalog_keys(ZH_CN):
        assert placeholder_names(ZH_CN, key) == placeholder_names(EN_US, key)


def test_catalog_contains_all_shared_keys() -> None:
    required_keys = {
        "header.about",
        "header.settings",
        "header.hide",
        "summary.tasks.one",
        "summary.tasks.other",
        "group.running",
        "group.completed",
        "group.clear_all",
        "empty.no_tasks",
        "quota.kimi",
        "quota.codex",
        "quota.authorize",
        "quota.authorizing",
        "quota.unavailable",
        "quota.partial",
        "quota.stale",
        "quota.refresh",
        "quota.five_hour",
        "quota.week",
        "quota.month",
        "quota.membership",
        "quota.booster",
        "quota.last_update",
        "task.switch",
        "task.manual_status",
        "task.copy",
        "task.rename",
        "task.remove",
        "task.no_message",
        "task.last_activity",
        "task.updated",
        "compact.toggle",
        "topmost.toggle",
        "dock.top_right",
        "tray.show_hide",
        "tray.quit",
        "common.cancel",
        "common.done",
        "common.apply",
        "clear_completed.prompt.one",
        "clear_completed.prompt.other",
        "about.body.macos",
        "about.body.windows",
    }

    assert required_keys <= catalog_keys(ZH_CN)


def test_manager_persists_once_and_unsubscribe_stops_notifications(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "i18n.ini"), QSettings.Format.IniFormat)
    manager = LanguageManager(ZH_CN, settings)
    calls: list[str] = []
    unsubscribe = manager.subscribe(lambda: calls.append(manager.language))
    manager.set_language(EN_US)
    manager.set_language(EN_US)
    unsubscribe()
    manager.set_language(ZH_CN)

    assert calls == [EN_US]
    assert settings.value("ui_language") == ZH_CN


def test_unknown_key_returns_the_key_without_formatting_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = LanguageManager(EN_US)

    with caplog.at_level(logging.WARNING, logger="aacc.i18n"):
        assert manager.text("unknown.translation", secret="never-log-me") == "unknown.translation"

    assert "never-log-me" not in caplog.text


def test_text_formats_catalog_values() -> None:
    manager = LanguageManager(EN_US)

    assert manager.text("quota.reset", month=7, day=28, hour=9, minute=5) == "Resets 7/28 09:05"


def test_subscriber_exception_does_not_stop_remaining_subscribers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = LanguageManager(ZH_CN)
    calls: list[str] = []

    def broken_callback() -> None:
        raise RuntimeError("callback-secret")

    manager.subscribe(broken_callback)
    manager.subscribe(lambda: calls.append(manager.language))

    with caplog.at_level(logging.WARNING, logger="aacc.i18n"):
        manager.set_language(EN_US)

    assert calls == [EN_US]
    assert "Language subscriber failed" in caplog.text
    assert "callback-secret" not in caplog.text
