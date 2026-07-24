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
from aacc.kimi_oauth import save_credentials  # noqa: E402
from aacc.kimi_quota import BoosterWallet, KimiQuota, QuotaDetail  # noqa: E402
from aacc.models import AgentConfig, TaskConfig, TaskState, TerminalConfig  # noqa: E402
from aacc.persistence import StateStore  # noqa: E402
from aacc.quota_service import QuotaService  # noqa: E402
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
    usage: dict[str, object] | None = None,
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
    metadata: dict[str, object] = {}
    if work_dir:
        metadata["work_dir"] = work_dir
    if usage:
        metadata["usage"] = usage
    if metadata:
        state = state.model_copy(update={"metadata": metadata})
    return config, state


def _demo_quota() -> KimiQuota:
    return KimiQuota(
        weekly=QuotaDetail(
            used=64,
            limit=100,
            remaining=36,
            reset_at=datetime.now(UTC) + timedelta(days=3, hours=2),
            percentage=64,
        ),
        five_hour=QuotaDetail(
            used=30,
            limit=100,
            remaining=70,
            reset_at=datetime.now(UTC) + timedelta(hours=2, minutes=40),
            percentage=30,
        ),
        total_quota=QuotaDetail(used=0, limit=0, remaining=0, reset_at=None, percentage=0),
        membership_level="LEVEL_ADVANCED",
        booster=BoosterWallet(status="STATUS_ACTIVE", is_enabled=True, balance_yuan=3.15),
    )


def main() -> int:
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp())
    config = default_config()
    store = StateStore(tmp / "demo.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    # Fake authorized quota service so the QuotaBar renders populated; the
    # demo quota is pushed via the service's own signal below.
    quota_config_dir = tmp / "quota"
    save_credentials(quota_config_dir, {"auth_method": "api_key", "api_key": "sk-demo"})
    quota_service = QuotaService(quota_config_dir, version="demo", interval_seconds=3600)
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat),
        quota_service=quota_service,
        open_url=lambda _url: None,
    )

    demo = [
        _task("codex:demo-auth", 1, "重构登录鉴权模块", "codex_cli", "Codex",
              "RUNNING", "正在修改代码", minutes_ago=7.5),
        _task("kimi:demo-payment", 2, "修复支付回调重试", "kimi_code", "Kimi Code",
              "RUNNING", "正在运行", minutes_ago=3.2,
              work_dir="/Users/dev/Desktop/codelight",
              usage={"total_input_tokens": 48_200, "output_tokens": 6_100,
                     "cache_read_pct": 76, "speed_tps": 58}),
        _task("codex:demo-deps", 3, "升级依赖并跑回归", "codex_cli", "Codex",
              "WAITING_APPROVAL", "等待批准：写 pyproject.toml", minutes_ago=12.0),
        _task("kimi_desktop:demo-notes", 4, "整理周会纪要", "kimi_desktop", "Kimi Desktop",
              "COMPLETED", "回合已完成", minutes_ago=25.0),
        _task("kimi:demo-migrate", 5, "数据迁移脚本", "kimi_code", "Kimi Code",
              "ERROR", "进程异常退出", minutes_ago=41.0,
              work_dir="/Users/dev/Desktop/servercheck",
              usage={"total_input_tokens": 12_300, "output_tokens": 1_200,
                     "cache_read_pct": 68, "speed_tps": 42}),
    ]
    for task_config, task_state in demo:
        manager.register(task_config, task_state)
    window.set_codex_selected_ids({"demo-auth", "demo-deps"})
    window.set_kimi_selected_ids({"demo-payment", "demo-migrate"})
    window.set_kimi_desktop_selected_ids({"demo-notes"})

    window.show()
    quota_service.quota_updated.emit(_demo_quota())
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
