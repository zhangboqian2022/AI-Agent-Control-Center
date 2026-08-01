import os
import stat
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import aacc.config as config_module
from aacc.config import (
    create_default_config,
    default_config,
    is_valid_token,
    load_config,
    rotate_api_token,
    save_config,
)
from aacc.file_security import FileProtectionError
from aacc.models import AppConfig


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced on Windows"
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


def test_save_config_rejects_symlink_parent(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        save_config(link_dir / "config.yaml", default_config())


@pytest.mark.skipif(sys.platform == "win32", reason="dir_fd is POSIX-only")
def test_temporary_config_name_retries_collisions_and_fails_after_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        (tmp_path / ".config.yaml.fixed").write_text("occupied", encoding="utf-8")
        monkeypatch.setattr(config_module.secrets, "token_hex", lambda _length: "fixed")
        with pytest.raises(FileExistsError, match="unique AACC"):
            config_module._open_posix_temporary(parent_descriptor, ".config.yaml.")
    finally:
        os.close(parent_descriptor)


@pytest.mark.skipif(sys.platform == "win32", reason="dir_fd is POSIX-only")
def test_save_config_closes_anchored_parent_when_temp_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    original_close = os.close

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "close", record_close)
    monkeypatch.setattr(
        config_module,
        "_open_posix_temporary",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("temporary allocation failed")),
    )
    with pytest.raises(RuntimeError, match="temporary allocation failed"):
        save_config(tmp_path / "config.yaml", default_config())
    assert closed


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced on Windows"
)
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

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        save_config(path, config)

    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".config.yaml.*"))


@pytest.mark.skipif(sys.platform == "win32", reason="dir_fd is POSIX-only")
def test_save_config_renames_inside_an_anchored_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    replace_calls: list[tuple[object, ...]] = []
    original_replace = os.replace

    def record_replace(*args: object, **kwargs: object) -> None:
        replace_calls.append((*args, *kwargs.values()))
        original_replace(*args, **kwargs)

    monkeypatch.setattr(os, "replace", record_replace)
    save_config(path, default_config())

    assert replace_calls
    assert len(replace_calls[0]) == 4
    assert isinstance(replace_calls[0][2], int)
    assert replace_calls[0][2] == replace_calls[0][3]


def test_save_config_does_not_replace_target_when_protection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("original", encoding="utf-8")
    replace_calls: list[tuple[Path, Path]] = []

    def fail_protection(*_args: object, **_kwargs: object) -> None:
        raise FileProtectionError("denied")

    monkeypatch.setattr(config_module, "protect_file", fail_protection)
    monkeypatch.setattr(
        os,
        "replace",
        lambda source, target: replace_calls.append((Path(source), Path(target))),
    )

    with pytest.raises(FileProtectionError, match="denied"):
        save_config(path, default_config())

    assert replace_calls == []
    assert path.read_text(encoding="utf-8") == "original"
    assert "CRITICAL" in caplog.text
    assert default_config().app.api.token not in caplog.text


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced on Windows"
)
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


def test_default_terminal_config_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    config = default_config()
    for task in config.tasks:
        assert task.terminal.type == "terminal_app"
        assert task.terminal.app_bundle_id == "com.apple.Terminal"


def test_default_terminal_config_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    config = default_config()
    for task in config.tasks:
        assert task.terminal.type == "windows_terminal"
        assert task.terminal.app_bundle_id is None


