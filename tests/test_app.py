import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import aacc.app as app_module
from aacc.adapter_discovery_service import AdapterDiscoveryService
from aacc.app import build_runtime
from aacc.discovery_service import (
    CodexDiscoveryService,
    KimiDesktopDiscoveryService,
    KimiDiscoveryService,
)
from aacc.file_security import FileProtectionError
from aacc.i18n import EN_US, LanguageManager


class FakeApplication:
    pass


def _runtime_for_application_test(events: list[str]) -> SimpleNamespace:
    class Service:
        catalog: dict[str, object] = {}
        auto_active_ids: set[str] = set()
        retained_ids: set[str] = set()
        muted_ids: set[str] = set()
        health = None

        def set_monitoring_preferences(self, *_args: object) -> None:
            pass

        def subscribe_health(self, *_args: object) -> None:
            pass

        def start(self) -> None:
            events.append("service-start")

    return SimpleNamespace(
        manager=SimpleNamespace(),
        automation_executor=SimpleNamespace(),
        discovery=Service(),
        kimi_discovery=Service(),
        kimi_desktop_discovery=Service(),
        opencode_discovery=Service(),
        quota_service=None,
        kimi_web_quota_service=None,
        codex_quota_service=None,
        opencode_web_quota_service=None,
        qwen_web_quota_service=None,
        config=SimpleNamespace(
            tasks=[],
            hotkeys={},
            app=SimpleNamespace(api=SimpleNamespace(enabled=False)),
        ),
        config_path=Path("config.yaml"),
        close=lambda: events.append("runtime-close"),
    )


def _patch_application_shell(
    monkeypatch: object,
    events: list[str],
    runtime: object,
    *,
    trusted: bool = True,
) -> None:
    scheduled: list[object] = []

    class Signal:
        def __init__(self) -> None:
            self.callbacks: list[object] = []

        def connect(self, callback) -> None:
            events.append("cleanup-connected")
            self.callbacks.append(callback)

        def emit(self) -> None:
            for callback in self.callbacks:
                callback()  # type: ignore[operator]

    class Application:
        aboutToQuit = Signal()

        def __init__(self) -> None:
            self.exit_code = 0
            runtime.qt_app = self  # type: ignore[attr-defined]

        def exit(self, code: int) -> None:
            self.exit_code = code
            self.aboutToQuit.emit()

        def exec(self) -> int:
            events.append("exec")
            for callback in scheduled:
                callback()  # type: ignore[operator]
            return self.exit_code

    class Timer:
        def __init__(self, *_args: object) -> None:
            self.timeout = Signal()

        def setInterval(self, _interval: int) -> None:  # noqa: N802
            pass

        def start(self) -> None:
            pass

        @staticmethod
        def singleShot(_delay: int, callback) -> None:  # noqa: N802
            scheduled.append(callback)

    class Window:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.config = runtime.config  # type: ignore[attr-defined]
            self.selected_task_id = None
            self.external_action = SimpleNamespace(emit=lambda *_args: None)
            runtime.window_language_manager = _kwargs.get(  # type: ignore[attr-defined]
                "language_manager"
            )

        def winId(self) -> int:  # noqa: N802
            return 88

        def show(self) -> None:
            events.append("window-show")

        def show_accessibility_guidance(self) -> None:
            events.append("guidance-show")

    monkeypatch.setattr(app_module, "configure_logging", lambda *_args: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "initialize_native_webview", lambda _data_dir: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "_create_qapplication", Application)  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "is_accessibility_trusted", lambda: trusted)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "build_runtime",
        lambda *_args, **kwargs: (
            setattr(runtime, "runtime_language_manager", kwargs.get("language_manager")) or runtime
        ),
    )
    monkeypatch.setattr(app_module, "MainWindow", Window)  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "QTimer", Timer)  # type: ignore[attr-defined]


def test_run_application_assembles_one_language_manager_for_runtime_and_window(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)
    settings = object()
    created: list[tuple[str, object]] = []
    _patch_application_shell(monkeypatch, events, runtime)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "QSettings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "load_language",
        lambda received: EN_US if received is settings else None,
    )

    class TrackingLanguageManager(LanguageManager):
        def __init__(self, language, received_settings=None):
            super().__init__(language)
            created.append((language, received_settings))

    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "LanguageManager",
        TrackingLanguageManager,
    )

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert created == [(EN_US, settings)]
    assert runtime.runtime_language_manager is runtime.window_language_manager


def test_windows_opencode_factory_keeps_configured_quota_service(
    monkeypatch, tmp_path: Path
) -> None:
    config = app_module.load_config(tmp_path / "missing.yaml")
    config.opencode_workspace_url = "https://opencode.ai/workspace/wrk_1/go"
    monkeypatch.setattr(app_module.sys, "platform", "win32")

    service = app_module._default_opencode_web_quota_service_factory(tmp_path, config)

    assert service is not None
    assert service.workspace_url == config.opencode_workspace_url
    service.stop()


