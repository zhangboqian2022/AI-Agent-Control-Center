from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from importlib import resources
from pathlib import PurePath

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from aacc import public_version
from aacc.automation import AUTOMATION_ERROR_CATEGORIES, AutomationError
from aacc.automation_executor import AutomationExecutor
from aacc.codex_discovery import CodexSession
from aacc.codex_quota import CodexQuotaSnapshot, CodexQuotaStatus
from aacc.codex_quota_service import CodexQuotaService
from aacc.constants import APP_SUPPORT_DIR, DEFAULT_CONFIG_PATH
from aacc.discovery_service import DiscoveryHealth, HealthSubscriber
from aacc.i18n import ZH_CN, LanguageManager, other_language
from aacc.kimi_desktop_discovery import KimiDesktopSession
from aacc.kimi_discovery import KimiSession
from aacc.kimi_metrics import format_usage_line
from aacc.kimi_quota import (
    KimiQuota,
    QuotaDetail,
    QuotaStatus,
    format_balance,
    format_reset_countdown,
)
from aacc.kimi_web_error import (
    KimiCodeQuotaErrorCategory,
    KimiWebQuotaErrorCategory,
    kimi_code_quota_error_text,
    kimi_web_error_text,
    normalize_kimi_code_quota_error_category,
    normalize_kimi_web_quota_error_category,
)
from aacc.kimi_web_quota import merge_kimi_quota
from aacc.kimi_web_quota_service import KimiWebQuotaService
from aacc.models import TaskConfig, TaskState, TaskStatus
from aacc.opencode_discovery import OpenCodeSession
from aacc.opencode_web_error import (
    OpenCodeQuotaErrorCategory,
    normalize_opencode_quota_error_category,
    opencode_quota_error_text,
)
from aacc.opencode_web_quota import OpenCodeQuota, OpenCodeUsage
from aacc.opencode_web_quota_service import OpenCodeWebQuotaService
from aacc.quota_service import STATE_AUTHORIZED, STATE_PENDING, QuotaService
from aacc.task_manager import TaskManager

_logger = logging.getLogger("aacc.gui")

STATUS_COLORS = {
    TaskStatus.UNCONFIGURED: "#778195",
    TaskStatus.IDLE: "#778195",
    TaskStatus.STARTING: "#35d3dc",
    TaskStatus.THINKING: "#4d9fff",
    TaskStatus.RUNNING: "#4d9fff",
    TaskStatus.WAITING_INPUT: "#f4c84a",
    TaskStatus.WAITING_APPROVAL: "#f4c84a",
    TaskStatus.COMPLETED: "#3ddc97",
    TaskStatus.WARNING: "#ff9f43",
    TaskStatus.ERROR: "#ff5d6c",
    TaskStatus.PAUSED: "#a879ff",
    TaskStatus.CANCELLED: "#566071",
    TaskStatus.STOPPED: "#566071",
    TaskStatus.UNKNOWN: "#b8c0cc",
}

STATUS_NAME_KEYS = {
    TaskStatus.UNCONFIGURED: "status.unconfigured",
    TaskStatus.IDLE: "status.idle",
    TaskStatus.STARTING: "status.starting",
    TaskStatus.THINKING: "status.thinking",
    TaskStatus.RUNNING: "status.running",
    TaskStatus.WAITING_INPUT: "status.waiting_input",
    TaskStatus.WAITING_APPROVAL: "status.waiting_approval",
    TaskStatus.COMPLETED: "status.completed",
    TaskStatus.WARNING: "status.warning",
    TaskStatus.ERROR: "status.error",
    TaskStatus.PAUSED: "status.paused",
    TaskStatus.CANCELLED: "status.cancelled",
    TaskStatus.STOPPED: "status.stopped",
    TaskStatus.UNKNOWN: "status.unknown",
}

STATUS_LIGHT_FONT_SIZE = 64
AACC_MESSAGE_CATEGORY_KEY = "aacc_message_category"
# Restoring the panel asks the Kimi web quota service for an immediate
# catch-up refresh, at most once per interval so rapid hide/show toggling
# does not relaunch the browser session back to back.
RESTORE_QUOTA_REFRESH_INTERVAL_SECONDS = 60.0
AACC_MESSAGE_TEXT_KEYS = {
    "manual_update": "feedback.manual_update",
    **{
        f"automation.{category}": f"automation.{category}"
        for category in AUTOMATION_ERROR_CATEGORIES
    },
}
TASK_MESSAGE_KEYS = {
    "正在运行": "task.message.running",
    "正在分析任务": "task.message.analyzing",
    "正在生成回复": "task.message.generating",
    "正在运行测试": "task.message.testing",
    "正在构建程序": "task.message.building",
    "正在检查代码": "task.message.inspecting",
    "正在执行命令": "task.message.executing",
    "正在修改代码": "task.message.editing",
    "正在查询资料": "task.message.researching",
    "等待输入": "task.message.waiting_input",
    "等待同意": "task.message.waiting_approval",
    "空闲": "task.message.idle",
    "已完成": "task.message.completed",
    "回合已完成": "task.message.turn_completed",
    "未检测到运行进程": "task.message.no_process",
    "最近更新，未检测到运行进程": "task.message.recent_no_process",
}
_STANDARD_BUTTON_TEXT_KEYS = {
    QMessageBox.StandardButton.Ok: "common.ok",
    QMessageBox.StandardButton.Yes: "common.yes",
    QMessageBox.StandardButton.Cancel: "common.cancel",
    QMessageBox.StandardButton.Close: "common.close",
}


def _localize_standard_buttons(
    box: QMessageBox,
    language_manager: LanguageManager,
) -> None:
    for standard_button, text_key in _STANDARD_BUTTON_TEXT_KEYS.items():
        button = box.button(standard_button)
        if button is not None:
            button.setText(language_manager.text(text_key))


def status_name(status: TaskStatus, language: LanguageManager) -> str:
    return language.text(STATUS_NAME_KEYS[status])


def _task_message_text(state: TaskState, language: LanguageManager) -> str:
    category = state.metadata.get(AACC_MESSAGE_CATEGORY_KEY)
    if isinstance(category, str):
        key = AACC_MESSAGE_TEXT_KEYS.get(category)
        if key is not None:
            return language.text(key)
    message_key = TASK_MESSAGE_KEYS.get(state.message)
    if message_key is not None:
        return language.text(message_key)
    return state.message or language.text("task.no_message")


def format_quota_reset(
    reset_at: datetime | None,
    language: LanguageManager,
    *,
    local_zone: tzinfo | None = None,
) -> str:
    if reset_at is None:
        return "--"
    local = reset_at.astimezone(local_zone)
    return language.text(
        "quota.reset",
        month=local.month,
        day=local.day,
        hour=local.hour,
        minute=local.minute,
    )


def load_stylesheet() -> str:
    stylesheet = resources.files("aacc").joinpath("styles.qss").read_text(encoding="utf-8")
    return stylesheet.replace(
        "#headerButton {",
        "#headerButton, #languageButton {",
    ).replace(
        "#headerButton:hover {",
        "#headerButton:hover, #languageButton:hover {",
    )


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.ERROR,
    TaskStatus.CANCELLED,
    TaskStatus.STOPPED,
}


