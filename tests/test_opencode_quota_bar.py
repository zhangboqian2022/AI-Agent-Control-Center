from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aacc.gui import OpenCodeQuotaBar
from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.kimi_quota import QuotaStatus
from aacc.opencode_web_quota import OpenCodeQuota, OpenCodeUsage


def _quota() -> OpenCodeQuota:
    now = datetime.now(UTC)
    return OpenCodeQuota(
        rolling=OpenCodeUsage(0, 17760, now + timedelta(seconds=17760)),
        weekly=OpenCodeUsage(42, 226800, now + timedelta(seconds=226800)),
        monthly=OpenCodeUsage(100, 2674800, now + timedelta(seconds=2674800)),
        status=QuotaStatus.OK,
        fetched_at=now,
    )


def test_bar_shows_three_metric_rows(qapp: object) -> None:
    bar = OpenCodeQuotaBar()
    assert bar.metric_row_count() == 3
    assert bar.period_labels() == ["5H", "WEEK", "MONTH"]


def test_bar_renders_quota_percentages_and_resets(qapp: object) -> None:
    bar = OpenCodeQuotaBar()
    bar.show_quota(_quota())
    assert bar.percent_labels() == ["0%", "42%", "100%"]
    assert bar.rolling_label.text() == "0%"
    assert bar.monthly_label.text() == "100%"
    assert all(bar.reset_labels())


def test_bar_unauthorized_state(qapp: object) -> None:
    bar = OpenCodeQuotaBar()
    bar.show_unauthorized()
    assert "点击授权" in bar.summary_label.text()
    assert bar.percent_labels() == ["--", "--", "--"]


def test_bar_error_preserves_last_quota_as_stale(qtbot) -> None:
    bar = OpenCodeQuotaBar()
    bar.show_quota(_quota())
    bar.show_error("refresh_timeout")
    assert bar.percent_labels() == ["0%", "42%", "100%"]
    assert "点击重试" in bar.toolTip()
    assert "刷新超时" in bar.toolTip()


def test_bar_retranslate_switches_language(qapp: object) -> None:
    bar = OpenCodeQuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(_quota())
    bar.language_manager = LanguageManager(EN_US)
    bar.retranslate_ui()
    assert bar.summary_label.text() == "OpenCode usage"


def test_bar_click_emits_signal(qtbot) -> None:
    bar = OpenCodeQuotaBar()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(0, 0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    with qtbot.waitSignal(bar.clicked, timeout=1000):
        bar.mouseReleaseEvent(event)
