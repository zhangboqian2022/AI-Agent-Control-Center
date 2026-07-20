from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path

import httpx

from aacc.config import load_config
from aacc.constants import DEFAULT_CONFIG_PATH


def terminate_process(process: subprocess.Popen[bytes], timeout: float = 3.0) -> None:
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aacc-run")
    parser.add_argument("--task", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _status(
    config_path: Path, task_id: str, state: str, message: str, pid: int | None = None
) -> None:
    config = load_config(config_path)
    with suppress(httpx.HTTPError):
        httpx.post(
            f"http://{config.app.api.host}:{config.app.api.port}/api/v1/tasks/{task_id}/status",
            headers={"Authorization": f"Bearer {config.app.api.token}"},
            json={"status": state, "message": message, "source": "wrapper", "confidence": 0.95},
            timeout=1.5,
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("aacc-run: 缺少要运行的命令", file=sys.stderr)
        return 2
    _status(args.config, args.task, "starting", f"正在启动 {command[0]}")
    stop = threading.Event()
    received_signal: int | None = None

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal received_signal
        received_signal = signum
        stop.set()

    previous_handlers = {
        signum: signal.signal(signum, request_stop) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(command, shell=False)
        _status(args.config, args.task, "running", f"{command[0]} 正在运行", process.pid)
        while True:
            return_code = process.poll()
            if return_code is not None:
                break
            if stop.wait(0.1):
                terminate_process(process)
                _status(args.config, args.task, "cancelled", "收到终止信号")
                return 128 + (received_signal or signal.SIGINT)
    except KeyboardInterrupt:
        if process is not None:
            terminate_process(process)
        _status(args.config, args.task, "cancelled", "用户中断")
        return 130
    except OSError as error:
        _status(args.config, args.task, "error", f"启动失败: {error}")
        print(f"aacc-run: {error}", file=sys.stderr)
        return 127
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    if return_code == 0:
        _status(args.config, args.task, "stopped", "进程已退出；业务完成状态未知")
    else:
        _status(args.config, args.task, "error", f"进程退出码 {return_code}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