def _elapsed(state: TaskState, now: datetime | None = None) -> str:
    anchor = state.started_at or state.updated_at
    end = state.finished_at or now or datetime.now(UTC)
    seconds = max(0, int((end - anchor).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _elapsed_label(
    state: TaskState,
    language: LanguageManager,
    now: datetime | None = None,
) -> str:
    elapsed = _elapsed(state, now)
    if state.status in TERMINAL_STATUSES:
        return language.text("time.total", elapsed=elapsed)
    return elapsed


class ElidedLabel(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__()
        self._full_text = text
        self.setToolTip(text)
        self._update_elision()

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_elision()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_elision()

    def _update_elision(self) -> None:
        available_width = max(0, self.contentsRect().width())
        QLabel.setText(
            self,
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideRight,
                available_width,
            ),
        )


@dataclass(frozen=True)
class _QuotaMetricRow:
    period_label: QLabel
    percent_label: QLabel
    progress_bar: QProgressBar
    reset_label: QLabel


@dataclass(frozen=True)
class _SubtitlePresentation:
    key: str
    values: dict[str, object]
    uppercase: bool = True
    prefix: str = ""


def _add_quota_metric_row(
    layout: QGridLayout,
    row_index: int,
    period: str,
) -> _QuotaMetricRow:
    period_label = QLabel(period)
    period_label.setObjectName("quotaPeriod")
    period_label.setMinimumWidth(40)
    percent_label = QLabel("--")
    percent_label.setObjectName("quotaPercent")
    percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    percent_label.setMinimumWidth(36)
    progress_bar = QProgressBar()
    progress_bar.setObjectName("quotaProgress")
    progress_bar.setRange(0, 100)
    progress_bar.setTextVisible(False)
    progress_bar.setFixedHeight(7)
    progress_bar.setMinimumWidth(16)
    progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    reset_label = QLabel("--")
    reset_label.setObjectName("quotaReset")
    reset_label.setMinimumWidth(152)
    reset_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(period_label, row_index, 0)
    layout.addWidget(percent_label, row_index, 1)
    layout.addWidget(progress_bar, row_index, 2)
    layout.addWidget(reset_label, row_index, 3)
    return _QuotaMetricRow(period_label, percent_label, progress_bar, reset_label)


def _set_quota_metric(
    row: _QuotaMetricRow,
    percentage: int | None,
    reset_at: datetime | None,
    language: LanguageManager,
) -> None:
    unknown = percentage is None
    row.percent_label.setText("--" if unknown else f"{percentage}%")
    row.progress_bar.setValue(0 if percentage is None else percentage)
    row.progress_bar.setProperty("unknown", unknown)
    row.reset_label.setText(format_quota_reset(reset_at, language) if not unknown else "--")


class QuotaBar(QFrame):
    """Kimi account quota strip shown above the task list."""

    clicked = Signal()

    def __init__(self, language_manager: LanguageManager | None = None) -> None:
        super().__init__()
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self._last_quota: KimiQuota | None = None
        self._display_state = "unauthorized"
        self._last_code_error: KimiCodeQuotaErrorCategory | None = None
        self._last_web_error: KimiWebQuotaErrorCategory | None = None
        self.setObjectName("quotaBar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(4)
        self.dot = QLabel("●")
        self.dot.setObjectName("quotaDot")
        layout.addWidget(self.dot, 0, 0, Qt.AlignmentFlag.AlignTop)
        self.summary_label = QLabel("Kimi 额度")
        self.summary_label.setObjectName("quotaSummary")
        self.summary_label.setFixedWidth(98)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label, 0, 1, Qt.AlignmentFlag.AlignTop)
        self.balance_label = QLabel("")
        self.balance_label.setObjectName("quotaBalance")
        layout.addWidget(self.balance_label, 1, 1, 1, 1, Qt.AlignmentFlag.AlignTop)

        metric_layout = QGridLayout()
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setHorizontalSpacing(4)
        metric_layout.setVerticalSpacing(4)
        metric_layout.setColumnMinimumWidth(0, 40)
        metric_layout.setColumnMinimumWidth(1, 36)
        metric_layout.setColumnMinimumWidth(2, 16)
        metric_layout.setColumnMinimumWidth(3, 152)
        metric_layout.setColumnStretch(2, 1)
        layout.addLayout(metric_layout, 0, 2, 3, 1)
        layout.setColumnStretch(2, 1)

        self._five_hour_row = _add_quota_metric_row(metric_layout, 0, "5H")
        self._weekly_row = _add_quota_metric_row(metric_layout, 1, "WEEK")
        self._monthly_row = _add_quota_metric_row(metric_layout, 2, "MONTH")
        self._metric_rows = [
            self._five_hour_row,
            self._weekly_row,
            self._monthly_row,
        ]
        self.five_hour_label = self._five_hour_row.percent_label
        self.five_hour_bar = self._five_hour_row.progress_bar
        self.weekly_label = self._weekly_row.percent_label
        self.weekly_bar = self._weekly_row.progress_bar
        self.monthly_label = self._monthly_row.percent_label
        self.monthly_bar = self._monthly_row.progress_bar
        self.show_unauthorized()

    def period_labels(self) -> list[str]:
        return [row.period_label.text() for row in self._metric_rows]

    def percent_labels(self) -> list[str]:
        return [row.percent_label.text() for row in self._metric_rows]

    def reset_labels(self) -> list[str]:
        return [row.reset_label.text() for row in self._metric_rows]

    def metric_row_count(self) -> int:
        return len(self._metric_rows)

    def metric_label_pairs(self) -> list[tuple[QLabel, QLabel]]:
        return [(row.percent_label, row.reset_label) for row in self._metric_rows]

    def show_unauthorized(self) -> None:
        self._display_state = "unauthorized"
        self._last_quota = None
        self._last_code_error = None
        self._last_web_error = None
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self.dot.setStyleSheet("color: #e06c75;")
        self.summary_label.setText(
            "Kimi 额度\n点击授权"
            if self.language_manager.language == ZH_CN
            else "Kimi quota\nAuthorize"
        )
        for row in self._metric_rows:
            _set_quota_metric(row, None, None, self.language_manager)
        self.balance_label.setText("")
        self.setToolTip(
            "点击通过 Kimi 官方设备授权登录，查询账户额度"
            if self.language_manager.language == ZH_CN
            else "Authorize with Kimi's official device flow to view account quota"
        )

    def show_pending(self) -> None:
        self._display_state = "pending"
        self._last_code_error = None
        self._last_web_error = None
        self.dot.setStyleSheet("color: #e5c07b;")
        self.summary_label.setText(
            f"{self.language_manager.text('quota.kimi')}\n"
            f"{self.language_manager.text('quota.authorizing')}"
        )
        self.setToolTip(self.language_manager.text("quota.authorizing"))

    def show_quota(
        self,
        quota: KimiQuota,
        *,
        preserve_errors: bool = False,
    ) -> None:
        self._last_quota = quota
        if not preserve_errors:
            self._last_code_error = None
            self._last_web_error = None
        if self._last_code_error is not None or self._last_web_error is not None:
            self._display_state = "error"
            self._render_error()
            return
        self._display_state = "quota"
        self._render_quota(quota)

    def _render_quota(self, quota: KimiQuota) -> None:
        self._has_known_quota = quota.status is not QuotaStatus.UNKNOWN
        if quota.status is QuotaStatus.UNKNOWN:
            self.dot.setStyleSheet("color: #8997aa;")
            self.summary_label.setText(
                "Kimi 额度\n数据不可用"
                if self.language_manager.language == ZH_CN
                else "Kimi quota\nQuota unavailable"
            )
        elif quota.status is QuotaStatus.PARTIAL:
            self.dot.setStyleSheet("color: #e5c07b;")
            self.summary_label.setText(
                "Kimi 额度\n部分数据"
                if self.language_manager.language == ZH_CN
                else "Kimi quota\nPartial quota data"
            )
        elif quota.status is QuotaStatus.STALE:
            self.dot.setStyleSheet("color: #8997aa;")
            self.summary_label.setText(
                f"{self.language_manager.text('quota.kimi')}\n"
                f"{self.language_manager.text('quota.stale')}"
            )
        else:
            self.dot.setStyleSheet("color: #98c379;")
            self.summary_label.setText(self.language_manager.text("quota.kimi"))
        self._show_detail(self._five_hour_row, quota.five_hour)
        self._show_detail(self._weekly_row, quota.weekly)
        self._show_detail(self._monthly_row, quota.monthly)
        balance = format_balance(quota.booster.balance_yuan) if quota.booster is not None else ""
        self.balance_label.setText(balance)
        tooltip_lines = [
            self._detail_tooltip(self.language_manager.text("quota.five_hour"), quota.five_hour),
            self._detail_tooltip(self.language_manager.text("quota.week"), quota.weekly),
            self._detail_tooltip(self.language_manager.text("quota.month"), quota.monthly),
        ]
        if quota.membership_level:
            tooltip_lines.append(
                f"{self.language_manager.text('quota.membership')}: {quota.membership_level}"
            )
        if balance:
            tooltip_lines.append(f"{self.language_manager.text('quota.booster')}: {balance}")
        if quota.fetched_at is not None:
            tooltip_lines.append(
                self.language_manager.text(
                    "quota.last_update",
                    updated=quota.fetched_at.astimezone().strftime("%H:%M:%S"),
                )
            )
        tooltip_lines.append(self.language_manager.text("quota.refresh"))
        self._last_quota_tooltip = "\n".join(tooltip_lines)
        self.setToolTip(self._last_quota_tooltip)

    def show_code_error(self, category: object) -> None:
        self._display_state = "error"
        self._last_code_error = normalize_kimi_code_quota_error_category(category)
        self._render_error()

    def show_web_error(self, category: object) -> None:
        self._display_state = "error"
        self._last_web_error = normalize_kimi_web_quota_error_category(category)
        self._render_error()

    def clear_code_error(self) -> None:
        self._last_code_error = None

    def clear_web_error(self) -> None:
        self._last_web_error = None

    def _render_error(self) -> None:
        if self._last_quota is not None:
            self._render_quota(self._last_quota)
        self.dot.setStyleSheet("color: #8997aa;")
        if self._has_known_quota:
            state_text = (
                "数据过期"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("quota.stale")
            )
        else:
            state_text = (
                "数据不可用"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("quota.unavailable")
            )
        self.summary_label.setText(f"{self.language_manager.text('quota.kimi')}\n{state_text}")
        previous = f"{self._last_quota_tooltip}\n" if self._last_quota_tooltip else ""
        retry = "点击重试" if self.language_manager.language == ZH_CN else "Click to retry"
        errors: list[str] = []
        if self._last_code_error is not None:
            errors.append(
                kimi_code_quota_error_text(
                    self._last_code_error,
                    self.language_manager,
                )
            )
        if self._last_web_error is not None:
            errors.append(kimi_web_error_text(self._last_web_error, self.language_manager))
        self.setToolTip(f"{previous}{'\n'.join(errors)}\n{retry}")

    def _show_detail(self, row: _QuotaMetricRow, detail: QuotaDetail | None) -> None:
        if detail is None:
            _set_quota_metric(row, None, None, self.language_manager)
            return
        _set_quota_metric(
            row,
            detail.percentage,
            detail.reset_at,
            self.language_manager,
        )

    def _detail_tooltip(self, name: str, detail: QuotaDetail | None) -> str:
        if detail is None:
            unknown = "未知" if self.language_manager.language == ZH_CN else "Unknown"
            return f"{name}: {unknown}"
        reset = (
            format_reset_countdown(detail.reset_at)
            if self.language_manager.language == ZH_CN
            else format_quota_reset(detail.reset_at, self.language_manager)
        )
        return f"{name}: {detail.percentage}% ({reset})"

    def retranslate_ui(self) -> None:
        if self._display_state == "pending":
            self.show_pending()
        elif self._display_state == "quota" and self._last_quota is not None:
            self._render_quota(self._last_quota)
        elif self._display_state == "error":
            self._render_error()
        else:
            self.show_unauthorized()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class OpenCodeQuotaBar(QFrame):
    """OpenCode workspace quota strip (ROLLING / WEEK / MONTH) from web session data."""

    clicked = Signal()

    def __init__(self, language_manager: LanguageManager | None = None) -> None:
        super().__init__()
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self._last_quota: OpenCodeQuota | None = None
        self._display_state = "unauthorized"
        self._last_error: OpenCodeQuotaErrorCategory | None = None
        self.setObjectName("quotaBar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(4)
        self.dot = QLabel("●")
        self.dot.setObjectName("quotaDot")
        layout.addWidget(self.dot, 0, 0, Qt.AlignmentFlag.AlignTop)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("quotaSummary")
        self.summary_label.setFixedWidth(98)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label, 0, 1, Qt.AlignmentFlag.AlignTop)
        metric_layout = QGridLayout()
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setHorizontalSpacing(4)
        metric_layout.setVerticalSpacing(4)
        metric_layout.setColumnMinimumWidth(0, 40)
        metric_layout.setColumnMinimumWidth(1, 36)
        metric_layout.setColumnMinimumWidth(2, 16)
        metric_layout.setColumnMinimumWidth(3, 152)
        metric_layout.setColumnStretch(2, 1)
        layout.addLayout(metric_layout, 0, 2, 1, 1)
        layout.setColumnStretch(2, 1)
        self._rolling_row = _add_quota_metric_row(metric_layout, 0, "")
        self._weekly_row = _add_quota_metric_row(metric_layout, 1, "")
        self._monthly_row = _add_quota_metric_row(metric_layout, 2, "")
        self._metric_rows = [self._rolling_row, self._weekly_row, self._monthly_row]
        self.rolling_label = self._rolling_row.percent_label
        self.rolling_bar = self._rolling_row.progress_bar
        self.weekly_label = self._weekly_row.percent_label
        self.weekly_bar = self._weekly_row.progress_bar
        self.monthly_label = self._monthly_row.percent_label
        self.monthly_bar = self._monthly_row.progress_bar
        self._set_period_labels()
        self.show_unauthorized()

    def _set_period_labels(self) -> None:
        self._rolling_row.period_label.setText(self.language_manager.text("opencode.rolling"))
        self._weekly_row.period_label.setText(self.language_manager.text("quota.week"))
        self._monthly_row.period_label.setText(self.language_manager.text("quota.month"))

    def period_labels(self) -> list[str]:
        return [row.period_label.text() for row in self._metric_rows]

    def percent_labels(self) -> list[str]:
        return [row.percent_label.text() for row in self._metric_rows]

    def reset_labels(self) -> list[str]:
        return [row.reset_label.text() for row in self._metric_rows]

    def metric_row_count(self) -> int:
        return len(self._metric_rows)

    def show_unauthorized(self) -> None:
        self._display_state = "unauthorized"
        self._last_quota = None
        self._last_error = None
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self.dot.setStyleSheet("color: #e06c75;")
        self.summary_label.setText(
            f"{self.language_manager.text('opencode.quota')}\n"
            + ("点击授权" if self.language_manager.language == ZH_CN else "Authorize")
        )
        for row in self._metric_rows:
            _set_quota_metric(row, None, None, self.language_manager)
        self.setToolTip(
            "点击登录 opencode.ai 工作区，同步 Go 套餐额度"
            if self.language_manager.language == ZH_CN
            else "Sign in to the opencode.ai workspace to sync Go plan quota"
        )

    def show_pending(self) -> None:
        self._display_state = "pending"
        self._last_error = None
        self.dot.setStyleSheet("color: #e5c07b;")
        self.summary_label.setText(
            f"{self.language_manager.text('opencode.quota')}\n"
            f"{self.language_manager.text('quota.authorizing')}"
        )
        self.setToolTip(self.language_manager.text("quota.authorizing"))

    def show_quota(
        self,
        quota: OpenCodeQuota,
        *,
        preserve_errors: bool = False,
    ) -> None:
        self._last_quota = quota
        if not preserve_errors:
            self._last_error = None
        if self._last_error is not None:
            self._display_state = "error"
            self._render_error()
            return
        self._display_state = "quota"
        self._render_quota(quota)

    def show_error(self, category: object) -> None:
        self._display_state = "error"
        self._last_error = normalize_opencode_quota_error_category(category)
        self._render_error()

    def _render_quota(self, quota: OpenCodeQuota) -> None:
        self._has_known_quota = quota.status is not QuotaStatus.UNKNOWN
        if quota.status is QuotaStatus.UNKNOWN:
            self.dot.setStyleSheet("color: #8997aa;")
            self.summary_label.setText(
                f"{self.language_manager.text('opencode.quota')}\n"
                f"{self.language_manager.text('quota.unavailable')}"
            )
        elif quota.status is QuotaStatus.PARTIAL:
            self.dot.setStyleSheet("color: #e5c07b;")
            self.summary_label.setText(
                f"{self.language_manager.text('opencode.quota')}\n"
                f"{self.language_manager.text('quota.partial')}"
            )
        elif quota.status is QuotaStatus.STALE:
            self.dot.setStyleSheet("color: #8997aa;")
            self.summary_label.setText(
                f"{self.language_manager.text('opencode.quota')}\n"
                f"{self.language_manager.text('quota.stale')}"
            )
        else:
            self.dot.setStyleSheet("color: #98c379;")
            self.summary_label.setText(self.language_manager.text("opencode.quota"))
        self._show_detail(self._rolling_row, quota.rolling)
        self._show_detail(self._weekly_row, quota.weekly)
        self._show_detail(self._monthly_row, quota.monthly)
        tooltip_lines = [
            self._detail_tooltip(self.language_manager.text("opencode.rolling"), quota.rolling),
            self._detail_tooltip(self.language_manager.text("quota.week"), quota.weekly),
            self._detail_tooltip(self.language_manager.text("quota.month"), quota.monthly),
        ]
        if quota.fetched_at is not None:
            tooltip_lines.append(
                self.language_manager.text(
                    "quota.last_update",
                    updated=quota.fetched_at.astimezone().strftime("%H:%M:%S"),
                )
            )
        tooltip_lines.append(self.language_manager.text("quota.refresh"))
        self._last_quota_tooltip = "\n".join(tooltip_lines)
        self.setToolTip(self._last_quota_tooltip)

    def _show_detail(self, row: _QuotaMetricRow, usage: OpenCodeUsage | None) -> None:
        if usage is None:
            _set_quota_metric(row, None, None, self.language_manager)
            return
        _set_quota_metric(row, usage.percentage, usage.reset_at, self.language_manager)

    def _detail_tooltip(self, name: str, usage: OpenCodeUsage | None) -> str:
        if usage is None:
            unknown = "未知" if self.language_manager.language == ZH_CN else "Unknown"
            return f"{name}: {unknown}"
        reset = (
            format_reset_countdown(usage.reset_at)
            if self.language_manager.language == ZH_CN
            else format_quota_reset(usage.reset_at, self.language_manager)
        )
        percentage = "--" if usage.percentage is None else f"{usage.percentage}%"
        return f"{name}: {percentage} ({reset})"

    def _render_error(self) -> None:
        if self._last_quota is not None:
            self._render_quota(self._last_quota)
        self.dot.setStyleSheet("color: #8997aa;")
        if self._has_known_quota:
            state_text = (
                "数据过期"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("quota.stale")
            )
        else:
            state_text = self.language_manager.text("quota.unavailable")
        self.summary_label.setText(f"{self.language_manager.text('opencode.quota')}\n{state_text}")
        previous = f"{self._last_quota_tooltip}\n" if self._last_quota_tooltip else ""
        retry = "点击重试" if self.language_manager.language == ZH_CN else "Click to retry"
        error_text = opencode_quota_error_text(self._last_error, self.language_manager)
        self.setToolTip(f"{previous}{error_text}\n{retry}")

    def retranslate_ui(self) -> None:
        self._set_period_labels()
        if self._display_state == "pending":
            self.show_pending()
        elif self._display_state == "quota" and self._last_quota is not None:
            self._render_quota(self._last_quota)
        elif self._display_state == "error":
            self._render_error()
        else:
            self.show_unauthorized()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class CodexQuotaBar(QFrame):
    """Read-only Codex weekly quota from live account data or local metadata."""

    clicked = Signal()

    def __init__(self, language_manager: LanguageManager | None = None) -> None:
        super().__init__()
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self._last_codex_quota: CodexQuotaSnapshot | None = None
        self._display_state = "unknown"
        self._last_error = ""
        self.setObjectName("quotaBar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(4)
        self.dot = QLabel("●")
        self.dot.setObjectName("quotaDot")
        layout.addWidget(self.dot, 0, 0)
        self.summary_label = QLabel("Codex 额度")
        self.summary_label.setObjectName("quotaSummary")
        self.summary_label.setFixedWidth(98)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label, 0, 1)

        metric_layout = QGridLayout()
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setHorizontalSpacing(4)
        metric_layout.setColumnMinimumWidth(0, 40)
        metric_layout.setColumnMinimumWidth(1, 36)
        metric_layout.setColumnMinimumWidth(2, 16)
        metric_layout.setColumnMinimumWidth(3, 152)
        metric_layout.setColumnStretch(2, 1)
        layout.addLayout(metric_layout, 0, 2)
        layout.setColumnStretch(2, 1)
        self._weekly_row = _add_quota_metric_row(metric_layout, 0, "WEEK")
        self._metric_rows = [self._weekly_row]
        self.weekly_label = self._weekly_row.percent_label
        self.weekly_bar = self._weekly_row.progress_bar
        self.show_unknown()

    def period_labels(self) -> list[str]:
        return [row.period_label.text() for row in self._metric_rows]

    def percent_labels(self) -> list[str]:
        return [row.percent_label.text() for row in self._metric_rows]

    def reset_labels(self) -> list[str]:
        return [row.reset_label.text() for row in self._metric_rows]

    def metric_row_count(self) -> int:
        return len(self._metric_rows)

    def metric_label_pairs(self) -> list[tuple[QLabel, QLabel]]:
        return [(row.percent_label, row.reset_label) for row in self._metric_rows]

    def show_unknown(self) -> None:
        self._display_state = "unknown"
        self._last_codex_quota = None
        self._last_error = ""
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self._render_unknown()

    def _render_unknown(self) -> None:
        self.dot.setStyleSheet("color: #8997aa;")
        self.summary_label.setText(
            "Codex 额度\n数据不可用"
            if self.language_manager.language == ZH_CN
            else "Codex quota\nQuota unavailable"
        )
        _set_quota_metric(self._weekly_row, None, None, self.language_manager)
        self.setToolTip(
            "尚未发现有效 Codex 周额度；点击重新读取"
            if self.language_manager.language == ZH_CN
            else "No valid Codex weekly quota found; click to read again"
        )

    def show_quota(self, quota: CodexQuotaSnapshot) -> None:
        if quota.status is CodexQuotaStatus.UNKNOWN or quota.weekly is None:
            if self._has_known_quota:
                self.show_error("live quota source temporarily unavailable")
                return
            self._last_codex_quota = quota
            self._last_error = ""
            self._display_state = "unknown"
            self._has_known_quota = False
            self._last_quota_tooltip = ""
            self._render_unknown()
            return
        self._last_codex_quota = quota
        self._last_error = ""
        self._display_state = "quota"
        self._render_quota(quota)

    def _render_quota(self, quota: CodexQuotaSnapshot) -> None:
        if quota.weekly is None:
            return
        self._has_known_quota = True
        self.dot.setStyleSheet("color: #98c379;")
        self.summary_label.setText(self.language_manager.text("quota.codex"))
        _set_quota_metric(
            self._weekly_row,
            quota.weekly.used_percent,
            quota.weekly.resets_at,
            self.language_manager,
        )
        weekly_prefix = "每周已用" if self.language_manager.language == ZH_CN else "Weekly used"
        reset_text = (
            format_reset_countdown(quota.weekly.resets_at)
            if self.language_manager.language == ZH_CN
            else format_quota_reset(quota.weekly.resets_at, self.language_manager)
        )
        tooltip_lines = [f"{weekly_prefix}: {quota.weekly.used_percent}% ({reset_text})"]
        if quota.plan_type:
            plan_prefix = "套餐" if self.language_manager.language == ZH_CN else "Plan"
            tooltip_lines.append(f"{plan_prefix}: {quota.plan_type}")
        if quota.observed_at is not None:
            observed_prefix = (
                "本机观测" if self.language_manager.language == ZH_CN else "Observed locally"
            )
            tooltip_lines.append(
                f"{observed_prefix}: {quota.observed_at.astimezone().strftime('%H:%M:%S')}"
            )
        tooltip_lines.append(
            "点击刷新 Codex 周额度"
            if self.language_manager.language == ZH_CN
            else "Refresh Codex weekly quota"
        )
        self._last_quota_tooltip = "\n".join(tooltip_lines)
        self.setToolTip(self._last_quota_tooltip)

    def show_error(self, message: str) -> None:
        self._display_state = "error"
        self._last_error = message
        self._render_error()

    def _render_error(self) -> None:
        if self._last_codex_quota is not None:
            self._render_quota(self._last_codex_quota)
        self.dot.setStyleSheet("color: #8997aa;")
        if self._has_known_quota:
            state_text = (
                "数据过期"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("quota.stale")
            )
        else:
            state_text = (
                "数据不可用"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("quota.unavailable")
            )
        self.summary_label.setText(f"{self.language_manager.text('quota.codex')}\n{state_text}")
        previous = f"{self._last_quota_tooltip}\n" if self._last_quota_tooltip else ""
        error_prefix = (
            "额度读取失败" if self.language_manager.language == ZH_CN else "Quota read failed"
        )
        retry = "点击重试" if self.language_manager.language == ZH_CN else "Click to retry"
        self.setToolTip(f"{previous}{error_prefix}: {self._last_error}\n{retry}")

    def retranslate_ui(self) -> None:
        if self._display_state == "quota" and self._last_codex_quota is not None:
            self._render_quota(self._last_codex_quota)
        elif self._display_state == "error":
            self._render_error()
        else:
            self._render_unknown()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class KimiOAuthDialog(QDialog):
    cancelled = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        language_manager: LanguageManager | None = None,
    ) -> None:
        super().__init__(parent)
        parent_language = getattr(parent, "language_manager", None)
        self.language_manager = (
            language_manager
            or (parent_language if isinstance(parent_language, LanguageManager) else None)
            or LanguageManager(ZH_CN)
        )
        self._cancel_emitted = False
        self._finishing = False
        self._unsubscribe_language: Callable[[], None] = lambda: None
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        self.opened_label = QLabel()
        layout.addWidget(self.opened_label)
        self.code_label = QLabel("")
        self.code_label.setObjectName("oauthCode")
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.code_label)
        self.hint_label = QLabel()
        self.hint_label.setObjectName("quotaText")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_button)
        self._unsubscribe_language = self.language_manager.subscribe(
            self.retranslate_ui,
            component="kimi_oauth_dialog",
        )
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.language_manager.text("kimi.device_title"))
        self.opened_label.setText(self.language_manager.text("kimi.device_opened"))
        self.hint_label.setText(self.language_manager.text("kimi.device_finished"))
        self.cancel_button.setText(self.language_manager.text("kimi.device_cancel"))

    def set_code(self, user_code: str) -> None:
        self.code_label.setText(user_code)

    def _on_cancel(self) -> None:
        self.cancel_once()
        self.reject()

    def cancel_once(self) -> None:
        if self._cancel_emitted:
            return
        self._cancel_emitted = True
        self.cancelled.emit()

    def finish_and_close(self) -> None:
        self._finishing = True
        self.close()

    def _stop_language_updates(self) -> None:
        self._unsubscribe_language()
        self._unsubscribe_language = lambda: None

    def reject(self) -> None:
        if not self._finishing:
            self.cancel_once()
        self._stop_language_updates()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._stop_language_updates()
        if not self._finishing:
            self.cancel_once()
        super().closeEvent(event)


