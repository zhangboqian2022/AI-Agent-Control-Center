from pathlib import Path

import aacc.app as app_module
from aacc.app import build_runtime
from aacc.discovery_service import (
    CodexDiscoveryService,
    KimiDesktopDiscoveryService,
    KimiDiscoveryService,
)
from aacc.file_security import FileProtectionError


class FakeApplication:
    pass


def test_security_failure_shows_sanitized_dialog_and_returns_nonzero(
    tmp_path: Path, monkeypatch: object
) -> None:
    shown: list[tuple[str, str]] = []
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
    runtime.close()


def test_runtime_includes_kimi_desktop_discovery(tmp_path: Path) -> None:
    runtime = build_runtime(tmp_path / "config.yaml", tmp_path / "aacc.db")
    assert isinstance(runtime.kimi_desktop_discovery, KimiDesktopDiscoveryService)
    runtime.close()


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
    from aacc.codex_app_server import CodexAppServerReader
    from aacc.codex_quota import CodexQuotaReader, CompositeCodexQuotaReader

    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "find_codex_executable", lambda: executable)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="darwin",
    )

    assert service is not None
    assert isinstance(service._reader, CompositeCodexQuotaReader)
    assert isinstance(service._reader._primary, CodexAppServerReader)
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
    monkeypatch.setattr(app_module, "find_codex_executable", lambda: codex)

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
    command = service._reader._primary._process_command()
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
    monkeypatch.setattr(app_module, "find_codex_executable", lambda: codex)

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
    monkeypatch.setattr(app_module, "find_codex_executable", lambda: codex)

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
    monkeypatch.setattr(app_module, "find_codex_executable", lambda: codex)
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
    command = with_override._reader._primary._process_command()
    assert command.args[0] == str(broker)
    assert command.args[6] == str(broker.parent)


def test_non_windows_ignores_broker_override_and_keeps_direct_reader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex = tmp_path / "codex"
    codex.write_bytes(b"")
    monkeypatch.setattr(app_module, "find_codex_executable", lambda: codex)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml"),
        platform="darwin",
        environ={"AACC_SPAWN_BROKER_PATH": str(tmp_path / "broker")},
    )

    assert service is not None
    assert service._reader._primary is not None
    assert service._reader._primary._process_command().args == (
        str(codex),
        "app-server",
        "--stdio",
    )


def test_default_codex_quota_factory_keeps_local_reader_without_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aacc.codex_quota import CompositeCodexQuotaReader

    monkeypatch.setattr(app_module, "find_codex_executable", lambda: None)

    service = app_module._default_codex_quota_service_factory(
        app_module.load_config(tmp_path / "config.yaml")
    )

    assert service is not None
    assert isinstance(service._reader, CompositeCodexQuotaReader)
    assert service._reader._primary is None
