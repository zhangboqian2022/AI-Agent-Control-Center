"""Render the panel with demo tasks and capture a screenshot for the docs.

Usage: .venv/bin/python scripts/capture_panel_screenshot.py [output.png]
Runs offscreen; no real tasks or apps are touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from aacc.automation import MacAutomation  # noqa: E402
from aacc.automation_executor import AutomationExecutor  # noqa: E402
from aacc.config import default_config  # noqa: E402
from aacc.gui import MainWindow  # noqa: E402
from aacc.models import AgentConfig, TaskConfig, TaskState, TerminalConfig  # noqa: E402
from aacc.persistence import StateStore  # noqa: E402
from aacc.task_manager import TaskManager  # noqa: E402

OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/images/panel-overview.png")


def _task(
    task_id: str,
    slot: int,
    name: str,
    agent_type: str,
    display: str,
    status: str,
    message: str,
    *,
    minutes_ago: float,
    work_dir: str | None = None,
) -> tuple[TaskConfig, TaskState]:
    config = TaskConfig(
        id=task_id,
        slot=slot,
        name=name,
        agent=AgentConfig(type=agent_type, display_name=display),
        terminal=TerminalConfig(type="mac_app", app_bundle_id="com.example.app"),
    )
    state = TaskState.new(task_id, status, message=message, source="demo")
    started = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    updates: dict[str, object] = {"updated_at": started}
    if state.started_at is not None:
        updates["started_at"] = started
    if state.finished_at is not None:
        updates["finished_at"] = started
    state = state.model_copy(update=updates)
    if work_dir:
        state = state.model_copy(update={"metadata": {"work_dir": work_dir}})
    return config, state


def main() -> int:
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp())
    config = default_config()
    store = StateStore(tmp / "demo.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat),
    )

    demo = [
        _task("codex:demo-auth", 1, "重构登录鉴权模块", "codex_cli", "Codex",
              "RUNNING", "正在修改代码", minutes_ago=7.5),
        _task("kimi:demo-payment", 2, "修复支付回调重试", "kimi_code", "Kimi Code",
              "RUNNING", "正在运行", minutes_ago=3.2,
              work_dir="/Users/dev/Desktop/codelight"),
        _task("codex:demo-deps", 3, "升级依赖并跑回归", "codex_cli", "Codex",
              "WAITING_APPROVAL", "等待批准：写 pyproject.toml", minutes_ago=12.0),
        _task("kimi_desktop:demo-notes", 4, "整理周会纪要", "kimi_desktop", "Kimi Desktop",
              "COMPLETED", "回合已完成", minutes_ago=25.0),
        _task("kimi:demo-migrate", 5, "数据迁移脚本", "kimi_code", "Kimi Code",
              "ERROR", "进程异常退出", minutes_ago=41.0,
              work_dir="/Users/dev/Desktop/servercheck"),
    ]
    for task_config, task_state in demo:
        manager.register(task_config, task_state)
    window.set_codex_selected_ids({"demo-auth", "demo-deps"})
    window.set_kimi_selected_ids({"demo-payment", "demo-migrate"})
    window.set_kimi_desktop_selected_ids({"demo-notes"})

    window.show()
    window.resize(window.sizeHint())
    app.processEvents()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(OUTPUT)):
        print("failed to save screenshot", file=sys.stderr)
        return 1
    print(f"saved {OUTPUT} ({window.width()}x{window.height()})")
    manager.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
