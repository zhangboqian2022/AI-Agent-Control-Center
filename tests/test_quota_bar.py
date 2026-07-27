from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from aacc.gui import QuotaBar, format_quota_reset
from aacc.kimi_quota import BoosterWallet, KimiQuota, QuotaDetail, QuotaStatus


def test_format_quota_reset_uses_local_absolute_date_without_strftime_flags():
    reset_at = datetime(2026, 8, 2, 0, 40, tzinfo=UTC)
    local_zone = timezone(timedelta(hours=8))

    assert format_quota_reset(reset_at, local_zone=local_zone) == "8月2日 08:40 重置"
    assert format_quota_reset(None, local_zone=local_zone) == "--"


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
    )


def test_unauthorized_state(qapp):
    bar = QuotaBar()
    bar.show_unauthorized()
    assert "授权" in bar.summary_label.text()
    assert bar.weekly_bar.value() == 0


def test_pending_state(qapp):
    bar = QuotaBar()
    bar.show_pending()
    assert "授权中" in bar.summary_label.text()


def test_show_quota(qapp):
    bar = QuotaBar()
    bar.show_quota(make_quota())
    assert bar.weekly_bar.value() == 42
    assert bar.five_hour_bar.value() == 10
    assert bar.monthly_bar.value() == 36
    assert bar.period_labels() == ["5H", "WEEK", "MONTH"]
    assert bar.percent_labels() == ["10%", "42%", "36%"]
    assert bar.metric_row_count() == 3
    assert "¥3.15" in bar.balance_label.text()
    assert "PRO" in bar.toolTip()


def test_show_quota_without_booster_hides_balance(qapp):
    quota = make_quota()
    bar = QuotaBar()
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
    bar = QuotaBar()
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
    bar = QuotaBar()
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
    bar = QuotaBar()
    bar.show_quota(make_quota())

    bar.show_error("network unavailable")

    assert "数据过期" in bar.summary_label.text()
    assert bar.percent_labels() == ["10%", "42%", "36%"]
    assert "network unavailable" in bar.toolTip()


def test_kimi_quota_bar_shows_absolute_reset_times(qapp):
    quota = make_quota()
    bar = QuotaBar()

    bar.show_quota(quota)

    assert bar.reset_labels() == [
        format_quota_reset(quota.five_hour.reset_at),
        format_quota_reset(quota.weekly.reset_at),
        format_quota_reset(quota.monthly.reset_at),
    ]
    assert all(text.endswith(" 重置") for text in bar.reset_labels())


def test_kimi_balance_stays_visible_beside_three_metric_rows(qapp):
    bar = QuotaBar()

    bar.show_quota(make_quota())

    assert not bar.balance_label.isHidden()
    assert "¥3.15" in bar.balance_label.text()


def test_kimi_metric_labels_do_not_overlap_at_default_panel_width(qapp):
    bar = QuotaBar()
    bar.resize(420, bar.sizeHint().height())
    bar.show_quota(make_quota())
    bar.show()
    qapp.processEvents()

    for percent_label, reset_label in bar.metric_label_pairs():
        percent_right = percent_label.mapTo(bar, percent_label.rect().topRight()).x()
        reset_left = reset_label.mapTo(bar, reset_label.rect().topLeft()).x()
        assert percent_right < reset_left


def test_clicked_signal(qapp):
    bar = QuotaBar()
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton)
    assert clicks == [True]


def test_kimi_quota_enabled_default():
    from aacc.models import AppSettings

    assert AppSettings().kimi_quota_enabled is True
