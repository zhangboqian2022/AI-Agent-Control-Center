from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from PySide6.QtWidgets import QLabel, QProgressBar

from aacc.gui import QuotaBar, format_quota_reset, load_stylesheet
from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.kimi_quota import (
    BoosterWallet,
    KimiQuota,
    QuotaDetail,
    QuotaStatus,
    format_reset_countdown,
)


def test_format_quota_reset_uses_local_absolute_date_without_strftime_flags():
    reset_at = datetime(2026, 8, 2, 0, 40, tzinfo=UTC)
    local_zone = timezone(timedelta(hours=8))

    language = LanguageManager(ZH_CN)
    assert format_quota_reset(reset_at, language, local_zone=local_zone) == "8月2日 08:40 重置"
    assert format_quota_reset(None, language, local_zone=local_zone) == "--"


def test_format_quota_reset_uses_english_absolute_date() -> None:
    reset_at = datetime(2026, 8, 2, 0, 40, tzinfo=UTC)
    local_zone = timezone(timedelta(hours=8))

    assert (
        format_quota_reset(
            reset_at,
            LanguageManager(EN_US),
            local_zone=local_zone,
        )
        == "Resets 8/2 08:40"
    )


def make_quota() -> KimiQuota:
    return KimiQuota(
        weekly=QuotaDetail(
            used=420,
            limit=1000,
            remaining=580,
            reset_at=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
            percentage=42,
        ),
        five_hour=QuotaDetail(
            used=10,
            limit=100,
            remaining=90,
            reset_at=datetime(2026, 7, 24, 20, 0, tzinfo=UTC),
            percentage=10,
        ),
        monthly=QuotaDetail(
            used=360,
            limit=1000,
            remaining=640,
            reset_at=datetime(2026, 8, 24, 5, 28, tzinfo=UTC),
            percentage=36,
        ),
        membership_level="PRO",
        booster=BoosterWallet(status="STATUS_ACTIVE", is_enabled=True, balance_yuan=3.15),
        fetched_at=datetime(2026, 7, 24, 14, 0, tzinfo=UTC),
    )


