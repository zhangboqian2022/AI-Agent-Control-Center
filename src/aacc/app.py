from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from aacc.accessibility import is_accessibility_trusted, open_accessibility_settings
from aacc.api import create_api
from aacc.automation import MacAutomation
from aacc.automation_executor import AutomationExecutor
from aacc.config import load_config, rotate_api_token
from aacc.constants import APP_SUPPORT_DIR, DEFAULT_CONFIG_PATH, DEFAULT_DATABASE_PATH
from aacc.discovery_service import (
    CodexDiscoveryService,
    KimiDesktopDiscoveryService,
    KimiDiscoveryService,
)
from aacc.gui import MainWindow
from aacc.hotkeys import GlobalHotkeys
from aacc.instance_guard import InstanceGuard, activate_existing_instance
from aacc.logging_setup import configure_logging
from aacc.models import AppConfig
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


@dataclass
class Runtime:
    config_path: Path
    config: AppConfig
    manager: TaskManager
    automation: MacAutomation
    automation_executor: AutomationExecutor
    discovery: CodexDiscoveryService
    kimi_discovery: KimiDiscoveryService
    kimi_desktop_discovery: KimiDesktopDiscoveryService

    def close(self) -> None:
        self.kimi_desktop_discovery.stop()
        self.kimi_discovery.stop()
        self.discovery.stop()
        self.automation_executor.close()
        self.manager.close()


def build_runtime(
    config_path: Path,
    database_path: Path,
    *,
    accessibility_trusted: Callable[[], bool] = lambda: True,
) -> Runtime:
    config = load_config(config_path)
    store = StateStore(database_path)
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    automation = MacAutomation(config, accessibility_trusted=accessibility_trusted)
    return Runtime(
        config_path=config_path,
        config=config,
        manager=manager,
        automation=automation,
        automation_executor=AutomationExecutor(automation),
        discovery=CodexDiscoveryService(manager),
        kimi_discovery=KimiDiscoveryService(manager),
        kimi_desktop_discovery=KimiDesktopDiscoveryService(manager),
    )


class APIServerThread:
    def __init__(self, runtime: Runtime) -> None:
        api = create_api(runtime.config, runtime.manager, runtime.automation_executor)
        self.server = uvicorn.Server(
            uvicorn.Config(
                api,
                host=runtime.config.app.api.host,
                port=runtime.config.app.api.port,
                log_level="warning",
                access_log=False,
            )
        )
        self.thread = threading.Thread(target=self.server.run, name="aacc-api", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=3)


def _hotkey_actions(window: MainWindow) -> dict[str, object]:
    actions: dict[str, object] = {}
    for task in window.config.tasks:
        action_name = f"focus_task_{task.slot}"
        actions[action_name] = lambda task_id=task.id: window.external_action.emit("focus", task_id)
    actions.update(
        {
            "send_enter": lambda: window.external_action.emit("key:ENTER", window.selected_task_id),
            "send_1": lambda: window.external_action.emit("key:1", window.selected_task_id),
            "send_2": lambda: window.external_action.emit("key:2", window.selected_task_id),
            "voice": lambda: window.external_action.emit("voice", window.selected_task_id),
        }
    )
    return actions


def _run_application(config_path: Path, database_path: Path, data_dir: Path) -> int:
    configure_logging(data_dir / "logs")
    trusted = is_accessibility_trusted()
    runtime = build_runtime(
        config_path,
        database_path,
        accessibility_trusted=is_accessibility_trusted,
    )

    existing_app = QApplication.instance()
    qt_app = existing_app if isinstance(existing_app, QApplication) else QApplication(sys.argv)
    qt_app.setApplicationName("AACC")
    qt_app.setOrganizationName("AACC")
    qt_app.setQuitOnLastWindowClosed(False)
    window = MainWindow(
        runtime.manager,
        runtime.automation_executor,
        codex_sessions=runtime.discovery.catalog,
        codex_auto_active_ids=runtime.discovery.auto_active_ids,
        codex_retained_ids=runtime.discovery.retained_ids,
        codex_muted_ids=runtime.discovery.muted_ids,
        set_codex_monitoring_preferences=runtime.discovery.set_monitoring_preferences,
        kimi_sessions=runtime.kimi_discovery.catalog,
        kimi_auto_active_ids=runtime.kimi_discovery.auto_active_ids,
        kimi_retained_ids=runtime.kimi_discovery.retained_ids,
        kimi_muted_ids=runtime.kimi_discovery.muted_ids,
        set_kimi_monitoring_preferences=runtime.kimi_discovery.set_monitoring_preferences,
        kimi_desktop_sessions=runtime.kimi_desktop_discovery.catalog,
        kimi_desktop_auto_active_ids=runtime.kimi_desktop_discovery.auto_active_ids,
        kimi_desktop_retained_ids=runtime.kimi_desktop_discovery.retained_ids,
        kimi_desktop_muted_ids=runtime.kimi_desktop_discovery.muted_ids,
        set_kimi_desktop_monitoring_preferences=runtime.kimi_desktop_discovery.set_monitoring_preferences,
        rotate_api_token_callback=lambda: rotate_api_token(runtime.config_path, runtime.config),
        discovery_health=runtime.discovery.health,
        subscribe_discovery_health=runtime.discovery.subscribe_health,
        kimi_discovery_health=runtime.kimi_discovery.health,
        subscribe_kimi_discovery_health=runtime.kimi_discovery.subscribe_health,
        kimi_desktop_discovery_health=runtime.kimi_desktop_discovery.health,
        subscribe_kimi_desktop_discovery_health=runtime.kimi_desktop_discovery.subscribe_health,
        discovery_log_path=str(data_dir / "logs" / "app.log"),
        accessibility_trusted=trusted,
        open_accessibility_settings_callback=open_accessibility_settings,
    )
    window.show()
    if not trusted:
        QTimer.singleShot(0, window.show_accessibility_guidance)
    runtime.discovery.start()
    runtime.kimi_discovery.start()
    runtime.kimi_desktop_discovery.start()

    api_server: APIServerThread | None = None
    if runtime.config.app.api.enabled:
        api_server = APIServerThread(runtime)
        api_server.start()

    hotkeys = GlobalHotkeys(runtime.config.hotkeys, _hotkey_actions(window))  # type: ignore[arg-type]
    if trusted:
        hotkeys.start()

    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        hotkeys.stop()
        if api_server is not None:
            api_server.stop()
        runtime.close()

    qt_app.aboutToQuit.connect(cleanup)
    try:
        return qt_app.exec()
    finally:
        cleanup()


def main() -> int:
    config_path = Path(os.environ.get("AACC_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    database_path = Path(os.environ.get("AACC_DATABASE_PATH", DEFAULT_DATABASE_PATH))
    data_dir = config_path.parent if config_path != DEFAULT_CONFIG_PATH else APP_SUPPORT_DIR
    guard = InstanceGuard(data_dir / "aacc.lock")
    if not guard.acquire():
        activate_existing_instance()
        return 0
    try:
        return _run_application(config_path, database_path, data_dir)
    finally:
        guard.close()
