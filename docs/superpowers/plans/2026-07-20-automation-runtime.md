# Transactional Automation Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize complete desktop automation transactions without blocking Qt, while preserving bounded API behavior and safe Unicode text input.

**Architecture:** `MacAutomation` is a safe synchronous primitive guarded by one reentrant lock. `AutomationExecutor` is the sole application-level queue; GUI receives completion through Qt signals and API endpoints wait on bounded futures.

**Tech Stack:** Python threading/futures, subprocess, PySide6 signals, FastAPI, pytest/pytest-qt.

## Global Constraints

- Target release is `v1.3.0-rc.1`; supported operating systems are macOS 13 or newer.
- A transaction is always `focus → delay → input`; no separate focus/input locks.
- Queue capacity is exactly 32 and osascript timeout defaults to 5 seconds within a 2–15 second configuration range.
- Text remains Unicode-capable, rejects NUL, accepts 1–2,000 characters, and is never interpolated into AppleScript source or logs.
- Every behavior change starts with a failing regression test; focused modified-line coverage must be at least 90%.

---

### Task 1: Safe synchronous automation primitive

**Files:**
- Modify: `src/aacc/models.py`
- Modify: `src/aacc/automation.py`
- Modify: `tests/test_automation.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Produces: `MacAutomation.focus/send_key/send_text/start_voice`, each transaction-safe; `AutomationError` contains sanitized user-facing errors.

- [ ] **Step 1: Add failing concurrency, timeout, OS error, and text argv tests**

```python
def test_send_text_passes_payload_as_argv(config: AppConfig, task: TaskConfig) -> None:
    calls: list[list[str]] = []
    automation = MacAutomation(config, runner=lambda args, **_: completed(calls.append(args) or args))
    payload = '中文🙂"; do shell script "false"\r\n\t\\'
    automation.send_text(task, payload)
    assert calls[-1][-1] == payload
    assert payload not in calls[-1][2]

def test_timeout_becomes_automation_error(config: AppConfig, task: TaskConfig) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("osascript", 5)
    with pytest.raises(AutomationError, match="timed out"):
        MacAutomation(config, runner=timeout).focus(task)
```

Add a barrier-based ten-thread test; each runner records focus and input pairs and asserts no pair interleaves with a different task.

- [ ] **Step 2: Prove automation tests fail**

Run: `uv run --extra dev pytest tests/test_automation.py tests/test_models.py -q`

Expected: payload-as-argv, normalized exception, and transaction-order tests fail.

- [ ] **Step 3: Add timeout setting, RLock, private focus, and fixed argv script**

```python
TEXT_SCRIPT = (
    "on run argv\n"
    "  tell application \"System Events\" to keystroke (item 1 of argv)\n"
    "end run"
)

def _run(self, args: list[str]) -> str:
    try:
        completed = self._runner(args, capture_output=True, text=True,
                                 timeout=self.config.app.automation_timeout_seconds,
                                 check=False)
    except subprocess.TimeoutExpired as error:
        raise AutomationError("Desktop automation timed out") from error
    except OSError as error:
        raise AutomationError("Desktop automation is unavailable") from error
```

Add `automation_timeout_seconds: float = Field(default=5.0, ge=2.0, le=15.0)`. Public methods hold a single `threading.RLock`; `_focus_unlocked` performs nested focus. `send_text` rejects `"\0" in text` and calls `osascript -e TEXT_SCRIPT -- text`.

- [ ] **Step 4: Run automation tests**

Run: `uv run --extra dev pytest tests/test_automation.py tests/test_models.py -q`

Expected: all tests pass, including Chinese/emoji/control-character payloads.

- [ ] **Step 5: Commit synchronous automation safety**

```bash
git add src/aacc/models.py src/aacc/automation.py tests/test_automation.py tests/test_models.py
git commit -m "fix: serialize desktop automation transactions"
```

### Task 2: Bounded executor and API adapter

**Files:**
- Create: `src/aacc/automation_executor.py`
- Modify: `src/aacc/api.py`
- Modify: `tests/test_automation_executor.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: `AutomationExecutor(controller: Controller, capacity: int = 32)`, `submit(method: str, *args: object) -> Future[str]`, synchronous controller methods, and `close() -> None`.
- Raises: `AutomationBusyError(AutomationError)` when 32 queued/running operations are outstanding.

