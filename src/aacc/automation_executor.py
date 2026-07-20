from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from typing import Protocol

from aacc.automation import AutomationError
from aacc.models import TaskConfig


class AutomationBusyError(AutomationError):
    pass


class AutomationController(Protocol):
    def focus(self, task: TaskConfig, *, cancel_event: threading.Event | None = None) -> str: ...
    def send_key(
        self,
        task: TaskConfig,
        key: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str: ...
    def send_text(
        self,
        task: TaskConfig,
        text: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str: ...
    def start_voice(
        self, task: TaskConfig, *, cancel_event: threading.Event | None = None
    ) -> str: ...


class AutomationExecutor:
    def __init__(
        self,
        controller: AutomationController,
        *,
        capacity: int = 32,
        total_timeout: float = 12.0,
    ) -> None:
        if capacity < 1:
            raise ValueError("Automation queue capacity must be positive")
        if total_timeout <= 0:
            raise ValueError("Automation timeout must be positive")
        self._controller = controller
        self._total_timeout = total_timeout
        self._slots = threading.BoundedSemaphore(capacity)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aacc-automation")
        self._lock = threading.Lock()
        self._closed = False
        self._logger = logging.getLogger("aacc.automation")

    def _execute(self, method: str, cancel_event: threading.Event, *args: object) -> str:
        started = time.monotonic()
        task = args[0] if args and isinstance(args[0], TaskConfig) else None
        target = task.terminal.app_bundle_id if task is not None else None
        outcome = "error"
        try:
            if cancel_event.is_set():
                outcome = "cancelled"
                raise AutomationError("Desktop automation was cancelled")
            if method == "focus" and task is not None:
                result = self._controller.focus(task, cancel_event=cancel_event)
            elif method == "send_key" and task is not None and len(args) == 2:
                result = self._controller.send_key(task, str(args[1]), cancel_event=cancel_event)
            elif method == "send_text" and task is not None and len(args) == 2:
                result = self._controller.send_text(task, str(args[1]), cancel_event=cancel_event)
            elif method == "start_voice" and task is not None:
                result = self._controller.start_voice(task, cancel_event=cancel_event)
            else:
                raise AutomationError("Unsupported desktop automation operation")
            outcome = "success"
            return result
        except AutomationError:
            if cancel_event.is_set():
                outcome = "cancelled"
            raise
        finally:
            self._logger.info(
                "automation operation=%s target=%s outcome=%s elapsed_ms=%d",
                method,
                target or "unknown",
                outcome,
                round((time.monotonic() - started) * 1000),
            )

    def _submit(self, method: str, *args: object) -> tuple[Future[str], threading.Event]:
        cancel_event = threading.Event()
        with self._lock:
            if self._closed:
                self._logger.warning(
                    "automation operation=%s target=unknown outcome=busy reason=closed", method
                )
                raise AutomationBusyError("Desktop automation executor is closed")
            if not self._slots.acquire(blocking=False):
                self._logger.warning(
                    "automation operation=%s target=unknown outcome=busy reason=queue_full",
                    method,
                )
                raise AutomationBusyError("Desktop automation queue is full")
            try:
                future = self._pool.submit(self._execute, method, cancel_event, *args)
            except Exception:
                self._slots.release()
                raise
        future.add_done_callback(lambda _future: self._slots.release())
        return future, cancel_event

    def submit(self, method: str, *args: object) -> Future[str]:
        future, _cancel_event = self._submit(method, *args)
        return future

    def _wait(self, method: str, *args: object) -> str:
        future, cancel_event = self._submit(method, *args)
        try:
            return future.result(timeout=self._total_timeout)
        except FutureTimeoutError as error:
            cancel_event.set()
            future.cancel()
            self._logger.warning("automation operation=%s target=unknown outcome=timeout", method)
            raise AutomationError("Desktop automation timed out") from error

    def focus(self, task: TaskConfig) -> str:
        return self._wait("focus", task)

    def send_key(self, task: TaskConfig, key: str) -> str:
        return self._wait("send_key", task, key)

    def send_text(self, task: TaskConfig, text: str) -> str:
        return self._wait("send_text", task, text)

    def start_voice(self, task: TaskConfig) -> str:
        return self._wait("start_voice", task)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
