from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QProgressBar

from aacc.codex_quota import (
    CodexQuotaSnapshot,
    CodexQuotaStatus,
    CodexQuotaWindow,
)
from aacc.gui import CodexQuotaBar, format_quota_reset, load_stylesheet
from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.kimi_quota import format_reset_countdown

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)


def snapshot(percent: int = 9) -> CodexQuotaSnapshot:
    return CodexQuotaSnapshot(
        weekly=CodexQuotaWindow(
            used_percent=percent,
            window_minutes=10080,
            resets_at=NOW + timedelta(days=2),
        ),
        observed_at=NOW,
        status=CodexQuotaStatus.OK,
        plan_type="prolite",
    )


def test_codex_bar_shows_weekly_window_only(qapp):
    bar = CodexQuotaBar(LanguageManager(ZH_CN))

    bar.show_quota(snapshot())

    assert bar.period_labels() == ["WEEK"]
    assert bar.percent_labels() == ["9%"]
    assert bar.reset_labels() == [
        format_quota_reset(snapshot().weekly.resets_at, bar.language_manager)
    ]
    assert bar.metric_row_count() == 1
    assert bar.weekly_bar.value() == 9
    assert not hasattr(bar, "five_hour_label")
    assert "prolite" in bar.toolTip()
    assert "5h" not in bar.toolTip()
    assert format_reset_countdown(snapshot().weekly.resets_at) in bar.toolTip()


def test_codex_quota_retranslates_from_retained_snapshot_without_clicking(qapp):
    language_manager = LanguageManager(ZH_CN)
    bar = CodexQuotaBar(language_manager)
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))
    bar.show_quota(snapshot())

    language_manager.set_language(EN_US)
    bar.retranslate_ui()

    assert bar.summary_label.text() == "Codex quota"
    assert bar.period_labels() == ["WEEK"]
    assert bar.reset_labels()[0].startswith("Resets ")
    assert "Weekly used: 9%" in bar.toolTip()
    assert clicks == []


def test_codex_unknown_state_retranslates(qapp):
    language_manager = LanguageManager(ZH_CN)
    bar = CodexQuotaBar(language_manager)

    language_manager.set_language(EN_US)
    bar.retranslate_ui()

    assert bar.summary_label.text() == "Codex quota\nQuota unavailable"
    assert "No valid Codex weekly quota found" in bar.toolTip()


def test_codex_unknown_snapshot_retranslates_without_clearing_raw_state(qapp):
    language_manager = LanguageManager(ZH_CN)
    bar = CodexQuotaBar(language_manager)
    unknown = CodexQuotaSnapshot(
        weekly=None,
        observed_at=None,
        status=CodexQuotaStatus.UNKNOWN,
    )

    bar.show_quota(unknown)

    assert bar._last_codex_quota is unknown
    show_unknown_calls: list[bool] = []
    bar.show_unknown = lambda: show_unknown_calls.append(True)  # type: ignore[method-assign]

    language_manager.set_language(EN_US)
    bar.retranslate_ui()

    assert show_unknown_calls == []
    assert bar._last_codex_quota is unknown
    assert bar.summary_label.text() == "Codex quota\nQuota unavailable"


def test_codex_bar_unknown_does_not_display_zero_percent(qapp):
    bar = CodexQuotaBar(LanguageManager(ZH_CN))
    unknown = CodexQuotaSnapshot(
        weekly=None,
        observed_at=None,
        status=CodexQuotaStatus.UNKNOWN,
    )

    bar.show_quota(unknown)

    assert "数据不可用" in bar.summary_label.text()
    assert bar.period_labels() == ["WEEK"]
    assert bar.percent_labels() == ["--"]
    assert bar.reset_labels() == ["--"]
    assert "0%" not in bar.toolTip()


def test_codex_bar_error_preserves_last_value_and_marks_stale(qapp):
    bar = CodexQuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(snapshot(27))

    bar.show_error("read failed")

    assert "数据过期" in bar.summary_label.text()
    assert bar.percent_labels() == ["27%"]
    assert "read failed" in bar.toolTip()


def test_codex_unknown_refresh_preserves_last_value_and_marks_stale(qapp):
    bar = CodexQuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(snapshot(27))
    unknown = CodexQuotaSnapshot(
        weekly=None,
        observed_at=None,
        status=CodexQuotaStatus.UNKNOWN,
    )

    bar.show_quota(unknown)

    assert "数据过期" in bar.summary_label.text()
    assert bar.percent_labels() == ["27%"]
    assert "27%" in bar.toolTip()


def test_codex_bar_click_emits_refresh(qapp):
    bar = CodexQuotaBar(LanguageManager(ZH_CN))
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton)

    assert clicks == [True]


def test_codex_metric_labels_do_not_overlap_at_default_panel_width(qapp):
    bar = CodexQuotaBar(LanguageManager(ZH_CN))
    bar.resize(420, bar.sizeHint().height())
    bar.show_quota(snapshot())
    bar.show()
    qapp.processEvents()

    for percent_label, reset_label in bar.metric_label_pairs():
        percent_right = percent_label.mapTo(bar, percent_label.rect().topRight()).x()
        reset_left = reset_label.mapTo(bar, reset_label.rect().topLeft()).x()
        assert percent_right < reset_left


def test_english_codex_quota_labels_do_not_overlap_at_default_panel_width(qapp):
    bar = CodexQuotaBar(LanguageManager(EN_US))
    bar.resize(420, bar.sizeHint().height())
    bar.show_quota(snapshot())
    bar.show()
    qapp.processEvents()

    for percent_label, reset_label in bar.metric_label_pairs():
        percent_right = percent_label.mapTo(bar, percent_label.rect().topRight()).x()
        reset_left = reset_label.mapTo(bar, reset_label.rect().topLeft()).x()
        assert percent_right < reset_left


def test_codex_quota_metrics_are_large_enough_to_read(qapp):
    bar = CodexQuotaBar(LanguageManager(ZH_CN))
    bar.setStyleSheet(load_stylesheet())
    bar.show_quota(snapshot(18))
    bar.show()
    qapp.processEvents()

    percent = bar.findChild(QLabel, "quotaPercent")
    reset = bar.findChild(QLabel, "quotaReset")
    progress = bar.findChild(QProgressBar, "quotaProgress")

    assert percent is not None
    assert percent.font().pixelSize() >= 11
    assert percent.minimumWidth() >= 36
    assert reset is not None
    assert reset.font().pixelSize() >= 10
    assert progress is not None
    assert progress.height() == 7
    assert bar.summary_label.minimumWidth() >= bar.summary_label.fontMetrics().horizontalAdvance(
        "Codex 额度"
    )
