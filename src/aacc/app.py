from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

import aacc
from aacc.accessibility import is_accessibility_trusted, open_accessibility_settings
from aacc.api import create_api
from aacc.automation import create_automation
from aacc.automation_executor import AutomationController, AutomationExecutor
from aacc.codex_app_server import CodexAppServerReader, find_codex_executable
from aacc.codex_quota import CodexQuotaReader, CompositeCodexQuotaReader
from aacc.codex_quota_service import CodexQuotaService
from aacc.config import load_config, rotate_api_token
from aacc.constants import (
    APP_SUPPORT_DIR,
    DEFAULT_CONFIG_PATH,
    resolve_database_path,
)
from aacc.discovery_service import (
    CodexDiscoveryService,
    KimiDesktopDiscoveryService,
    KimiDiscoveryService,
)
from aacc.file_security import FileProtectionError
from aacc.gui import MainWindow
from aacc.hotkeys import AccessibilityHotkeySync, GlobalHotkeys, HotkeyDriver
from aacc.i18n import LanguageManager, load_language
from aacc.instance_guard import InstanceGuard, activate_existing_instance
from aacc.kimi_web_quota_service import KimiWebQuotaService
from aacc.kimi_web_session import initialize_native_webview
from aacc.logging_setup import configure_logging
from aacc.models import AppConfig
from aacc.persistence import StateStore
from aacc.quota_service import QuotaService
from aacc.shutdown_windows import WindowsShutdownListener, request_shutdown_for_update
from aacc.task_manager import TaskManager
from aacc.webview_smoke import run_native_webview_smoke
from aacc.windows_broker import build_broker_command, packaged_broker_path

_logger = logging.getLogger("aacc.app")


@dataclass
class Runtime:
    config_path: Path
    config: AppConfig
    manager: TaskManager
    automation: AutomationController
    automation_executor: AutomationExecutor
    discovery: CodexDiscoveryService
    kimi_discovery: KimiDiscoveryService
    kimi_desktop_discovery: KimiDesktopDiscoveryService
    codex_quota_service: CodexQuotaService | None = None
    quota_service: QuotaService | None = None
    kimi_web_quota_service: KimiWebQuotaService | None = None

    def close(self) -> None:
        operations: tuple[tuple[str, Callable[[], None]], ...] = (
            (
                "codex-quota",
                self.codex_quota_service.stop
                if self.codex_quota_service is not None
                else lambda: None,
            ),
            (
                "kimi-quota",
                self.quota_service.stop if self.quota_service is not None else lambda: None,
            ),
            (
                "kimi-web-quota",
                self.kimi_web_quota_service.stop
                if self.kimi_web_quota_service is not None
                else lambda: None,
            ),
            ("kimi-desktop-discovery", self.kimi_desktop_discovery.stop),
            ("kimi-discovery", self.kimi_discovery.stop),
            ("discovery", self.discovery.stop),
            ("automation-executor", self.automation_executor.close),
            ("manager", self.manager.close),
        )
        for stage, operation in operations:
            _logger.info("Runtime cleanup starting stage=%s", stage)
            try:
                operation()
            except Exception:  # noqa: BLE001 - cleanup must reach SQLite close
                _logger.error("Runtime cleanup failed stage=%s", stage)
            else:
                _logger.info("Runtime cleanup completed stage=%s", stage)


def _default_quota_service_factory(config_dir: Path, config: AppConfig) -> QuotaService | None:
    if not config.app.kimi_quota_enabled:
        return None
    return QuotaService(config_dir, version=aacc.__version__)


def _default_kimi_web_quota_service_factory(
    config_dir: Path,
    config: AppConfig,
    language_manager: LanguageManager | None = None,
) -> KimiWebQuotaService | None:
    if not config.app.kimi_quota_enabled:
        return None
    return KimiWebQuotaService(
        config_dir,
        language_manager=language_manager,
    )


