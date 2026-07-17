from pathlib import Path

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
