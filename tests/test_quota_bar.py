from __future__ import annotations

from datetime import UTC, datetime

from aacc.gui import QuotaBar
from aacc.kimi_quota import BoosterWallet, KimiQuota, QuotaDetail


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


def test_clicked_signal(qapp):
    bar = QuotaBar()
    clicks: list[bool] = []
    bar.clicked.connect(lambda: clicks.append(True))
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPoint(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mouseReleaseEvent(event)
    assert clicks == [True]


def test_kimi_quota_enabled_default():
    from aacc.models import AppSettings

    assert AppSettings().kimi_quota_enabled is True