def test_save_config_skips_directory_fsync_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(config_module, "protect_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(config_module, "protect_directory", lambda *_args, **_kwargs: None)
    path = tmp_path / "config.yaml"
    config = default_config()

    save_config(path, config)

    persisted = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert persisted["app"]["api"]["token"] == config.app.api.token
    assert len(persisted["tasks"]) == 4


def test_save_config_skips_fchmod_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # os.fchmod does not exist on Windows; the Windows path must use the
    # shared ACL helper instead.
    monkeypatch.setattr(sys, "platform", "win32")
    protected: list[Path] = []
    monkeypatch.setattr(
        config_module,
        "protect_file",
        lambda path, **_kwargs: protected.append(Path(path)),
    )
    monkeypatch.setattr(config_module, "protect_directory", lambda *_args, **_kwargs: None)

    def raise_attribute_error(_descriptor: int, _mode: int) -> None:
        raise AttributeError("simulating windows")

    monkeypatch.setattr(os, "fchmod", raise_attribute_error, raising=False)
    path = tmp_path / "config.yaml"
    config = default_config()

    save_config(path, config)

    persisted = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert persisted["app"]["api"]["token"] == config.app.api.token
    assert len(persisted["tasks"]) == 4
    assert len(protected) == 2
    assert protected[0].name.startswith(".config.yaml.")
    assert protected[1] == protected[0]


def test_save_config_protects_empty_windows_temp_before_writing_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    protected_sizes: list[int] = []
    monkeypatch.setattr(
        config_module,
        "protect_file",
        lambda path, **_kwargs: protected_sizes.append(Path(path).stat().st_size),
    )
    monkeypatch.setattr(config_module, "protect_directory", lambda *_args, **_kwargs: None)

    save_config(tmp_path / "config.yaml", default_config())

    assert protected_sizes[0] == 0
    assert protected_sizes[1] > 0


def test_load_config_repairs_existing_windows_file_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    config = default_config()
    path.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "platform", "win32")
    protected: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda *_args, **_kwargs: pytest.fail("valid config must not be republished"),
    )
    monkeypatch.setattr(
        config_module,
        "protect_file",
        lambda target, *, platform: protected.append((target, platform)),
    )
    monkeypatch.setattr(config_module, "protect_directory", lambda *_args, **_kwargs: None)

    loaded = load_config(path)

    assert loaded.app.api.token == config.app.api.token
    assert protected == [(path, "win32")]


def test_opencode_workspace_url_accepts_valid_workspace_page() -> None:
    config = default_config()
    config.opencode_workspace_url = (
        "https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go"
    )
    assert config.opencode_workspace_url.endswith("/go")


def test_opencode_workspace_url_defaults_empty() -> None:
    assert AppConfig().opencode_workspace_url == ""


def test_opencode_workspace_url_rejects_foreign_host() -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url="https://example.com/workspace/wrk_1")


def test_opencode_workspace_url_rejects_http_scheme() -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url="http://opencode.ai/workspace/wrk_1")


@pytest.mark.parametrize(
    "url",
    [
        "https://opencode.ai/workspace/wrk_1/go?tab=quota",
        "https://opencode.ai/workspace/wrk_1/go#quota",
    ],
)
def test_opencode_workspace_url_rejects_query_and_fragment(url: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url=url)


def test_opencode_workspace_url_rejects_non_workspace_path() -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url="https://opencode.ai/zen")


@pytest.mark.parametrize(
    "url",
    [
        "https://opencode.ai/workspace/../admin",
        "https://opencode.ai/workspace/%2e%2e/admin",
        "https://opencode.ai/workspace/wrk.1",
        "https://opencode.ai/workspace/wrk_1/extra",
    ],
)
def test_opencode_workspace_url_rejects_non_whitelisted_workspace_path(url: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url=url)


def test_opencode_workspace_url_rejects_urlparse_failure(monkeypatch) -> None:
    import aacc.models as models

    def broken_parse(value: str) -> None:
        del value
        raise ValueError("malformed URL")

    monkeypatch.setattr(models, "urlparse", broken_parse)
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url="https://opencode.ai/workspace/wrk_1")


def test_opencode_workspace_url_round_trips_through_config_file(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    config = default_config()
    config.opencode_workspace_url = "https://opencode.ai/workspace/wrk_123/go"
    save_config(path, config)
    loaded = load_config(path)
    assert loaded.opencode_workspace_url == config.opencode_workspace_url
