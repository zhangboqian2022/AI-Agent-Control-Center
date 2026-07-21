from pathlib import Path

import aacc.app as app_module
from aacc.app import build_runtime
from aacc.discovery_service import (
    CodexDiscoveryService,
    KimiDesktopDiscoveryService,
    KimiDiscoveryService,
)


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