class TaskCard(QFrame):
    action_requested = Signal(str, str)
    remove_requested = Signal(str)

    def __init__(
        self,
        task: TaskConfig,
        state: TaskState,
        blink_attention: bool = True,
        display_name: str | None = None,
        language_manager: LanguageManager | None = None,
    ) -> None:
        super().__init__()
        self.task = task
        self.state = state
        self.blink_attention = blink_attention
        self.display_name = display_name or task.name
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self.setObjectName("taskCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(11, 8, 10, 8)
        root.setSpacing(11)
        self.dot = QLabel("●")
        self.dot.setObjectName("statusDot")
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dot.setFixedSize(68, 68)
        root.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.slot_label = QLabel(f"{task.slot:02d}")
        self.slot_label.setObjectName("slotLabel")
        self.slot_label.hide()
        self.agent_label = QLabel(
            (task.agent.display_name or task.agent.type.replace("_", " ")).upper()
        )
        self.agent_label.setObjectName("agentLabel")

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(2)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(7)
        self.status_label = ElidedLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.workdir_label = ElidedLabel("")
        self.workdir_label.setObjectName("workdirLabel")
        self.workdir_label.setMaximumWidth(140)
        self.workdir_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.workdir_label.hide()
        meta_row.addWidget(self.agent_label, 0)
        meta_row.addWidget(self.status_label, 1)
        meta_row.addWidget(self.workdir_label, 2)
        meta_row.addStretch()
        details_layout.addLayout(meta_row)

        self.name_label = ElidedLabel(self.display_name)
        self.name_label.setObjectName("taskName")
        self.name_label.setWordWrap(False)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name_label.setToolTip(self.display_name)
        details_layout.addWidget(self.name_label)

        activity_row = QHBoxLayout()
        activity_row.setContentsMargins(0, 0, 0, 0)
        activity_row.setSpacing(9)
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setObjectName("timerLabel")
        self.message_label = QLabel()
        self.message_label.setObjectName("messageLabel")
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_label.setWordWrap(False)
        activity_row.addWidget(self.timer_label)
        activity_row.addWidget(self.message_label, 1)
        details_layout.addLayout(activity_row)

        self.usage_label = QLabel()
        self.usage_label.setObjectName("usageLabel")
        self.usage_label.hide()
        details_layout.addWidget(self.usage_label)

        self.updated_label = QLabel()
        self.updated_label.setObjectName("updatedLabel")
        self.updated_label.hide()
        root.addWidget(self.details, 1)

        self.remove_button: QPushButton | None = None
        if task.id.startswith(("codex:", "kimi:", "kimi_desktop:", "opencode:")):
            self.remove_button = QPushButton("×")
            self.remove_button.setObjectName("removeTaskButton")
            self.remove_button.setFixedSize(24, 24)
            self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.task.id))
            root.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)
        self._effect = QGraphicsOpacityEffect(self.dot)
        self.dot.setGraphicsEffect(self._effect)
        self._pulse = QPropertyAnimation(self._effect, b"opacity", self)
        self._pulse.setDuration(900)
        self._pulse.setStartValue(1.0)
        self._pulse.setEndValue(0.35)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)
        self.set_state(state)
        self.retranslate_ui()

    def set_display_name(self, display_name: str) -> None:
        self.display_name = display_name
        self.name_label.setText(display_name)
        self.name_label.setToolTip(display_name)
        self.set_state(self.state)

    def set_state(self, state: TaskState) -> None:
        self.state = state
        self._render_state()

    def _render_state(self) -> None:
        state = self.state
        color = STATUS_COLORS[state.status]
        self.dot.setStyleSheet(f"color: {color}; font-size: {STATUS_LIGHT_FONT_SIZE}px;")
        self.status_label.setText(status_name(state.status, self.language_manager))
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 700;")
        work_dir = state.metadata.get("work_dir")
        if (
            self.task.agent.type in ("codex_cli", "kimi_code", "opencode_cli")
            and isinstance(work_dir, str)
            and work_dir
        ):
            self.workdir_label.setText(f"· {PurePath(work_dir).name}")
            self.workdir_label.setToolTip(work_dir)
            self.workdir_label.show()
        else:
            self.workdir_label.hide()
        usage = state.metadata.get("usage")
        if self.task.agent.type == "kimi_code" and isinstance(usage, dict):
            self.usage_label.setText(
                format_usage_line(
                    usage,
                    cache_label=self.language_manager.text("usage.cache"),
                )
            )
            self.usage_label.show()
        else:
            self.usage_label.hide()
        self.message_label.setText(_task_message_text(state, self.language_manager))
        updated_time = state.updated_at.astimezone().strftime("%H:%M:%S")
        self.updated_label.setText(
            f"最后活动：{updated_time}"
            if self.language_manager.language == ZH_CN
            else self.language_manager.text(
                "task.last_activity",
                elapsed=updated_time,
            )
        )
        self.timer_label.setText(_elapsed_label(state, self.language_manager))
        updated_text = (
            f"更新：{updated_time}"
            if self.language_manager.language == ZH_CN
            else self.language_manager.text("task.updated", updated=updated_time)
        )
        interaction_text = (
            "单击切换任务，右键查看更多操作"
            if self.language_manager.language == ZH_CN
            else "Click to switch tasks; right-click for more actions"
        )
        self.setToolTip(
            f"{interaction_text}\n{self.display_name}\n"
            f"{status_name(state.status, self.language_manager)} · {state.source} · "
            f"{state.confidence:.0%}\n"
            f"{updated_text}"
        )
        attention = state.status in {TaskStatus.WAITING_INPUT, TaskStatus.WAITING_APPROVAL}
        if attention and self.blink_attention:
            if self._pulse.state() != QPropertyAnimation.State.Running:
                self._pulse.start()
        else:
            self._pulse.stop()
            self._effect.setOpacity(1.0)

    def retranslate_ui(self) -> None:
        if self.remove_button is not None:
            self.remove_button.setAccessibleName(
                "从面板移除" if self.language_manager.language == ZH_CN else "Remove from panel"
            )
            self.remove_button.setToolTip(
                "停止监控并从面板移除"
                if self.language_manager.language == ZH_CN
                else "Stop monitoring and remove from panel"
            )
        self._render_state()

    def set_compact(self, compact: bool) -> None:
        self.details.setVisible(not compact)
        card_layout = self.layout()
        if card_layout is not None:
            card_layout.setContentsMargins(10, 6 if compact else 8, 9, 6 if compact else 8)

    def create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        switch_text = (
            "切换到任务"
            if self.language_manager.language == ZH_CN
            else self.language_manager.text("task.switch")
        )
        actions = [(switch_text, "focus")]
        for label, command in actions:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=command: self.action_requested.emit(
                    value, self.task.id
                )
            )
        menu.addSeparator()
        state_menu = menu.addMenu(
            "手动标记状态"
            if self.language_manager.language == ZH_CN
            else self.language_manager.text("task.manual_status")
        )
        for status, chinese_label in (
            (TaskStatus.RUNNING, "执行中"),
            (TaskStatus.WAITING_INPUT, "等待输入"),
            (TaskStatus.WAITING_APPROVAL, "等待批准"),
            (TaskStatus.COMPLETED, "已完成"),
            (TaskStatus.ERROR, "失败"),
            (TaskStatus.IDLE, "重置"),
        ):
            label = (
                chinese_label
                if self.language_manager.language == ZH_CN
                else (
                    "Reset"
                    if status is TaskStatus.IDLE
                    else status_name(status, self.language_manager)
                )
            )
            action = state_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=status.value: self.action_requested.emit(
                    f"status:{value}", self.task.id
                )
            )
        copy_action = menu.addAction(
            "复制任务信息"
            if self.language_manager.language == ZH_CN
            else self.language_manager.text("task.copy")
        )
        copy_action.triggered.connect(lambda: self.action_requested.emit("copy", self.task.id))
        if self.task.id.startswith(("codex:", "kimi:", "kimi_desktop:", "opencode:")):
            rename_action = menu.addAction(
                "重命名任务"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("task.rename")
            )
            rename_action.triggered.connect(
                lambda: self.action_requested.emit("rename", self.task.id)
            )
            remove_action = menu.addAction(
                "从面板移除"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("task.remove")
            )
            remove_action.triggered.connect(lambda: self.remove_requested.emit(self.task.id))
        return menu

    def contextMenuEvent(self, event: object) -> None:
        self.create_context_menu().exec(event.globalPos())  # type: ignore[attr-defined]

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.action_requested.emit("select", self.task.id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.action_requested.emit("voice", self.task.id)
        super().mouseDoubleClickEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        language = window.language_manager
        self.setWindowTitle(language.text("settings.title"))
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(language.text("settings.opacity")))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(35, 100)
        slider.setValue(round(window.windowOpacity() * 100))
        slider.valueChanged.connect(lambda value: window.setWindowOpacity(value / 100))
        layout.addWidget(slider)
        layout.addWidget(QLabel(language.text("settings.config_file", path=DEFAULT_CONFIG_PATH)))
        layout.addWidget(QLabel(window.accessibility_status_text()))
        if not window.accessibility_trusted:
            accessibility = QPushButton(language.text("settings.accessibility"))
            accessibility.clicked.connect(window.open_accessibility_settings)
            layout.addWidget(accessibility)
        compact = QPushButton(language.text("settings.compact"))
        compact.clicked.connect(lambda: window.set_compact(not window.compact_mode))
        layout.addWidget(compact)
        top = QPushButton(language.text("settings.topmost"))
        top.clicked.connect(window.toggle_always_on_top)
        layout.addWidget(top)
        dock = QPushButton(language.text("settings.dock"))
        dock.clicked.connect(window.dock_top_right)
        layout.addWidget(dock)
        codex_tasks = QPushButton(
            language.text("settings.select_codex")
            + language.text(
                "settings.selected_counts",
                selected=len(window.codex_selected_ids),
                automatic=len(window.codex_auto_active_ids()),
            )
        )
        codex_tasks.clicked.connect(window.open_codex_task_selector)
        layout.addWidget(codex_tasks)
        kimi_tasks = QPushButton(
            language.text("settings.select_kimi_code")
            + language.text(
                "settings.selected_counts",
                selected=len(window.kimi_selected_ids),
                automatic=len(window.kimi_auto_active_ids()),
            )
        )
        kimi_tasks.clicked.connect(window.open_kimi_task_selector)
        layout.addWidget(kimi_tasks)
        kimi_desktop_tasks = QPushButton(
            language.text("settings.select_kimi_desktop")
            + language.text(
                "settings.selected_counts",
                selected=len(window.kimi_desktop_selected_ids),
                automatic=len(window.kimi_desktop_auto_active_ids()),
            )
        )
        kimi_desktop_tasks.clicked.connect(window.open_kimi_desktop_task_selector)
        layout.addWidget(kimi_desktop_tasks)
        opencode_tasks = QPushButton(
            language.text("settings.select_opencode")
            + language.text(
                "settings.selected_counts",
                selected=len(window.opencode_selected_ids),
                automatic=len(window.opencode_auto_active_ids()),
            )
        )
        opencode_tasks.clicked.connect(window.open_opencode_task_selector)
        layout.addWidget(opencode_tasks)
        rotate_credentials = QPushButton(language.text("settings.rotate_api"))
        rotate_credentials.clicked.connect(window.rotate_credentials)
        layout.addWidget(rotate_credentials)
        if window.quota_service is not None:
            layout.addWidget(QLabel(language.text("settings.kimi_fallback")))
            api_key = QLineEdit()
            api_key.setPlaceholderText("sk-kimi-…")
            api_key.setEchoMode(QLineEdit.EchoMode.Password)
            layout.addWidget(api_key)
            save_key = QPushButton(language.text("settings.save_kimi_key"))
            save_key.clicked.connect(lambda: window.save_kimi_api_key(api_key.text()))
            layout.addWidget(save_key)
        if window.kimi_web_quota_service is not None:
            login_key = (
                "settings.kimi_edge_login" if sys.platform == "win32" else "settings.kimi_web_login"
            )
            web_login = QPushButton(language.text(login_key))
            web_login.clicked.connect(window.open_kimi_web_login)
            layout.addWidget(web_login)
        if window.quota_service is not None or window.kimi_web_quota_service is not None:
            kimi_logout = QPushButton(language.text("settings.kimi_logout"))
            kimi_logout.clicked.connect(window.kimi_logout)
            layout.addWidget(kimi_logout)
        if window.opencode_web_quota_service is not None:
            opencode_login = QPushButton(language.text("settings.opencode_web_login"))
            opencode_login.clicked.connect(window.open_opencode_web_login)
            layout.addWidget(opencode_login)
            opencode_logout = QPushButton(language.text("settings.opencode_logout"))
            opencode_logout.clicked.connect(window.opencode_logout)
            layout.addWidget(opencode_logout)
        layout.addWidget(QLabel(language.text("settings.visible_agents")))
        labels = {
            "codex_cli": "Codex",
            "claude_code": "Claude Code",
            "kimi_code": "Kimi Code",
            "kimi_desktop": "Kimi Desktop",
            "opencode_cli": "OpenCode",
            "generic_cli": language.text("settings.generic_cli"),
        }
        for agent_type, label in labels.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(agent_type in window.visible_agent_types)
            checkbox.toggled.connect(
                lambda checked, value=agent_type: window.set_agent_visible(value, checked)
            )
            layout.addWidget(checkbox)
        close = QPushButton(language.text("common.done"))
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class TaskSelectionDialog(QDialog):
    def __init__(
        self,
        sessions: list[tuple[str, str, datetime]],
        selected_ids: set[str],
        auto_active_ids: set[str],
        parent: QWidget,
        *,
        window_title_key: str,
    ) -> None:
        super().__init__(parent)
        parent_language = getattr(parent, "language_manager", None)
        self.language_manager = (
            parent_language
            if isinstance(parent_language, LanguageManager)
            else LanguageManager(ZH_CN)
        )
        self.setWindowTitle(self.language_manager.text(window_title_key))
        self.setMinimumSize(540, 460)
        self._auto_active_ids = set(auto_active_ids)
        self._restore_auto = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.language_manager.text("selector.running_hint")))
        self.tasks = QListWidget()
        for session_id, title, updated_at in sessions:
            automatic = session_id in self._auto_active_ids
            automatic_label = (
                self.language_manager.text("selector.auto_running") if automatic else ""
            )
            item = QListWidgetItem(
                f"{title}\n{updated_at.astimezone().strftime('%Y-%m-%d %H:%M')}{automatic_label}"
            )
            item.setData(Qt.ItemDataRole.UserRole, session_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if session_id in selected_ids else Qt.CheckState.Unchecked
            )
            self.tasks.addItem(item)
        layout.addWidget(self.tasks)
        select_all = QPushButton(self.language_manager.text("selector.select_all"))
        select_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        clear_all = QPushButton(self.language_manager.text("selector.clear_all"))
        clear_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        restore_auto = QPushButton(self.language_manager.text("selector.restore_auto"))
        restore_auto.clicked.connect(self.restore_automatic_detection)
        buttons = QHBoxLayout()
        buttons.addWidget(select_all)
        buttons.addWidget(clear_all)
        buttons.addWidget(restore_auto)
        buttons.addStretch()
        cancel = QPushButton(self.language_manager.text("common.cancel"))
        cancel.clicked.connect(self.reject)
        apply = QPushButton(self.language_manager.text("selector.start_monitoring"))
        apply.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def selected_ids(self) -> set[str]:
        return {
            str(self.tasks.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.tasks.count())
            if self.tasks.item(index).checkState() is Qt.CheckState.Checked
        }

    def _set_all(self, state: Qt.CheckState) -> None:
        for index in range(self.tasks.count()):
            self.tasks.item(index).setCheckState(state)

    def restore_automatic_detection(self) -> None:
        self._restore_auto = True
        for index in range(self.tasks.count()):
            item = self.tasks.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole)) in self._auto_active_ids:
                item.setCheckState(Qt.CheckState.Checked)

    def restore_auto_requested(self) -> bool:
        return self._restore_auto


