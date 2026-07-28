from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
)

from aacc.automation import AutomationError, MacAutomation
from aacc.automation_executor import AutomationExecutor
from aacc.codex_discovery import CodexSession
from aacc.codex_quota import CodexQuotaSnapshot, CodexQuotaStatus, CodexQuotaWindow
from aacc.config import create_default_config, default_config, rotate_api_token
from aacc.discovery_service import DiscoveryHealth
from aacc.gui import (
    STATUS_COLORS,
    CodexQuotaBar,
    CodexTaskSelectionDialog,
    KimiDesktopTaskSelectionDialog,
    KimiOAuthDialog,
    KimiTaskSelectionDialog,
    MainWindow,
    QuotaBar,
    SettingsDialog,
    TaskCard,
    _elapsed,
    status_name,
)
from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.kimi_desktop_discovery import KimiDesktopSession
from aacc.kimi_discovery import KimiSession
from aacc.kimi_quota import KimiQuota, QuotaDetail
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


def build_window(
    tmp_path: Path,
    qtbot: object,
    *,
    settings: QSettings | None = None,
    language_manager: LanguageManager | None = None,
) -> tuple[MainWindow, TaskManager]:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    settings = settings or QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    language_manager = language_manager or LanguageManager(ZH_CN, settings)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=settings,
        language_manager=language_manager,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    return window, manager


def test_header_language_button_switches_live_and_persists(tmp_path: Path, qtbot: object) -> None:
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    language_manager = LanguageManager(ZH_CN, settings)
    window, manager = build_window(
        tmp_path,
        qtbot,
        settings=settings,
        language_manager=language_manager,
    )
    assert window.language_button.text() == "EN"
    assert window.language_button.toolTip() == "Switch to English"
    assert window.running_group_label.text() == "运行中"

    window.language_button.click()

    assert language_manager.language == EN_US
    assert settings.value("ui_language") == EN_US
    assert window.language_button.text() == "中"
    assert window.language_button.toolTip() == "切换到中文"
    assert window.running_group_label.text() == "Running"
    assert window.retained_group_label.text() == "Completed · Retained until removed"
    assert window.empty_tasks_label.text().startswith("No Codex / Kimi Code")
    assert window.task_summary_label.text() == "Running: 0 · Completed: 0 · 0 tasks"
    assert window.about_button.toolTip() == "About"
    assert window.settings_button.toolTip() == "Settings"
    assert window.hide_button.toolTip() == "Hide"
    manager.close()


def test_language_switch_preserves_geometry_after_queued_events(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.show()
    window.resize(420, 700)
    qtbot.wait(10)  # type: ignore[attr-defined]
    geometry_before = window.geometry()
    resize_calls: list[bool] = []

    def resize_content() -> None:
        resize_calls.append(True)
        window.resize(geometry_before.width(), geometry_before.height() - 1)

    window._resize_to_card_content = resize_content  # type: ignore[method-assign]

    window.language_button.click()
    qtbot.wait(10)  # type: ignore[attr-defined]

    assert window.geometry() == geometry_before
    assert resize_calls == []
    manager.close()


def test_header_replaces_compact_button_but_settings_keeps_compact(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)

    header_buttons = {button.objectName(): button for button in window.findChildren(QPushButton)}
    assert header_buttons["languageButton"] is window.language_button
    assert all(button.text() != "↕" for button in header_buttons.values())
    assert "#languageButton" in window.styleSheet()
    dialog = SettingsDialog(window)
    assert "切换紧凑 / 展开模式" in {button.text() for button in dialog.findChildren(QPushButton)}
    manager.close()


def test_settings_and_selector_use_current_language(tmp_path: Path, qtbot: object) -> None:
    language_manager = LanguageManager(EN_US)
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=language_manager,
    )

    settings = SettingsDialog(window)
    selector = CodexTaskSelectionDialog([], set(), set(), window)
    settings_buttons = {button.text() for button in settings.findChildren(QPushButton)}

    assert settings.windowTitle() == "AACC Settings"
    assert selector.windowTitle() == "Select Codex tasks to monitor"
    assert "Compact / expanded mode" in settings_buttons
    assert "Start monitoring" in {button.text() for button in selector.findChildren(QPushButton)}
    manager.close()


