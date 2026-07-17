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