def _default_codex_quota_service_factory(
    config: AppConfig,
    *,
    platform: str | None = None,
    frozen: bool | None = None,
    application_executable: Path | None = None,
    frozen_bundle_dir: Path | None = None,
    environ: dict[str, str] | None = None,
    parent_pid: int | None = None,
) -> CodexQuotaService | None:
    if not config.app.codex_quota_enabled:
        return None
    resolved_platform = sys.platform if platform is None else platform
    executable = find_codex_executable()
    live_reader: CodexAppServerReader | None = None
    if executable is not None and resolved_platform != "win32":
        live_reader = CodexAppServerReader(executable, platform=resolved_platform)
    elif executable is not None:
        is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        process_executable = (
            Path(sys.executable) if application_executable is None else application_executable
        )
        environment = dict(os.environ) if environ is None else environ
        broker = packaged_broker_path(
            platform=resolved_platform,
            frozen=is_frozen,
            executable=process_executable,
            environ=environment,
        )
        bundle_dir: Path | None
        if is_frozen:
            raw_bundle = (
                getattr(sys, "_MEIPASS", None) if frozen_bundle_dir is None else frozen_bundle_dir
            )
            bundle_dir = Path(raw_bundle) if raw_bundle is not None else None
            expected_bundle = process_executable.parent / "_internal"
            if bundle_dir != expected_bundle:
                bundle_dir = None
        else:
            # Source mode has no PyInstaller bundle. The broker's validated
            # absolute parent is the narrowest deterministic protocol value.
            bundle_dir = broker.parent if broker is not None else None
        process_pid = os.getpid() if parent_pid is None else parent_pid
        if broker is not None and bundle_dir is not None:
            try:
                build_broker_command(
                    broker,
                    executable,
                    parent_pid=process_pid,
                    bundle_dir=bundle_dir,
                )
            except ValueError:
                pass
            else:
                live_reader = CodexAppServerReader(
                    executable,
                    platform="win32",
                    command_factory=lambda codex: build_broker_command(
                        broker,
                        codex,
                        parent_pid=process_pid,
                        bundle_dir=bundle_dir,
                    ),
                )
        if live_reader is None:
            _logger.warning("Windows Codex quota broker unavailable; using local fallback")
    reader = CompositeCodexQuotaReader(
        live_reader,
        CodexQuotaReader(Path.home() / ".codex" / "sessions"),
    )
    return CodexQuotaService(reader)


def build_runtime(
    config_path: Path,
    database_path: Path,
    *,
    accessibility_trusted: Callable[[], bool] = lambda: True,
    quota_service_factory: Callable[[Path], QuotaService | None] | None = None,
    kimi_web_quota_service_factory: (Callable[[Path], KimiWebQuotaService | None] | None) = None,
    codex_quota_service_factory: Callable[[], CodexQuotaService | None] | None = None,
    language_manager: LanguageManager | None = None,
) -> Runtime:
    config = load_config(config_path)
    store = StateStore(database_path)
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    automation = create_automation(config, accessibility_trusted=accessibility_trusted)
    factory = quota_service_factory or (
        lambda config_dir: _default_quota_service_factory(config_dir, config)
    )
    codex_quota_factory = codex_quota_service_factory or (
        lambda: _default_codex_quota_service_factory(config)
    )
    kimi_web_quota_factory = kimi_web_quota_service_factory or (
        lambda config_dir: _default_kimi_web_quota_service_factory(
            config_dir,
            config,
            language_manager,
        )
    )
    quota_service = factory(config_path.parent)
    kimi_web_quota_service = kimi_web_quota_factory(config_path.parent)
    if quota_service is not None and kimi_web_quota_service is not None:
        quota_service.set_externally_scheduled(True)
        kimi_web_quota_service.set_fallback_refresh(quota_service.refresh_now)
    return Runtime(
        config_path=config_path,
        config=config,
        manager=manager,
        automation=automation,
        automation_executor=AutomationExecutor(automation),
        discovery=CodexDiscoveryService(manager),
        kimi_discovery=KimiDiscoveryService(manager),
        kimi_desktop_discovery=KimiDesktopDiscoveryService(manager),
        codex_quota_service=codex_quota_factory(),
        quota_service=quota_service,
        kimi_web_quota_service=kimi_web_quota_service,
    )