def test_kimi_device_authorization_uses_current_language(qapp: object) -> None:
    dialog = KimiOAuthDialog(language_manager=LanguageManager(EN_US))

    assert dialog.windowTitle() == "Kimi authorization"
    assert any(
        label.text().startswith("The Kimi authorization page")
        for label in dialog.findChildren(QLabel)
    )
    assert "Cancel authorization" in {button.text() for button in dialog.findChildren(QPushButton)}


def test_confirmations_accessibility_and_about_use_current_language(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    language_manager = LanguageManager(EN_US)
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=language_manager,
    )
    captured_questions: list[tuple[str, str]] = []
    shown_boxes: list[QMessageBox] = []
    shown_box_titles: list[str] = []
    shown_about: list[tuple[str, str]] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "setWindowTitle",
        lambda _box, title: shown_box_titles.append(title),
    )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda _parent, title, prompt, *_args: (
            captured_questions.append((title, prompt)) or QMessageBox.StandardButton.Cancel
        ),
    )
    window.rotate_credentials()
    assert captured_questions[-1] == (
        "Reset credentials",
        "The old credentials will become invalid immediately. Continue?",
    )

    task = TaskConfig(
        id="codex:english-confirmation",
        slot=1,
        name="English confirmation",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "completed", source="codex_local"))
    window.set_codex_monitoring_preferences(set(), {"english-confirmation"}, set())
    window.clear_retained_tasks()
    assert captured_questions[-1] == (
        "Clear completed tasks",
        "Remove 1 completed tasks from the panel?",
    )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "exec",
        lambda box: shown_boxes.append(box) or QMessageBox.StandardButton.Cancel,
    )
    window.accessibility_trusted = False
    window.show_accessibility_guidance()
    assert shown_box_titles[-1] == "Accessibility permission required"
    assert shown_boxes[-1].text().startswith("AACC needs Accessibility permission")
    assert shown_boxes[-1].checkBox() is not None
    assert shown_boxes[-1].checkBox().text() == "Do not remind me again"

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "aacc.gui.QMessageBox.about",
        lambda _parent, title, text: shown_about.append((title, text)),
    )
    window.show_about()
    assert shown_about[-1][0] == "About AACC"
    assert "\nVersion " in shown_about[-1][1]
    assert "\nInstaller AACC-" in shown_about[-1][1]
    manager.close()


def test_credential_result_and_rename_prompt_use_current_language(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    language_manager = LanguageManager(EN_US)
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=language_manager,
    )
    shown_boxes: list[QMessageBox] = []
    shown_box_titles: list[str] = []
    rename_prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "setWindowTitle",
        lambda _box, title: shown_box_titles.append(title),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox, "exec", lambda box: shown_boxes.append(box) or 0
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QInputDialog,
        "getText",
        lambda _parent, title, prompt, **_kwargs: (
            rename_prompts.append((title, prompt)) or ("", False)
        ),
    )

    window.rotate_credentials()
    result_box = shown_boxes[-1]
    assert shown_box_titles[-1] == "Credentials reset"
    assert result_box.text().startswith("The old credentials are invalid.")
    assert "Copy" in {button.text() for button in result_box.buttons()}

    task = TaskConfig(
        id="codex:english-rename",
        slot=1,
        name="English rename",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.rename_task(task.id)
    assert rename_prompts == [("Rename task", "Task name (leave blank to restore the default):")]
    manager.close()


def test_manual_selection_and_copy_feedback_use_current_language(
    tmp_path: Path, qtbot: object
) -> None:
    language_manager = LanguageManager(EN_US)
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=language_manager,
    )

    window._perform_action("select", "task-1")
    assert window.subtitle.text().startswith("SELECTED ")

    window._perform_action("copy", "task-1")
    assert window.subtitle.text() == "TASK INFORMATION COPIED"

    window._perform_action("status:running", "task-1")
    assert window.subtitle.text() == "MARKED AS RUNNING"
    assert manager.get("task-1").message == "Manually updated"
    manager.close()