def test_windows_listener_is_installed_before_hotkeys_and_survives_hotkey_failure(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)
    runtime.kimi_web_quota_service = SimpleNamespace(start=lambda: events.append("kimi-web-start"))
    _patch_application_shell(monkeypatch, events, runtime)

    class Listener:
        def start(self, _app, _window) -> None:
            events.append("listener-start")

        def stop(self) -> None:
            events.append("listener-stop")

    class FailingHotkeys:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("hotkeys-created")

        def start(self) -> bool:
            events.append("hotkeys-failed")
            return False

        def stop(self) -> None:
            events.append("hotkeys-stop")

    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "WindowsShutdownListener", Listener)  # type: ignore[attr-defined]
    monkeypatch.setitem(  # type: ignore[attr-defined]
        sys.modules,
        "aacc.hotkeys_windows",
        SimpleNamespace(WindowsGlobalHotkeys=FailingHotkeys),
    )

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("listener-start") < events.index("window-show")
    assert events.index("listener-start") < events.index("hotkeys-created")
    assert events.index("exec") < events.index("service-start")
    assert events.index("exec") < events.index("kimi-web-start")
    assert events.index("exec") < events.index("hotkeys-created")
    assert "hotkeys-failed" in events
    assert events.index("listener-stop") < events.index("hotkeys-stop")
    assert events.index("hotkeys-stop") < events.index("runtime-close")


def test_untrusted_guidance_is_shown_after_core_services_start(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)
    _patch_application_shell(monkeypatch, events, runtime, trusted=False)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("exec") < events.index("service-start")
    assert events.index("service-start") < events.index("guidance-show")


