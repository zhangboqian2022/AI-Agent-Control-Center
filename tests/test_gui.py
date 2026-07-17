from pathlib import Path

from PySide6.QtWidgets import QApplication

from aacc.automation import MacAutomation
from aacc.config import default_config
from aacc.gui import MainWindow, STATUS_COLORS, TaskCard
from aacc.models import TaskState, TaskStatus
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


def build_window(tmp_path: Path, qtbot: object) -> tuple[MainWindow, TaskManager]:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    window = MainWindow(manager, MacAutomation(config), enable_tray=False)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    return window, manager


def test_window_displays_four_task_cards(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert len(window.findChildren(TaskCard)) == 4
    assert [card.slot_label.text() for card in window.cards.values()] == ["01", "02", "03", "04"]
    manager.close()


def test_all_statuses_have_a_color() -> None:
    assert set(STATUS_COLORS) == set(TaskStatus)


def test_refresh_updates_card_text_and_color(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    manager.update(
        TaskState.new(
            "task-2", "waiting-approval", message="等待批准 npm test", source="manual"
        )
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


def test_card_context_menu_exposes_safe_controls(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    menu = window.cards["task-1"].create_context_menu()
    labels = {action.text() for action in menu.actions()}
    assert {"切换到任务", "发送 Enter", "发送 1", "发送 2", "启动语音输入"} <= labels
    manager.close()


def test_window_declares_persisted_setting_keys(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert window.settings_keys == {"geometry", "compact_mode", "always_on_top", "opacity"}
    assert QApplication.instance() is not None
    manager.close()
