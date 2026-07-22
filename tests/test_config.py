import os
import stat
from pathlib import Path

import pytest
import yaml

from aacc.config import (
    create_default_config,
    default_config,
    is_valid_token,
    load_config,
    rotate_api_token,
    save_config,
)


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
    assert first.config_version == 1
    assert stat.S_IMODE((tmp_path / "first.yaml").stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "value",
    [
        "",
        "short",
        " " * 32,
        "change-me",
        "replace-me",
        "your-token-here",
        "x" * 31,
        "x" * 16 + " " + "y" * 16,
        "x" * 16 + "\t" + "y" * 16,
        # Placeholder-shaped values must be rejected even when long enough;
        # the shipped example token is a public constant, not a credential.
        "replace-with-a-random-token-generated-on-first-launch",
        "replace-" + "a" * 40,
        "change-me" + "b" * 40,
        "your-token-" + "c" * 40,
        "placeholder-" + "d" * 40,
    ],
)
def test_invalid_tokens_are_rejected(value: str) -> None:
    assert not is_valid_token(value)


def test_legit_high_entropy_tokens_with_placeholder_substrings_are_accepted() -> None:
    assert is_valid_token("kJ9" + "x" * 29 + "replace" + "Qm2" + "y" * 20)


def test_loading_shipped_example_config_rotates_the_public_token(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parent.parent / "examples" / "config.example.yaml"
    path = tmp_path / "config.yaml"
    path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    config = load_config(path)

    assert is_valid_token(config.app.api.token)
    persisted = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert persisted["app"]["api"]["token"] == config.app.api.token


def test_load_repairs_empty_token_and_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("app:\n  api:\n    token: ''\n", encoding="utf-8")
    os.chmod(path, 0o644)

    config = load_config(path)

    persisted = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert is_valid_token(config.app.api.token)
    assert persisted["app"]["api"]["token"] == config.app.api.token
    assert persisted["config_version"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_load_migrates_legacy_config_without_changing_valid_token(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    token = "a" * 32
    path.write_text(f"app:\n  api:\n    token: {token}\n", encoding="utf-8")

    config = load_config(path)

    assert config.config_version == 1
    assert config.app.api.token == token
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["config_version"] == 1


def test_load_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "config.yaml"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symbolic link"):
        load_config(link)


def test_atomic_save_keeps_original_if_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    original = "config_version: 1\n"
    path.write_text(original, encoding="utf-8")
    config = create_default_config(tmp_path / "other.yaml")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        save_config(path, config)

    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".config.yaml.*"))


def test_rotate_token_updates_same_object_and_disk(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config = create_default_config(path)
    old = config.app.api.token

    new = rotate_api_token(path, config)

    assert new != old
    assert config.app.api.token == new
    assert load_config(path).app.api.token == new
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_config_rejects_non_loopback_api(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "app:\n  api:\n    host: 0.0.0.0\n    token: abcdefghijklmnopqrstuvwxyz123456\n",
        encoding="utf-8",
    )
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


def test_default_visible_agent_types_include_kimi_desktop() -> None:
    assert "kimi_desktop" in default_config().app.visible_agent_types