def test_configured_adapter_starts_with_core_discovery_services(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    def start_adapter() -> None:
        events.append("adapter-start")
        runtime.qt_app.exit(0)

    runtime.adapter_discovery = SimpleNamespace(start=start_adapter)
    _patch_application_shell(monkeypatch, events, runtime)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("service-start") < events.index("adapter-start")


def test_web_start_shutdown_does_not_show_later_guidance_or_restart_components(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    class QuittingWebService:
        running = False

        def start(self) -> None:
            events.append("kimi-web-start")
            self.running = True
            runtime.qt_app.exit(0)
            events.append("kimi-web-return")
            self.running = True

        def stop(self) -> None:
            events.append("kimi-web-stop")
            self.running = False

    class Hotkeys:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("hotkeys-created")

        def start(self) -> bool:
            events.append("hotkeys-start")
            return True

        def stop(self) -> None:
            events.append("hotkeys-stop")

    web_service = QuittingWebService()
    runtime.kimi_web_quota_service = web_service
    runtime.close = lambda: (web_service.stop(), events.append("runtime-close"))
    runtime.codex_quota_service = SimpleNamespace(
        start=lambda: events.append("codex-quota-start"),
        stop=lambda: events.append("codex-quota-stop"),
    )
    _patch_application_shell(monkeypatch, events, runtime, trusted=False)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "GlobalHotkeys", Hotkeys)  # type: ignore[attr-defined]

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("hotkeys-created") < events.index("kimi-web-start")
    assert events.index("codex-quota-start") < events.index("kimi-web-start")
    assert events.index("kimi-web-start") < events.index("runtime-close")
    assert events.count("kimi-web-stop") == 2
    assert not web_service.running
    assert "guidance-show" not in events


def test_deferred_kimi_web_start_failure_stops_partial_service(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    class FailingWebService:
        def start(self) -> None:
            events.append("kimi-web-start")
            raise RuntimeError("partial web start")

        def stop(self) -> None:
            events.append("kimi-web-stop")

    runtime.kimi_web_quota_service = FailingWebService()
    _patch_application_shell(monkeypatch, events, runtime)

    class Listener:
        def start(self, _app, _window) -> None:
            pass

        def stop(self) -> None:
            pass

    class Hotkeys:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> bool:
            return True

        def stop(self) -> None:
            pass

    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "WindowsShutdownListener", Listener)  # type: ignore[attr-defined]
    monkeypatch.setitem(  # type: ignore[attr-defined]
        sys.modules,
        "aacc.hotkeys_windows",
        SimpleNamespace(WindowsGlobalHotkeys=Hotkeys),
    )

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("exec") < events.index("kimi-web-start")
    assert events.count("kimi-web-stop") == 1


def test_deferred_opencode_web_start_runs_after_event_loop(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)
    runtime.opencode_web_quota_service = SimpleNamespace(
        start=lambda: events.append("opencode-web-start"),
        workspace_url="https://opencode.ai/workspace/wrk_1/go",
    )
    _patch_application_shell(monkeypatch, events, runtime)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("exec") < events.index("opencode-web-start")
    assert events.count("opencode-web-start") == 1


def test_deferred_opencode_web_start_skips_empty_workspace_url(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)
    runtime.opencode_web_quota_service = SimpleNamespace(
        start=lambda: events.append("opencode-web-start"),
        workspace_url="",
    )
    _patch_application_shell(monkeypatch, events, runtime)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert "opencode-web-start" not in events


def test_opencode_web_start_shutdown_stops_after_cleanup(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    class QuittingWebService:
        def start(self) -> None:
            events.append("opencode-web-start")
            runtime.qt_app.exit(0)
            events.append("opencode-web-return")

        def stop(self) -> None:
            events.append("opencode-web-stop")
            raise RuntimeError("private opencode stop failure")

    web_service = QuittingWebService()
    runtime.opencode_web_quota_service = SimpleNamespace(
        start=web_service.start,
        stop=web_service.stop,
        workspace_url="https://opencode.ai/workspace/wrk_1/go",
    )
    runtime.close = lambda: web_service.stop()
    _patch_application_shell(monkeypatch, events, runtime, trusted=False)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("opencode-web-start") < events.index("opencode-web-return")
    assert events.count("opencode-web-stop") == 2
    assert "guidance-show" not in events


def test_opencode_web_start_skipped_after_shutdown(tmp_path: Path, monkeypatch: object) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    class QuittingWebService:
        def start(self) -> None:
            events.append("kimi-web-start")
            runtime.qt_app.exit(0)
            events.append("kimi-web-return")

        def stop(self) -> None:
            events.append("kimi-web-stop")

    runtime.kimi_web_quota_service = QuittingWebService()
    runtime.opencode_web_quota_service = SimpleNamespace(
        start=lambda: events.append("opencode-web-start"),
        stop=lambda: events.append("opencode-web-stop"),
        workspace_url="https://opencode.ai/workspace/wrk_1/go",
    )
    runtime.close = lambda: (runtime.kimi_web_quota_service.stop(), events.append("runtime-close"))
    _patch_application_shell(monkeypatch, events, runtime, trusted=False)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert "kimi-web-start" in events
    assert "opencode-web-start" not in events


def test_deferred_opencode_web_start_failure_stops_partial_service(
    tmp_path: Path, monkeypatch: object
) -> None:
    # This test exercises deferred Qt startup, not the native Windows update
    # listener. Keep the shell deterministic when the suite runs on Windows.
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    class FailingWebService:
        def start(self) -> None:
            events.append("opencode-web-start")
            raise RuntimeError("partial opencode web start")

        def stop(self) -> None:
            events.append("opencode-web-stop")
            raise RuntimeError("private opencode rollback failure")

    web_service = FailingWebService()
    runtime.opencode_web_quota_service = SimpleNamespace(
        start=web_service.start,
        stop=web_service.stop,
        workspace_url="https://opencode.ai/workspace/wrk_1/go",
    )
    _patch_application_shell(monkeypatch, events, runtime)

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("exec") < events.index("opencode-web-start")
    assert events.count("opencode-web-stop") == 1


def test_opencode_web_start_failure_after_shutdown_stops_partial_service(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    class QuittingFailingWebService:
        def start(self) -> None:
            events.append("opencode-web-start")
            runtime.qt_app.exit(0)
            raise RuntimeError("opencode web start failed during shutdown")

        def stop(self) -> None:
            events.append("opencode-web-stop")

    web_service = QuittingFailingWebService()
    runtime.opencode_web_quota_service = SimpleNamespace(
        start=web_service.start,
        stop=web_service.stop,
        workspace_url="https://opencode.ai/workspace/wrk_1/go",
    )
    runtime.close = lambda: web_service.stop()
    _patch_application_shell(monkeypatch, events, runtime, trusted=False)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.count("opencode-web-stop") == 2
    assert "guidance-show" not in events


def test_event_loop_startup_failure_cleans_up_and_exits_nonzero(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    def fail_discovery_start() -> None:
        events.append("service-failed")
        raise RuntimeError("startup failed")

    runtime.discovery.start = fail_discovery_start
    _patch_application_shell(monkeypatch, events, runtime)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "GlobalHotkeys",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("hotkeys must not start after discovery failure")
        ),
    )

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 1
    )
    assert events.index("exec") < events.index("service-failed")
    assert events.count("runtime-close") == 1


def test_api_server_does_not_configure_console_logging(
    monkeypatch: object,
) -> None:
    captured: dict[str, object] = {}
    forwarded: list[tuple[int, str, tuple[object, ...]]] = []
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            app=SimpleNamespace(api=SimpleNamespace(host="127.0.0.1", port=8787))
        ),
        manager=SimpleNamespace(),
        automation_executor=SimpleNamespace(),
    )
    monkeypatch.setattr(app_module, "create_api", lambda *_args: object())  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.uvicorn,
        "Config",
        lambda *_args, **kwargs: captured.update(kwargs) or object(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.uvicorn,
        "Server",
        lambda _config: SimpleNamespace(run=lambda: None, should_exit=False),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module._logger,
        "log",
        lambda level, message, *args, **_kwargs: forwarded.append((level, message, args)),
    )

    server = app_module.APIServerThread(runtime)

    assert captured["log_config"] is None
    logging.getLogger("uvicorn.error").error("bind failed: %s", "smoke")
    assert forwarded == [(logging.ERROR, "API server: %s", ("bind failed: smoke",))]

    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module._logger,
        "log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("logging unavailable")),
    )
    logging.getLogger("uvicorn.error").error("bridge must not crash the GUI")

    server.stop()
    logging.getLogger("uvicorn.error").error("after stop")
    assert len(forwarded) == 1


