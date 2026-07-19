from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from aacc.automation import MacAutomation
from aacc.codex_discovery import CodexSession
from aacc.config import default_config
from aacc.gui import STATUS_COLORS, CodexTaskSelectionDialog, MainWindow, TaskCard
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


def build_window(tmp_path: Path, qtbot: object) -> tuple[MainWindow, TaskManager]:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(manager, MacAutomation(config), enable_tray=False, settings=settings)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    return window, manager


def test_window_starts_with_no_codex_cards_until_tasks_are_selected(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert len(window.findChildren(TaskCard)) == 0
    assert "未选择 Codex 任务" in window.empty_tasks_label.text()
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

    assert "font-size: 95px" in window.cards[task.id].dot.styleSheet()
    assert window.minimumHeight() >= 270
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
    window = MainWindow(manager, MacAutomation(config), enable_tray=False)
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
    preferences: list[tuple[set[str], set[str]]] = []
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        MacAutomation(config),
        enable_tray=False,
        codex_auto_active_ids=lambda: set(auto_ids),
        set_codex_monitoring_preferences=lambda manual, muted: preferences.append(
            (set(manual), set(muted))
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

    window.set_codex_monitoring_preferences(set(), {"auto-now"})

    assert not window.cards
    assert preferences[-1] == (set(), {"auto-now"})
    manager.close()
