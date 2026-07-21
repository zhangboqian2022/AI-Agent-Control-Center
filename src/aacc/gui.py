from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, datetime
from importlib import resources

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
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
from aacc.automation import AutomationError
from aacc.automation_executor import AutomationExecutor
from aacc.codex_discovery import CodexSession
from aacc.constants import DEFAULT_CONFIG_PATH
from aacc.discovery_service import DiscoveryHealth
from aacc.kimi_discovery import KimiSession
from aacc.models import TaskConfig, TaskState, TaskStatus
from aacc.task_manager import TaskManager

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

STATUS_NAMES = {
    TaskStatus.UNCONFIGURED: "未配置",
    TaskStatus.IDLE: "空闲",
    TaskStatus.STARTING: "启动中",
    TaskStatus.THINKING: "思考中",
    TaskStatus.RUNNING: "执行中",
    TaskStatus.WAITING_INPUT: "等待输入",
    TaskStatus.WAITING_APPROVAL: "等待批准",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.WARNING: "警告",
    TaskStatus.ERROR: "失败",
    TaskStatus.PAUSED: "已暂停",
    TaskStatus.CANCELLED: "已取消",
    TaskStatus.STOPPED: "已停止",
    TaskStatus.UNKNOWN: "状态未知",
}

STATUS_LIGHT_FONT_SIZE = 64


def load_stylesheet() -> str:
    return resources.files("aacc").joinpath("styles.qss").read_text(encoding="utf-8")


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


def _elapsed_label(state: TaskState, now: datetime | None = None) -> str:
    prefix = "总用时 " if state.status in TERMINAL_STATUSES else ""
    return f"{prefix}{_elapsed(state, now)}"