def test_api_server_rejects_non_loopback_host_before_uvicorn(
    monkeypatch: object,
) -> None:
    runtime = SimpleNamespace(
        config=SimpleNamespace(app=SimpleNamespace(api=SimpleNamespace(host="0.0.0.0", port=8787))),
        manager=SimpleNamespace(),
        automation_executor=SimpleNamespace(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "create_api",
        lambda *_args: pytest.fail("API application must not be created for a non-loopback host"),
    )

    with pytest.raises(RuntimeError, match="must bind to loopback"):
        app_module.APIServerThread(runtime)


def test_windows_listener_registration_failure_is_visible_sanitized_and_closes_runtime(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    shown: list[str] = []
    runtime = _runtime_for_application_test(events)
    _patch_application_shell(monkeypatch, events, runtime)

    class FailingListener:
        def start(self, _app, _window) -> None:
            raise ValueError(r"secret=C:\private")

        def stop(self) -> None:
            events.append("listener-stop")
            raise RuntimeError("private stop failure")

    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "WindowsShutdownListener", FailingListener)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, text: shown.append(text),
    )

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 1
    )
    assert events == ["listener-stop", "runtime-close"]
    assert shown and "secret" not in shown[0] and "STARTUP-SHUTDOWN-ValueError" in shown[0]


def test_windows_shutdown_control_command_runs_before_paths_or_guard(
    monkeypatch: object,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module.sys, "argv", ["AACC.exe", "--shutdown-for-update"])  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "request_shutdown_for_update",
        lambda: calls.append("shutdown") or 9,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "resolve_database_path",
        lambda: (_ for _ in ()).throw(AssertionError("paths must not resolve")),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "InstanceGuard",
        lambda *_args: (_ for _ in ()).throw(AssertionError("guard must not start")),
    )

    assert app_module.main() == 9
    assert calls == ["shutdown"]


def test_windows_shutdown_control_command_requires_exact_arguments(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.sys,
        "argv",
        ["AACC.exe", "--shutdown-for-update", "extra"],
    )
    monkeypatch.setenv("AACC_CONFIG_PATH", str(tmp_path / "config.yaml"))  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "InstanceGuard",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("normal startup reached")),
    )

    with pytest.raises(RuntimeError, match="normal startup reached"):
        app_module.main()


def test_windows_edge_cdp_smoke_runs_before_paths_or_guard(
    tmp_path: Path, monkeypatch: object
) -> None:
    calls: list[tuple[Path, Path]] = []
    result_path = tmp_path / "result.txt"
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.sys,
        "argv",
        ["AACC.exe", "--smoke-edge-cdp"],
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))  # type: ignore[attr-defined]
    monkeypatch.setenv("AACC_EDGE_CDP_SMOKE_RESULT_PATH", str(result_path))  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "run_edge_cdp_smoke",
        lambda data_dir, result: calls.append((data_dir, result)) or 7,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "resolve_database_path",
        lambda: (_ for _ in ()).throw(AssertionError("paths must not resolve")),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "InstanceGuard",
        lambda *_args: (_ for _ in ()).throw(AssertionError("guard must not start")),
    )

    assert app_module.main() == 7
    assert calls == [(local_app_data, result_path)]


def test_windows_edge_cdp_smoke_requires_exact_arguments(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.sys,
        "argv",
        ["AACC.exe", "--smoke-edge-cdp", "extra"],
    )
    monkeypatch.setenv("AACC_CONFIG_PATH", str(tmp_path / "config.yaml"))  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "InstanceGuard",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("normal startup reached")),
    )

    with pytest.raises(RuntimeError, match="normal startup reached"):
        app_module.main()


def test_removed_windows_native_webview_smoke_flag_reaches_normal_startup(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.sys,
        "argv",
        ["AACC.exe", "--smoke-native-webview"],
    )
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("AACC_CONFIG_PATH", str(config_path))  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "InstanceGuard",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("normal startup reached")),
    )

    with pytest.raises(RuntimeError, match="normal startup reached"):
        app_module.main()


def test_windows_native_webview_smoke_requires_exact_arguments(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.sys,
        "argv",
        ["AACC.exe", "--smoke-native-webview", "extra"],
    )
    monkeypatch.setenv("AACC_CONFIG_PATH", str(tmp_path / "config.yaml"))  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "InstanceGuard",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("normal startup reached")),
    )

    with pytest.raises(RuntimeError, match="normal startup reached"):
        app_module.main()