- [ ] **Step 1: Add failing order, overflow, close, and API timeout tests**

```python
def test_executor_preserves_submission_order() -> None:
    controller = RecordingController()
    executor = AutomationExecutor(controller)
    futures = [executor.submit("focus", task(index)) for index in range(10)]
    assert [future.result(timeout=1) for future in futures] == [str(i) for i in range(10)]
    assert controller.calls == list(range(10))
    executor.close()

def test_executor_rejects_overflow() -> None:
    executor = AutomationExecutor(BlockingController(), capacity=2)
    executor.submit("focus", task(1))
    executor.submit("focus", task(2))
    with pytest.raises(AutomationBusyError):
        executor.submit("focus", task(3))
```

- [ ] **Step 2: Prove executor tests fail**

Run: `uv run --extra dev pytest tests/test_automation_executor.py tests/test_api.py -q`

Expected: import failure for `aacc.automation_executor`.

- [ ] **Step 3: Implement one-worker bounded submission**

```python
class AutomationExecutor:
    def __init__(self, controller: Controller, capacity: int = 32) -> None:
        self._controller = controller
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aacc-automation")
        self._slots = threading.BoundedSemaphore(capacity)

    def submit(self, method: str, *args: object) -> Future[str]:
        if not self._slots.acquire(blocking=False):
            raise AutomationBusyError("Desktop automation queue is full")
        future = self._pool.submit(getattr(self._controller, method), *args)
        future.add_done_callback(lambda _future: self._slots.release())
        return future
```

Expose synchronous protocol methods that call `submit(...).result(timeout=total_timeout)` so `create_api` remains unchanged; convert `FutureTimeoutError` into `AutomationError`.

- [ ] **Step 4: Run executor/API tests**

Run: `uv run --extra dev pytest tests/test_automation_executor.py tests/test_api.py -q`

Expected: all tests pass and overflow maps to HTTP 409.

- [ ] **Step 5: Commit executor**

```bash
git add src/aacc/automation_executor.py src/aacc/api.py tests/test_automation_executor.py tests/test_api.py
git commit -m "feat: add bounded automation executor"
```

### Task 3: Nonblocking Qt action completion

**Files:**
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/app.py`
- Modify: `tests/test_gui.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `AutomationExecutor.submit` from Task 2.
- Produces: `MainWindow.automation_finished = Signal(str, str, object)` and immediate-return `_perform_action` for automation commands.

- [ ] **Step 1: Add a failing responsive-event-loop test**

```python
def test_focus_action_does_not_block_qt(qtbot, window, blocking_executor) -> None:
    fired = False
    QTimer.singleShot(0, lambda: marker.set())
    window._perform_action("focus", "task-1")
    qtbot.waitUntil(marker.is_set, timeout=100)
    assert blocking_executor.submitted == [("focus", "task-1")]
```

Also assert a completed future updates the subtitle and a failed future marks the task warning on the Qt thread.

- [ ] **Step 2: Prove GUI test fails**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_gui.py tests/test_app.py -q`

Expected: `_perform_action` blocks on the fake controller.

- [ ] **Step 3: Route only automation actions through futures and Qt signals**

```python
future = self.automation.submit(method, task, *arguments)
future.add_done_callback(
    lambda completed, action=action, task_id=task_id:
        self.automation_finished.emit(action, task_id, completed)
)
```

The signal handler calls `future.result()` on the GUI thread only after completion, updates subtitle, and emits warning state for `AutomationError`. Select, copy, and manual status remain synchronous. `Runtime.close` closes the executor after discovery and before state-store close.

- [ ] **Step 4: Run GUI/app/API slice**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_gui.py tests/test_app.py tests/test_api.py tests/test_automation_executor.py -q`

Expected: all tests pass and the Qt timer fires within 100 ms while automation is blocked.

- [ ] **Step 5: Commit nonblocking GUI integration**

```bash
git add src/aacc/gui.py src/aacc/app.py tests/test_gui.py tests/test_app.py
git commit -m "fix: keep Qt responsive during automation"
```