class ElidedLabel(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__()
        self._full_text = text
        self.setToolTip(text)
        self._update_elision()

    def setText(self, text: str) -> None:
        self._full_text = text
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


class TaskCard(QFrame):
    action_requested = Signal(str, str)
    remove_requested = Signal(str)

    def __init__(
        self,
        task: TaskConfig,
        state: TaskState,
        blink_attention: bool = True,
        display_name: str | None = None,
    ) -> None:
        super().__init__()
        self.task = task
        self.state = state
        self.blink_attention = blink_attention
        self.display_name = display_name or task.name
        self.setObjectName("taskCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("单击切换任务，右键查看更多操作")

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
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        meta_row.addWidget(self.agent_label)
        meta_row.addWidget(self.status_label)
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

        self.updated_label = QLabel()
        self.updated_label.setObjectName("updatedLabel")
        self.updated_label.hide()
        root.addWidget(self.details, 1)

        if task.id.startswith(("codex:", "kimi:")):
            remove_button = QPushButton("×")
            remove_button.setObjectName("removeTaskButton")
            remove_button.setAccessibleName("从面板移除")
            remove_button.setToolTip("停止监控并从面板移除")
            remove_button.setFixedSize(24, 24)
            remove_button.clicked.connect(lambda: self.remove_requested.emit(self.task.id))
            root.addWidget(remove_button, 0, Qt.AlignmentFlag.AlignTop)

        self._effect = QGraphicsOpacityEffect(self.dot)
        self.dot.setGraphicsEffect(self._effect)
        self._pulse = QPropertyAnimation(self._effect, b"opacity", self)
        self._pulse.setDuration(900)
        self._pulse.setStartValue(1.0)
        self._pulse.setEndValue(0.35)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)
        self.set_state(state)

    def set_display_name(self, display_name: str) -> None:
        self.display_name = display_name
        self.name_label.setText(display_name)
        self.name_label.setToolTip(display_name)
        self.set_state(self.state)

    def set_state(self, state: TaskState) -> None:
        self.state = state
        color = STATUS_COLORS[state.status]
        self.dot.setStyleSheet(f"color: {color}; font-size: {STATUS_LIGHT_FONT_SIZE}px;")
        self.status_label.setText(STATUS_NAMES[state.status])
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 700;")
        self.message_label.setText(state.message or "暂无状态说明")
        self.updated_label.setText(
            f"最后活动：{state.updated_at.astimezone().strftime('%H:%M:%S')}"
        )
        self.timer_label.setText(_elapsed_label(state))
        self.setToolTip(
            f"{self.display_name}\n{STATUS_NAMES[state.status]} · {state.source} · "
            f"{state.confidence:.0%}\n"
            f"更新：{state.updated_at.astimezone().strftime('%H:%M:%S')}"
        )
        attention = state.status in {TaskStatus.WAITING_INPUT, TaskStatus.WAITING_APPROVAL}
        if attention and self.blink_attention:
            if self._pulse.state() != QPropertyAnimation.State.Running:
                self._pulse.start()
        else:
            self._pulse.stop()
            self._effect.setOpacity(1.0)

    def set_compact(self, compact: bool) -> None:
        self.details.setVisible(not compact)
        card_layout = self.layout()
        if card_layout is not None:
            card_layout.setContentsMargins(10, 6 if compact else 8, 9, 6 if compact else 8)

    def create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        actions = [
            ("切换到任务", "focus"),
            ("启动语音输入", "voice"),
            ("发送 Enter", "key:ENTER"),
            ("发送 1", "key:1"),
            ("发送 2", "key:2"),
            ("发送 ↑", "key:UP"),
            ("发送 ↓", "key:DOWN"),
        ]
        for label, command in actions:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=command: self.action_requested.emit(
                    value, self.task.id
                )
            )
        menu.addSeparator()
        state_menu = menu.addMenu("手动标记状态")
        for label, status in (
            ("执行中", "RUNNING"),
            ("等待输入", "WAITING_INPUT"),
            ("等待批准", "WAITING_APPROVAL"),
            ("已完成", "COMPLETED"),
            ("失败", "ERROR"),
            ("重置", "IDLE"),
        ):
            action = state_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=status: self.action_requested.emit(
                    f"status:{value}", self.task.id
                )
            )
        copy_action = menu.addAction("复制任务信息")
        copy_action.triggered.connect(lambda: self.action_requested.emit("copy", self.task.id))
        if self.task.id.startswith(("codex:", "kimi:")):
            rename_action = menu.addAction("重命名任务")
            rename_action.triggered.connect(
                lambda: self.action_requested.emit("rename", self.task.id)
            )
            remove_action = menu.addAction("从面板移除")
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
        self.setWindowTitle("AACC 设置")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("面板透明度"))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(35, 100)
        slider.setValue(round(window.windowOpacity() * 100))
        slider.valueChanged.connect(lambda value: window.setWindowOpacity(value / 100))
        layout.addWidget(slider)
        layout.addWidget(QLabel(f"配置文件\n{DEFAULT_CONFIG_PATH}"))
        layout.addWidget(QLabel(window.accessibility_status_text()))
        if not window.accessibility_trusted:
            accessibility = QPushButton("打开辅助功能设置")
            accessibility.clicked.connect(window.open_accessibility_settings)
            layout.addWidget(accessibility)
        compact = QPushButton("切换紧凑 / 展开模式")
        compact.clicked.connect(lambda: window.set_compact(not window.compact_mode))
        layout.addWidget(compact)
        top = QPushButton("切换始终置顶")
        top.clicked.connect(window.toggle_always_on_top)
        layout.addWidget(top)
        dock = QPushButton("停靠到桌面右上角")
        dock.clicked.connect(window.dock_top_right)
        layout.addWidget(dock)
        codex_tasks = QPushButton(
            "选择监控的 Codex 任务"
            f"（{len(window.codex_selected_ids)} 已选 · "
            f"{len(window.codex_auto_active_ids())} 自动运行）"
        )
        codex_tasks.clicked.connect(window.open_codex_task_selector)
        layout.addWidget(codex_tasks)
        kimi_tasks = QPushButton(
            "选择监控的 Kimi Code 任务"
            f"（{len(window.kimi_selected_ids)} 已选 · "
            f"{len(window.kimi_auto_active_ids())} 自动运行）"
        )
        kimi_tasks.clicked.connect(window.open_kimi_task_selector)
        layout.addWidget(kimi_tasks)
        rotate_credentials = QPushButton("重置 API 凭证")
        rotate_credentials.clicked.connect(window.rotate_credentials)
        layout.addWidget(rotate_credentials)
        layout.addWidget(QLabel("显示哪些程序"))
        labels = {
            "codex_cli": "Codex",
            "claude_code": "Claude Code",
            "kimi_code": "Kimi Code",
            "generic_cli": "Z Code / 通用 CLI",
        }
        for agent_type, label in labels.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(agent_type in window.visible_agent_types)
            checkbox.toggled.connect(
                lambda checked, value=agent_type: window.set_agent_visible(value, checked)
            )
            layout.addWidget(checkbox)
        close = QPushButton("完成")
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
        window_title: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.setMinimumSize(540, 460)
        self._auto_active_ids = set(auto_active_ids)
        self._restore_auto = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("运行中的任务会自动勾选；取消勾选可停止自动监控该任务。"))
        self.tasks = QListWidget()
        for session_id, title, updated_at in sessions:
            automatic = session_id in self._auto_active_ids
            automatic_label = "\n自动监控 · 运行中" if automatic else ""
            item = QListWidgetItem(
                f"{title}\n{updated_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
                f"{automatic_label}"
            )
            item.setData(Qt.ItemDataRole.UserRole, session_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if session_id in selected_ids
                else Qt.CheckState.Unchecked
            )
            self.tasks.addItem(item)
        layout.addWidget(self.tasks)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        clear_all = QPushButton("全部取消")
        clear_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        restore_auto = QPushButton("恢复自动识别")
        restore_auto.clicked.connect(self.restore_automatic_detection)
        buttons = QHBoxLayout()
        buttons.addWidget(select_all)
        buttons.addWidget(clear_all)
        buttons.addWidget(restore_auto)
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("开始监控")
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
            [
                (session.conversation_id, session.title, session.updated_at)
                for session in sessions
            ],
            selected_ids,
            auto_active_ids,
            parent,
            window_title="选择监控的 Codex 任务",
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
            [
                (session.session_id, session.title, session.updated_at)
                for session in sessions
            ],
            selected_ids,
            auto_active_ids,
            parent,
            window_title="选择监控的 Kimi Code 任务",
        )


