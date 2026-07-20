from pathlib import Path

import aacc.app as app_module
from aacc.app import build_runtime


def test_build_runtime_creates_default_config_database_and_four_tasks(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "aacc.db"
    runtime = build_runtime(config_path, database_path)
    assert config_path.exists()
    assert database_path.exists()
    assert len(runtime.manager.list()) == 4
    assert runtime.automation.config is runtime.config
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
