from pathlib import Path

import pytest

from aacc.config import create_default_config, load_config


def test_default_config_has_four_agents_and_random_token(tmp_path: Path) -> None:
    first = create_default_config(tmp_path / "first.yaml")
    second = create_default_config(tmp_path / "second.yaml")
    assert len(first.tasks) == 4
    assert [task.agent.type for task in first.tasks] == [
        "codex_cli",
        "claude_code",
        "kimi_code",
        "generic_cli",
    ]
    assert len(first.app.api.token) >= 32
    assert first.app.api.token != second.app.api.token


def test_load_config_rejects_non_loopback_api(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("app:\n  api:\n    host: 0.0.0.0\n    token: abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
    with pytest.raises(ValueError, match="127.0.0.1"):
        load_config(path)


def test_load_config_rejects_invalid_adapter_regex(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "tasks:\n  - id: task-1\n    slot: 1\n    name: Bad\n    agent:\n"
        "      type: generic_cli\n      running_patterns: ['[']\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="regular expression"):
        load_config(path)