def test_queued_automation_and_empty_kimi_key_use_current_language(
    tmp_path: Path, qtbot: object
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "gui.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    executor = DeferredExecutor()
    language_manager = LanguageManager(ZH_CN)
    window = MainWindow(  # type: ignore[arg-type]
        manager,
        executor,
        enable_tray=False,
        language_manager=language_manager,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    window._perform_action("focus", "task-1")
    assert window.subtitle.text() == "自动操作已排队"

    class EmptyKeyService:
        def set_api_key(self, _key: str) -> None:
            raise ValueError("API Key 不能为空")

    window.quota_service = EmptyKeyService()  # type: ignore[assignment]
    language_manager.set_language(EN_US)
    window.save_kimi_api_key("")
    assert window.subtitle.text() == "API Key is required"
    manager.close()


def test_oauth_failure_feedback_does_not_translate_remote_details(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=LanguageManager(EN_US),
    )

    window._on_oauth_finished(
        False,
        "https://remote.invalid/?code=remote-code#access_token=remote-token",
    )

    assert window.subtitle.text() == "KIMI authorization failed"
    assert "remote-code" not in window.subtitle.text()
    assert "remote-token" not in window.subtitle.text()
    manager.close()


def test_existing_task_and_quota_widgets_retranslate_without_refreshing_services(
    tmp_path: Path, qtbot: object
) -> None:
    language_manager = LanguageManager(ZH_CN)
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=language_manager,
    )
    task = TaskConfig(
        id="codex:live-language",
        slot=1,
        name="Live language",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    state = TaskState.new(
        task.id,
        "waiting_approval",
        source="codex_local",
    )
    manager.register(task, state)
    window.set_codex_selected_ids({"live-language"})
    card = window.cards[task.id]
    card_state_before = card.state
    window.quota_bar = QuotaBar(language_manager)
    quota = KimiQuota(
        weekly=QuotaDetail(
            used=42,
            limit=100,
            remaining=58,
            reset_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            percentage=42,
        ),
        five_hour=None,
        monthly=None,
        membership_level=None,
        booster=None,
    )
    window.quota_bar.show_quota(quota)
    window.codex_quota_bar = CodexQuotaBar(language_manager)
    snapshot = CodexQuotaSnapshot(
        weekly=CodexQuotaWindow(
            used_percent=9,
            window_minutes=10080,
            resets_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        ),
        observed_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        status=CodexQuotaStatus.OK,
    )
    window.codex_quota_bar.show_quota(snapshot)
    refresh_calls: list[str] = []
    window.refresh = lambda: refresh_calls.append("window")  # type: ignore[method-assign]

    class TrackingService:
        def refresh_now(self) -> None:
            refresh_calls.append("service")

    window.quota_service = TrackingService()  # type: ignore[assignment]
    window.codex_quota_service = TrackingService()  # type: ignore[assignment]
    compact_before = window.compact_mode
    geometry_before = window.geometry()
    login_before = window._kimi_web_authorized

    language_manager.set_language(EN_US)

    assert card.state is card_state_before
    assert card.status_label.text() == "Waiting for approval"
    assert "Click to switch tasks" in card.toolTip()
    assert card.remove_button is not None
    assert card.remove_button.accessibleName() == "Remove from panel"
    context_labels = {action.text() for action in card.create_context_menu().actions()}
    assert {"Switch to task", "Manual status", "Copy", "Rename", "Remove"} <= context_labels
    assert window.quota_bar._last_quota is quota
    assert window.codex_quota_bar._last_codex_quota is snapshot
    assert window.quota_bar.reset_labels()[1].startswith("Resets ")
    assert window.codex_quota_bar.reset_labels()[0].startswith("Resets ")
    assert refresh_calls == []
    assert window.compact_mode is compact_before
    assert window.geometry() == geometry_before
    assert window._kimi_web_authorized is login_before
    manager.close()


def test_language_subscription_is_single_and_removed_on_close(
    tmp_path: Path, qtbot: object
) -> None:
    language_manager = LanguageManager(ZH_CN)
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=language_manager,
    )
    assert len(language_manager._subscribers) == 1

    language_manager.set_language(EN_US)
    language_manager.set_language(ZH_CN)

    assert len(language_manager._subscribers) == 1
    window.close()
    assert language_manager._subscribers == []
    manager.close()


def test_tray_actions_retranslate_and_keep_compact_toggle(tmp_path: Path, qtbot: object) -> None:
    language_manager = LanguageManager(ZH_CN)
    window, manager = build_window(
        tmp_path,
        qtbot,
        language_manager=language_manager,
    )
    window._create_tray()
    assert window.tray_show_action is not None
    assert window.tray_compact_action is not None
    assert window.tray_quit_action is not None
    assert window.tray_show_action.text() == "显示/隐藏 AACC"
    compact_before = window.compact_mode

    window.tray_compact_action.trigger()
    language_manager.set_language(EN_US)

    assert window.compact_mode is not compact_before
    assert window.tray_show_action.text() == "Show/Hide AACC"
    assert window.tray_compact_action.text() == "Compact mode"
    assert window.tray_quit_action.text() == "Quit AACC"
    window._quitting = True
    window.close()
    manager.close()


def test_app_reactivation_shows_hidden_window(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.show()
    window.hide()
    assert not window.isVisible()
    window.handle_app_state_change(Qt.ApplicationState.ApplicationActive)
    assert window.isVisible()
    assert not window.isMinimized()
    window.hide()
    window.handle_app_state_change(Qt.ApplicationState.ApplicationInactive)
    assert not window.isVisible()
    manager.close()


def test_toggle_visible_restores_minimized_window_instead_of_hiding_it(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.show()
    window.showMinimized()
    assert window.isMinimized()
    window.toggle_visible()
    assert window.isVisible()
    assert not window.isMinimized()
    window.toggle_visible()
    assert not window.isVisible()
    manager.close()


def test_window_starts_with_no_codex_cards_until_tasks_are_selected(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert len(window.findChildren(TaskCard)) == 0
    assert "未选择 Codex / Kimi Code / Kimi Desktop 任务" in window.empty_tasks_label.text()
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


def test_status_names_and_terminal_elapsed_label_retranslate_live(qapp: object) -> None:
    language_manager = LanguageManager(ZH_CN)
    task = TaskConfig(
        id="codex:localized-card",
        slot=1,
        name="Localized",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    state = TaskState.new(task.id, "waiting_approval", source="codex_local")
    state = TaskState(
        task_id=state.task_id,
        status=TaskStatus.WAITING_APPROVAL,
        message="",
        updated_at=state.updated_at,
        started_at=state.started_at,
        finished_at=state.finished_at,
        source=state.source,
        confidence=state.confidence,
        metadata=state.metadata,
    )
    card = TaskCard(task, state, language_manager=language_manager)

    assert status_name(TaskStatus.WAITING_APPROVAL, language_manager) == "等待批准"
    assert card.status_label.text() == "等待批准"
    assert card.message_label.text() == "暂无消息"

    language_manager.set_language(EN_US)
    card.retranslate_ui()

    assert status_name(TaskStatus.WAITING_APPROVAL, language_manager) == ("Waiting for approval")
    assert card.status_label.text() == "Waiting for approval"
    assert card.message_label.text() == "No message"
    assert card.updated_label.text().startswith("Last activity ")
    started_at = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    card.set_state(
        TaskState(
            task_id=task.id,
            status=TaskStatus.COMPLETED,
            started_at=started_at,
            updated_at=started_at + timedelta(minutes=1),
            finished_at=started_at + timedelta(minutes=1),
        )
    )
    assert card.timer_label.text() == "Total 00:01:00"


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
    card = TaskCard(task, state, language_manager=LanguageManager(ZH_CN))
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


def test_sync_cards_skips_layout_rebuild_when_order_unchanged(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="codex:stable",
        slot=1,
        name="稳定任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.set_codex_selected_ids({"stable"})
    rebuilds: list[bool] = []
    original = window._rebuild_card_layout

    def counting(layout: object, tasks: object) -> None:
        rebuilds.append(True)
        original(layout, tasks)  # type: ignore[arg-type]

    window._rebuild_card_layout = counting  # type: ignore[method-assign]
    window.sync_cards()

    assert rebuilds == []
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
    assert "切换到任务" in labels
    assert "手动标记状态" in labels
    assert labels.isdisjoint({"启动语音输入", "发送 Enter", "发送 1", "发送 2", "发送 ↑", "发送 ↓"})
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
        "agent_visibility_migrated_v2",
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
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        language_manager=LanguageManager(ZH_CN),
    )
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
    window = MainWindow(  # type: ignore[arg-type]
        manager,
        executor,
        enable_tray=False,
        language_manager=LanguageManager(ZH_CN),
    )
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
    window = MainWindow(  # type: ignore[arg-type]
        manager,
        executor,
        enable_tray=False,
        language_manager=LanguageManager(ZH_CN),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    window._perform_action("focus", "task-1")
    executor.future.set_exception(AutomationError("window missing"))

    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: manager.get("task-1").status is TaskStatus.WARNING, timeout=500
    )
    assert "window missing" in window.subtitle.text()
    manager.close()


def test_rotate_credentials_shows_token_and_copies_only_on_request(
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
    shown: list[QMessageBox] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox, "exec", lambda box: shown.append(box) or 0
    )
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        language_manager=LanguageManager(ZH_CN),
        rotate_api_token_callback=lambda: rotate_api_token(config_path, config),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    QGuiApplication.clipboard().setText("unrelated clipboard content")

    window.rotate_credentials()

    assert config.app.api.token != old
    assert shown and shown[0].informativeText() == config.app.api.token
    # Token is never pushed to the clipboard without an explicit user action.
    assert QGuiApplication.clipboard().text() == "unrelated clipboard content"

    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "clickedButton",
        lambda box: next(button for button in box.buttons() if button.text() == "复制"),
    )
    window.rotate_credentials()

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


def test_kimi_discovery_warning_banner_names_kimi_source(tmp_path: Path, qtbot: object) -> None:
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
        QMessageBox, "exec", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )
    settings = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        language_manager=LanguageManager(ZH_CN),
        accessibility_trusted=False,
        open_accessibility_settings_callback=lambda: opened.append(True),
        settings=settings,
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    window.show_accessibility_guidance()

    assert opened == [True]
    assert "辅助功能" in window.accessibility_status_text()
    manager.close()


def test_accessibility_guidance_skipped_after_do_not_show_again(
    tmp_path: Path, qtbot: object, monkeypatch: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.accessibility_trusted = False
    exec_calls: list[bool] = []

    def fake_exec(box: QMessageBox) -> QMessageBox.StandardButton:
        exec_calls.append(True)
        checkbox = box.checkBox()
        assert checkbox is not None
        checkbox.setChecked(True)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)  # type: ignore[attr-defined]

    window.show_accessibility_guidance()
    assert exec_calls == [True]
    assert window._settings.value("accessibility_guidance_dismissed", False, type=bool)

    window.show_accessibility_guidance()
    assert exec_calls == [True]
    manager.close()


def test_new_agent_types_seeded_once_then_user_choice_respected(
    tmp_path: Path, qtbot: object
) -> None:
    seed = QSettings(str(tmp_path / "gui-settings.ini"), QSettings.Format.IniFormat)
    seed.setValue("visible_agents", ["codex_cli"])
    seed.sync()

    window, manager = build_window(tmp_path, qtbot)
    assert {"codex_cli", "kimi_code", "kimi_desktop"} <= window.visible_agent_types
    manager.close()

    # The user then hides the new brands; a fresh window must not re-add them.
    seed.setValue("visible_agents", ["codex_cli"])
    seed.sync()
    window2, manager2 = build_window(tmp_path, qtbot)
    assert window2.visible_agent_types == {"codex_cli"}
    manager2.close()


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


def test_kimi_card_shows_work_dir_basename_next_to_status(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="kimi:workdir",
        slot=1,
        name="带目录的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(
        task,
        TaskState.new(
            task.id,
            "running",
            source="kimi_local",
            metadata={"work_dir": "/Users/test/Desktop/codelight"},
        ),
    )
    window.set_kimi_selected_ids({"workdir"})
    card = window.cards[task.id]

    assert card.workdir_label.text() == "· codelight"
    assert not card.workdir_label.isHidden()
    assert card.workdir_label.toolTip() == "/Users/test/Desktop/codelight"
    manager.close()


def test_codex_card_hides_work_dir_label(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="codex:no-dir",
        slot=1,
        name="Codex 任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="codex_local"))
    window.set_codex_selected_ids({"no-dir"})
    card = window.cards[task.id]

    assert card.workdir_label.isHidden()
    manager.close()


def test_remove_request_with_unknown_brand_prefix_logs_error(
    tmp_path: Path, qtbot: object, caplog: pytest.LogCaptureFixture
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    with caplog.at_level("ERROR", logger="aacc.gui"):
        window._remove_task_requested("futurebrand:abc")
    assert "futurebrand:abc" in caplog.text
    assert window.subtitle.text() == "操作未生效"
    manager.close()


def test_remove_request_dispatches_to_known_brand(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    task = TaskConfig(
        id="kimi:dispatch",
        slot=1,
        name="可移除的 Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    manager.register(task, TaskState.new(task.id, "running", source="kimi_local"))
    window.set_kimi_selected_ids({"dispatch"})

    window._remove_task_requested("kimi:dispatch")

    assert "kimi:dispatch" not in window.cards
    assert "dispatch" in window.kimi_muted_ids
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        language_manager=LanguageManager(ZH_CN),
    )
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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
        language_manager=LanguageManager(ZH_CN),
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


def test_kimi_desktop_preferences_persist_and_apply(tmp_path: Path, qtbot: object) -> None:
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


def test_kimi_desktop_preferences_reload_from_settings(tmp_path: Path, qtbot: object) -> None:
    window, manager, _ = build_kimi_desktop_window(tmp_path, qtbot)
    window.set_kimi_desktop_monitoring_preferences({"conv-1"}, {"conv-2"}, set())
    manager.close()
    reloaded_settings = QSettings(str(tmp_path / "gui-kd-settings.ini"), QSettings.Format.IniFormat)
    config = default_config()
    store = StateStore(tmp_path / "gui-kd2.db")
    store.initialize(config.tasks)
    manager2 = TaskManager(config, store)
    reloaded = MainWindow(
        manager2,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        language_manager=LanguageManager(ZH_CN),
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


def test_kimi_desktop_visible_by_default_in_fresh_window(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert "kimi_desktop" in window.visible_agent_types
    manager.close()


def test_kimi_desktop_task_selection_dialog_applies_preferences(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager, applied = build_kimi_desktop_window(tmp_path, qtbot)
    sessions = [
        KimiDesktopSession(
            session_id="conv-1",
            title="桌面会话",
            updated_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
        )
    ]
    dialog = KimiDesktopTaskSelectionDialog(sessions, set(), set(), window)
    assert dialog.tasks.count() == 1
    dialog.tasks.item(0).setCheckState(Qt.CheckState.Checked)
    selected = dialog.selected_ids()
    assert selected == {"conv-1"}
    manager.close()


def test_kimi_desktop_health_warning_merges_all_brands(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.show()
    assert not window.discovery_warning.isVisible()
    window.kimi_desktop_discovery_health_received.emit(
        DiscoveryHealth(degraded=True, summary="index unreadable", brand="Kimi Desktop")
    )
    assert window.discovery_warning.isVisible()
    assert "Kimi Desktop" in window.discovery_warning_label.text()
    window.kimi_desktop_discovery_health_received.emit(DiscoveryHealth(brand="Kimi Desktop"))
    assert not window.discovery_warning.isVisible()
    manager.close()


def test_empty_tasks_label_mentions_kimi_desktop(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert "Kimi Desktop" in window.empty_tasks_label.text()
    manager.close()