def test_security_failure_shows_sanitized_dialog_and_returns_nonzero(
    tmp_path: Path, monkeypatch: object
) -> None:
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(app_module, "initialize_native_webview", lambda _data_dir: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "_create_qapplication", FakeApplication)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.QMessageBox,
        "critical",
        lambda _parent, title, text: shown.append((title, text)),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "build_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileProtectionError(r"token=C:\\secret")),
    )

    result = app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path)

    assert result == 1
    assert shown
    assert "token" not in shown[0][1]
    assert str(tmp_path / "logs" / "app.log") in shown[0][1]


def test_webview_user_data_protection_failure_stops_before_runtime(
    tmp_path: Path, monkeypatch: object
) -> None:
    shown: list[tuple[str, str]] = []
    events: list[str] = []
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "configure_logging", lambda *_args: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "initialize_native_webview",
        lambda _data_dir: (_ for _ in ()).throw(FileProtectionError(r"token=C:\\secret")),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "_create_qapplication",
        lambda: events.append("qapplication"),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module.QMessageBox,
        "critical",
        lambda _parent, title, text: shown.append((title, text)),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "build_runtime",
        lambda *_args, **_kwargs: events.append("runtime"),
    )

    result = app_module._run_application(
        tmp_path / "config.yaml",
        tmp_path / "aacc.db",
        tmp_path,
    )

    assert result == 1
    assert events == ["qapplication"]
    assert shown
    assert "token" not in shown[0][1]
    assert "STARTUP-ACL-FileProtectionError" in shown[0][1]


def test_windows_web_quota_backend_does_not_initialize_native_webview(
    tmp_path: Path, monkeypatch: object
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(app_module.sys, "platform", "win32")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "initialize_native_webview",
        calls.append,
    )

    app_module.initialize_web_quota_backend(tmp_path)

    assert calls == []


def test_macos_web_quota_backend_keeps_native_initialization(
    tmp_path: Path, monkeypatch: object
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "initialize_native_webview",
        calls.append,
    )

    app_module.initialize_web_quota_backend(tmp_path)

    assert calls == [tmp_path]


def test_build_runtime_creates_default_config_database_and_four_tasks(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    runtime = build_runtime(config_path, database_path)
    assert config_path.exists()
    assert database_path.exists()
    assert len(runtime.manager.list()) == 4
    assert runtime.automation.config is runtime.config
    assert isinstance(runtime.discovery, CodexDiscoveryService)
    assert isinstance(runtime.kimi_discovery, KimiDiscoveryService)
    assert isinstance(runtime.adapter_discovery, AdapterDiscoveryService)
    runtime.close()


def test_runtime_includes_kimi_desktop_discovery(tmp_path: Path) -> None:
    runtime = build_runtime(tmp_path / "config.yaml", tmp_path / "aacc.db")
    assert isinstance(runtime.kimi_desktop_discovery, KimiDesktopDiscoveryService)
    runtime.close()


def test_runtime_close_reaches_manager_after_earlier_stop_failure(caplog) -> None:
    calls: list[str] = []

    class Component:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def stop(self) -> None:
            calls.append(self.name)
            if self.fail:
                raise RuntimeError("private details")

        def close(self) -> None:
            self.stop()

    runtime = app_module.Runtime(
        config_path=Path("config.yaml"),
        config=SimpleNamespace(),  # type: ignore[arg-type]
        manager=Component("manager"),  # type: ignore[arg-type]
        automation=SimpleNamespace(),  # type: ignore[arg-type]
        automation_executor=Component("executor"),  # type: ignore[arg-type]
        discovery=Component("codex", fail=True),  # type: ignore[arg-type]
        kimi_discovery=Component("kimi"),  # type: ignore[arg-type]
        kimi_desktop_discovery=Component("desktop"),  # type: ignore[arg-type]
        opencode_discovery=Component("opencode"),  # type: ignore[arg-type]
        codex_quota_service=Component("codex-quota", fail=True),  # type: ignore[arg-type]
        quota_service=Component("kimi-quota"),  # type: ignore[arg-type]
        kimi_web_quota_service=Component("web-quota"),  # type: ignore[arg-type]
    )

    runtime.close()

    assert calls == [
        "codex-quota",
        "kimi-quota",
        "web-quota",
        "opencode",
        "desktop",
        "kimi",
        "codex",
        "executor",
        "manager",
    ]
    assert "private details" not in caplog.text
    assert "Runtime cleanup failed stage=codex-quota" in caplog.text
    assert "Runtime cleanup failed stage=discovery" in caplog.text


def test_second_launch_activates_existing_instance_without_runtime(
    tmp_path: Path, monkeypatch: object
) -> None:
    activated: list[bool] = []

    class BusyGuard:
        def __init__(self, _path: Path) -> None:
            pass

        def acquire(self) -> bool:
            return False

        def close(self) -> None:
            raise AssertionError("unacquired guard must not close")

    monkeypatch.setenv("AACC_CONFIG_PATH", str(tmp_path / "config.yaml"))  # type: ignore[attr-defined]
    monkeypatch.setenv("AACC_DATABASE_PATH", str(tmp_path / "aacc.db"))  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "InstanceGuard", BusyGuard)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module, "activate_existing_instance", lambda: activated.append(True)
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "_run_application",
        lambda *_args: (_ for _ in ()).throw(AssertionError("runtime should not start")),
    )

    assert app_module.main() == 0
    assert activated == [True]


