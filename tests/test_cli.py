from pathlib import Path

import pytest

from aacc.cli import build_parser


def test_status_command_accepts_documented_spelling() -> None:
    args = build_parser().parse_args(
        ["status", "task-1", "waiting-approval", "--message", "approve"]
    )
    assert args.command == "status"
    assert args.task_id == "task-1"
    assert args.status == "waiting-approval"
    assert args.message == "approve"


def test_key_command_uses_whitelisted_choices() -> None:
    parser = build_parser()
    args = parser.parse_args(["key", "task-1", "enter"])
    assert args.key == "enter"


def test_doctor_command_parses_without_network_request() -> None:
    args = build_parser().parse_args(["doctor"])
    assert args.command == "doctor"


def test_doctor_reports_the_same_database_path_the_app_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aacc.cli import _doctor
    from aacc.constants import resolve_database_path

    custom_db = tmp_path / "custom.db"
    custom_db.touch()
    monkeypatch.setenv("AACC_DATABASE_PATH", str(custom_db))

    assert resolve_database_path() == custom_db
    _doctor(tmp_path / "config.yaml")
    assert str(custom_db) in capsys.readouterr().out


def test_resolve_database_path_defaults_to_app_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aacc.constants import DEFAULT_DATABASE_PATH, resolve_database_path

    monkeypatch.delenv("AACC_DATABASE_PATH", raising=False)
    assert resolve_database_path() == DEFAULT_DATABASE_PATH
