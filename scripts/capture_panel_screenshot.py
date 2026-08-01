"""Render the panel with demo tasks and capture a screenshot for the docs.

Usage: .venv/bin/python scripts/capture_panel_screenshot.py [output.png]
Runs offscreen; no real tasks or apps are touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TZ"] = "Asia/Shanghai"
if hasattr(time, "tzset"):
    time.tzset()

from PySide6.QtCore import QObject, QSettings, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton  # noqa: E402

from aacc import gui as gui_module  # noqa: E402
from aacc.automation import MacAutomation  # noqa: E402
from aacc.automation_executor import AutomationExecutor  # noqa: E402
from aacc.codex_quota import (  # noqa: E402
    CodexQuotaSnapshot,
    CodexQuotaStatus,
    CodexQuotaWindow,
)
from aacc.codex_quota_service import CodexQuotaService  # noqa: E402
from aacc.config import default_config  # noqa: E402
from aacc.gui import MainWindow  # noqa: E402
from aacc.i18n import EN_US, ZH_CN, LanguageManager  # noqa: E402
from aacc.kimi_quota import BoosterWallet, KimiQuota, QuotaDetail, QuotaStatus  # noqa: E402
from aacc.models import AgentConfig, TaskConfig, TaskState, TerminalConfig  # noqa: E402
from aacc.opencode_web_quota import OpenCodeQuota, OpenCodeUsage  # noqa: E402
from aacc.persistence import StateStore  # noqa: E402
from aacc.task_manager import TaskManager  # noqa: E402

LANGUAGE = os.environ.get("AACC_SCREENSHOT_LANG", ZH_CN)
OUTPUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(
        "docs/images/panel-overview.en.png"
        if LANGUAGE == EN_US
        else "docs/images/panel-overview.png"
    )
)
DEMO_NOW = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
CODEX_WEEK = 17
KIMI_5H = 30
KIMI_WEEK = 72
KIMI_MONTH = 31
OPENCODE_5H = 12
OPENCODE_WEEK = 44
OPENCODE_MONTH = 68


class _DemoDateTime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return DEMO_NOW.replace(tzinfo=None)
        return DEMO_NOW.astimezone(tz)


class _DemoKimiWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    error_occurred = Signal(str)
    web_error_occurred = Signal(object)
    code_error_occurred = Signal(object)

    def open_login(self, _parent: object = None) -> None:
        return

    def refresh_now(self) -> None:
        return

    def logout(self) -> None:
        return


class _DemoOpenCodeWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    error_occurred = Signal(str)

    def open_login(self, _parent: object = None) -> None:
        return

    def refresh_now(self) -> None:
        return

    def logout(self) -> None:
        return


def _assert_label_fits(label: QLabel) -> None:
    assert "…" not in label.text(), f"{label.objectName()} is elided: {label.text()}"
    text_width = label.fontMetrics().horizontalAdvance(label.text())
    assert text_width <= label.contentsRect().width(), (
        f"{label.objectName()} needs {text_width}px, has {label.contentsRect().width()}px"
    )


def _task(
    task_id: str,
    slot: int,
    name: str,
    agent_type: str,
    display: str,
    status: str,
    message: str,
    *,
    minutes_ago: float,
    work_dir: str | None = None,
    usage: dict[str, object] | None = None,
) -> tuple[TaskConfig, TaskState]:
    config = TaskConfig(
        id=task_id,
        slot=slot,
        name=name,
        agent=AgentConfig(type=agent_type, display_name=display),
        terminal=TerminalConfig(type="mac_app", app_bundle_id="com.example.app"),
    )
    state = TaskState.new(task_id, status, message=message, source="demo")
    started = DEMO_NOW - timedelta(minutes=minutes_ago)
    updates: dict[str, object] = {"updated_at": started}
    if state.started_at is not None:
        updates["started_at"] = started
    if state.finished_at is not None:
        updates["finished_at"] = started
    state = state.model_copy(update=updates)
    metadata: dict[str, object] = {}
    if work_dir:
        metadata["work_dir"] = work_dir
    if usage:
        metadata["usage"] = usage
    if metadata:
        state = state.model_copy(update={"metadata": metadata})
    return config, state


def _demo_quota() -> KimiQuota:
    return KimiQuota(
        weekly=QuotaDetail(
            used=KIMI_WEEK,
            limit=100,
            remaining=100 - KIMI_WEEK,
            reset_at=DEMO_NOW + timedelta(days=3, hours=2),
            percentage=KIMI_WEEK,
        ),
        five_hour=QuotaDetail(
            used=KIMI_5H,
            limit=100,
            remaining=100 - KIMI_5H,
            reset_at=DEMO_NOW + timedelta(hours=2, minutes=40),
            percentage=KIMI_5H,
        ),
        monthly=QuotaDetail(
            used=KIMI_MONTH,
            limit=100,
            remaining=100 - KIMI_MONTH,
            reset_at=DEMO_NOW + timedelta(days=28),
            percentage=KIMI_MONTH,
        ),
        membership_level="LEVEL_ADVANCED",
        booster=BoosterWallet(status="STATUS_ACTIVE", is_enabled=True, balance_yuan=3.15),
    )


def _demo_opencode_quota() -> OpenCodeQuota:
    return OpenCodeQuota(
        rolling=OpenCodeUsage(
            OPENCODE_5H,
            3600 * 2,
            DEMO_NOW + timedelta(hours=2),
        ),
        weekly=OpenCodeUsage(
            OPENCODE_WEEK,
            3600 * 24 * 3,
            DEMO_NOW + timedelta(days=3),
        ),
        monthly=OpenCodeUsage(
            OPENCODE_MONTH,
            3600 * 24 * 28,
            DEMO_NOW + timedelta(days=28),
        ),
        status=QuotaStatus.OK,
        fetched_at=DEMO_NOW,
    )


def main() -> int:
    gui_module.datetime = _DemoDateTime
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp())
    config = default_config()
    settings = QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat)
    language_manager = LanguageManager(LANGUAGE, settings)
    store = StateStore(tmp / "demo.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    kimi_web_quota_service = _DemoKimiWebQuotaService()
    opencode_web_quota_service = _DemoOpenCodeWebQuotaService()
    codex_snapshot = CodexQuotaSnapshot(
        weekly=CodexQuotaWindow(
            used_percent=CODEX_WEEK,
            window_minutes=10_080,
            resets_at=DEMO_NOW + timedelta(days=6),
        ),
        observed_at=DEMO_NOW,
        status=CodexQuotaStatus.OK,
        plan_type="prolite",
    )

    class DemoCodexReader:
        def read_latest(self) -> CodexQuotaSnapshot:
            return codex_snapshot

    codex_quota_service = CodexQuotaService(DemoCodexReader(), interval_seconds=3600)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=settings,
        language_manager=language_manager,
        kimi_web_quota_service=kimi_web_quota_service,  # type: ignore[arg-type]
        codex_quota_service=codex_quota_service,
        opencode_web_quota_service=opencode_web_quota_service,  # type: ignore[arg-type]
        open_url=lambda _url: None,
    )
    # Keep the full title visible in the fixed-width documentation raster by
    # tightening the header gaps and slightly shrinking the header buttons;
    # all text stays intact.
    panel = window.findChild(QFrame, "panel")
    if panel is not None and panel.layout() is not None:
        header_layout = panel.layout().itemAt(0).layout()
        if header_layout is not None:
            header_layout.setSpacing(4)
    for header_button in window.findChildren(QPushButton):
        if header_button.objectName() in {"headerButton", "languageButton"}:
            header_button.setFixedSize(24, 24)
    title = window.findChild(QLabel, "title")
    language_button = window.findChild(QPushButton, "languageButton")
    expected_button = "中" if LANGUAGE == EN_US else "EN"
    assert language_button is not None and language_button.text() == expected_button

    if LANGUAGE == EN_US:
        payment_name, running_message = "Fix payment retry", "Running"
        deps_name = "Upgrade dependencies"
        approval_message = "Approve: write pyproject.toml"
        notes_name, completed_message = "Meeting notes", "Turn completed"
    else:
        payment_name, running_message = "修复支付回调重试", "正在运行"
        deps_name = "升级依赖并跑回归"
        approval_message = "等待批准：写 pyproject.toml"
        notes_name, completed_message = "整理周会纪要", "回合已完成"

    demo = [
        _task(
            "kimi:demo-payment",
            1,
            payment_name,
            "kimi_code",
            "Kimi Code",
            "RUNNING",
            running_message,
            minutes_ago=3.2,
            work_dir="C:/AACC-Demo/aacc-demo",
            usage={
                "total_input_tokens": 48_200,
                "output_tokens": 6_100,
                "cache_read_pct": 76,
                "speed_tps": 58,
            },
        ),
        _task(
            "codex:demo-deps",
            2,
            deps_name,
            "codex_cli",
            "Codex",
            "WAITING_APPROVAL",
            approval_message,
            minutes_ago=12.0,
            work_dir="C:/AACC-Demo/aacc-demo",
        ),
        _task(
            "kimi_desktop:demo-notes",
            3,
            notes_name,
            "kimi_desktop",
            "Kimi Desktop",
            "COMPLETED",
            completed_message,
            minutes_ago=25.0,
        ),
    ]
    for task_config, task_state in demo:
        manager.register(task_config, task_state)
    window.set_codex_selected_ids({"demo-deps"})
    window.set_kimi_selected_ids({"demo-payment"})
    window.set_kimi_desktop_selected_ids({"demo-notes"})

    window.show()
    kimi_web_quota_service.login_state_changed.emit(True)
    kimi_web_quota_service.quota_updated.emit(_demo_quota())
    codex_quota_service.quota_updated.emit(codex_snapshot)
    opencode_web_quota_service.login_state_changed.emit(True)
    opencode_web_quota_service.quota_updated.emit(_demo_opencode_quota())
    window.resize(420, 650)
    window.setFixedSize(420, 650)
    app.processEvents()
    if title is not None:
        _assert_label_fits(title)
    for quota_bar in (window.codex_quota_bar, window.quota_bar):
        if quota_bar is not None:
            for percent_label, reset_label in quota_bar.metric_label_pairs():
                _assert_label_fits(percent_label)
                _assert_label_fits(reset_label)
    for card in window.cards.values():
        _assert_label_fits(card.name_label)
        _assert_label_fits(card.message_label)
        for label in (card.status_label, card.workdir_label):
            if not label.isHidden():
                _assert_label_fits(label)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    screenshot = window.grab()
    screenshot_size = (screenshot.width(), screenshot.height())
    assert screenshot_size == (420, 650), f"unexpected screenshot size: {screenshot_size}"
    if not screenshot.save(str(OUTPUT)):
        print("failed to save screenshot", file=sys.stderr)
        return 1
    print(f"saved {OUTPUT} ({window.width()}x{window.height()})")
    manager.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
