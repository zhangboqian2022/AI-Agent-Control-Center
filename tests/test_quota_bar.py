from __future__ import annotations

from datetime import UTC, datetime

from aacc.gui import QuotaBar
from aacc.kimi_quota import BoosterWallet, KimiQuota, QuotaDetail, QuotaStatus


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
        total_quota=QuotaDetail(used=0, limit=0, remaining=0, reset_at=None, percentage=0),
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
    assert "42%" in bar.weekly_label.text()
    assert "10%" in bar.five_hour_label.text()
    assert "¥3.15" in bar.balance_label.text()
    assert "PRO" in bar.toolTip()


def test_show_quota_without_booster_hides_balance(qapp):
    quota = make_quota()
    bar = QuotaBar()
    bar.show_quota(
        KimiQuota(
            weekly=quota.weekly,
            five_hour=quota.five_hour,
            total_quota=quota.total_quota,
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
            total_quota=None,
            membership_level=None,
            booster=None,
            status=QuotaStatus.PARTIAL,
        )
    )
    assert bar.weekly_label.text() == "周 42%"
    assert bar.five_hour_label.text() == "5h --"


def test_show_unknown_quota_does_not_display_zero_percent(qapp):
    bar = QuotaBar()
    bar.show_quota(
        KimiQuota(
            weekly=None,
            five_hour=None,
            total_quota=None,
            membership_level=None,
            booster=None,
            status=QuotaStatus.UNKNOWN,
        )
    )
    assert "数据不可用" in bar.summary_label.text()
    assert bar.weekly_label.text() == "周 --"
    assert bar.five_hour_label.text() == "5h --"
    assert "0%" not in bar.toolTip()


def test_refresh_error_preserves_values_and_marks_them_stale(qapp):
    bar = QuotaBar()
    bar.show_quota(make_quota())

    bar.show_error("network unavailable")

    assert "数据过期" in bar.summary_label.text()
    assert bar.weekly_label.text() == "周 42%"
    assert bar.five_hour_label.text() == "5h 10%"
    assert "network unavailable" in bar.toolTip()


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