def test_unauthorized_state(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_unauthorized()
    assert "授权" in bar.summary_label.text()
    assert bar.weekly_bar.value() == 0


def test_pending_state(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_pending()
    assert "授权中" in bar.summary_label.text()


def test_show_quota(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(make_quota())
    assert bar.weekly_bar.value() == 42
    assert bar.five_hour_bar.value() == 10
    assert bar.monthly_bar.value() == 36
    assert bar.period_labels() == ["5H", "WEEK", "MONTH"]
    assert bar.percent_labels() == ["10%", "42%", "36%"]
    assert bar.metric_row_count() == 3
    assert "¥3.15" in bar.balance_label.text()
    assert "PRO" in bar.toolTip()
    assert format_reset_countdown(make_quota().weekly.reset_at) in bar.toolTip()


def test_explicit_zero_percent_is_not_rendered_as_missing(qapp):
    quota = make_quota()
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(
        KimiQuota(
            weekly=quota.weekly,
            five_hour=QuotaDetail(
                used=0,
                limit=100,
                remaining=100,
                reset_at=quota.five_hour.reset_at,
                percentage=0,
            ),
            monthly=quota.monthly,
            membership_level=quota.membership_level,
            booster=quota.booster,
            status=quota.status,
            fetched_at=quota.fetched_at,
        )
    )

    assert bar.percent_labels()[0] == "0%"
    assert bar.five_hour_bar.value() == 0


def test_kimi_quota_retranslates_from_retained_snapshot_without_clicking(qapp):
    language_manager = LanguageManager(ZH_CN)
    bar = QuotaBar(language_manager)
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))
    bar.show_quota(make_quota())

    language_manager.set_language(EN_US)
    bar.retranslate_ui()

    assert bar.summary_label.text() == "Kimi quota"
    assert bar.period_labels() == ["5H", "WEEK", "MONTH"]
    assert all(label.startswith("Resets ") for label in bar.reset_labels())
    assert "Membership quota: PRO" in bar.toolTip()
    assert "Last updated " in bar.toolTip()
    assert clicks == []


def test_kimi_quota_source_errors_retranslate_both_directions_without_losing_snapshot(qapp):
    language_manager = LanguageManager(ZH_CN)
    bar = QuotaBar(language_manager)
    bar.show_quota(make_quota())
    bar.show_code_error("private code response token=secret")
    bar.show_web_error("private web response access_token=secret")

    assert "Kimi Code 额度刷新失败" in bar.toolTip()
    assert "Kimi 会员额度刷新失败" in bar.toolTip()
    assert "secret" not in bar.toolTip()

    language_manager.set_language(EN_US)
    bar.retranslate_ui()

    assert bar.summary_label.text() == "Kimi quota\nQuota data is stale"
    assert bar.percent_labels() == ["10%", "42%", "36%"]
    assert "Kimi Code quota refresh failed" in bar.toolTip()
    assert "Kimi membership quota refresh failed" in bar.toolTip()
    assert "会员额度刷新失败" not in bar.toolTip()

    bar.show_web_error("web_refresh_timeout")
    language_manager.set_language(ZH_CN)
    bar.retranslate_ui()

    assert "Kimi 会员额度刷新超时" in bar.toolTip()
    assert "Kimi Code 额度刷新失败" in bar.toolTip()
    assert "membership quota refresh timed out" not in bar.toolTip()


def test_kimi_authorization_unknown_and_partial_states_retranslate(qapp):
    language_manager = LanguageManager(ZH_CN)
    bar = QuotaBar(language_manager)

    language_manager.set_language(EN_US)
    bar.retranslate_ui()
    assert bar.summary_label.text() == "Kimi quota\nAuthorize"

    bar.show_pending()
    assert bar.summary_label.text() == "Kimi quota\nAuthorizing…"

    unknown = KimiQuota(
        weekly=None,
        five_hour=None,
        monthly=None,
        membership_level=None,
        booster=None,
        status=QuotaStatus.UNKNOWN,
    )
    bar.show_quota(unknown)
    assert bar.summary_label.text() == "Kimi quota\nQuota unavailable"

    quota = make_quota()
    partial = KimiQuota(
        weekly=quota.weekly,
        five_hour=None,
        monthly=None,
        membership_level=None,
        booster=None,
        status=QuotaStatus.PARTIAL,
    )
    bar.show_quota(partial)
    assert bar.summary_label.text() == "Kimi quota\nPartial quota data"


def test_show_quota_without_booster_hides_balance(qapp):
    quota = make_quota()
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(
        KimiQuota(
            weekly=quota.weekly,
            five_hour=quota.five_hour,
            monthly=quota.monthly,
            membership_level=None,
            booster=None,
        )
    )
    assert bar.balance_label.text() == ""


def test_show_partial_quota_uses_dashes_for_missing_window(qapp):
    quota = make_quota()
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(
        KimiQuota(
            weekly=quota.weekly,
            five_hour=None,
            monthly=None,
            membership_level=None,
            booster=None,
            status=QuotaStatus.PARTIAL,
        )
    )
    assert bar.period_labels() == ["5H", "WEEK", "MONTH"]
    assert bar.percent_labels() == ["--", "42%", "--"]
    assert bar.reset_labels()[0] == "--"
    assert bar.reset_labels()[2] == "--"


def test_show_unknown_quota_does_not_display_zero_percent(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(
        KimiQuota(
            weekly=None,
            five_hour=None,
            monthly=None,
            membership_level=None,
            booster=None,
            status=QuotaStatus.UNKNOWN,
        )
    )
    assert "数据不可用" in bar.summary_label.text()
    assert bar.period_labels() == ["5H", "WEEK", "MONTH"]
    assert bar.percent_labels() == ["--", "--", "--"]
    assert "0%" not in bar.toolTip()


def test_refresh_error_preserves_values_and_marks_them_stale(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(make_quota())

    bar.show_web_error("web_refresh_failed")

    assert "数据过期" in bar.summary_label.text()
    assert bar.percent_labels() == ["10%", "42%", "36%"]
    assert "Kimi 会员额度刷新失败" in bar.toolTip()


def test_kimi_quota_bar_shows_absolute_reset_times(qapp):
    quota = make_quota()
    bar = QuotaBar(LanguageManager(ZH_CN))

    bar.show_quota(quota)

    assert bar.reset_labels() == [
        format_quota_reset(quota.five_hour.reset_at, bar.language_manager),
        format_quota_reset(quota.weekly.reset_at, bar.language_manager),
        format_quota_reset(quota.monthly.reset_at, bar.language_manager),
    ]
    assert all(text.endswith(" 重置") for text in bar.reset_labels())


def test_kimi_balance_stays_visible_beside_three_metric_rows(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))

    bar.show_quota(make_quota())

    assert not bar.balance_label.isHidden()
    assert "¥3.15" in bar.balance_label.text()


def test_kimi_metric_labels_do_not_overlap_at_default_panel_width(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.resize(420, bar.sizeHint().height())
    bar.show_quota(make_quota())
    bar.show()
    qapp.processEvents()

    for percent_label, reset_label in bar.metric_label_pairs():
        percent_right = percent_label.mapTo(bar, percent_label.rect().topRight()).x()
        reset_left = reset_label.mapTo(bar, reset_label.rect().topLeft()).x()
        assert percent_right < reset_left


def test_english_kimi_quota_labels_do_not_overlap_at_default_panel_width(qapp):
    bar = QuotaBar(LanguageManager(EN_US))
    bar.resize(420, bar.sizeHint().height())
    bar.show_quota(make_quota())
    bar.show()
    qapp.processEvents()

    for percent_label, reset_label in bar.metric_label_pairs():
        percent_right = percent_label.mapTo(bar, percent_label.rect().topRight()).x()
        reset_left = reset_label.mapTo(bar, reset_label.rect().topLeft()).x()
        assert percent_right < reset_left


def test_kimi_quota_metrics_are_large_enough_to_read(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    bar.setStyleSheet(load_stylesheet())
    bar.show_quota(make_quota())
    bar.show()
    qapp.processEvents()

    percent_labels = bar.findChildren(QLabel, "quotaPercent")
    reset_labels = bar.findChildren(QLabel, "quotaReset")
    progress_bars = bar.findChildren(QProgressBar, "quotaProgress")

    assert all(label.font().pixelSize() >= 11 for label in percent_labels)
    assert all(label.minimumWidth() >= 36 for label in percent_labels)
    assert all(label.font().pixelSize() >= 10 for label in reset_labels)
    assert all(progress.height() == 7 for progress in progress_bars)


def test_clicked_signal(qapp):
    bar = QuotaBar(LanguageManager(ZH_CN))
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton)
    assert clicks == [True]


def test_kimi_quota_enabled_default():
    from aacc.models import AppSettings

    assert AppSettings().kimi_quota_enabled is True
