from pathlib import Path

import pytest

import aacc.cli as cli_module
from aacc.cli import build_parser
from aacc.config import default_config
from aacc.constants import local_api_url


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


def test_local_api_url_brackets_ipv6_loopback() -> None:
    assert local_api_url("127.0.0.1", 17650, "/api/v1/health") == (
        "http://127.0.0.1:17650/api/v1/health"
    )
    assert local_api_url("::1", 17650, "/api/v1/health") == ("http://[::1]:17650/api/v1/health")


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


def test_status_request_disables_proxy_and_marks_cli_as_manual(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "ok"}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def request(self, _method: str, _url: str, **kwargs: object) -> Response:
            captured["request"] = kwargs
            return Response()

    monkeypatch.setattr(cli_module.httpx, "Client", Client)
    monkeypatch.setattr(cli_module, "load_config", lambda _path: default_config())

    assert cli_module.main(["status", "task-1", "paused"]) == 0
    request = captured["request"]
    assert isinstance(request, dict)
    payload = request["json"]
    assert isinstance(payload, dict)
    assert payload["source"] == "manual"
    assert payload["metadata"] == {"transport": "cli"}
    assert captured["trust_env"] is False
    capsys.readouterr()