def test_primary_launch_runs_application_and_closes_guard(
    tmp_path: Path, monkeypatch: object
) -> None:
    closed: list[bool] = []
    received: list[tuple[Path, Path, Path]] = []

    class AcquiredGuard:
        def __init__(self, _path: Path) -> None:
            pass

        def acquire(self) -> bool:
            return True

        def close(self) -> None:
            closed.append(True)

    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    monkeypatch.setenv("AACC_CONFIG_PATH", str(config_path))  # type: ignore[attr-defined]
    monkeypatch.setenv("AACC_DATABASE_PATH", str(database_path))  # type: ignore[attr-defined]
    monkeypatch.setattr(app_module, "InstanceGuard", AcquiredGuard)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "_run_application",
        lambda config, database, data: received.append((config, database, data)) or 7,
    )

    assert app_module.main() == 7
    assert received == [(config_path, database_path, tmp_path)]
    assert closed == [True]


def test_build_runtime_creates_quota_service_when_enabled(tmp_path: Path) -> None:
    import httpx

    from aacc.quota_service import QuotaService

    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    runtime = build_runtime(
        config_path,
        database_path,
        quota_service_factory=lambda config_dir: QuotaService(
            config_dir,
            version="test",
            client_factory=lambda: httpx.Client(transport=transport),
        ),
    )
    try:
        assert runtime.quota_service is not None
    finally:
        runtime.close()


def test_build_runtime_passes_language_manager_to_default_web_service(
    tmp_path: Path, monkeypatch: object
) -> None:
    language_manager = LanguageManager(EN_US)
    received: list[LanguageManager] = []

    def create_web_service(
        _config_dir: Path,
        _config: object,
        manager: LanguageManager | None,
    ) -> object:
        assert manager is not None
        received.append(manager)
        return SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        app_module,
        "_default_kimi_web_quota_service_factory",
        create_web_service,
    )
    runtime = build_runtime(
        tmp_path / "config.yaml",
        tmp_path / "aacc.db",
        language_manager=language_manager,
        quota_service_factory=lambda _dir: None,
        codex_quota_service_factory=lambda: None,
    )
    try:
        assert received == [language_manager]
    finally:
        runtime.close()


def test_build_runtime_links_web_cycle_to_kimi_code_fallback(tmp_path: Path) -> None:
    class FakeCodeQuotaService:
        def __init__(self) -> None:
            self.external_values: list[bool] = []
            self.refreshes = 0

        def set_externally_scheduled(self, enabled: bool) -> None:
            self.external_values.append(enabled)

        def refresh_now(self) -> None:
            self.refreshes += 1

        def stop(self) -> None:
            pass

    class FakeWebQuotaService:
        def __init__(self) -> None:
            self.fallback = None

        def set_fallback_refresh(self, callback) -> None:
            self.fallback = callback

        def stop(self) -> None:
            pass

    code = FakeCodeQuotaService()
    web = FakeWebQuotaService()
    runtime = build_runtime(
        tmp_path / "config.yaml",
        tmp_path / "aacc.db",
        quota_service_factory=lambda _dir: code,  # type: ignore[arg-type,return-value]
        kimi_web_quota_service_factory=lambda _dir: web,  # type: ignore[arg-type,return-value]
    )
    try:
        assert code.external_values == [True]
        assert web.fallback is not None
        web.fallback()
        assert code.refreshes == 1
    finally:
        runtime.close()


def test_build_runtime_leaves_code_polling_independent_without_web(tmp_path: Path) -> None:
    class FakeCodeQuotaService:
        def __init__(self) -> None:
            self.external_values: list[bool] = []

        def set_externally_scheduled(self, enabled: bool) -> None:
            self.external_values.append(enabled)

        def stop(self) -> None:
            pass

    code = FakeCodeQuotaService()
    runtime = build_runtime(
        tmp_path / "config.yaml",
        tmp_path / "aacc.db",
        quota_service_factory=lambda _dir: code,  # type: ignore[arg-type,return-value]
        kimi_web_quota_service_factory=lambda _dir: None,
    )
    try:
        assert code.external_values == []
    finally:
        runtime.close()


def test_build_runtime_skips_quota_service_when_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    runtime = build_runtime(config_path, database_path, quota_service_factory=lambda _dir: None)
    try:
        assert runtime.quota_service is None
    finally:
        runtime.close()


