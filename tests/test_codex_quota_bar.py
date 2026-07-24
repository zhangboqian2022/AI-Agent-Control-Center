from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from aacc.codex_quota import (
    CodexQuotaSnapshot,
    CodexQuotaStatus,
    CodexQuotaWindow,
)
from aacc.gui import CodexQuotaBar

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
    bar = CodexQuotaBar()

    bar.show_quota(snapshot())

    assert bar.weekly_label.text() == "周 9%"
    assert bar.weekly_bar.value() == 9
    assert not hasattr(bar, "five_hour_label")
    assert "prolite" in bar.toolTip()
    assert "5h" not in bar.toolTip()


def test_codex_bar_unknown_does_not_display_zero_percent(qapp):
    bar = CodexQuotaBar()
    unknown = CodexQuotaSnapshot(
        weekly=None,
        observed_at=None,
        status=CodexQuotaStatus.UNKNOWN,
    )

    bar.show_quota(unknown)

    assert "数据不可用" in bar.summary_label.text()
    assert bar.weekly_label.text() == "周 --"
    assert "0%" not in bar.toolTip()


def test_codex_bar_error_preserves_last_value_and_marks_stale(qapp):
    bar = CodexQuotaBar()
    bar.show_quota(snapshot(27))

    bar.show_error("read failed")

    assert "数据过期" in bar.summary_label.text()
    assert bar.weekly_label.text() == "周 27%"
    assert "read failed" in bar.toolTip()


def test_codex_bar_click_emits_refresh(qapp):
    bar = CodexQuotaBar()
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton)

    assert clicks == [True]