class _UvicornLogBridge(logging.Handler):
    """Route Uvicorn warnings/errors through AACC's redacting file handler."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _logger.log(
                record.levelno,
                "API server: %s",
                record.getMessage(),
                exc_info=record.exc_info,
            )
        except Exception:  # noqa: BLE001 - logging must never crash the GUI
            return


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
                log_config=None,
            )
        )
        self._uvicorn_logger = logging.getLogger("uvicorn.error")
        self._previous_uvicorn_propagate = self._uvicorn_logger.propagate
        self._log_bridge = _UvicornLogBridge(level=logging.WARNING)
        self._uvicorn_logger.addHandler(self._log_bridge)
        self._uvicorn_logger.propagate = False
        self.thread = threading.Thread(target=self.server.run, name="aacc-api", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        try:
            self.server.should_exit = True
            if self.thread.is_alive():
                self.thread.join(timeout=3)
        finally:
            self._uvicorn_logger.removeHandler(self._log_bridge)
            self._uvicorn_logger.propagate = self._previous_uvicorn_propagate


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


def _create_qapplication() -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setApplicationName("AACC")
    app.setOrganizationName("AACC")
    app.setQuitOnLastWindowClosed(False)
    return app


def _show_startup_security_error(data_dir: Path, error: FileProtectionError) -> int:
    category = type(error).__name__
    _logger.critical("Startup credential protection failed: %s", category)
    QMessageBox.critical(
        None,
        "AACC 启动失败 / Startup failed",
        "AACC 无法保护本机凭据文件，因此没有保存新的凭据。\n"
        "AACC could not protect its local credential file, so no new "
        "credential was saved.\n\n"
        f"日志 / Log: {data_dir / 'logs' / 'app.log'}\n"
        f"诊断 / Diagnostic: STARTUP-ACL-{category}",
    )
    return 1


def _show_startup_shutdown_error(data_dir: Path, error: BaseException) -> int:
    category = type(error).__name__
    _logger.critical("Startup shutdown-listener failed: %s", category)
    QMessageBox.critical(
        None,
        "AACC 启动失败 / Startup failed",
        "AACC 无法安装 Windows 更新退出监听器，已安全停止启动。\n"
        "AACC could not install its Windows update shutdown listener and "
        "stopped safely.\n\n"
        f"日志 / Log: {data_dir / 'logs' / 'app.log'}\n"
        f"诊断 / Diagnostic: STARTUP-SHUTDOWN-{category}",
    )
    return 1


def _run_application(config_path: Path, database_path: Path, data_dir: Path) -> int:
    configure_logging(data_dir / "logs")
    try:
        initialize_native_webview(data_dir)
    except FileProtectionError as error:
        _create_qapplication()
        return _show_startup_security_error(data_dir, error)
    qt_app = _create_qapplication()
    settings = QSettings()
    language_manager = LanguageManager(load_language(settings), settings)
    trusted = is_accessibility_trusted()
    try:
        runtime = build_runtime(
            config_path,
            database_path,
            accessibility_trusted=is_accessibility_trusted,
            language_manager=language_manager,
        )
    except FileProtectionError as error:
        return _show_startup_security_error(data_dir, error)
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
        quota_service=runtime.quota_service,
        kimi_web_quota_service=runtime.kimi_web_quota_service,
        codex_quota_service=runtime.codex_quota_service,
        discovery_health=runtime.discovery.health,
        subscribe_discovery_health=runtime.discovery.subscribe_health,
        kimi_discovery_health=runtime.kimi_discovery.health,
        subscribe_kimi_discovery_health=runtime.kimi_discovery.subscribe_health,
        kimi_desktop_discovery_health=runtime.kimi_desktop_discovery.health,
        subscribe_kimi_desktop_discovery_health=runtime.kimi_desktop_discovery.subscribe_health,
        discovery_log_path=str(data_dir / "logs" / "app.log"),
        accessibility_trusted=trusted,
        open_accessibility_settings_callback=open_accessibility_settings,
        settings=settings,
        language_manager=language_manager,
    )
    shutdown_listener: WindowsShutdownListener | None = None
    if sys.platform == "win32":
        shutdown_listener = WindowsShutdownListener()
        try:
            shutdown_listener.start(qt_app, window)
        except Exception as error:  # noqa: BLE001 - listener startup must fail closed
            try:
                shutdown_listener.stop()
            except Exception:  # noqa: BLE001 - runtime/SQLite still must close
                _logger.error("Application cleanup failed stage=shutdown-listener")
            try:
                runtime.close()
            except Exception:  # noqa: BLE001 - preserve the sanitized startup error
                _logger.error("Application cleanup failed stage=runtime")
            return _show_startup_shutdown_error(data_dir, error)
    window.show()
    api_server: APIServerThread | None = None
    hotkeys: HotkeyDriver | None = None
    accessibility_timer: QTimer | None = None
    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        operations: tuple[tuple[str, Callable[[], None]], ...] = (
            (
                "shutdown-listener",
                shutdown_listener.stop if shutdown_listener is not None else lambda: None,
            ),
            ("hotkeys", hotkeys.stop if hotkeys is not None else lambda: None),
            ("api-server", api_server.stop if api_server is not None else lambda: None),
            ("runtime", runtime.close),
        )
        for stage, operation in operations:
            _logger.info("Application cleanup starting stage=%s", stage)
            try:
                operation()
            except Exception:  # noqa: BLE001 - all remaining cleanup must run
                _logger.error("Application cleanup failed stage=%s", stage)
            else:
                _logger.info("Application cleanup completed stage=%s", stage)

    def start_kimi_web_quota() -> None:
        if cleaned or runtime.kimi_web_quota_service is None:
            return
        kimi_web_quota_service = runtime.kimi_web_quota_service

        def stop_after_shutdown() -> None:
            try:
                kimi_web_quota_service.stop()
            except Exception:  # noqa: BLE001 - shutdown must keep unwinding
                _logger.error("Application post-shutdown cleanup failed stage=kimi-web-quota")

        _logger.info("Application startup beginning stage=kimi-web-quota")
        try:
            kimi_web_quota_service.start()
        except Exception:  # noqa: BLE001 - optional web quota must not block the app
            if cleaned:
                stop_after_shutdown()
                return
            _logger.error("Application startup failed stage=kimi-web-quota")
            try:
                kimi_web_quota_service.stop()
            except Exception:  # noqa: BLE001 - app startup must still continue
                _logger.error("Application startup rollback failed stage=kimi-web-quota")
        else:
            if cleaned:
                stop_after_shutdown()
                return
            _logger.info("Application startup completed stage=kimi-web-quota")

    def show_accessibility_guidance() -> None:
        if not cleaned:
            window.show_accessibility_guidance()

    def start_runtime_components() -> None:
        nonlocal accessibility_timer, api_server, hotkeys
        if cleaned:
            return
        _logger.info("Application event-loop startup beginning")
        try:
            runtime.discovery.start()
            if cleaned:
                return
            runtime.kimi_discovery.start()
            if cleaned:
                return
            runtime.kimi_desktop_discovery.start()
            if cleaned:
                return
            if runtime.quota_service is not None:
                runtime.quota_service.start()
                if cleaned:
                    return
            if runtime.codex_quota_service is not None:
                runtime.codex_quota_service.start()
                if cleaned:
                    return

            if runtime.config.app.api.enabled:
                api_server = APIServerThread(runtime)
                api_server.start()
                if cleaned:
                    return

            if sys.platform == "win32":
                from aacc.hotkeys_windows import WindowsGlobalHotkeys

                hotkeys = WindowsGlobalHotkeys(
                    runtime.config.hotkeys,
                    _hotkey_actions(window),  # type: ignore[arg-type]
                    hwnd=int(window.winId()),
                )
            else:
                hotkeys = GlobalHotkeys(
                    runtime.config.hotkeys,
                    _hotkey_actions(window),  # type: ignore[arg-type]
                )
            hotkey_sync = AccessibilityHotkeySync(hotkeys)
            hotkey_sync.sync(trusted)
            if cleaned:
                return

            def refresh_accessibility() -> None:
                trusted_now = is_accessibility_trusted()
                window.accessibility_trusted = trusted_now
                hotkey_sync.sync(trusted_now)

            accessibility_timer = QTimer(qt_app)
            accessibility_timer.setInterval(5000)
            accessibility_timer.timeout.connect(refresh_accessibility)
            accessibility_timer.start()
        except Exception:  # noqa: BLE001 - async startup must exit instead of hanging
            if cleaned:
                return
            _logger.exception("Application event-loop startup failed")
            cleanup()
            qt_app.exit(1)
            return
        _logger.info("Application event-loop startup completed")
        if runtime.kimi_web_quota_service is not None:
            QTimer.singleShot(0, start_kimi_web_quota)
        if not trusted:
            QTimer.singleShot(0, show_accessibility_guidance)

    qt_app.aboutToQuit.connect(cleanup)
    try:
        _logger.info("Application entering Qt event loop")
        # Keep this as the final operation before exec(). Scheduling it earlier allows
        # a nested native/Qt event pump to start services without a top-level event loop.
        QTimer.singleShot(0, start_runtime_components)
        return qt_app.exec()
    finally:
        cleanup()


def main() -> int:
    if sys.platform == "win32" and sys.argv[1:] == ["--shutdown-for-update"]:
        return request_shutdown_for_update()
    if sys.platform == "win32" and sys.argv[1:] == ["--smoke-native-webview"]:
        return run_native_webview_smoke()
    config_path = Path(os.environ.get("AACC_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    database_path = resolve_database_path()
    data_dir = config_path.parent if config_path != DEFAULT_CONFIG_PATH else APP_SUPPORT_DIR
    guard = InstanceGuard(data_dir / "aacc.lock")
    if not guard.acquire():
        activate_existing_instance()
        return 0
    try:
        return _run_application(config_path, database_path, data_dir)
    finally:
        guard.close()
