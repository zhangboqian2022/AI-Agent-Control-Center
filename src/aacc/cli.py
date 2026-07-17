from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx

from aacc.config import load_config
from aacc.constants import DEFAULT_CONFIG_PATH, DEFAULT_DATABASE_PATH

KEYS = ("enter", "esc", "up", "down", "left", "right", "ctrl_c", "1", "2")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aacc", description="控制 AACC 任务状态和窗口")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出任务")
    show = sub.add_parser("show", help="显示单个任务")
    show.add_argument("task_id")
    focus = sub.add_parser("focus", help="切换到任务")
    focus.add_argument("task_id")
    status_parser = sub.add_parser("status", help="更新任务状态")
    status_parser.add_argument("task_id")
    status_parser.add_argument("status")
    status_parser.add_argument("--message", default="")
    key = sub.add_parser("key", help="向任务发送白名单按键")
    key.add_argument("task_id")
    key.add_argument("key", choices=KEYS)
    send = sub.add_parser("send", help="向已绑定任务发送文本")
    send.add_argument("task_id")
    send.add_argument("text")
    voice = sub.add_parser("voice", help="启动配置的语音输入")
    voice.add_argument("task_id")
    reset = sub.add_parser("reset", help="重置任务状态")
    reset.add_argument("task_id")
    sub.add_parser("doctor", help="检查配置、数据库、权限与 API")
    return parser


def _request(
    config_path: Path, method: str, path: str, payload: dict[str, Any] | None = None
) -> Any:
    config = load_config(config_path)
    url = f"http://{config.app.api.host}:{config.app.api.port}{path}"
    headers = {"Authorization": f"Bearer {config.app.api.token}"}
    with httpx.Client(timeout=3.0) as client:
        response = client.request(method, url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


def _doctor(config_path: Path) -> int:
    checks: list[tuple[str, bool, str]] = []
    try:
        config = load_config(config_path)
        checks.append(("配置文件", True, str(config_path)))
    except ValueError as error:
        checks.append(("配置文件", False, str(error)))
        config = None
    db_path = (
        DEFAULT_DATABASE_PATH
        if config_path == DEFAULT_CONFIG_PATH
        else config_path.with_name("aacc.db")
    )
    try:
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA quick_check").fetchone()
        connection.close()
        checks.append(("SQLite", True, str(db_path)))
    except sqlite3.Error as error:
        checks.append(("SQLite", False, str(error)))
    if config is not None:
        try:
            result = _request(config_path, "GET", "/api/v1/health")
            checks.append(("本地 API", result.get("status") == "ok", "可连接"))
        except (httpx.HTTPError, OSError):
            checks.append(("本地 API", False, "未运行；请启动 AACC"))
    for name, passed, detail in checks:
        print(f"{'✓' if passed else '✗'} {name}: {detail}")
    return 0 if all(item[1] for item in checks) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    try:
        if args.command == "list":
            result = _request(args.config, "GET", "/api/v1/tasks")
        elif args.command == "show":
            result = _request(args.config, "GET", f"/api/v1/tasks/{args.task_id}")
        elif args.command == "status":
            result = _request(
                args.config,
                "POST",
                f"/api/v1/tasks/{args.task_id}/status",
                {"status": args.status, "message": args.message, "source": "cli"},
            )
        elif args.command == "key":
            result = _request(
                args.config,
                "POST",
                f"/api/v1/tasks/{args.task_id}/send-key",
                {"key": args.key.upper()},
            )
        elif args.command == "send":
            result = _request(
                args.config,
                "POST",
                f"/api/v1/tasks/{args.task_id}/send-text",
                {"text": args.text},
            )
        else:
            result = _request(args.config, "POST", f"/api/v1/tasks/{args.task_id}/{args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (httpx.HTTPError, OSError, ValueError) as error:
        print(f"AACC 请求失败: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
