from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from PySide6.QtWidgets import QApplication

from aacc.api import create_api
from aacc.automation import MacAutomation
from aacc.config import load_config
from aacc.constants import APP_SUPPORT_DIR, DEFAULT_CONFIG_PATH, DEFAULT_DATABASE_PATH
from aacc.discovery_service import CodexDiscoveryService
from aacc.gui import MainWindow
from aacc.hotkeys import GlobalHotkeys
from aacc.logging_setup import configure_logging
from aacc.models import AppConfig
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


@dataclass
class Runtime:
    config: AppConfig
    manager: TaskManager
    automation: MacAutomation
    discovery: CodexDiscoveryService

    def close(self) -> None:
        self.discovery.stop()
        self.manager.close()


def build_runtime(config_path: Path, database_path: Path) -> Runtime:
    config = load_config(config_path)
    store = StateStore(database_path)
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    return Runtime(
        config=config,
        manager=manager,
        automation=MacAutomation(config),
        discovery=CodexDiscoveryService(manager),
    )


class APIServerThread:
    def __init__(self, runtime: Runtime) -> None:
        api = create_api(runtime.config, runtime.manager, runtime.automation)
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


def main() -> int:
    config_path = Path(os.environ.get("AACC_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    database_path = Path(os.environ.get("AACC_DATABASE_PATH", DEFAULT_DATABASE_PATH))
    data_dir = config_path.parent if config_path != DEFAULT_CONFIG_PATH else APP_SUPPORT_DIR
    configure_logging(data_dir / "logs")
    runtime = build_runtime(config_path, database_path)

    existing_app = QApplication.instance()
    qt_app = existing_app if isinstance(existing_app, QApplication) else QApplication(sys.argv)
    qt_app.setApplicationName("AACC")
    qt_app.setOrganizationName("AACC")
    qt_app.setQuitOnLastWindowClosed(False)
    window = MainWindow(
        runtime.manager,
        runtime.automation,
        codex_sessions=runtime.discovery.catalog,
        set_codex_monitoring=runtime.discovery.set_selected_ids,
    )
    window.show()
    runtime.discovery.start()

    api_server: APIServerThread | None = None
    if runtime.config.app.api.enabled:
        api_server = APIServerThread(runtime)
        api_server.start()

    hotkeys = GlobalHotkeys(runtime.config.hotkeys, _hotkey_actions(window))  # type: ignore[arg-type]
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
