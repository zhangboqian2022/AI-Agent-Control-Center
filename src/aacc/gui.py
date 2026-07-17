from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from aacc.automation import AutomationError, MacAutomation
from aacc.constants import DEFAULT_CONFIG_PATH
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


def _elapsed(state: TaskState) -> str:
    anchor = state.started_at or state.updated_at
    end = state.finished_at or datetime.now(UTC)
    seconds = max(0, int((end - anchor).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class TaskCard(QFrame):
    action_requested = Signal(str, str)

    def __init__(self, task: TaskConfig, state: TaskState, blink_attention: bool = True) -> None:
        super().__init__()
        self.task = task
        self.state = state
        self.blink_attention = blink_attention
        self.setObjectName("taskCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("单击切换任务，右键查看更多操作")

        root = QVBoxLayout(self)
        root.setContentsMargins(15, 12, 15, 12)
        root.setSpacing(7)
        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setObjectName("statusDot")
        self.slot_label = QLabel(f"{task.slot:02d}")
        self.slot_label.setObjectName("slotLabel")
        self.agent_label = QLabel(
            task.agent.display_name or task.agent.type.replace("_", " ").title()
        )
        self.agent_label.setObjectName("agentLabel")
        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("timerLabel")
        top.addWidget(self.dot)
        top.addWidget(self.slot_label)
        top.addWidget(self.agent_label)
        top.addStretch()
        top.addWidget(self.timer_label)
        root.addLayout(top)

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(37, 0, 0, 0)
        details_layout.setSpacing(3)
        self.name_label = QLabel(task.name)
        self.name_label.setObjectName("taskName")
        status_line = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.message_label = QLabel()
        self.message_label.setObjectName("messageLabel")
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_label.setWordWrap(True)
        status_line.addWidget(self.status_label)
        status_line.addWidget(QLabel("·"))
        status_line.addWidget(self.message_label, 1)
        details_layout.addWidget(self.name_label)
        details_layout.addLayout(status_line)
        root.addWidget(self.details)

        self._effect = QGraphicsOpacityEffect(self.dot)
        self.dot.setGraphicsEffect(self._effect)
        self._pulse = QPropertyAnimation(self._effect, b"opacity", self)
        self._pulse.setDuration(900)
        self._pulse.setStartValue(1.0)
        self._pulse.setEndValue(0.35)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)
        self.set_state(state)

    def set_state(self, state: TaskState) -> None:
        self.state = state
        color = STATUS_COLORS[state.status]
        self.dot.setStyleSheet(f"color: {color}; font-size: 19px;")
        self.status_label.setText(STATUS_NAMES[state.status])
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 700;")
        self.message_label.setText(state.message or "暂无状态说明")
        self.timer_label.setText(_elapsed(state))
        self.setToolTip(
            f"{self.task.name}\n{STATUS_NAMES[state.status]} · {state.source} · "
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
            card_layout.setContentsMargins(13, 7 if compact else 12, 13, 7 if compact else 12)

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
        return menu

    def contextMenuEvent(self, event: object) -> None:
        self.create_context_menu().exec(event.globalPos())  # type: ignore[attr-defined]

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.action_requested.emit("focus", self.task.id)
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
        compact = QPushButton("切换紧凑 / 展开模式")
        compact.clicked.connect(lambda: window.set_compact(not window.compact_mode))
        layout.addWidget(compact)
        top = QPushButton("切换始终置顶")
        top.clicked.connect(window.toggle_always_on_top)
        layout.addWidget(top)
        dock = QPushButton("停靠到桌面右上角")
        dock.clicked.connect(window.dock_top_right)
        layout.addWidget(dock)
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


class MainWindow(QWidget):
    state_received = Signal(object)
    external_action = Signal(str, str)
    settings_keys = {"geometry", "compact_mode", "always_on_top", "opacity", "visible_agents"}

    def __init__(
        self,
        manager: TaskManager,
        automation: MacAutomation,
        *,
        enable_tray: bool = True,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.automation = automation
        self.config = manager.config
        self.selected_task_id = self.config.tasks[0].id if self.config.tasks else ""
        self.compact_mode = self.config.app.compact_mode
        self.always_on_top = self.config.app.always_on_top
        self._drag_position: QPoint | None = None
        self._quitting = False
        self._settings = QSettings("AACC", "AACC")
        saved_agents = self._settings.value("visible_agents")
        if isinstance(saved_agents, str):
            self.visible_agent_types = {saved_agents}
        elif isinstance(saved_agents, list):
            self.visible_agent_types = {str(value) for value in saved_agents}
        else:
            self.visible_agent_types = set(self.config.app.visible_agent_types)
        self._unsubscribe = self.manager.subscribe(self.state_received.emit)
        self.state_received.connect(self._apply_state)
        self.external_action.connect(self._perform_action)

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("AI Agent Control Center")
        self.setMinimumWidth(350)
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
        settings_button = QPushButton("⚙")
        settings_button.setToolTip("设置")
        settings_button.clicked.connect(self.open_settings)
        hide_button = QPushButton("—")
        hide_button.setToolTip("隐藏到菜单栏")
        hide_button.clicked.connect(self.hide)
        for button in (compact_button, settings_button, hide_button):
            button.setObjectName("headerButton")
            button.setFixedSize(28, 28)
            header.addWidget(button)
        layout.addLayout(header)

        self.cards: dict[str, TaskCard] = {}
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(9)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
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
        self.setStyleSheet(
            """
            #panel {
              background: rgba(18, 23, 34, 238);
              border: 1px solid rgba(122, 145, 180, 70);
              border-radius: 18px;
            }
            #title { color: #f2f6ff; font-size: 14px; font-weight: 800; letter-spacing: 1.2px; }
            #subtitle { color: #5fd7ce; font-size: 9px; font-weight: 700; letter-spacing: 1px; }
            #taskCard {
              background: rgba(31, 39, 55, 220);
              border: 1px solid rgba(112, 132, 165, 48);
              border-radius: 13px;
            }
            #taskCard:hover {
              background: rgba(40, 50, 70, 235);
              border-color: rgba(91, 158, 255, 110);
            }
            #cardsScroll { background: transparent; border: none; }
            #slotLabel { color: #77879f; font-size: 12px; font-weight: 800; }
            #agentLabel { color: #eef3fc; font-size: 13px; font-weight: 800; }
            #timerLabel { color: #8f9cb0; font-family: Menlo; font-size: 11px; }
            #taskName { color: #d6deea; font-size: 13px; font-weight: 600; }
            #messageLabel { color: #8997aa; font-size: 11px; }
            #footer { color: #65758b; font-size: 10px; }
            #headerButton {
              color: #aab6c7;
              background: rgba(255,255,255,12);
              border: none;
              border-radius: 7px;
            }
            #headerButton:hover { color: white; background: rgba(78,158,255,70); }
            QMenu { background: #1d2635; color: #e7edf7; border: 1px solid #38465b; padding: 6px; }
            QMenu::item { padding: 6px 22px; border-radius: 5px; }
            QMenu::item:selected { background: #347bd1; }
            """
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
        self.sync_cards()
        for state in self.manager.list():
            self._apply_state(state)

    def _visible_tasks(self) -> list[TaskConfig]:
        tasks = [
            task
            for task in self.manager.task_configs()
            if task.enabled and task.agent.type in self.visible_agent_types
        ]
        has_discovered_codex = any(task.id.startswith("codex:") for task in tasks)
        if has_discovered_codex:
            tasks = [
                task
                for task in tasks
                if task.id.startswith("codex:") or task.agent.type != "codex_cli"
            ]
        return tasks

    def sync_cards(self) -> None:
        states = {state.task_id: state for state in self.manager.list()}
        visible = self._visible_tasks()
        desired_ids = {task.id for task in visible}
        for task_id, card in tuple(self.cards.items()):
            if task_id not in desired_ids:
                self.cards_layout.removeWidget(card)
                card.deleteLater()
                del self.cards[task_id]
        for task in visible:
            existing_card = self.cards.get(task.id)
            if existing_card is None:
                new_card = TaskCard(task, states[task.id], self.config.app.blink_attention)
                new_card.action_requested.connect(self._perform_action)
                new_card.set_compact(self.compact_mode)
                self.cards[task.id] = new_card
                self.cards_layout.insertWidget(self.cards_layout.count() - 1, new_card)

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
        self.adjustSize()
        self._settings.setValue("compact_mode", compact)

    def set_agent_visible(self, agent_type: str, visible: bool) -> None:
        if visible:
            self.visible_agent_types.add(agent_type)
        else:
            self.visible_agent_types.discard(agent_type)
        self._settings.setValue("visible_agents", sorted(self.visible_agent_types))
        self.sync_cards()

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
            if action == "focus":
                result = self.automation.focus(task)
            elif action == "voice":
                result = self.automation.start_voice(task)
            elif action.startswith("key:"):
                result = self.automation.send_key(task, action.split(":", 1)[1])
            elif action.startswith("status:"):
                status = action.split(":", 1)[1]
                self.manager.update(
                    TaskState.new(task_id, status, message="手动更新", source="manual")
                )
                result = f"已标记为 {STATUS_NAMES[TaskStatus.parse(status)]}"
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

    def open_settings(self) -> None:
        SettingsDialog(self).exec()

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
        self._unsubscribe()
        event.accept()