def test_build_runtime_default_factory_honors_kimi_quota_enabled(tmp_path: Path) -> None:
    import yaml

    from aacc.kimi_web_quota_service import KimiWebQuotaService
    from aacc.quota_service import QuotaService

    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"

    # Default config has kimi_quota_enabled=True, so the service is created.
    runtime = build_runtime(config_path, database_path)
    try:
        assert isinstance(runtime.quota_service, QuotaService)
        assert isinstance(runtime.kimi_web_quota_service, KimiWebQuotaService)
    finally:
        runtime.close()

    config_path.write_text(
        yaml.safe_dump({"app": {"kimi_quota_enabled": False}}),
        encoding="utf-8",
    )
    runtime = build_runtime(config_path, tmp_path / "aacc-disabled.db")
    try:
        assert runtime.quota_service is None
        assert runtime.kimi_web_quota_service is None
    finally:
        runtime.close()


def test_build_runtime_default_factory_honors_codex_quota_enabled(
    tmp_path: Path,
) -> None:
    import yaml

    from aacc.codex_quota_service import CodexQuotaService

    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    runtime = build_runtime(config_path, database_path)
    try:
        assert isinstance(runtime.codex_quota_service, CodexQuotaService)
    finally:
        runtime.close()

    config_path.write_text(
        yaml.safe_dump({"app": {"codex_quota_enabled": False}}),
        encoding="utf-8",
    )
    runtime = build_runtime(config_path, tmp_path / "aacc-disabled.db")
    try:
        assert runtime.codex_quota_service is None
    finally:
        runtime.close()


def test_default_codex_quota_factory_composes_live_and_local_readers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aacc.codex_app_server import RediscoveringCodexQuotaReader
    from aacc.codex_quota import CodexQuotaReader, CompositeCodexQuotaReader

    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "find_codex_executable", lambda **_kwargs: executable)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="darwin",
    )

    assert service is not None
    assert isinstance(service._reader, CompositeCodexQuotaReader)
    assert isinstance(service._reader._primary, RediscoveringCodexQuotaReader)
    assert isinstance(service._reader._fallback, CodexQuotaReader)


def test_frozen_windows_codex_quota_uses_only_packaged_broker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aacc.codex_quota import CompositeCodexQuotaReader

    install = tmp_path / "AACC"
    executable = install / "AACC.exe"
    broker = install / "aacc-spawn.exe"
    bundle = install / "_internal"
    codex = tmp_path / "tools" / "codex.cmd"
    for path in (executable, broker, codex):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ")
    bundle.mkdir()
    monkeypatch.setattr(app_module, "find_codex_executable", lambda **_kwargs: codex)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="win32",
        frozen=True,
        application_executable=executable,
        frozen_bundle_dir=bundle,
        environ={"AACC_SPAWN_BROKER_PATH": str(tmp_path / "ignored.exe")},
        parent_pid=42,
    )

    assert service is not None
    assert isinstance(service._reader, CompositeCodexQuotaReader)
    assert service._reader._primary is not None
    one_shot = service._reader._primary._reader_factory(codex)
    command = one_shot._process_command()
    assert command.args[0] == str(broker)
    assert command.args[6] == str(bundle)
    assert command.args[-1] == str(codex)


def test_frozen_windows_missing_or_inconsistent_bundle_uses_local_fallback(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    install = tmp_path / "private-install"
    executable = install / "AACC.exe"
    broker = install / "aacc-spawn.exe"
    codex = tmp_path / "private-tools" / "codex.cmd"
    wrong_bundle = tmp_path / "wrong" / "_internal"
    for path in (executable, broker, codex):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ")
    wrong_bundle.mkdir(parents=True)
    monkeypatch.setattr(app_module, "find_codex_executable", lambda **_kwargs: codex)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="win32",
        frozen=True,
        application_executable=executable,
        frozen_bundle_dir=wrong_bundle,
        environ={},
        parent_pid=42,
    )

    assert service is not None
    assert service._reader._primary is None
    assert "Windows Codex quota broker unavailable" in caplog.text
    assert str(install) not in caplog.text
    assert str(codex) not in caplog.text


def test_frozen_windows_missing_broker_uses_local_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install = tmp_path / "AACC"
    executable = install / "AACC.exe"
    bundle = install / "_internal"
    codex = tmp_path / "tools" / "codex.cmd"
    for path in (executable, codex):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ")
    bundle.mkdir()
    monkeypatch.setattr(app_module, "find_codex_executable", lambda **_kwargs: codex)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="win32",
        frozen=True,
        application_executable=executable,
        frozen_bundle_dir=bundle,
        environ={"AACC_SPAWN_BROKER_PATH": str(tmp_path / "ignored.exe")},
        parent_pid=42,
    )

    assert service is not None
    assert service._reader._primary is None