class CodexTaskSelectionDialog(TaskSelectionDialog):
    def __init__(
        self,
        sessions: list[CodexSession],
        selected_ids: set[str],
        auto_active_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(
            [(session.conversation_id, session.title, session.updated_at) for session in sessions],
            selected_ids,
            auto_active_ids,
            parent,
            window_title_key="settings.select_codex",
        )


class KimiTaskSelectionDialog(TaskSelectionDialog):
    def __init__(
        self,
        sessions: list[KimiSession],
        selected_ids: set[str],
        auto_active_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(
            [(session.session_id, session.title, session.updated_at) for session in sessions],
            selected_ids,
            auto_active_ids,
            parent,
            window_title_key="settings.select_kimi_code",
        )


class OpenCodeTaskSelectionDialog(TaskSelectionDialog):
    def __init__(
        self,
        sessions: list[OpenCodeSession],
        selected_ids: set[str],
        auto_active_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(
            [(session.session_id, session.title, session.updated_at) for session in sessions],
            selected_ids,
            auto_active_ids,
            parent,
            window_title_key="settings.select_opencode",
        )


class KimiDesktopTaskSelectionDialog(TaskSelectionDialog):
    def __init__(
        self,
        sessions: list[KimiDesktopSession],
        selected_ids: set[str],
        auto_active_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(
            [(session.session_id, session.title, session.updated_at) for session in sessions],
            selected_ids,
            auto_active_ids,
            parent,
            window_title_key="settings.select_kimi_desktop",
        )


class MainWindow(QWidget):
    state_received = Signal(object)
    external_action = Signal(str, str)
    automation_finished = Signal(str, str, object)
    discovery_health_received = Signal(object)
    kimi_discovery_health_received = Signal(object)
    kimi_desktop_discovery_health_received = Signal(object)
    opencode_discovery_health_received = Signal(object)
    settings_keys = {
        "geometry",
        "compact_mode",
        "always_on_top",
        "opacity",
        "visible_agents",
        "agent_visibility_migrated_v2",
        "agent_visibility_migrated_v3",
    }

    def __init__(
        self,
        manager: TaskManager,
        automation: AutomationExecutor,
        *,
        enable_tray: bool = True,
        codex_sessions: Callable[[], list[CodexSession]] | None = None,
        codex_auto_active_ids: Callable[[], set[str]] | None = None,
        codex_retained_ids: Callable[[], set[str]] | None = None,
        codex_muted_ids: Callable[[], set[str]] | None = None,
        set_codex_monitoring_preferences: Callable[[set[str], set[str], set[str]], None]
        | None = None,
        kimi_sessions: Callable[[], list[KimiSession]] | None = None,
        kimi_auto_active_ids: Callable[[], set[str]] | None = None,
        kimi_retained_ids: Callable[[], set[str]] | None = None,
        kimi_muted_ids: Callable[[], set[str]] | None = None,
        set_kimi_monitoring_preferences: Callable[[set[str], set[str], set[str]], None]
        | None = None,
        kimi_desktop_sessions: Callable[[], list[KimiDesktopSession]] | None = None,
        kimi_desktop_auto_active_ids: Callable[[], set[str]] | None = None,
        kimi_desktop_retained_ids: Callable[[], set[str]] | None = None,
        kimi_desktop_muted_ids: Callable[[], set[str]] | None = None,
        set_kimi_desktop_monitoring_preferences: Callable[[set[str], set[str], set[str]], None]
        | None = None,
        rotate_api_token_callback: Callable[[], str] | None = None,
        discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_discovery_health: (
            Callable[[Callable[[DiscoveryHealth], None]], Callable[[], None]] | None
        ) = None,
        kimi_discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_kimi_discovery_health: (
            Callable[[Callable[[DiscoveryHealth], None]], Callable[[], None]] | None
        ) = None,
        kimi_desktop_discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_kimi_desktop_discovery_health: (
            Callable[[Callable[[DiscoveryHealth], None]], Callable[[], None]] | None
        ) = None,
        opencode_sessions: Callable[[], list[OpenCodeSession]] | None = None,
        opencode_auto_active_ids: Callable[[], set[str]] | None = None,
        opencode_retained_ids: Callable[[], set[str]] | None = None,
        opencode_muted_ids: Callable[[], set[str]] | None = None,
        set_opencode_monitoring_preferences: (
            Callable[[set[str], set[str], set[str]], None] | None
        ) = None,
        opencode_discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_opencode_discovery_health: (
            Callable[[HealthSubscriber], Callable[[], None]] | None
        ) = None,
        discovery_log_path: str = str(APP_SUPPORT_DIR / "logs" / "app.log"),
        accessibility_trusted: bool = True,
        open_accessibility_settings_callback: Callable[[], None] | None = None,
        settings: QSettings | None = None,
        quota_service: QuotaService | None = None,
        kimi_web_quota_service: KimiWebQuotaService | None = None,
        codex_quota_service: CodexQuotaService | None = None,
        opencode_web_quota_service: OpenCodeWebQuotaService | None = None,
        open_url: Callable[[str], None] | None = None,
        language_manager: LanguageManager | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.automation = automation
        self.config = manager.config
        self.selected_task_id = self.config.tasks[0].id if self.config.tasks else ""
        self.compact_mode = self.config.app.compact_mode
        self.always_on_top = self.config.app.always_on_top
        self._drag_position: QPoint | None = None
        self._adaptive_resize_pending = False
        self._quitting = False
        self._settings = settings or QSettings("AACC", "AACC")
        self.language_manager = language_manager or LanguageManager(ZH_CN, self._settings)
        self._codex_sessions = codex_sessions or (lambda: [])
        self._codex_auto_active_ids = codex_auto_active_ids or (lambda: set())
        self._codex_retained_ids = codex_retained_ids or (lambda: set())
        self._codex_muted_ids = codex_muted_ids or (lambda: set())
        self._set_codex_monitoring_preferences = set_codex_monitoring_preferences or (
            lambda _manual_ids, _retained_ids, _muted_ids: None
        )
        self._kimi_sessions = kimi_sessions or (lambda: [])
        self._kimi_auto_active_ids = kimi_auto_active_ids or (lambda: set())
        self._kimi_retained_ids = kimi_retained_ids or (lambda: set())
        self._kimi_muted_ids = kimi_muted_ids or (lambda: set())
        self._set_kimi_monitoring_preferences = set_kimi_monitoring_preferences or (
            lambda _manual_ids, _retained_ids, _muted_ids: None
        )
        self._kimi_desktop_sessions = kimi_desktop_sessions or (lambda: [])
        self._kimi_desktop_auto_active_ids = kimi_desktop_auto_active_ids or (lambda: set())
        self._kimi_desktop_retained_ids = kimi_desktop_retained_ids or (lambda: set())
        self._kimi_desktop_muted_ids = kimi_desktop_muted_ids or (lambda: set())
        self._set_kimi_desktop_monitoring_preferences = set_kimi_desktop_monitoring_preferences or (
            lambda _manual_ids, _retained_ids, _muted_ids: None
        )
        self._opencode_sessions = opencode_sessions or (lambda: [])
        self._opencode_auto_active_ids = opencode_auto_active_ids or (lambda: set())
        self._opencode_retained_ids = opencode_retained_ids or (lambda: set())
        self._opencode_muted_ids = opencode_muted_ids or (lambda: set())
        self._set_opencode_monitoring_preferences = set_opencode_monitoring_preferences or (
            lambda _m, _r, _u: None
        )
        self._unsubscribe_opencode_discovery_health = (
            subscribe_opencode_discovery_health(self.opencode_discovery_health_received.emit)
            if subscribe_opencode_discovery_health is not None
            else lambda: None
        )
        self._rotate_api_token = rotate_api_token_callback or (lambda: self.config.app.api.token)
        self._discovery_healths: dict[str, DiscoveryHealth] = {}
        for health in (
            (discovery_health or DiscoveryHealth)(),
            (kimi_discovery_health or (lambda: DiscoveryHealth(brand="Kimi")))(),
            (kimi_desktop_discovery_health or (lambda: DiscoveryHealth(brand="Kimi Desktop")))(),
            (opencode_discovery_health or (lambda: DiscoveryHealth(brand="OpenCode")))(),
        ):
            self._discovery_healths[health.brand] = health
        self._discovery_log_path = discovery_log_path
        self.accessibility_trusted = accessibility_trusted
        self._open_accessibility_settings = open_accessibility_settings_callback or (lambda: None)
        self.quota_service = quota_service
        self.kimi_web_quota_service = kimi_web_quota_service
        self.codex_quota_service = codex_quota_service
        self.opencode_web_quota_service = opencode_web_quota_service
        self._latest_opencode_quota: OpenCodeQuota | None = None
        self._opencode_authorized = False
        self._latest_kimi_code_quota: KimiQuota | None = None
        self._latest_kimi_web_quota: KimiQuota | None = None
        self._kimi_web_authorized = False
        self._last_restore_quota_refresh = float("-inf")
        self._open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self._oauth_dialog: KimiOAuthDialog | None = None
        self._subtitle_presentation: _SubtitlePresentation | None = None
        self.quota_bar: QuotaBar | None = None
        self.codex_quota_bar: CodexQuotaBar | None = None
        self.opencode_quota_bar: OpenCodeQuotaBar | None = None
        self._unsubscribe_discovery_health = (
            subscribe_discovery_health(self.discovery_health_received.emit)
            if subscribe_discovery_health is not None
            else lambda: None
        )
        self._unsubscribe_kimi_discovery_health = (
            subscribe_kimi_discovery_health(self.kimi_discovery_health_received.emit)
            if subscribe_kimi_discovery_health is not None
            else lambda: None
        )
        self._unsubscribe_kimi_desktop_discovery_health = (
            subscribe_kimi_desktop_discovery_health(
                self.kimi_desktop_discovery_health_received.emit
            )
            if subscribe_kimi_desktop_discovery_health is not None
            else lambda: None
        )
        saved_codex_tasks = self._settings.value(
            "codex_manual_tasks", self._settings.value("codex_selected_tasks")
        )
        if isinstance(saved_codex_tasks, str):
            self.codex_manual_ids = {saved_codex_tasks}
        elif isinstance(saved_codex_tasks, list):
            self.codex_manual_ids = {str(value) for value in saved_codex_tasks}
        else:
            self.codex_manual_ids = set()
        saved_retained_tasks = self._settings.value("codex_retained_tasks")
        if isinstance(saved_retained_tasks, str):
            self.codex_retained_ids = {saved_retained_tasks}
        elif isinstance(saved_retained_tasks, list):
            self.codex_retained_ids = {str(value) for value in saved_retained_tasks}
        else:
            self.codex_retained_ids = set()
        saved_muted_tasks = self._settings.value("codex_muted_tasks")
        if isinstance(saved_muted_tasks, str):
            self.codex_muted_ids = {saved_muted_tasks}
        elif isinstance(saved_muted_tasks, list):
            self.codex_muted_ids = {str(value) for value in saved_muted_tasks}
        else:
            self.codex_muted_ids = set()
        self._apply_codex_monitoring_preferences()
        saved_kimi_tasks = self._settings.value("kimi_manual_tasks")
        if isinstance(saved_kimi_tasks, str):
            self.kimi_manual_ids = {saved_kimi_tasks}
        elif isinstance(saved_kimi_tasks, list):
            self.kimi_manual_ids = {str(value) for value in saved_kimi_tasks}
        else:
            self.kimi_manual_ids = set()
        saved_kimi_retained_tasks = self._settings.value("kimi_retained_tasks")
        if isinstance(saved_kimi_retained_tasks, str):
            self.kimi_retained_ids = {saved_kimi_retained_tasks}
        elif isinstance(saved_kimi_retained_tasks, list):
            self.kimi_retained_ids = {str(value) for value in saved_kimi_retained_tasks}
        else:
            self.kimi_retained_ids = set()
        saved_kimi_muted_tasks = self._settings.value("kimi_muted_tasks")
        if isinstance(saved_kimi_muted_tasks, str):
            self.kimi_muted_ids = {saved_kimi_muted_tasks}
        elif isinstance(saved_kimi_muted_tasks, list):
            self.kimi_muted_ids = {str(value) for value in saved_kimi_muted_tasks}
        else:
            self.kimi_muted_ids = set()
        self._apply_kimi_monitoring_preferences()
        saved_kimi_desktop_tasks = self._settings.value("kimi_desktop_manual_tasks")
        if isinstance(saved_kimi_desktop_tasks, str):
            self.kimi_desktop_manual_ids = {saved_kimi_desktop_tasks}
        elif isinstance(saved_kimi_desktop_tasks, list):
            self.kimi_desktop_manual_ids = {str(value) for value in saved_kimi_desktop_tasks}
        else:
            self.kimi_desktop_manual_ids = set()
        saved_kimi_desktop_retained = self._settings.value("kimi_desktop_retained_tasks")
        if isinstance(saved_kimi_desktop_retained, str):
            self.kimi_desktop_retained_ids = {saved_kimi_desktop_retained}
        elif isinstance(saved_kimi_desktop_retained, list):
            self.kimi_desktop_retained_ids = {str(value) for value in saved_kimi_desktop_retained}
        else:
            self.kimi_desktop_retained_ids = set()
        saved_kimi_desktop_muted = self._settings.value("kimi_desktop_muted_tasks")
        if isinstance(saved_kimi_desktop_muted, str):
            self.kimi_desktop_muted_ids = {saved_kimi_desktop_muted}
        elif isinstance(saved_kimi_desktop_muted, list):
            self.kimi_desktop_muted_ids = {str(value) for value in saved_kimi_desktop_muted}
        else:
            self.kimi_desktop_muted_ids = set()
        self._apply_kimi_desktop_monitoring_preferences()
        saved_opencode_tasks = self._settings.value("opencode_manual_tasks")
        if isinstance(saved_opencode_tasks, str):
            self.opencode_manual_ids = {saved_opencode_tasks}
        elif isinstance(saved_opencode_tasks, list):
            self.opencode_manual_ids = {str(value) for value in saved_opencode_tasks}
        else:
            self.opencode_manual_ids = set()
        saved_opencode_retained = self._settings.value("opencode_retained_tasks")
        if isinstance(saved_opencode_retained, str):
            self.opencode_retained_ids = {saved_opencode_retained}
        elif isinstance(saved_opencode_retained, list):
            self.opencode_retained_ids = {str(value) for value in saved_opencode_retained}
        else:
            self.opencode_retained_ids = set()
        saved_opencode_muted = self._settings.value("opencode_muted_tasks")
        if isinstance(saved_opencode_muted, str):
            self.opencode_muted_ids = {saved_opencode_muted}
        elif isinstance(saved_opencode_muted, list):
            self.opencode_muted_ids = {str(value) for value in saved_opencode_muted}
        else:
            self.opencode_muted_ids = set()
        self._apply_opencode_monitoring_preferences()
        saved_custom_names = self._settings.value("custom_task_names")
        try:
            parsed_names = json.loads(saved_custom_names) if saved_custom_names else {}
        except (TypeError, json.JSONDecodeError):
            parsed_names = {}
        self.custom_task_names: dict[str, str] = (
            {str(key): str(value) for key, value in parsed_names.items()}
            if isinstance(parsed_names, dict)
            else {}
        )
        saved_agents = self._settings.value("visible_agents")
        if isinstance(saved_agents, str):
            self.visible_agent_types = {saved_agents}
        elif isinstance(saved_agents, list):
            self.visible_agent_types = {str(value) for value in saved_agents}
        else:
            self.visible_agent_types = set(self.config.app.visible_agent_types)
        # One-time upgrade seeding: agent types introduced after earlier
        # releases default to visible, then the stored value is authoritative.
        if not self._settings.value("agent_visibility_migrated_v2", False, type=bool):
            self.visible_agent_types.add("kimi_code")
            self.visible_agent_types.add("kimi_desktop")
            self._settings.setValue("agent_visibility_migrated_v2", True)
            self._settings.setValue("visible_agents", sorted(self.visible_agent_types))
        if not self._settings.value("agent_visibility_migrated_v3", False, type=bool):
            self.visible_agent_types.add("opencode_cli")
            self._settings.setValue("agent_visibility_migrated_v3", True)
            self._settings.setValue("visible_agents", sorted(self.visible_agent_types))
        self._unsubscribe = self.manager.subscribe(self.state_received.emit)
        self.state_received.connect(self._apply_state)
        self.external_action.connect(self._perform_action)
        self.automation_finished.connect(self._automation_completed)
        self.discovery_health_received.connect(self._apply_discovery_health)
        self.kimi_discovery_health_received.connect(self._apply_discovery_health)
        self.kimi_desktop_discovery_health_received.connect(self._apply_discovery_health)
        self.opencode_discovery_health_received.connect(self._apply_discovery_health)

        saved_top = self._settings.value("always_on_top", self.always_on_top, type=bool)
        self.always_on_top = bool(saved_top)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if self.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("AI Agent Control Center")
        self.setMinimumWidth(350)
        self.setMinimumHeight(300)
        self.resize(420, 590)
        self.setWindowOpacity(self.config.app.opacity)

        panel = QFrame(self)
        panel.setObjectName("panel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("AI AGENT CONTROL CENTER")
        title.setObjectName("title")
        self.subtitle = QLabel("LOCAL · SECURE · READY")
        self.subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.language_button = QPushButton()
        self.language_button.setObjectName("languageButton")
        self.language_button.clicked.connect(self.toggle_language)
        self.about_button = QPushButton("ⓘ")
        self.about_button.clicked.connect(self.show_about)
        self.settings_button = QPushButton("⚙")
        self.settings_button.clicked.connect(self.open_settings)
        self.hide_button = QPushButton("—")
        self.hide_button.clicked.connect(self.hide)
        self.quit_button = QPushButton("⏻")
        self.quit_button.clicked.connect(self.quit_application)
        for button in (
            self.language_button,
            self.about_button,
            self.settings_button,
            self.hide_button,
            self.quit_button,
        ):
            if button is not self.language_button:
                button.setObjectName("headerButton")
            button.setFixedSize(28, 28)
            header.addWidget(button)
        layout.addLayout(header)

        if self.codex_quota_service is not None:
            self.codex_quota_bar = CodexQuotaBar(self.language_manager)
            self.codex_quota_bar.clicked.connect(self.codex_quota_service.refresh_now)
            layout.addWidget(self.codex_quota_bar)
            self.codex_quota_service.quota_updated.connect(self._on_codex_quota_updated)
            self.codex_quota_service.error_occurred.connect(self._on_codex_quota_error)
        if self.quota_service is not None or self.kimi_web_quota_service is not None:
            self.quota_bar = QuotaBar(self.language_manager)
            self.quota_bar.clicked.connect(self._on_quota_bar_clicked)
            layout.addWidget(self.quota_bar)
        if self.quota_service is not None:
            self.quota_service.quota_updated.connect(self._on_quota_updated)
            self.quota_service.auth_state_changed.connect(self._on_quota_auth_state)
            self.quota_service.oauth_code_ready.connect(self._on_oauth_code_ready)
            self.quota_service.oauth_finished.connect(self._on_oauth_finished)
            self.quota_service.error_occurred.connect(self._on_kimi_code_quota_error)
            self._on_quota_auth_state(self.quota_service.state())
        if self.kimi_web_quota_service is not None:
            self.kimi_web_quota_service.quota_updated.connect(self._on_kimi_web_quota_updated)
            self.kimi_web_quota_service.login_state_changed.connect(self._on_kimi_web_login_state)
            self.kimi_web_quota_service.web_error_occurred.connect(self._on_kimi_web_quota_error)
            self.kimi_web_quota_service.code_error_occurred.connect(self._on_kimi_code_quota_error)
        if self.opencode_web_quota_service is not None:
            self.opencode_quota_bar = OpenCodeQuotaBar(self.language_manager)
            self.opencode_quota_bar.clicked.connect(self._on_opencode_quota_bar_clicked)
            layout.addWidget(self.opencode_quota_bar)
            self.opencode_web_quota_service.quota_updated.connect(self._on_opencode_quota_updated)
            self.opencode_web_quota_service.login_state_changed.connect(
                self._on_opencode_login_state
            )
            self.opencode_web_quota_service.error_occurred.connect(self._on_opencode_quota_error)

        self.discovery_warning = QFrame()
        self.discovery_warning.setObjectName("discoveryWarning")
        discovery_warning_layout = QHBoxLayout(self.discovery_warning)
        discovery_warning_layout.setContentsMargins(10, 8, 10, 8)
        self.discovery_warning_label = QLabel()
        self.discovery_warning_label.setObjectName("discoveryWarningLabel")
        self.discovery_warning_label.setWordWrap(True)
        self.copy_diagnostics_button = QPushButton("复制详情")
        self.copy_diagnostics_button.setObjectName("copyDiagnosticsButton")
        self.copy_diagnostics_button.clicked.connect(self.copy_discovery_diagnostics)
        discovery_warning_layout.addWidget(self.discovery_warning_label, 1)
        discovery_warning_layout.addWidget(self.copy_diagnostics_button)
        layout.addWidget(self.discovery_warning)
        self._refresh_discovery_warning()

        self.cards: dict[str, TaskCard] = {}
        self._card_order_ids: list[str] = []
        self._layout_group_ids: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(9)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.task_summary_label = QLabel("运行中：0 · 已完成：0 · 显示：0")
        self.task_summary_label.setObjectName("taskSummary")
        self.empty_tasks_label = QLabel(
            "未选择 Codex / Kimi Code / Kimi Desktop 任务 · 点击 ⚙ 选择监控任务"
        )
        self.empty_tasks_label.setObjectName("emptyTasks")
        self.empty_tasks_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.running_group_label = QLabel("运行中")
        self.running_group_label.setObjectName("taskGroupLabel")
        self.running_cards_widget = QWidget()
        self.running_cards_layout = QVBoxLayout(self.running_cards_widget)
        self.running_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.running_cards_layout.setSpacing(9)
        self.retained_header = QWidget()
        retained_header_layout = QHBoxLayout(self.retained_header)
        retained_header_layout.setContentsMargins(0, 0, 0, 0)
        self.retained_group_label = QLabel("已完成 · 保留")
        self.retained_group_label.setObjectName("taskGroupLabel")
        self.clear_retained_button = QPushButton("全部清除")
        self.clear_retained_button.setObjectName("clearRetainedButton")
        self.clear_retained_button.clicked.connect(self.clear_retained_tasks)
        retained_header_layout.addWidget(self.retained_group_label)
        retained_header_layout.addStretch()
        retained_header_layout.addWidget(self.clear_retained_button)
        self.retained_cards_widget = QWidget()
        self.retained_cards_layout = QVBoxLayout(self.retained_cards_widget)
        self.retained_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.retained_cards_layout.setSpacing(9)
        self.cards_layout.addWidget(self.task_summary_label)
        self.cards_layout.addWidget(self.empty_tasks_label)
        self.cards_layout.addWidget(self.running_group_label)
        self.cards_layout.addWidget(self.running_cards_widget)
        self.cards_layout.addWidget(self.retained_header)
        self.cards_layout.addWidget(self.retained_cards_widget)
        self.cards_layout.addStretch()
        self.cards_container.setLayout(self.cards_layout)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setObjectName("cardsScroll")
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_scroll.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll, 1)
        footer = QHBoxLayout()
        self.connection_label = QLabel(f"● API {self.config.app.api.host}")
        self.connection_label.setObjectName("footer")
        footer.addWidget(self.connection_label)
        footer.addStretch()
        footer.addWidget(QSizeGrip(self))
        layout.addLayout(footer)
        self._apply_styles()

        saved_geometry = self._settings.value("geometry")
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
        else:
            QTimer.singleShot(0, self.dock_top_right)
        self.set_compact(bool(self._settings.value("compact_mode", self.compact_mode, type=bool)))
        saved_opacity = self._settings.value("opacity", self.windowOpacity())
        if isinstance(saved_opacity, (int, float, str)):
            self.setWindowOpacity(float(saved_opacity))

        self.tray: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.tray_show_action: QAction | None = None
        self.tray_compact_action: QAction | None = None
        self.tray_quit_action: QAction | None = None
        if enable_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._create_tray()
        # macOS: clicking the Dock icon (or Cmd-Tabbing back) activates the
        # app but does not unhide a hidden panel; restore it like other Mac
        # apps do.
        app = QGuiApplication.instance()
        if sys.platform == "darwin" and isinstance(app, QGuiApplication):
            app.applicationStateChanged.connect(self.handle_app_state_change)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.sync_cards()
        self._unsubscribe_language = self.language_manager.subscribe(
            self.retranslate_ui,
            component="main_window",
        )
        self.retranslate_ui()

    def _apply_styles(self) -> None:
        self.setStyleSheet(load_stylesheet())

    def toggle_language(self) -> None:
        self.language_manager.set_language(other_language(self.language_manager.language))

    def retranslate_ui(self) -> None:
        english_target = self.language_manager.language == ZH_CN
        self.language_button.setText("EN" if english_target else "中")
        self.language_button.setToolTip("Switch to English" if english_target else "切换到中文")
        self.about_button.setToolTip(self.language_manager.text("header.about"))
        self.settings_button.setToolTip(self.language_manager.text("header.settings"))
        self.hide_button.setToolTip(self.language_manager.text("header.hide"))
        self.quit_button.setToolTip(self.language_manager.text("header.quit"))
        self.running_group_label.setText(self.language_manager.text("group.running"))
        self.retained_group_label.setText(
            "已完成 · 保留" if self.language_manager.language == ZH_CN else "Completed · Retained"
        )
        self.clear_retained_button.setText(self.language_manager.text("group.clear_all"))
        self.empty_tasks_label.setText(
            "未选择 Codex / Kimi Code / Kimi Desktop / OpenCode 任务 · 点击 ⚙ 选择监控任务"
            if self.language_manager.language == ZH_CN
            else (
                "No Codex / Kimi Code / Kimi Desktop / OpenCode tasks selected "
                "· Click ⚙ to select tasks"
            )
        )
        self.copy_diagnostics_button.setText(
            "复制详情" if self.language_manager.language == ZH_CN else "Copy details"
        )
        self._render_task_summary()
        for card in self.cards.values():
            card.retranslate_ui()
        if self.quota_bar is not None:
            self.quota_bar.retranslate_ui()
        if self.codex_quota_bar is not None:
            self.codex_quota_bar.retranslate_ui()
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.retranslate_ui()
        if self.tray_show_action is not None:
            self.tray_show_action.setText(self.language_manager.text("tray.show_hide"))
        if self.tray_compact_action is not None:
            self.tray_compact_action.setText(self.language_manager.text("compact.toggle"))
        if self.tray_quit_action is not None:
            self.tray_quit_action.setText(self.language_manager.text("tray.quit"))
        self._render_subtitle_presentation()

    def _set_subtitle_presentation(
        self,
        translation_key: str,
        *,
        uppercase: bool = True,
        prefix: str = "",
        **values: object,
    ) -> None:
        self._subtitle_presentation = _SubtitlePresentation(
            key=translation_key,
            values=dict(values),
            uppercase=uppercase,
            prefix=prefix,
        )
        self._render_subtitle_presentation()

    def _render_subtitle_presentation(self) -> None:
        presentation = self._subtitle_presentation
        if presentation is None:
            return
        values = {
            name: (
                status_name(value, self.language_manager)
                if isinstance(value, TaskStatus)
                else value
            )
            for name, value in presentation.values.items()
        }
        text = self.language_manager.text(presentation.key, **values)
        if presentation.uppercase:
            text = text.upper()
        self.subtitle.setText(f"{presentation.prefix}{text}")

    def _set_external_subtitle(self, text: str) -> None:
        self._subtitle_presentation = None
        self.subtitle.setText(text)

    def _render_task_summary(self) -> None:
        running_count, terminal_count = (len(group) for group in self._layout_group_ids)
        if self.language_manager.language == ZH_CN:
            self.task_summary_label.setText(
                f"运行中：{running_count} · 已完成：{terminal_count} · "
                f"显示：{len(self._card_order_ids)}"
            )
        else:
            shown_count = len(self._card_order_ids)
            summary_key = "summary.tasks.one" if shown_count == 1 else "summary.tasks.other"
            shown = self.language_manager.text(summary_key, count=shown_count)
            self.task_summary_label.setText(
                f"{self.language_manager.text('group.running')}: {running_count} · "
                f"{self.language_manager.text('group.completed')}: {terminal_count} · "
                f"{shown}"
            )

    def _create_tray(self) -> None:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#4d9fff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(3, 3, 18, 18)
        painter.setPen(QColor("white"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "A")
        painter.end()
        self.tray = QSystemTrayIcon(QIcon(pixmap), self)
        menu = QMenu(self)
        self.tray_menu = menu
        self.tray_show_action = menu.addAction("")
        self.tray_show_action.triggered.connect(self.toggle_visible)
        self.tray_compact_action = menu.addAction("")
        self.tray_compact_action.triggered.connect(lambda: self.set_compact(not self.compact_mode))
        menu.addSeparator()
        self.tray_quit_action = menu.addAction("")
        self.tray_quit_action.triggered.connect(self.quit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.retranslate_ui()
        self.tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason is QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def refresh(self) -> None:
        if self.manager.closed:
            self._timer.stop()
            return
        self._sync_codex_retained_ids()
        self._sync_codex_muted_ids()
        self._sync_kimi_retained_ids()
        self._sync_kimi_muted_ids()
        self._sync_kimi_desktop_retained_ids()
        self._sync_kimi_desktop_muted_ids()
        self._sync_opencode_retained_ids()
        self._sync_opencode_muted_ids()
        self.sync_cards()
        for state in self.manager.list():
            self._apply_state(state)

    def _visible_tasks(self) -> list[TaskConfig]:
        tasks = [
            task
            for task in self.manager.task_configs()
            if task.enabled and task.agent.type in self.visible_agent_types
        ]
        tasks = [
            task
            for task in tasks
            if task.agent.type != "codex_cli"
            or (
                task.id.startswith("codex:")
                and task.id.removeprefix("codex:") in self.codex_selected_ids
            )
        ]
        tasks = [
            task
            for task in tasks
            if task.agent.type != "kimi_code"
            or (
                task.id.startswith("kimi:")
                and task.id.removeprefix("kimi:") in self.kimi_selected_ids
            )
        ]
        tasks = [
            task
            for task in tasks
            if task.agent.type != "kimi_desktop"
            or (
                task.id.startswith("kimi_desktop:")
                and task.id.removeprefix("kimi_desktop:") in self.kimi_desktop_selected_ids
            )
        ]
        tasks = [
            task
            for task in tasks
            if task.agent.type != "opencode_cli"
            or (
                task.id.startswith("opencode:")
                and task.id.removeprefix("opencode:") in self.opencode_selected_ids
            )
        ]
        return tasks

    @property
    def codex_selected_ids(self) -> set[str]:
        return (
            self.codex_manual_ids | self.codex_retained_ids | self.codex_auto_active_ids()
        ) - self.codex_muted_ids

    def codex_auto_active_ids(self) -> set[str]:
        return set(self._codex_auto_active_ids())

    @property
    def kimi_selected_ids(self) -> set[str]:
        return (
            self.kimi_manual_ids | self.kimi_retained_ids | self.kimi_auto_active_ids()
        ) - self.kimi_muted_ids

    def kimi_auto_active_ids(self) -> set[str]:
        return set(self._kimi_auto_active_ids())

    @property
    def kimi_desktop_selected_ids(self) -> set[str]:
        return (
            self.kimi_desktop_manual_ids
            | self.kimi_desktop_retained_ids
            | self.kimi_desktop_auto_active_ids()
        ) - self.kimi_desktop_muted_ids

    def kimi_desktop_auto_active_ids(self) -> set[str]:
        return set(self._kimi_desktop_auto_active_ids())

    def sync_cards(self) -> None:
        states = {state.task_id: state for state in self.manager.list()}
        visible = self._visible_tasks()
        desired_ids = {task.id for task in visible}
        for task_id, card in tuple(self.cards.items()):
            if task_id not in desired_ids:
                card.setParent(None)
                card.deleteLater()
                del self.cards[task_id]
        for task in visible:
            display_name = self.custom_task_names.get(task.id, task.name)
            existing_card = self.cards.get(task.id)
            if existing_card is None:
                new_card = TaskCard(
                    task,
                    states[task.id],
                    self.config.app.blink_attention,
                    display_name,
                    self.language_manager,
                )
                new_card.action_requested.connect(self._perform_action)
                new_card.remove_requested.connect(self._remove_task_requested)
                new_card.set_compact(self.compact_mode)
                self.cards[task.id] = new_card
            elif existing_card.display_name != display_name:
                existing_card.set_display_name(display_name)
        running_tasks, terminal_tasks = self._grouped_tasks(visible, states)
        # Layout only changes when group membership or order does; rebuilding
        # the same layout every second is wasted work.
        group_ids = (
            tuple(task.id for task in running_tasks),
            tuple(task.id for task in terminal_tasks),
        )
        if group_ids != self._layout_group_ids:
            self._rebuild_card_layout(self.running_cards_layout, running_tasks)
            self._rebuild_card_layout(self.retained_cards_layout, terminal_tasks)
            self._layout_group_ids = group_ids
        self._card_order_ids = [task.id for task in running_tasks + terminal_tasks]
        self._render_task_summary()
        self.empty_tasks_label.setVisible(not self.cards)
        self.task_summary_label.setVisible(bool(self.cards))
        self.running_group_label.setVisible(bool(running_tasks))
        self.running_cards_widget.setVisible(bool(running_tasks))
        self.retained_header.setVisible(bool(terminal_tasks))
        self.retained_cards_widget.setVisible(bool(terminal_tasks))
        self.clear_retained_button.setVisible(
            any(
                task.id.startswith(("codex:", "kimi:", "kimi_desktop:", "opencode:"))
                for task in terminal_tasks
            )
        )
        self._schedule_adaptive_resize()

    def _schedule_adaptive_resize(self) -> None:
        if self._adaptive_resize_pending:
            return
        self._adaptive_resize_pending = True
        QTimer.singleShot(0, self._resize_to_card_content)

    def _available_screen_height(self) -> int:
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        screen = screen or self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry().height() if screen is not None else self.height()

    def _resize_to_card_content(self) -> None:
        self._adaptive_resize_pending = False
        self.cards_layout.invalidate()
        self.cards_layout.activate()
        content_height = self.cards_layout.sizeHint().height()
        viewport_height = self.cards_scroll.viewport().height()
        chrome_height = max(0, self.height() - viewport_height)
        desired_height = max(self.minimumHeight(), chrome_height + content_height)
        height_cap = max(self.minimumHeight(), int(self._available_screen_height() * 0.8))
        overflow = desired_height > height_cap
        self.cards_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
            if overflow
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        target_height = min(desired_height, height_cap)
        if target_height != self.height():
            self.resize(self.width(), target_height)

    @staticmethod
    def _is_terminal(state: TaskState) -> bool:
        return state.status in TERMINAL_STATUSES

    def _grouped_tasks(
        self, visible: list[TaskConfig], states: dict[str, TaskState]
    ) -> tuple[list[TaskConfig], list[TaskConfig]]:
        active = [task for task in visible if not self._is_terminal(states[task.id])]
        terminal = [task for task in visible if self._is_terminal(states[task.id])]
        active.sort(key=lambda task: states[task.id].updated_at, reverse=True)
        terminal.sort(key=lambda task: states[task.id].updated_at, reverse=True)
        return active, terminal

    def _rebuild_card_layout(self, layout: QVBoxLayout, tasks: list[TaskConfig]) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for task in tasks:
            layout.addWidget(self.cards[task.id])

    def card_order(self) -> list[str]:
        return list(self._card_order_ids)

    def _apply_state(self, state: TaskState) -> None:
        card = self.cards.get(state.task_id)
        if card is not None:
            previous = card.state.status
            card.set_state(state)
            if (
                self.tray is not None
                and previous != state.status
                and state.status
                in {
                    TaskStatus.COMPLETED,
                    TaskStatus.ERROR,
                }
            ):
                self.tray.showMessage(
                    card.task.name,
                    (
                        _task_message_text(state, self.language_manager)
                        if state.message or AACC_MESSAGE_CATEGORY_KEY in state.metadata
                        else status_name(state.status, self.language_manager)
                    ),
                )

    def _on_quota_bar_clicked(self) -> None:
        if self.kimi_web_quota_service is not None:
            if not self._kimi_web_authorized:
                self.kimi_web_quota_service.open_login(self)
                return
            self.kimi_web_quota_service.refresh_now()
            return
        if self.quota_service is None:
            return
        if self.quota_service.state() == STATE_AUTHORIZED:
            self.quota_service.refresh_now()
        elif self.quota_service.state() != STATE_PENDING:
            self.quota_service.begin_oauth()

    def _on_codex_quota_updated(self, quota: object) -> None:
        if self.codex_quota_bar is not None and isinstance(quota, CodexQuotaSnapshot):
            self.codex_quota_bar.show_quota(quota)

    def _on_codex_quota_error(self, message: str) -> None:
        if self.codex_quota_bar is not None:
            self.codex_quota_bar.show_error(message)

    def _on_quota_updated(self, quota: object) -> None:
        if not isinstance(quota, KimiQuota):
            return
        self._latest_kimi_code_quota = quota
        if self.quota_bar is not None:
            self.quota_bar.clear_code_error()
        self._render_kimi_quota()

    def _on_kimi_web_quota_updated(self, quota: object) -> None:
        if not isinstance(quota, KimiQuota):
            return
        self._latest_kimi_web_quota = quota
        self._kimi_web_authorized = True
        if self.quota_bar is not None:
            self.quota_bar.clear_web_error()
        self._render_kimi_quota()

    def _render_kimi_quota(self) -> None:
        if self.quota_bar is None:
            return
        quota = merge_kimi_quota(
            self._latest_kimi_web_quota,
            self._latest_kimi_code_quota,
        )
        self.quota_bar.show_quota(quota, preserve_errors=True)

    def _on_kimi_web_login_state(self, authorized: bool) -> None:
        self._kimi_web_authorized = authorized
        if authorized:
            return
        self._latest_kimi_web_quota = None
        if self.quota_bar is None:
            return
        fallback = merge_kimi_quota(None, self._latest_kimi_code_quota)
        if fallback.status in (QuotaStatus.UNKNOWN, QuotaStatus.STALE):
            self.quota_bar.show_unauthorized()
            return
        self.quota_bar.show_quota(fallback)

    def _on_quota_auth_state(self, state: str) -> None:
        if self.quota_bar is None:
            return
        if state == STATE_PENDING:
            self.quota_bar.show_pending()
        elif state != STATE_AUTHORIZED:
            self.quota_bar.show_unauthorized()

    def _on_kimi_code_quota_error(self, category: str) -> None:
        if self.quota_bar is not None:
            self.quota_bar.show_code_error(category)

    def _on_kimi_web_quota_error(self, category: str) -> None:
        if self.quota_bar is not None:
            self.quota_bar.show_web_error(category)

    def _on_opencode_quota_bar_clicked(self) -> None:
        if self.opencode_web_quota_service is None:
            return
        if not self._opencode_authorized:
            self.opencode_web_quota_service.open_login(self)
            return
        self.opencode_web_quota_service.refresh_now()

    def _on_opencode_quota_updated(self, quota: object) -> None:
        if not isinstance(quota, OpenCodeQuota):
            return
        self._latest_opencode_quota = quota
        self._opencode_authorized = quota.status is not QuotaStatus.UNKNOWN
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_quota(quota)

    def _on_opencode_login_state(self, authorized: bool) -> None:
        self._opencode_authorized = authorized
        if authorized:
            return
        self._latest_opencode_quota = None
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_unauthorized()

    def _on_opencode_quota_error(self, category: str) -> None:
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_error(category)

    def open_opencode_web_login(self) -> None:
        if self.opencode_web_quota_service is not None:
            self.opencode_web_quota_service.open_login(self)

    def opencode_logout(self) -> None:
        if self.opencode_web_quota_service is None:
            return
        self.opencode_web_quota_service.logout()
        self._latest_opencode_quota = None
        self._opencode_authorized = False
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_unauthorized()

    def _on_oauth_code_ready(self, user_code: str, url: str) -> None:
        if self._oauth_dialog is None:
            self._oauth_dialog = KimiOAuthDialog(
                self,
                language_manager=self.language_manager,
            )
            self._oauth_dialog.cancelled.connect(self._on_oauth_cancelled)
        self._oauth_dialog.set_code(user_code)
        self._oauth_dialog.show()
        self._open_url(url)

    def _on_oauth_cancelled(self) -> None:
        if self.quota_service is not None:
            self.quota_service.cancel_oauth()

    def _on_oauth_finished(self, success: bool, message: str) -> None:
        if self._oauth_dialog is not None:
            self._oauth_dialog.finish_and_close()
            self._oauth_dialog.deleteLater()
            self._oauth_dialog = None
        if not success and message:
            self._set_subtitle_presentation("kimi.oauth_failed", uppercase=False)

    def save_kimi_api_key(self, key: str) -> None:
        if self.quota_service is None:
            return
        try:
            self.quota_service.set_api_key(key)
        except ValueError:
            self._set_subtitle_presentation("settings.kimi_key_required", uppercase=False)

    def open_kimi_web_login(self) -> None:
        if self.kimi_web_quota_service is not None:
            self.kimi_web_quota_service.open_login(self)

    def kimi_logout(self) -> None:
        logout_failed = False
        if self.kimi_web_quota_service is not None:
            try:
                web_logout_succeeded = self.kimi_web_quota_service.logout()
                if web_logout_succeeded is False:
                    logout_failed = True
            except Exception:  # noqa: BLE001 - the other logout path must still run
                logout_failed = True
                _logger.error("Kimi logout cleanup failed source=web")
        if self.quota_service is not None:
            try:
                self.quota_service.logout()
            except Exception:  # noqa: BLE001 - the other logout path already ran
                logout_failed = True
                _logger.error("Kimi logout cleanup failed source=code")
        self._latest_kimi_code_quota = None
        self._latest_kimi_web_quota = None
        self._kimi_web_authorized = False
        if self.quota_bar is not None:
            self.quota_bar.show_unauthorized()
        if logout_failed:
            self._on_kimi_web_quota_error(KimiWebQuotaErrorCategory.LOGOUT_PARTIAL.value)

    def set_compact(self, compact: bool) -> None:
        self.compact_mode = compact
        for card in self.cards.values():
            card.set_compact(compact)
        self._schedule_adaptive_resize()
        self._settings.setValue("compact_mode", compact)

    def set_agent_visible(self, agent_type: str, visible: bool) -> None:
        if visible:
            self.visible_agent_types.add(agent_type)
        else:
            self.visible_agent_types.discard(agent_type)
        self._settings.setValue("visible_agents", sorted(self.visible_agent_types))
        self.sync_cards()

    def set_codex_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_codex_monitoring_preferences(selected_ids, set(), set())

    def set_codex_monitoring_preferences(
        self, manual_ids: set[str], retained_ids: set[str], muted_ids: set[str]
    ) -> None:
        self.codex_manual_ids = set(manual_ids)
        self.codex_retained_ids = set(retained_ids) - self.codex_manual_ids
        self.codex_muted_ids = set(muted_ids) - self.codex_manual_ids
        self._settings.setValue("codex_selected_tasks", sorted(self.codex_manual_ids))
        self._settings.setValue("codex_manual_tasks", sorted(self.codex_manual_ids))
        self._settings.setValue("codex_retained_tasks", sorted(self.codex_retained_ids))
        self._settings.setValue("codex_muted_tasks", sorted(self.codex_muted_ids))
        self._apply_codex_monitoring_preferences()
        self.sync_cards()

    def _apply_codex_monitoring_preferences(self) -> None:
        self._set_codex_monitoring_preferences(
            self.codex_manual_ids, self.codex_retained_ids, self.codex_muted_ids
        )

    def _sync_codex_retained_ids(self) -> None:
        retained_ids = self._codex_retained_ids()
        if retained_ids != self.codex_retained_ids:
            self.codex_retained_ids = set(retained_ids)
            self._settings.setValue("codex_retained_tasks", sorted(self.codex_retained_ids))

    def _sync_codex_muted_ids(self) -> None:
        muted_ids = self._codex_muted_ids()
        if muted_ids != self.codex_muted_ids:
            self.codex_muted_ids = set(muted_ids)
            self._settings.setValue("codex_muted_tasks", sorted(self.codex_muted_ids))

    def set_kimi_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_kimi_monitoring_preferences(selected_ids, set(), set())

    def set_kimi_monitoring_preferences(
        self, manual_ids: set[str], retained_ids: set[str], muted_ids: set[str]
    ) -> None:
        self.kimi_manual_ids = set(manual_ids)
        self.kimi_retained_ids = set(retained_ids) - self.kimi_manual_ids
        self.kimi_muted_ids = set(muted_ids) - self.kimi_manual_ids
        self._settings.setValue("kimi_manual_tasks", sorted(self.kimi_manual_ids))
        self._settings.setValue("kimi_retained_tasks", sorted(self.kimi_retained_ids))
        self._settings.setValue("kimi_muted_tasks", sorted(self.kimi_muted_ids))
        self._apply_kimi_monitoring_preferences()
        self.sync_cards()

    def _apply_kimi_monitoring_preferences(self) -> None:
        self._set_kimi_monitoring_preferences(
            self.kimi_manual_ids, self.kimi_retained_ids, self.kimi_muted_ids
        )

    def _sync_kimi_retained_ids(self) -> None:
        retained_ids = self._kimi_retained_ids()
        if retained_ids != self.kimi_retained_ids:
            self.kimi_retained_ids = set(retained_ids)
            self._settings.setValue("kimi_retained_tasks", sorted(self.kimi_retained_ids))

    def _sync_kimi_muted_ids(self) -> None:
        muted_ids = self._kimi_muted_ids()
        if muted_ids != self.kimi_muted_ids:
            self.kimi_muted_ids = set(muted_ids)
            self._settings.setValue("kimi_muted_tasks", sorted(self.kimi_muted_ids))

    def set_kimi_desktop_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_kimi_desktop_monitoring_preferences(selected_ids, set(), set())

    def set_kimi_desktop_monitoring_preferences(
        self, manual_ids: set[str], retained_ids: set[str], muted_ids: set[str]
    ) -> None:
        self.kimi_desktop_manual_ids = set(manual_ids)
        self.kimi_desktop_retained_ids = set(retained_ids) - self.kimi_desktop_manual_ids
        self.kimi_desktop_muted_ids = set(muted_ids) - self.kimi_desktop_manual_ids
        self._settings.setValue("kimi_desktop_manual_tasks", sorted(self.kimi_desktop_manual_ids))
        self._settings.setValue(
            "kimi_desktop_retained_tasks", sorted(self.kimi_desktop_retained_ids)
        )
        self._settings.setValue("kimi_desktop_muted_tasks", sorted(self.kimi_desktop_muted_ids))
        self._apply_kimi_desktop_monitoring_preferences()
        self.sync_cards()

    def _apply_kimi_desktop_monitoring_preferences(self) -> None:
        self._set_kimi_desktop_monitoring_preferences(
            self.kimi_desktop_manual_ids,
            self.kimi_desktop_retained_ids,
            self.kimi_desktop_muted_ids,
        )

    def _sync_kimi_desktop_retained_ids(self) -> None:
        retained_ids = self._kimi_desktop_retained_ids()
        if retained_ids != self.kimi_desktop_retained_ids:
            self.kimi_desktop_retained_ids = set(retained_ids)
            self._settings.setValue(
                "kimi_desktop_retained_tasks", sorted(self.kimi_desktop_retained_ids)
            )

    def _sync_kimi_desktop_muted_ids(self) -> None:
        muted_ids = self._kimi_desktop_muted_ids()
        if muted_ids != self.kimi_desktop_muted_ids:
            self.kimi_desktop_muted_ids = set(muted_ids)
            self._settings.setValue("kimi_desktop_muted_tasks", sorted(self.kimi_desktop_muted_ids))

    def set_opencode_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_opencode_monitoring_preferences(selected_ids, set(), set())

    def set_opencode_monitoring_preferences(
        self, manual_ids: set[str], retained_ids: set[str], muted_ids: set[str]
    ) -> None:
        self.opencode_manual_ids = set(manual_ids)
        self.opencode_retained_ids = set(retained_ids) - self.opencode_manual_ids
        self.opencode_muted_ids = set(muted_ids) - self.opencode_manual_ids
        self._settings.setValue("opencode_manual_tasks", sorted(self.opencode_manual_ids))
        self._settings.setValue("opencode_retained_tasks", sorted(self.opencode_retained_ids))
        self._settings.setValue("opencode_muted_tasks", sorted(self.opencode_muted_ids))
        self._apply_opencode_monitoring_preferences()
        self.sync_cards()

    def _apply_opencode_monitoring_preferences(self) -> None:
        self._set_opencode_monitoring_preferences(
            self.opencode_manual_ids,
            self.opencode_retained_ids,
            self.opencode_muted_ids,
        )

    def _sync_opencode_retained_ids(self) -> None:
        retained_ids = self._opencode_retained_ids()
        if retained_ids != self.opencode_retained_ids:
            self.opencode_retained_ids = set(retained_ids)
            self._settings.setValue("opencode_retained_tasks", sorted(self.opencode_retained_ids))

    def _sync_opencode_muted_ids(self) -> None:
        muted_ids = self._opencode_muted_ids()
        if muted_ids != self.opencode_muted_ids:
            self.opencode_muted_ids = set(muted_ids)
            self._settings.setValue("opencode_muted_tasks", sorted(self.opencode_muted_ids))

    def opencode_auto_active_ids(self) -> set[str]:
        return set(self._opencode_auto_active_ids())

    @property
    def opencode_selected_ids(self) -> set[str]:
        return (
            self.opencode_manual_ids | self.opencode_retained_ids | self.opencode_auto_active_ids()
        ) - self.opencode_muted_ids

    def _remove_task_requested(self, task_id: str) -> None:
        # Single funnel for card removal: a card whose task id matches no
        # known brand would otherwise be ignored silently by every
        # brand-specific guard (e.g. a future brand wired incompletely).
        if task_id.startswith("codex:"):
            self.remove_codex_task(task_id)
        elif task_id.startswith("kimi:"):
            self.remove_kimi_task(task_id)
        elif task_id.startswith("kimi_desktop:"):
            self.remove_kimi_desktop_task(task_id)
        elif task_id.startswith("opencode:"):
            self.remove_opencode_task(task_id)
        else:
            _logger.error("Unknown brand dispatch: %s", task_id)
            self._set_subtitle_presentation("feedback.operation_failed", uppercase=False)

    def remove_opencode_task(self, task_id: str) -> None:
        if not task_id.startswith("opencode:"):
            return
        session_id = task_id.removeprefix("opencode:")
        self.opencode_manual_ids.discard(session_id)
        self.opencode_retained_ids.discard(session_id)
        self.opencode_muted_ids.add(session_id)
        self._settings.setValue("opencode_manual_tasks", sorted(self.opencode_manual_ids))
        self._settings.setValue("opencode_retained_tasks", sorted(self.opencode_retained_ids))
        self._settings.setValue("opencode_muted_tasks", sorted(self.opencode_muted_ids))
        self._apply_opencode_monitoring_preferences()
        self.sync_cards()

    def remove_kimi_desktop_task(self, task_id: str) -> None:
        if not task_id.startswith("kimi_desktop:"):
            return
        session_id = task_id.removeprefix("kimi_desktop:")
        self.kimi_desktop_manual_ids.discard(session_id)
        self.kimi_desktop_retained_ids.discard(session_id)
        self.kimi_desktop_muted_ids.add(session_id)
        self._settings.setValue("kimi_desktop_manual_tasks", sorted(self.kimi_desktop_manual_ids))
        self._settings.setValue(
            "kimi_desktop_retained_tasks", sorted(self.kimi_desktop_retained_ids)
        )
        self._settings.setValue("kimi_desktop_muted_tasks", sorted(self.kimi_desktop_muted_ids))
        self._apply_kimi_desktop_monitoring_preferences()
        self.sync_cards()

    def remove_kimi_task(self, task_id: str) -> None:
        if not task_id.startswith("kimi:"):
            return
        session_id = task_id.removeprefix("kimi:")
        self.kimi_manual_ids.discard(session_id)
        self.kimi_retained_ids.discard(session_id)
        self.kimi_muted_ids.add(session_id)
        self._settings.setValue("kimi_manual_tasks", sorted(self.kimi_manual_ids))
        self._settings.setValue("kimi_retained_tasks", sorted(self.kimi_retained_ids))
        self._settings.setValue("kimi_muted_tasks", sorted(self.kimi_muted_ids))
        self._apply_kimi_monitoring_preferences()
        self.sync_cards()

    def rename_task(self, task_id: str) -> None:
        if not task_id.startswith(("codex:", "kimi:", "kimi_desktop:", "opencode:")):
            return
        try:
            task = self.manager.task_config(task_id)
        except KeyError:
            return
        current_name = self.custom_task_names.get(task_id, task.name)
        dialog = QInputDialog(self)
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setWindowTitle(self.language_manager.text("rename.title"))
        dialog.setLabelText(self.language_manager.text("rename.prompt"))
        dialog.setTextValue(current_name)
        dialog.setOkButtonText(self.language_manager.text("common.ok"))
        dialog.setCancelButtonText(self.language_manager.text("common.cancel"))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.textValue()
        name = name.strip()[:120]
        if name and name != task.name:
            self.custom_task_names[task_id] = name
        else:
            self.custom_task_names.pop(task_id, None)
        self._settings.setValue("custom_task_names", json.dumps(self.custom_task_names))
        self.sync_cards()

    def remove_codex_task(self, task_id: str) -> None:
        if not task_id.startswith("codex:"):
            return
        session_id = task_id.removeprefix("codex:")
        self.codex_manual_ids.discard(session_id)
        self.codex_retained_ids.discard(session_id)
        self.codex_muted_ids.add(session_id)
        self._settings.setValue("codex_selected_tasks", sorted(self.codex_manual_ids))
        self._settings.setValue("codex_manual_tasks", sorted(self.codex_manual_ids))
        self._settings.setValue("codex_retained_tasks", sorted(self.codex_retained_ids))
        self._settings.setValue("codex_muted_tasks", sorted(self.codex_muted_ids))
        self._apply_codex_monitoring_preferences()
        self.sync_cards()

    def clear_retained_tasks(self) -> None:
        task_ids = [
            task_id
            for task_id in self._card_order_ids
            if task_id.startswith(("codex:", "kimi:", "kimi_desktop:", "opencode:"))
            and self._is_terminal(self.manager.get(task_id))
        ]
        if not task_ids:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.language_manager.text("clear_completed.title"))
        box.setText(
            self.language_manager.text(
                "clear_completed.prompt.one"
                if len(task_ids) == 1
                else "clear_completed.prompt.other",
                count=len(task_ids),
            )
        )
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        _localize_standard_buttons(box, self.language_manager)
        answer = box.exec()
        if answer != QMessageBox.StandardButton.Yes:
            return
        for task_id in task_ids:
            if task_id.startswith("codex:"):
                self.remove_codex_task(task_id)
            elif task_id.startswith("kimi_desktop:"):
                self.remove_kimi_desktop_task(task_id)
            elif task_id.startswith("opencode:"):
                self.remove_opencode_task(task_id)
            else:
                self.remove_kimi_task(task_id)

    def open_codex_task_selector(self) -> None:
        auto_active_ids = self.codex_auto_active_ids()
        dialog = CodexTaskSelectionDialog(
            self._codex_sessions(), self.codex_selected_ids, auto_active_ids, self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dialog.selected_ids()
            retained_ids = self.codex_retained_ids & selected_ids
            manual_ids = (self.codex_manual_ids & selected_ids) | (
                selected_ids - auto_active_ids - retained_ids
            )
            muted_ids = (self.codex_muted_ids | (auto_active_ids - selected_ids)) - selected_ids
            if dialog.restore_auto_requested():
                muted_ids -= auto_active_ids
            self.set_codex_monitoring_preferences(manual_ids, retained_ids, muted_ids)

    def open_kimi_task_selector(self) -> None:
        auto_active_ids = self.kimi_auto_active_ids()
        dialog = KimiTaskSelectionDialog(
            self._kimi_sessions(), self.kimi_selected_ids, auto_active_ids, self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dialog.selected_ids()
            retained_ids = self.kimi_retained_ids & selected_ids
            manual_ids = (self.kimi_manual_ids & selected_ids) | (
                selected_ids - auto_active_ids - retained_ids
            )
            muted_ids = (self.kimi_muted_ids | (auto_active_ids - selected_ids)) - selected_ids
            if dialog.restore_auto_requested():
                muted_ids -= auto_active_ids
            self.set_kimi_monitoring_preferences(manual_ids, retained_ids, muted_ids)

    def open_kimi_desktop_task_selector(self) -> None:
        auto_active_ids = self.kimi_desktop_auto_active_ids()
        dialog = KimiDesktopTaskSelectionDialog(
            self._kimi_desktop_sessions(),
            self.kimi_desktop_selected_ids,
            auto_active_ids,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dialog.selected_ids()
            retained_ids = self.kimi_desktop_retained_ids & selected_ids
            manual_ids = (self.kimi_desktop_manual_ids & selected_ids) | (
                selected_ids - auto_active_ids - retained_ids
            )
            muted_ids = (
                self.kimi_desktop_muted_ids | (auto_active_ids - selected_ids)
            ) - selected_ids
            if dialog.restore_auto_requested():
                muted_ids -= auto_active_ids
            self.set_kimi_desktop_monitoring_preferences(manual_ids, retained_ids, muted_ids)

    def open_opencode_task_selector(self) -> None:
        auto_active_ids = self.opencode_auto_active_ids()
        dialog = OpenCodeTaskSelectionDialog(
            self._opencode_sessions(),
            self.opencode_selected_ids,
            auto_active_ids,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dialog.selected_ids()
            retained_ids = self.opencode_retained_ids & selected_ids
            manual_ids = (self.opencode_manual_ids & selected_ids) | (
                selected_ids - auto_active_ids - retained_ids
            )
            muted_ids = (self.opencode_muted_ids | (auto_active_ids - selected_ids)) - selected_ids
            if dialog.restore_auto_requested():
                muted_ids -= auto_active_ids
            self.set_opencode_monitoring_preferences(manual_ids, retained_ids, muted_ids)

    def dock_top_right(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.right() - self.width() - 18, available.top() + 18)
        self._settings.remove("geometry")

    def toggle_always_on_top(self) -> None:
        self.always_on_top = not self.always_on_top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.always_on_top)
        self.show()
        self._settings.setValue("always_on_top", self.always_on_top)

    def _perform_action(self, action: str, task_id: str) -> None:
        try:
            task = self.manager.task_config(task_id)
            self.selected_task_id = task_id
            if action == "select":
                self._set_subtitle_presentation("feedback.task_selected", name=task.name)
                return
            elif action == "focus":
                self._submit_automation(action, task_id, "focus", task)
                return
            elif action == "voice":
                self._submit_automation(action, task_id, "start_voice", task)
                return
            elif action.startswith("key:"):
                self._submit_automation(action, task_id, "send_key", task, action.split(":", 1)[1])
                return
            elif action.startswith("status:"):
                status = action.split(":", 1)[1]
                self.manager.update(
                    TaskState.new(
                        task_id,
                        status,
                        source="manual",
                        metadata={AACC_MESSAGE_CATEGORY_KEY: "manual_update"},
                    )
                )
                self._set_subtitle_presentation(
                    "feedback.status_marked",
                    status=TaskStatus.parse(status),
                )
                return
            elif action == "rename":
                self.rename_task(task_id)
                return
            elif action == "copy":
                state = self.manager.get(task_id)
                QGuiApplication.clipboard().setText(
                    f"{task.name}\n{state.status.value}\n"
                    f"{_task_message_text(state, self.language_manager)}\n"
                    f"{state.updated_at.isoformat()}"
                )
                self._set_subtitle_presentation("feedback.task_copied")
                return
            else:
                return
        except (AutomationError, KeyError, ValueError) as error:
            self._show_automation_error(task_id, error)

    def _submit_automation(
        self, action: str, task_id: str, method: str, *arguments: object
    ) -> None:
        future = self.automation.submit(method, *arguments)

        def notify(completed: Future[str]) -> None:
            self.automation_finished.emit(action, task_id, completed)

        future.add_done_callback(notify)
        self._set_subtitle_presentation("feedback.automation_queued")

    def _automation_completed(self, action: str, task_id: str, value: object) -> None:
        if not isinstance(value, Future):
            return
        try:
            external_result = value.result()
            task = self.manager.task_config(task_id)
            if action == "focus":
                self._set_subtitle_presentation("automation.focused", name=task.name)
            elif action == "voice":
                self._set_subtitle_presentation("automation.voice_started")
            elif action.startswith("key:"):
                self._set_subtitle_presentation(
                    "automation.key_sent",
                    key=action.split(":", 1)[1],
                )
            else:
                self._set_external_subtitle(external_result)
        except (AutomationError, KeyError, ValueError) as error:
            self._show_automation_error(task_id, error)

    def _show_automation_error(self, task_id: str, error: Exception) -> None:
        category = error.category if isinstance(error, AutomationError) else None
        if category in AUTOMATION_ERROR_CATEGORIES:
            marker = f"automation.{category}"
            self._set_subtitle_presentation(
                marker,
                uppercase=False,
                prefix="⚠ ",
            )
            message = ""
            metadata = {AACC_MESSAGE_CATEGORY_KEY: marker}
        else:
            self._set_external_subtitle(f"⚠ {error}")
            message = str(error)
            metadata = {}
        self.manager.update(
            TaskState.new(
                task_id,
                TaskStatus.WARNING,
                message=message,
                source="automation",
                confidence=0.85,
                metadata=metadata,
            )
        )

    def rotate_credentials(self) -> None:
        confirmation = QMessageBox(self)
        confirmation.setIcon(QMessageBox.Icon.Question)
        confirmation.setWindowTitle(self.language_manager.text("credentials.reset_title"))
        confirmation.setText(self.language_manager.text("credentials.reset_prompt"))
        confirmation.setInformativeText(self.language_manager.text("credentials.reset_warning"))
        warning_label = confirmation.findChild(QLabel, "qt_msgbox_informativelabel")
        if warning_label is not None:
            warning_label.setStyleSheet("color: #b42318; font-weight: 600;")
        confirmation.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes
        )
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        _localize_standard_buttons(confirmation, self.language_manager)
        answer = confirmation.exec()
        if answer != QMessageBox.StandardButton.Yes:
            return
        token = self._rotate_api_token()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self.language_manager.text("credentials.reset_done_title"))
        box.setText(self.language_manager.text("credentials.reset_done_text"))
        box.setInformativeText(token)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.setStandardButtons(QMessageBox.StandardButton.Close)
        _localize_standard_buttons(box, self.language_manager)
        copy_button = box.addButton(
            self.language_manager.text("credentials.copy"),
            QMessageBox.ButtonRole.ActionRole,
        )
        box.exec()
        if box.clickedButton() is copy_button:
            QGuiApplication.clipboard().setText(token)

    def _apply_discovery_health(self, value: object) -> None:
        if not isinstance(value, DiscoveryHealth):
            return
        self._discovery_healths[value.brand] = value
        self._refresh_discovery_warning()

    def _refresh_discovery_warning(self) -> None:
        degraded = [health for health in self._discovery_healths.values() if health.degraded]
        if not degraded:
            self.discovery_warning.setVisible(False)
            return
        summary = "；".join(f"{health.brand}: {health.summary}" for health in degraded)
        self.discovery_warning_label.setText(summary[:80])
        self.discovery_warning.setVisible(True)

    def copy_discovery_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(
            "\n\n".join(
                health.diagnostics(self._discovery_log_path)
                for health in self._discovery_healths.values()
            )
        )

    def accessibility_status_text(self) -> str:
        if self.accessibility_trusted:
            return self.language_manager.text("accessibility.enabled")
        return self.language_manager.text("accessibility.disabled")

    def open_accessibility_settings(self) -> None:
        self._open_accessibility_settings()

    def show_accessibility_guidance(self) -> None:
        if self.accessibility_trusted:
            return
        if self._settings.value("accessibility_guidance_dismissed", False, type=bool):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.language_manager.text("accessibility.title"))
        box.setText(self.language_manager.text("accessibility.prompt"))
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        box.setCheckBox(QCheckBox(self.language_manager.text("accessibility.do_not_remind"), box))
        _localize_standard_buttons(box, self.language_manager)
        answer = box.exec()
        checkbox = box.checkBox()
        if checkbox is not None and checkbox.isChecked():
            self._settings.setValue("accessibility_guidance_dismissed", True)
        if answer == QMessageBox.StandardButton.Yes:
            self.open_accessibility_settings()

    def open_settings(self) -> None:
        SettingsDialog(self).exec()

    def show_about(self) -> None:
        version = public_version()
        body_key = "about.body.windows" if sys.platform == "win32" else "about.body.macos"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self.language_manager.text("about.title"))
        box.setText(self.language_manager.text(body_key, version=version))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        _localize_standard_buttons(box, self.language_manager)
        box.exec()

    def toggle_visible(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
            self.raise_()
            self.activateWindow()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._request_quota_refresh_on_restore()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange and not self.isMinimized():
            self._request_quota_refresh_on_restore()
        super().changeEvent(event)

    def _request_quota_refresh_on_restore(self) -> None:
        if self.kimi_web_quota_service is None and self.opencode_web_quota_service is None:
            return
        now = time.monotonic()
        if now - self._last_restore_quota_refresh < RESTORE_QUOTA_REFRESH_INTERVAL_SECONDS:
            return
        self._last_restore_quota_refresh = now
        if self.kimi_web_quota_service is not None:
            self.kimi_web_quota_service.refresh_now()
        if self.opencode_web_quota_service is not None:
            self.opencode_web_quota_service.refresh_now()

    def handle_app_state_change(self, state: Qt.ApplicationState) -> None:
        if sys.platform != "darwin":
            return
        if state is Qt.ApplicationState.ApplicationActive and not self.isVisible():
            self.toggle_visible()

    def quit_application(self) -> None:
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        self.close()
        QGuiApplication.quit()

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        if hasattr(self, "cards_scroll"):
            self._schedule_adaptive_resize()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_position = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("opacity", self.windowOpacity())
        if self.tray is not None and not self._quitting:
            self.hide()
            event.ignore()
            return
        self._timer.stop()
        self._unsubscribe_language()
        self._unsubscribe()
        self._unsubscribe_discovery_health()
        self._unsubscribe_kimi_discovery_health()
        self._unsubscribe_kimi_desktop_discovery_health()
        self._unsubscribe_opencode_discovery_health()
        event.accept()
