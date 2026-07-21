from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QScrollArea,
)

from aacc.automation import AutomationError, MacAutomation
from aacc.automation_executor import AutomationExecutor
from aacc.codex_discovery import CodexSession
from aacc.config import create_default_config, default_config, rotate_api_token
from aacc.discovery_service import DiscoveryHealth
from aacc.gui import (
    STATUS_COLORS,
    CodexTaskSelectionDialog,
    KimiTaskSelectionDialog,
    MainWindow,
    TaskCard,
    _elapsed,
)
from aacc.kimi_discovery import KimiSession
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


def build_window(tmp_path: Path, qtbot: object) -> tuple[MainWindow, TaskManager]:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    return window, manager


def test_window_starts_with_no_codex_cards_until_tasks_are_selected(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert len(window.findChildren(TaskCard)) == 0
    assert "未选择 Codex / Kimi Code 任务" in window.empty_tasks_label.text()
    manager.close()


def test_about_button_shows_current_dmg_version(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    from aacc import public_version

    window, manager = build_window(tmp_path, qtbot)
    shown: dict[str, str] = {}
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "aacc.gui.QMessageBox.about",
        lambda _parent, title, text: shown.update(title=title, text=text),
    )

    window.about_button.click()

    assert "关于" in shown["title"]
    assert public_version() in shown["text"]
    assert f"AACC-{public_version()}.dmg" in shown["text"]
    manager.close()


def test_all_statuses_have_a_color() -> None:
    assert set(STATUS_COLORS) == set(TaskStatus)


def test_status_light_is_five_times_larger_for_fast_visual_scanning(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="codex:large-light",
        slot=1,
        name="大状态灯任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.set_codex_selected_ids({"large-light"})

    assert "font-size: 64px" in window.cards[task.id].dot.styleSheet()
    assert window.minimumHeight() >= 270
    manager.close()


def test_expanded_card_uses_compact_horizontal_information_hierarchy(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="codex:horizontal-card",
        slot=1,
        name="突出显示的任务名称",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(
        task,
        TaskState.new(task.id, "running", message="正在修改代码", source="codex_local"),
    )
    window.set_codex_selected_ids({"horizontal-card"})
    window.show()
    QApplication.processEvents()
    card = window.cards[task.id]

    assert isinstance(card.layout(), QHBoxLayout)
    assert 56 <= card.dot.width() <= 72
    assert card.dot.height() == card.dot.width()
    assert card.agent_label.text() == "CODEX"
    assert card.name_label.text() == "突出显示的任务名称"
    assert card.name_label.font().pixelSize() > card.agent_label.font().pixelSize()
    assert card.sizeHint().height() <= 110
    assert card.updated_label.isHidden()
    manager.close()


def test_long_task_name_is_elided_instead_of_clipped(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="codex:long-title",
        slot=1,
        name="这是一个非常长的 Codex 任务名称用于验证窗口较窄时能够显示清晰的省略号",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.set_codex_selected_ids({"long-title"})
    window.resize(350, window.height())
    window.show()
    QApplication.processEvents()

    assert window.cards[task.id].name_label.text().endswith("…")
    assert window.cards[task.id].name_label.toolTip() == task.name
    manager.close()


def test_refresh_updates_card_text_and_color(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.set_agent_visible("claude_code", True)
    manager.update(
        TaskState.new("task-2", "waiting-approval", message="等待批准 npm test", source="manual")
    )
    window.refresh()
    card = window.cards["task-2"]
    assert card.status_label.text() == "等待批准"
    assert card.message_label.text() == "等待批准 npm test"
    assert STATUS_COLORS[TaskStatus.WAITING_APPROVAL] in card.dot.styleSheet()
    manager.close()


def test_refresh_timer_stops_after_task_manager_closes(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)

    manager.close()
    window.refresh()

    assert not window._timer.isActive()


def test_elapsed_time_always_includes_hours() -> None:
    started_at = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    state = TaskState(
        task_id="task-1",
        status=TaskStatus.COMPLETED,
        started_at=started_at,
        updated_at=started_at + timedelta(minutes=18, seconds=42),
        finished_at=started_at + timedelta(minutes=18, seconds=42),
    )

    assert _elapsed(state) == "00:18:42"


def test_completed_card_labels_frozen_total_duration(qtbot: object) -> None:
    task = TaskConfig(id="task-1", slot=1, name="完整计时任务")
    started_at = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    state = TaskState(
        task_id=task.id,
        status=TaskStatus.COMPLETED,
        message="已完成",
        started_at=started_at,
        updated_at=started_at + timedelta(hours=1, minutes=26, seconds=8),
        finished_at=started_at + timedelta(hours=1, minutes=26, seconds=8),
    )
    card = TaskCard(task, state)
    qtbot.addWidget(card)  # type: ignore[attr-defined]

    assert card.timer_label.text() == "总用时 01:26:08"


def test_compact_mode_hides_detail_rows(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.set_compact(True)
    assert window.compact_mode is True
    assert all(not card.details.isVisible() for card in window.cards.values())
    window.set_compact(False)
    assert all(not card.details.isHidden() for card in window.cards.values())
    manager.close()


def test_discovered_codex_task_replaces_placeholder_card(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    discovered = TaskConfig(
        id="codex:task-1234",
        slot=1,
        name="自动识别任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
        terminal=TerminalConfig(type="mac_app", app_bundle_id="com.openai.codex"),
    )
    manager.register(discovered, TaskState.new(discovered.id, "running", source="codex_local"))
    window.set_codex_selected_ids({"task-1234"})
    window.refresh()

    assert list(window.cards) == ["codex:task-1234"]
    assert window.cards["codex:task-1234"].name_label.text() == "自动识别任务"
    assert window.findChild(QScrollArea, "cardsScroll") is not None
    manager.close()


def test_window_height_grows_and_shrinks_with_visible_task_cards(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window._available_screen_height = lambda: 1200  # type: ignore[method-assign]
    tasks = [
        TaskConfig(
            id=f"codex:adaptive-{index}",
            slot=index,
            name=f"自动高度任务 {index}",
            agent=AgentConfig(type="codex_cli", display_name="Codex"),
        )
        for index in range(1, 6)
    ]
    for task in tasks:
        manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.show()
    QApplication.processEvents()
    window.move(180, 120)
    original_position = window.pos()

    window.set_codex_selected_ids({task.id.removeprefix("codex:") for task in tasks})
    qtbot.waitUntil(lambda: len(window.cards) == 5)  # type: ignore[attr-defined]
    QApplication.processEvents()
    expanded_height = window.height()

    window.set_codex_selected_ids({tasks[0].id.removeprefix("codex:")})
    qtbot.waitUntil(lambda: len(window.cards) == 1)  # type: ignore[attr-defined]
    QApplication.processEvents()

    assert window.height() < expanded_height
    assert window.pos() == original_position
    manager.close()


def test_window_height_caps_at_eighty_percent_and_enables_internal_scroll(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window._available_screen_height = lambda: 500  # type: ignore[method-assign]
    tasks = [
        TaskConfig(
            id=f"codex:capped-{index}",
            slot=index,
            name=f"高度上限任务 {index}",
            agent=AgentConfig(type="codex_cli", display_name="Codex"),
        )
        for index in range(1, 9)
    ]
    for task in tasks:
        manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.show()
    QApplication.processEvents()

    window.set_codex_selected_ids({task.id.removeprefix("codex:") for task in tasks})
    qtbot.waitUntil(lambda: len(window.cards) == 8)  # type: ignore[attr-defined]
    QApplication.processEvents()

    assert window.height() == 400
    assert window.cards_scroll.verticalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOn

    window.set_codex_selected_ids({tasks[0].id.removeprefix("codex:")})
    qtbot.waitUntil(lambda: len(window.cards) == 1)  # type: ignore[attr-defined]
    QApplication.processEvents()

    assert window.height() < 400
    assert window.cards_scroll.verticalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    manager.close()


def test_only_selected_codex_tasks_are_visible_and_window_is_not_a_tool(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    first = TaskConfig(
        id="codex:first",
        slot=1,
        name="已选择任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    second = first.model_copy(update={"id": "codex:second", "name": "未选择任务", "slot": 2})
    manager.register(first, TaskState.new(first.id, "running", source="codex_local"))
    manager.register(second, TaskState.new(second.id, "running", source="codex_local"))

    window.set_codex_selected_ids({"first"})

    assert list(window.cards) == ["codex:first"]
    assert window.windowType() is Qt.WindowType.Window
    manager.close()


def test_card_context_menu_exposes_safe_controls(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="codex:context-menu",
        slot=1,
        name="菜单任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.set_codex_selected_ids({"context-menu"})
    menu = window.cards[task.id].create_context_menu()
    labels = {action.text() for action in menu.actions()}
    assert {"切换到任务", "发送 Enter", "发送 1", "发送 2", "启动语音输入"} <= labels
    manager.close()


def test_single_click_selects_task_without_switching_to_codex(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="codex:click",
        slot=1,
        name="点击任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.set_codex_selected_ids({"click"})
    requested: list[str] = []
    window.cards[task.id].action_requested.connect(
        lambda action, _task_id: requested.append(action)
    )

    qtbot.mouseClick(window.cards[task.id], Qt.MouseButton.LeftButton)

    assert requested == ["select"]
    manager.close()


def test_window_declares_persisted_setting_keys(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert window.settings_keys == {
        "geometry",
        "compact_mode",
        "always_on_top",
        "opacity",
        "visible_agents",
    }
    assert QApplication.instance() is not None
    manager.close()


def test_selector_marks_auto_running_task_checked_and_can_restore_automatic_detection(
    tmp_path: Path, qtbot: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    window = MainWindow(manager, AutomationExecutor(MacAutomation(config)), enable_tray=False)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    session = CodexSession(
        conversation_id="auto-now",
        title="自动运行任务",
        updated_at=TaskState.new("example", "running").updated_at,
    )

    dialog = CodexTaskSelectionDialog([session], {"auto-now"}, {"auto-now"}, window)

    assert dialog.tasks.item(0).checkState() is Qt.CheckState.Checked
    assert "自动监控 · 运行中" in dialog.tasks.item(0).text()
    dialog.restore_automatic_detection()
    assert dialog.restore_auto_requested() is True
    manager.close()


def test_auto_running_task_is_visible_without_manual_selection_and_can_be_muted(
    tmp_path: Path, qtbot: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    auto_ids = {"auto-now"}
    preferences: list[tuple[set[str], set[str], set[str]]] = []
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        codex_auto_active_ids=lambda: set(auto_ids),
        set_codex_monitoring_preferences=lambda manual, retained, muted: preferences.append(
            (set(manual), set(retained), set(muted))
        ),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="codex:auto-now",
        slot=1,
        name="自动加入的任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.sync_cards()

    assert list(window.cards) == [task.id]
    assert window.codex_selected_ids == {"auto-now"}

    window.set_codex_monitoring_preferences(set(), set(), {"auto-now"})

    assert not window.cards
    assert preferences[-1] == (set(), set(), {"auto-now"})
    manager.close()


def test_completed_codex_task_remains_visible_until_removed(tmp_path: Path, qtbot: object) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    preferences: list[tuple[set[str], set[str], set[str]]] = []
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        set_codex_monitoring_preferences=lambda manual, retained, muted: preferences.append(
            (set(manual), set(retained), set(muted))
        ),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="codex:kept-finished",
        slot=1,
        name="保留的已完成任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "completed", source="codex_local"))

    window.set_codex_monitoring_preferences(set(), {"kept-finished"}, set())

    assert task.id in window.cards
    window.remove_codex_task(task.id)
    assert task.id not in window.cards
    assert preferences[-1] == (set(), set(), {"kept-finished"})
    manager.close()


def test_codex_cards_are_grouped_running_before_retained_terminal(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    finished = TaskConfig(
        id="codex:finished",
        slot=1,
        name="已完成任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    running = TaskConfig(
        id="codex:running",
        slot=2,
        name="执行中任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(finished, TaskState.new(finished.id, "completed", source="codex_local"))
    manager.register(running, TaskState.new(running.id, "running", source="codex_local"))

    window.set_codex_monitoring_preferences(set(), {"finished", "running"}, set())

    assert window.card_order() == ["codex:running", "codex:finished"]
    assert "运行中：1" in window.task_summary_label.text()
    assert "已完成：1" in window.task_summary_label.text()
    assert window.cards[finished.id].updated_label.text().startswith("最后活动：")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "aacc.gui.QMessageBox.question", lambda *_args: 0x00004000
    )
    window.clear_retained_tasks()
    assert finished.id not in window.cards
    assert running.id in window.cards
    manager.close()


class DeferredExecutor:
    def __init__(self) -> None:
        self.future: Future[str] = Future()
        self.submitted: list[tuple[str, tuple[object, ...]]] = []

    def submit(self, method: str, *args: object) -> Future[str]:
        self.submitted.append((method, args))
        return self.future


def test_automation_action_does_not_block_qt_and_reports_completion(
    tmp_path: Path, qtbot: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    executor = DeferredExecutor()
    window = MainWindow(manager, executor, enable_tray=False)  # type: ignore[arg-type]
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    marker: list[bool] = []
    QTimer.singleShot(0, lambda: marker.append(True))

    window._perform_action("focus", "task-1")

    qtbot.waitUntil(lambda: marker == [True], timeout=100)  # type: ignore[attr-defined]
    assert executor.submitted[0][0] == "focus"
    assert not executor.future.done()
    executor.future.set_result("已聚焦 Codex 任务")
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: window.subtitle.text() == "已聚焦 CODEX 任务", timeout=500
    )
    manager.close()


def test_automation_failure_marks_warning_on_qt_thread(tmp_path: Path, qtbot: object) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    executor = DeferredExecutor()
    window = MainWindow(manager, executor, enable_tray=False)  # type: ignore[arg-type]
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    window._perform_action("focus", "task-1")
    executor.future.set_exception(AutomationError("window missing"))

    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: manager.get("task-1").status is TaskStatus.WARNING, timeout=500
    )
    assert "window missing" in window.subtitle.text()
    manager.close()


def test_rotate_credentials_updates_live_config_and_clipboard(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    config_path = tmp_path / "config.yaml"
    config = create_default_config(config_path)
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    old = config.app.api.token
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox, "information", lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok
    )
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        rotate_api_token_callback=lambda: rotate_api_token(config_path, config),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    window.rotate_credentials()

    assert config.app.api.token != old
    assert QGuiApplication.clipboard().text() == config.app.api.token
    manager.close()


def test_discovery_warning_banner_copies_sanitized_diagnostics(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    health = DiscoveryHealth(
        degraded=True,
        consecutive_failures=3,
        diagnostic_id="abc123",
        summary="Codex session index is unreadable",
    )

    window.discovery_health_received.emit(health)

    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: not window.discovery_warning.isHidden(), timeout=500
    )
    assert len(window.discovery_warning_label.text()) <= 80
    window.copy_discovery_diagnostics()
    copied = QGuiApplication.clipboard().text()
    assert "abc123" in copied
    assert "traceback" not in copied.lower()
    assert "token" not in copied.lower()

    window.discovery_health_received.emit(DiscoveryHealth())
    qtbot.waitUntil(window.discovery_warning.isHidden, timeout=500)  # type: ignore[attr-defined]
    manager.close()


def test_kimi_discovery_warning_banner_names_kimi_source(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    health = DiscoveryHealth(
        degraded=True,
        consecutive_failures=3,
        diagnostic_id="kimi123",
        summary="Kimi session index is unreadable",
        brand="Kimi",
    )

    window.kimi_discovery_health_received.emit(health)

    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: not window.discovery_warning.isHidden(), timeout=500
    )
    assert "Kimi" in window.discovery_warning_label.text()
    window.copy_discovery_diagnostics()
    copied = QGuiApplication.clipboard().text()
    assert "kimi123" in copied
    assert "AACC Codex discovery diagnostics" in copied
    assert "AACC Kimi discovery diagnostics" in copied

    window.kimi_discovery_health_received.emit(DiscoveryHealth(brand="Kimi"))
    qtbot.waitUntil(window.discovery_warning.isHidden, timeout=500)  # type: ignore[attr-defined]
    manager.close()


def test_missing_accessibility_guidance_can_open_system_settings(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    opened: list[bool] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        accessibility_trusted=False,
        open_accessibility_settings_callback=lambda: opened.append(True),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    window.show_accessibility_guidance()

    assert opened == [True]
    assert "辅助功能" in window.accessibility_status_text()
    manager.close()


def test_only_selected_kimi_tasks_are_visible(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    first = TaskConfig(
        id="kimi:first",
        slot=1,
        name="已选择的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    second = first.model_copy(update={"id": "kimi:second", "name": "未选择的 Kimi 任务", "slot": 2})
    manager.register(first, TaskState.new(first.id, "running", source="kimi_local"))
    manager.register(second, TaskState.new(second.id, "running", source="kimi_local"))

    window.set_kimi_selected_ids({"first"})

    assert list(window.cards) == ["kimi:first"]
    manager.close()


def test_kimi_card_exposes_remove_button_and_context_menu_action(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="kimi:removable",
        slot=1,
        name="可移除的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="kimi_local"))
    window.set_kimi_selected_ids({"removable"})
    card = window.cards[task.id]

    remove_button = card.findChild(QPushButton, "removeTaskButton")
    assert remove_button is not None
    menu_labels = {action.text() for action in card.create_context_menu().actions()}
    assert "从面板移除" in menu_labels
    manager.close()


def test_remove_kimi_task_mutes_and_persists_monitoring_preferences(
    tmp_path: Path, qtbot: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    preferences: list[tuple[set[str], set[str], set[str]]] = []
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        set_kimi_monitoring_preferences=lambda manual, retained, muted: preferences.append(
            (set(manual), set(retained), set(muted))
        ),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="kimi:finished",
        slot=1,
        name="保留的已完成 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(task, TaskState.new(task.id, "completed", source="kimi_local"))
    window.set_kimi_monitoring_preferences(set(), {"finished"}, set())
    assert task.id in window.cards

    window.remove_kimi_task(task.id)

    assert task.id not in window.cards
    assert window.kimi_selected_ids == set()
    assert preferences[-1] == (set(), set(), {"finished"})
    assert settings.value("kimi_manual_tasks") == []
    assert settings.value("kimi_retained_tasks") == []
    assert settings.value("kimi_muted_tasks") == ["finished"]
    manager.close()


def test_refresh_syncs_kimi_retained_ids_from_discovery(tmp_path: Path, qtbot: object) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    retained_ids = {"kept"}
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        kimi_retained_ids=lambda: set(retained_ids),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="kimi:kept",
        slot=1,
        name="保留中的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(task, TaskState.new(task.id, "completed", source="kimi_local"))

    window.refresh()

    assert task.id in window.cards
    assert window.kimi_selected_ids == {"kept"}
    assert settings.value("kimi_retained_tasks") == ["kept"]
    manager.close()


def test_refresh_unmutes_auto_active_codex_task(tmp_path: Path, qtbot: object) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    muted_ids = {"auto-now"}
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("codex_muted_tasks", ["auto-now"])
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        codex_auto_active_ids=lambda: {"auto-now"},
        codex_muted_ids=lambda: set(muted_ids),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="codex:auto-now",
        slot=1,
        name="自动运行的任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.sync_cards()
    assert task.id not in window.cards

    muted_ids.clear()
    window.refresh()

    assert task.id in window.cards
    assert window.codex_selected_ids == {"auto-now"}
    assert settings.value("codex_muted_tasks") == []
    manager.close()


def test_refresh_unmutes_auto_active_kimi_task(tmp_path: Path, qtbot: object) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    muted_ids = {"auto-now"}
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("kimi_muted_tasks", ["auto-now"])
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        kimi_auto_active_ids=lambda: {"auto-now"},
        kimi_muted_ids=lambda: set(muted_ids),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="kimi:auto-now",
        slot=1,
        name="自动运行的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="kimi_local"))
    window.sync_cards()
    assert task.id not in window.cards

    muted_ids.clear()
    window.refresh()

    assert task.id in window.cards
    assert window.kimi_selected_ids == {"auto-now"}
    assert settings.value("kimi_muted_tasks") == []
    manager.close()


def test_rename_codex_task_updates_card_and_persists(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        codex_auto_active_ids=lambda: {"auto-now"},
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="codex:auto-now",
        slot=1,
        name="原始标题",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.sync_cards()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QInputDialog, "getText", lambda *args, **kwargs: ("我的改名", True)
    )
    window.rename_task(task.id)

    assert window.cards[task.id].display_name == "我的改名"
    assert window.custom_task_names == {task.id: "我的改名"}

    updated = task.model_copy(update={"name": "发现的新标题"})
    manager.register(updated, TaskState.new(task.id, "running", source="codex_local"))
    window.sync_cards()
    assert window.cards[task.id].display_name == "我的改名"

    settings.sync()
    reloaded_settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    reloaded = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        codex_auto_active_ids=lambda: {"auto-now"},
        settings=reloaded_settings,
    )
    qtbot.addWidget(reloaded)  # type: ignore[attr-defined]
    reloaded.sync_cards()
    assert reloaded.cards[task.id].display_name == "我的改名"
    manager.close()


def test_rename_task_with_empty_name_restores_default(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        kimi_auto_active_ids=lambda: {"auto-now"},
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="kimi:auto-now",
        slot=1,
        name="原始 Kimi 标题",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="kimi_local"))
    window.sync_cards()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QInputDialog, "getText", lambda *args, **kwargs: ("自定义名", True)
    )
    window.rename_task(task.id)
    assert window.cards[task.id].display_name == "自定义名"

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QInputDialog, "getText", lambda *args, **kwargs: ("", True)
    )
    window.rename_task(task.id)

    assert window.cards[task.id].display_name == "原始 Kimi 标题"
    assert window.custom_task_names == {}
    manager.close()


def test_kimi_selector_marks_auto_running_task_checked_and_can_restore(
    tmp_path: Path, qtbot: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    window = MainWindow(manager, AutomationExecutor(MacAutomation(config)), enable_tray=False)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    session = KimiSession(
        session_id="auto-now",
        title="自动运行的 Kimi 任务",
        updated_at=TaskState.new("example", "running").updated_at,
    )

    dialog = KimiTaskSelectionDialog([session], {"auto-now"}, {"auto-now"}, window)

    assert dialog.windowTitle() == "选择监控的 Kimi Code 任务"
    assert dialog.tasks.item(0).checkState() is Qt.CheckState.Checked
    assert "自动监控 · 运行中" in dialog.tasks.item(0).text()
    dialog.tasks.item(0).setCheckState(Qt.CheckState.Unchecked)
    dialog.restore_automatic_detection()
    assert dialog.restore_auto_requested() is True
    assert dialog.tasks.item(0).checkState() is Qt.CheckState.Checked
    manager.close()


def test_kimi_auto_running_task_is_visible_without_manual_selection_and_can_be_muted(
    tmp_path: Path, qtbot: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    auto_ids = {"auto-now"}
    preferences: list[tuple[set[str], set[str], set[str]]] = []
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        kimi_auto_active_ids=lambda: set(auto_ids),
        set_kimi_monitoring_preferences=lambda manual, retained, muted: preferences.append(
            (set(manual), set(retained), set(muted))
        ),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    task = TaskConfig(
        id="kimi:auto-now",
        slot=1,
        name="自动加入的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="kimi_local"))
    window.sync_cards()

    assert list(window.cards) == [task.id]
    assert window.kimi_selected_ids == {"auto-now"}

    window.set_kimi_monitoring_preferences(set(), set(), {"auto-now"})

    assert not window.cards
    assert preferences[-1] == (set(), set(), {"auto-now"})
    manager.close()


def test_visible_agent_types_gain_kimi_code_for_stored_settings(
    tmp_path: Path, qtbot: object
) -> None:
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("visible_agents", ["codex_cli"])
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    assert "kimi_code" in window.visible_agent_types
    assert "codex_cli" in window.visible_agent_types
    manager.close()


def test_clear_retained_tasks_removes_terminal_kimi_cards(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    finished = TaskConfig(
        id="kimi:finished",
        slot=1,
        name="已完成的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(finished, TaskState.new(finished.id, "completed", source="kimi_local"))
    window.set_kimi_monitoring_preferences(set(), {"finished"}, set())

    assert not window.clear_retained_button.isHidden()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "aacc.gui.QMessageBox.question", lambda *_args: 0x00004000
    )
    window.clear_retained_tasks()

    assert finished.id not in window.cards
    assert window.kimi_selected_ids == set()
    manager.close()



def build_kimi_desktop_window(
    tmp_path: Path, qtbot: object
) -> tuple[MainWindow, TaskManager, list[tuple[set[str], set[str], set[str]]]]:
    config = default_config()
    store = StateStore(tmp_path / "gui-kd.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    settings = QSettings(str(tmp_path / "gui-kd-settings.ini"), QSettings.Format.IniFormat)
    applied: list[tuple[set[str], set[str], set[str]]] = []
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=settings,
        kimi_desktop_sessions=lambda: [],
        kimi_desktop_auto_active_ids=lambda: set(),
        kimi_desktop_retained_ids=lambda: set(),
        kimi_desktop_muted_ids=lambda: set(),
        set_kimi_desktop_monitoring_preferences=lambda manual, retained, muted: applied.append(
            (set(manual), set(retained), set(muted))
        ),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    return window, manager, applied


def test_kimi_desktop_preferences_persist_and_apply(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager, applied = build_kimi_desktop_window(tmp_path, qtbot)
    window.set_kimi_desktop_monitoring_preferences({"conv-1"}, {"conv-2"}, {"conv-3"})
    assert applied[-1] == ({"conv-1"}, {"conv-2"}, {"conv-3"})
    assert window.kimi_desktop_selected_ids == {"conv-1", "conv-2"}
    # Note: QSettings may round-trip a single-element list as a plain string,
    # so assert the parsed in-memory sets here; raw persistence is covered by
    # the reload test below (whose loader tolerates both forms).
    assert window.kimi_desktop_manual_ids == {"conv-1"}
    assert window.kimi_desktop_retained_ids == {"conv-2"}
    assert window.kimi_desktop_muted_ids == {"conv-3"}
    manager.close()


def test_kimi_desktop_preferences_reload_from_settings(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager, _ = build_kimi_desktop_window(tmp_path, qtbot)
    window.set_kimi_desktop_monitoring_preferences({"conv-1"}, {"conv-2"}, set())
    manager.close()
    reloaded_settings = QSettings(
        str(tmp_path / "gui-kd-settings.ini"), QSettings.Format.IniFormat
    )
    config = default_config()
    store = StateStore(tmp_path / "gui-kd2.db")
    store.initialize(config.tasks)
    manager2 = TaskManager(config, store)
    reloaded = MainWindow(
        manager2,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=reloaded_settings,
    )
    qtbot.addWidget(reloaded)  # type: ignore[attr-defined]
    assert reloaded.kimi_desktop_manual_ids == {"conv-1"}
    assert reloaded.kimi_desktop_retained_ids == {"conv-2"}
    assert reloaded.kimi_desktop_selected_ids == {"conv-1", "conv-2"}
    manager2.close()


def test_remove_kimi_desktop_task_mutes_and_hides(tmp_path: Path, qtbot: object) -> None:
    window, manager, applied = build_kimi_desktop_window(tmp_path, qtbot)
    window.set_kimi_desktop_monitoring_preferences({"conv-1"}, set(), set())
    manager.register(
        TaskConfig(
            id="kimi_desktop:conv-1",
            slot=1,
            name="桌面任务",
            agent=AgentConfig(type="kimi_desktop", display_name="Kimi Desktop"),
            terminal=TerminalConfig(type="mac_app", app_bundle_id="com.moonshot.kimichat"),
        ),
        TaskState.new("kimi_desktop:conv-1", "RUNNING"),
    )
    window.sync_cards()
    assert "kimi_desktop:conv-1" in window.cards
    window.remove_kimi_desktop_task("kimi_desktop:conv-1")
    assert applied[-1] == (set(), set(), {"conv-1"})
    assert "kimi_desktop:conv-1" not in window.cards
    manager.close()


def test_kimi_desktop_task_hidden_until_selected(tmp_path: Path, qtbot: object) -> None:
    window, manager, _ = build_kimi_desktop_window(tmp_path, qtbot)
    manager.register(
        TaskConfig(
            id="kimi_desktop:conv-9",
            slot=1,
            name="未选任务",
            agent=AgentConfig(type="kimi_desktop", display_name="Kimi Desktop"),
            terminal=TerminalConfig(type="mac_app", app_bundle_id="com.moonshot.kimichat"),
        ),
        TaskState.new("kimi_desktop:conv-9", "RUNNING"),
    )
    window.sync_cards()
    assert "kimi_desktop:conv-9" not in window.cards
    window.set_kimi_desktop_monitoring_preferences({"conv-9"}, set(), set())
    assert "kimi_desktop:conv-9" in window.cards
    manager.close()


def test_kimi_desktop_visible_by_default_in_fresh_window(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert "kimi_desktop" in window.visible_agent_types
    manager.close()
