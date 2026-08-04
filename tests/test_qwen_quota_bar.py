from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aacc.gui import QwenQuotaBar
from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.kimi_quota import QuotaDetail, QuotaStatus
from aacc.qwen_web_quota import QwenQuota


def _quota() -> QwenQuota:
    now = datetime.now(UTC)
    return QwenQuota(
        five_hour=QuotaDetail(30, 100, 70, now + timedelta(seconds=18000), 30),
        weekly=QuotaDetail(65, 100, 35, now + timedelta(seconds=604800), 65),
        status=QuotaStatus.OK,
        fetched_at=now,
    )


def test_bar_shows_two_metric_rows(qapp: object) -> None:
    bar = QwenQuotaBar()
    assert bar.metric_row_count() == 2
    assert bar.period_labels() == ["5 小时", "7 天"]


def test_bar_renders_quota_percentages_and_resets(qapp: object) -> None:
    bar = QwenQuotaBar()
    bar.show_quota(_quota())
    assert bar.percent_labels() == ["30%", "65%"]
    assert bar.five_hour_label.text() == "30%"
    assert bar.weekly_label.text() == "65%"
    assert all(bar.reset_labels())


def test_bar_uses_quota_wording_and_never_renders_none_percent(qapp: object) -> None:
    bar = QwenQuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(
        QwenQuota(
            five_hour=QuotaDetail(0, 100, 100, None, 0),
            weekly=None,
            status=QuotaStatus.PARTIAL,
            fetched_at=None,
        )
    )
    assert "Qwen Code 额度" in bar.summary_label.text()
    assert "None%" not in bar.toolTip()
    assert "5 小时: 0%" in bar.toolTip()
    assert "7 天: 未知" in bar.toolTip()

    english = QwenQuotaBar(LanguageManager(EN_US))
    assert english.language_manager.text("qwen.quota") == "Qwen Code quota"
    assert english.period_labels() == ["5H", "7D"]


def test_bar_unauthorized_state(qapp: object) -> None:
    bar = QwenQuotaBar()
    bar.show_unauthorized()
    assert "点击授权" in bar.summary_label.text()
    assert bar.percent_labels() == ["--", "--"]


def test_bar_error_preserves_last_quota_as_stale(qtbot) -> None:
    bar = QwenQuotaBar()
    bar.show_quota(_quota())
    bar.show_error("refresh_timeout")
    assert bar.percent_labels() == ["30%", "65%"]
    assert "点击重试" in bar.toolTip()
    assert "刷新超时" in bar.toolTip()


def test_bar_retranslate_switches_language(qapp: object) -> None:
    bar = QwenQuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(_quota())
    bar.language_manager = LanguageManager(EN_US)
    bar.retranslate_ui()
    assert bar.summary_label.text() == "Qwen Code quota"


def test_bar_pending_state(qtbot) -> None:
    bar = QwenQuotaBar()
    bar.show_pending()
    assert "授权中" in bar.summary_label.text()
    assert "授权中" in bar.toolTip()


def test_bar_unknown_quota_shows_unavailable_and_unknown_resets(qtbot) -> None:
    bar = QwenQuotaBar()
    bar.show_quota(QwenQuota(None, None, QuotaStatus.UNKNOWN, fetched_at=None))
    assert "额度不可用" in bar.summary_label.text()
    assert "未知" in bar.toolTip()
    assert bar.percent_labels() == ["--", "--"]


def test_bar_partial_quota_shows_partial(qtbot) -> None:
    bar = QwenQuotaBar()
    bar.show_quota(QwenQuota(None, None, QuotaStatus.PARTIAL, fetched_at=None))
    assert "部分额度可用" in bar.summary_label.text()


def test_bar_stale_quota_shows_stale(qtbot) -> None:
    bar = QwenQuotaBar()
    bar.show_quota(QwenQuota(None, None, QuotaStatus.STALE, fetched_at=None))
    assert "额度信息已过期" in bar.summary_label.text()


def test_bar_show_quota_preserves_last_error(qtbot) -> None:
    bar = QwenQuotaBar()
    bar.show_error("refresh_timeout")
    bar.show_quota(_quota(), preserve_errors=True)
    assert bar._display_state == "error"
    assert "点击重试" in bar.toolTip()


def test_bar_error_without_prior_quota_shows_unavailable(qtbot) -> None:
    bar = QwenQuotaBar()
    bar.show_error("refresh_timeout")
    assert "额度不可用" in bar.summary_label.text()
    assert "点击重试" in bar.toolTip()


def test_bar_retranslate_pending_and_error_states(qtbot) -> None:
    bar = QwenQuotaBar(LanguageManager(ZH_CN))
    bar.show_pending()
    bar.language_manager = LanguageManager(EN_US)
    bar.retranslate_ui()
    assert "Authorizing" in bar.summary_label.text()
    bar.show_error("refresh_timeout")
    bar.retranslate_ui()
    assert "Click to retry" in bar.toolTip()


def test_bar_click_emits_signal(qtbot) -> None:
    bar = QwenQuotaBar()
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