class MainWindow(QWidget):
    state_received = Signal(object)
    external_action = Signal(str, str)
    automation_finished = Signal(str, str, object)
    discovery_health_received = Signal(object)
    kimi_discovery_health_received = Signal(object)
    settings_keys = {"geometry", "compact_mode", "always_on_top", "opacity", "visible_agents"}

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
        rotate_api_token_callback: Callable[[], str] | None = None,
        discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_discovery_health: (
            Callable[[Callable[[DiscoveryHealth], None]], Callable[[], None]] | None
        ) = None,
        kimi_discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_kimi_discovery_health: (
            Callable[[Callable[[DiscoveryHealth], None]], Callable[[], None]] | None
        ) = None,
        discovery_log_path: str = "~/Library/Application Support/AACC/logs/app.log",
        accessibility_trusted: bool = True,
        open_accessibility_settings_callback: Callable[[], None] | None = None,
        settings: QSettings | None = None,
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
        self._rotate_api_token = rotate_api_token_callback or (lambda: self.config.app.api.token)
        self._discovery_health = (discovery_health or DiscoveryHealth)()
        self._kimi_discovery_health = (
            kimi_discovery_health or (lambda: DiscoveryHealth(brand="Kimi"))
        )()
        self._discovery_log_path = discovery_log_path
        self.accessibility_trusted = accessibility_trusted
        self._open_accessibility_settings = open_accessibility_settings_callback or (lambda: None)
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
        # New agent types default to visible even for existing stored settings.
        self.visible_agent_types.add("kimi_code")
        self._unsubscribe = self.manager.subscribe(self.state_received.emit)
        self.state_received.connect(self._apply_state)
        self.external_action.connect(self._perform_action)
        self.automation_finished.connect(self._automation_completed)
        self.discovery_health_received.connect(self._apply_discovery_health)
        self.kimi_discovery_health_received.connect(self._apply_kimi_discovery_health)

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
        compact_button = QPushButton("↕")
        compact_button.setToolTip("紧凑 / 展开")
        compact_button.clicked.connect(lambda: self.set_compact(not self.compact_mode))
        self.about_button = QPushButton("ⓘ")
        self.about_button.setToolTip("关于 AACC")
        self.about_button.clicked.connect(self.show_about)
        settings_button = QPushButton("⚙")
        settings_button.setToolTip("设置")
        settings_button.clicked.connect(self.open_settings)
        hide_button = QPushButton("—")
        hide_button.setToolTip("隐藏到菜单栏")
        hide_button.clicked.connect(self.hide)
        for button in (compact_button, self.about_button, settings_button, hide_button):
            button.setObjectName("headerButton")
            button.setFixedSize(28, 28)
            header.addWidget(button)
        layout.addLayout(header)

        self.discovery_warning = QFrame()
        self.discovery_warning.setObjectName("discoveryWarning")
        discovery_warning_layout = QHBoxLayout(self.discovery_warning)
        discovery_warning_layout.setContentsMargins(10, 8, 10, 8)
        self.discovery_warning_label = QLabel()
        self.discovery_warning_label.setObjectName("discoveryWarningLabel")
        self.discovery_warning_label.setWordWrap(True)
        copy_diagnostics = QPushButton("复制详情")
        copy_diagnostics.setObjectName("copyDiagnosticsButton")
        copy_diagnostics.clicked.connect(self.copy_discovery_diagnostics)
        discovery_warning_layout.addWidget(self.discovery_warning_label, 1)
        discovery_warning_layout.addWidget(copy_diagnostics)
        layout.addWidget(self.discovery_warning)
        self._apply_discovery_health(self._discovery_health)

        self.cards: dict[str, TaskCard] = {}
        self._card_order_ids: list[str] = []
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(9)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.task_summary_label = QLabel("运行中：0 · 已完成：0 · 显示：0")
        self.task_summary_label.setObjectName("taskSummary")
        self.empty_tasks_label = QLabel("未选择 Codex / Kimi Code 任务 · 点击 ⚙ 选择监控任务")
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
        self.retained_group_label = QLabel("已完成 · 保留直到移除")
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
        self.cards_scroll.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll, 1)
        footer = QHBoxLayout()
        self.connection_label = QLabel("● API 127.0.0.1")
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
        if enable_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._create_tray()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.sync_cards()

    def _apply_styles(self) -> None:
        self.setStyleSheet(load_stylesheet())

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
        menu = QMenu()
        show_action = menu.addAction("显示 / 隐藏 AACC")
        show_action.triggered.connect(self.toggle_visible)
        compact_action = menu.addAction("紧凑 / 展开")
        compact_action.triggered.connect(lambda: self.set_compact(not self.compact_mode))
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.quit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda _reason: self.toggle_visible())
        self.tray.show()

    def refresh(self) -> None:
        if self.manager.closed:
            self._timer.stop()
            return
        self._sync_codex_retained_ids()
        self._sync_codex_muted_ids()
        self._sync_kimi_retained_ids()
        self._sync_kimi_muted_ids()
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
                    task, states[task.id], self.config.app.blink_attention, display_name
                )
                new_card.action_requested.connect(self._perform_action)
                new_card.remove_requested.connect(self.remove_codex_task)
                new_card.remove_requested.connect(self.remove_kimi_task)
                new_card.set_compact(self.compact_mode)
                self.cards[task.id] = new_card
            elif existing_card.display_name != display_name:
                existing_card.set_display_name(display_name)
        running_tasks, terminal_tasks = self._grouped_tasks(visible, states)
        self._rebuild_card_layout(self.running_cards_layout, running_tasks)
        self._rebuild_card_layout(self.retained_cards_layout, terminal_tasks)
        self._card_order_ids = [task.id for task in running_tasks + terminal_tasks]
        self.task_summary_label.setText(
            f"运行中：{len(running_tasks)} · 已完成：{len(terminal_tasks)} · "
            f"显示：{len(self._card_order_ids)}"
        )
        self.empty_tasks_label.setVisible(not self.cards)
        self.task_summary_label.setVisible(bool(self.cards))
        self.running_group_label.setVisible(bool(running_tasks))
        self.running_cards_widget.setVisible(bool(running_tasks))
        self.retained_header.setVisible(bool(terminal_tasks))
        self.retained_cards_widget.setVisible(bool(terminal_tasks))
        self.clear_retained_button.setVisible(
            any(task.id.startswith(("codex:", "kimi:")) for task in terminal_tasks)
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
                self.tray.showMessage(card.task.name, state.message or STATUS_NAMES[state.status])

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
        if not task_id.startswith(("codex:", "kimi:")):
            return
        try:
            task = self.manager.task_config(task_id)
        except KeyError:
            return
        current_name = self.custom_task_names.get(task_id, task.name)
        name, accepted = QInputDialog.getText(
            self, "重命名任务", "任务名称（留空恢复默认）：", text=current_name
        )
        if not accepted:
            return
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
            if task_id.startswith(("codex:", "kimi:"))
            and self._is_terminal(self.manager.get(task_id))
        ]
        if not task_ids:
            return
        answer = QMessageBox.question(
            self,
            "清除已完成任务",
            f"确定从面板移除 {len(task_ids)} 个已完成任务吗？",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for task_id in task_ids:
            if task_id.startswith("codex:"):
                self.remove_codex_task(task_id)
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
                result = f"已选择 {task.name}"
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
                    TaskState.new(task_id, status, message="手动更新", source="manual")
                )
                result = f"已标记为 {STATUS_NAMES[TaskStatus.parse(status)]}"
            elif action == "rename":
                self.rename_task(task_id)
                return
            elif action == "copy":
                state = self.manager.get(task_id)
                QGuiApplication.clipboard().setText(
                    f"{task.name}\n{state.status.value}\n{state.message}\n{state.updated_at.isoformat()}"
                )
                result = "任务信息已复制"
            else:
                return
            self.subtitle.setText(result.upper())
        except (AutomationError, KeyError, ValueError) as error:
            self._show_automation_error(task_id, error)

    def _submit_automation(
        self, action: str, task_id: str, method: str, *arguments: object
    ) -> None:
        future = self.automation.submit(method, *arguments)

        def notify(completed: Future[str]) -> None:
            self.automation_finished.emit(action, task_id, completed)

        future.add_done_callback(notify)
        self.subtitle.setText("AUTOMATION QUEUED")

    def _automation_completed(self, _action: str, task_id: str, value: object) -> None:
        if not isinstance(value, Future):
            return
        try:
            self.subtitle.setText(value.result().upper())
        except (AutomationError, KeyError, ValueError) as error:
            self._show_automation_error(task_id, error)

    def _show_automation_error(self, task_id: str, error: Exception) -> None:
        self.subtitle.setText(f"⚠ {error}")
        self.manager.update(
            TaskState.new(
                task_id,
                TaskStatus.WARNING,
                message=str(error),
                source="automation",
                confidence=0.85,
            )
        )

    def rotate_credentials(self) -> None:
        answer = QMessageBox.question(
            self,
            "重置凭证",
            "旧凭证会立即失效，是否继续？",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        token = self._rotate_api_token()
        QGuiApplication.clipboard().setText(token)
        QMessageBox.information(self, "凭证已重置", "新凭证已复制；旧凭证已失效。")

    def _apply_discovery_health(self, value: object) -> None:
        if not isinstance(value, DiscoveryHealth):
            return
        self._discovery_health = value
        self._refresh_discovery_warning()

    def _apply_kimi_discovery_health(self, value: object) -> None:
        if not isinstance(value, DiscoveryHealth):
            return
        self._kimi_discovery_health = value
        self._refresh_discovery_warning()

    def _refresh_discovery_warning(self) -> None:
        degraded = [
            health
            for health in (self._discovery_health, self._kimi_discovery_health)
            if health.degraded
        ]
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
                for health in (self._discovery_health, self._kimi_discovery_health)
            )
        )

    def accessibility_status_text(self) -> str:
        if self.accessibility_trusted:
            return "辅助功能权限：已开启"
        return "辅助功能权限：未开启；全局热键与键盘输入不可用"

    def open_accessibility_settings(self) -> None:
        self._open_accessibility_settings()

    def show_accessibility_guidance(self) -> None:
        if self.accessibility_trusted:
            return
        answer = QMessageBox.question(
            self,
            "需要辅助功能权限",
            "AACC 需要辅助功能权限才能使用全局热键和键盘输入。是否打开系统设置？",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.open_accessibility_settings()

    def open_settings(self) -> None:
        SettingsDialog(self).exec()

    def show_about(self) -> None:
        version = public_version()
        QMessageBox.about(
            self,
            "关于 AACC",
            f"AI Agent Control Center\n版本 {version}\n安装包 AACC-{version}.dmg",
        )

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def quit_application(self) -> None:
        self._quitting = True
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
        self._unsubscribe()
        self._unsubscribe_discovery_health()
        self._unsubscribe_kimi_discovery_health()
        event.accept()
