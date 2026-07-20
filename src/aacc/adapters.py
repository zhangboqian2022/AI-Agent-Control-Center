from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import cast

import psutil
import regex as safe_regex

from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus

ANSI_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
MAX_LINE_LENGTH = 4096
EVENTS_STOPPED = object()


def strip_ansi(value: str) -> str:
    return ANSI_PATTERN.sub("", value)


class BaseAgentAdapter:
    def __init__(self, task_id: str, config: AgentConfig) -> None:
        self.task_id = task_id
        self.config = config
        self.display_name = config.display_name or config.type.replace("_", " ").title()
        self.capabilities: dict[str, bool] = {
            "can_detect_process": bool(config.process_patterns or config.executable_patterns),
            "can_read_structured_events": False,
            "can_parse_logs": False,
            "can_focus": True,
            "can_send_text": True,
            "can_send_keys": True,
            "can_approve": False,
            "can_stop": False,
        }
        self._connected = False
        self._events: asyncio.Queue[TaskState | object] = asyncio.Queue()

    async def detect(self) -> bool:
        patterns = self.config.process_patterns + self.config.executable_patterns
        if not patterns:
            return False
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                info = process.info
                haystack = " ".join([info.get("name") or "", *(info.get("cmdline") or [])])
                if any(re.search(pattern, haystack, re.IGNORECASE) for pattern in patterns):
                    return True
            except (psutil.Error, OSError):
                continue
        return False

    async def connect(self) -> None:
        if not self._connected:
            self._events = asyncio.Queue()
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self._events.put_nowait(EVENTS_STOPPED)

    async def events(self) -> AsyncIterator[TaskState]:
        while True:
            event = await self._events.get()
            if event is EVENTS_STOPPED:
                return
            yield cast(TaskState, event)

    async def get_status(self) -> TaskState:
        detected = await self.detect()
        status = TaskStatus.RUNNING if detected else TaskStatus.STOPPED
        return TaskState.new(self.task_id, status, source="process", confidence=0.55)

    def classify(self, line: str) -> TaskStatus | None:
        return None


class GenericCLIAdapter(BaseAgentAdapter):
    _ORDER = (
        ("error_patterns", TaskStatus.ERROR),
        ("waiting_approval_patterns", TaskStatus.WAITING_APPROVAL),
        ("waiting_input_patterns", TaskStatus.WAITING_INPUT),
        ("completed_patterns", TaskStatus.COMPLETED),
        ("running_patterns", TaskStatus.RUNNING),
    )

    def __init__(self, task_id: str, config: AgentConfig) -> None:
        super().__init__(task_id, config)
        self.capabilities["can_parse_logs"] = any(
            getattr(config, field) for field, _ in self._ORDER
        )
        self._compiled = {
            field: [
                safe_regex.compile(pattern, safe_regex.IGNORECASE)
                for pattern in getattr(config, field)
            ]
            for field, _ in self._ORDER
        }

    def classify(self, line: str) -> TaskStatus | None:
        clean = strip_ansi(line).strip()
        if not clean or len(clean) > MAX_LINE_LENGTH:
            return None
        for field, status in self._ORDER:
            for pattern in self._compiled[field]:
                try:
                    if pattern.search(clean, timeout=0.02):
                        return status
                except TimeoutError:
                    continue
        return None


PRESETS: dict[str, dict[str, object]] = {
    "codex_cli": {
        "display_name": "Codex CLI",
        "process_patterns": [r"(?:^|/)codex(?:\s|$)"],
        "running_patterns": [r"^(?:Thinking|Working|正在(?:思考|执行))\b"],
        "waiting_input_patterns": [r"^(?:Enter your choice|Waiting for input)\b"],
        "waiting_approval_patterns": [
            r"^(?:Would you like to run|Do you want to proceed|Approve this command|等待批准)\b"
        ],
        "completed_patterns": [r"^(?:Task completed|Finished successfully|修改完成)\b"],
        "error_patterns": [r"^(?:Error:|Fatal:|command failed|执行失败)\b"],
    },
    "claude_code": {
        "display_name": "Claude Code",
        "process_patterns": [r"(?:^|/)claude(?:\s|$)"],
        "running_patterns": [r"^(?:Thinking|Working|Running)\b"],
        "waiting_input_patterns": [r"^(?:Enter your choice|Waiting for input)\b"],
        "waiting_approval_patterns": [r"^(?:Allow|Approve|Do you want to proceed)\b"],
        "completed_patterns": [r"^Task completed successfully\b"],
        "error_patterns": [r"^(?:Error:|Failed:)\b"],
    },
    "kimi_code": {
        "display_name": "Kimi Code",
        "process_patterns": [r"(?:^|/)kimi(?:\s|$)"],
        "running_patterns": [r"^(?:正在思考|正在执行|Working)\b"],
        "waiting_input_patterns": [r"^(?:请输入|请选择|Waiting for input)\b"],
        "waiting_approval_patterns": [r"^(?:等待批准|是否允许)\b"],
        "completed_patterns": [r"^(?:分析完成|任务完成|Task completed)\b"],
        "error_patterns": [r"^(?:错误[:：]|执行失败|Error:)\b"],
    },
    "codex_app": {
        "display_name": "Codex App",
        "process_patterns": [r"(?:^|/)Codex(?:\.app)?(?:\s|$)"],
    },
}


class AdapterRegistry:
    @staticmethod
    def create(task: TaskConfig) -> GenericCLIAdapter:
        config = task.agent.model_copy(deep=True)
        preset = PRESETS.get(config.type, {})
        for key, value in preset.items():
            current = getattr(config, key)
            if current in (None, [], ""):
                setattr(config, key, value)
        return GenericCLIAdapter(task.id, config)