def test_source_windows_requires_absolute_broker_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex = tmp_path / "tools" / "codex.exe"
    codex.parent.mkdir()
    codex.write_bytes(b"MZ")
    monkeypatch.setattr(app_module, "find_codex_executable", lambda **_kwargs: codex)
    config = app_module.load_config(tmp_path / "config.yaml")

    without_override = app_module._default_codex_quota_service_factory(
        config,
        platform="win32",
        frozen=False,
        application_executable=tmp_path / "python.exe",
        environ={"AACC_SPAWN_BROKER_PATH": "relative/aacc-spawn.exe"},
        parent_pid=42,
    )

    assert without_override is not None
    assert without_override._reader._primary is None

    broker = tmp_path / "native" / "aacc-spawn.exe"
    broker.parent.mkdir()
    broker.write_bytes(b"MZ")
    with_override = app_module._default_codex_quota_service_factory(
        config,
        platform="win32",
        frozen=False,
        application_executable=tmp_path / "python.exe",
        environ={"AACC_SPAWN_BROKER_PATH": str(broker)},
        parent_pid=42,
    )

    assert with_override is not None
    assert with_override._reader._primary is not None
    one_shot = with_override._reader._primary._reader_factory(codex)
    command = one_shot._process_command()
    assert command.args[0] == str(broker)
    assert command.args[6] == str(broker.parent)


def test_non_windows_ignores_broker_override_and_keeps_direct_reader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex = tmp_path / "codex"
    codex.write_bytes(b"")
    monkeypatch.setattr(app_module, "find_codex_executable", lambda **_kwargs: codex)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="darwin",
        environ={"AACC_SPAWN_BROKER_PATH": str(tmp_path / "broker")},
    )

    assert service is not None
    assert service._reader._primary is not None
    one_shot = service._reader._primary._reader_factory(codex)
    assert one_shot._process_command().args == (
        str(codex),
        "app-server",
        "--stdio",
    )


def test_default_codex_quota_factory_keeps_local_reader_without_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aacc.codex_quota import CompositeCodexQuotaReader

    monkeypatch.setattr(app_module, "find_codex_executable", lambda **_kwargs: None)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="darwin",
    )

    assert service is not None
    assert isinstance(service._reader, CompositeCodexQuotaReader)
    assert service._reader._primary is not None


def test_default_codex_quota_factory_rediscovers_executable_after_startup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex = tmp_path / "ChatGPT" / "codex.exe"
    locations = iter((None, codex))
    monkeypatch.setattr(
        app_module,
        "find_codex_executable",
        lambda **_kwargs: next(locations),
    )

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="darwin",
    )

    assert service is not None
    primary = service._reader._primary
    assert primary is not None
    assert primary._locator() is None
    assert primary._locator() == codex


def test_runtime_wires_opencode_discovery(tmp_path) -> None:
    from aacc.app import build_runtime
    from aacc.config import create_default_config

    config_path = tmp_path / "config.yaml"
    create_default_config(config_path)
    runtime = build_runtime(
        config_path,
        tmp_path / "aacc.db",
        quota_service_factory=lambda config_dir: None,
        kimi_web_quota_service_factory=lambda config_dir: None,
        codex_quota_service_factory=lambda: None,
    )
    assert runtime.opencode_discovery is not None
    runtime.close()


def test_startup_stops_before_opencode_when_kimi_desktop_start_quits(
    tmp_path: Path, monkeypatch: object
) -> None:
    events: list[str] = []
    runtime = _runtime_for_application_test(events)

    class QuittingDesktop:
        catalog: dict[str, object] = {}
        auto_active_ids: set[str] = set()
        retained_ids: set[str] = set()
        muted_ids: set[str] = set()
        health = None

        def set_monitoring_preferences(self, *_args: object) -> None:
            pass

        def subscribe_health(self, *_args: object) -> None:
            pass

        def start(self) -> None:
            events.append("desktop-start")
            runtime.qt_app.exit(0)  # type: ignore[attr-defined]

        def stop(self) -> None:
            pass

    runtime.kimi_desktop_discovery = QuittingDesktop()  # type: ignore[attr-defined]
    _patch_application_shell(monkeypatch, events, runtime)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")  # type: ignore[attr-defined]

    assert (
        app_module._run_application(tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path) == 0
    )
    assert events.index("desktop-start") < events.index("runtime-close")
    assert events.count("service-start") == 2
    assert "hotkeys-created" not in events


def test_default_opencode_factory_keeps_windows_service(monkeypatch) -> None:
    import aacc.app as app_module
    from aacc.config import default_config

    monkeypatch.setattr(app_module.sys, "platform", "win32")
    service = app_module._default_opencode_web_quota_service_factory(Path("."), default_config())
    assert service is not None
    service.stop()


def test_default_opencode_factory_uses_configured_url(monkeypatch) -> None:
    import aacc.app as app_module
    from aacc.config import default_config

    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    config = default_config()
    config.opencode_workspace_url = (
        "https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go"
    )
    service = app_module._default_opencode_web_quota_service_factory(Path("."), config)
    assert service is not None
    assert service.workspace_url == config.opencode_workspace_url
    service.stop()
