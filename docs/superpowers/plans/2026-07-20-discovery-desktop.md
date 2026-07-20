# Discovery Health and macOS Desktop Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Codex discovery observable and identity-safe, and make macOS permission, process, and single-instance behavior explicit.

**Architecture:** Discovery publishes immutable health snapshots without discarding last-known task state. Small platform services own Accessibility checks and instance locking; the GUI only renders their results and opens System Settings on request.

**Tech Stack:** Python dataclasses/threading/fcntl/signal, psutil, PyObjC Quartz, PySide6, pytest/pytest-qt.

## Global Constraints

- Target release is `v1.3.0-rc.1`; supported operating systems are macOS 13 or newer.
- Discovery degrades after 3 consecutive failures and recovers only after 2 consecutive successful polls.
- Diagnostic UI contains no raw traceback, token, prompt, response, or conversation content.
- If a process record has a start time and live start time is unavailable, PID identity is rejected.
- No claim may promise child cleanup after `SIGKILL`.

---

### Task 1: Strict Codex PID identity and parser compatibility

**Files:**
- Modify: `src/aacc/codex_discovery.py`
- Create: `tests/fixtures/codex/session_index-current.jsonl`
- Create: `tests/fixtures/codex/session-current.jsonl`
- Modify: `tests/test_codex_discovery.py`

**Interfaces:**
- Produces: `CODEX_METADATA_COMPATIBILITY = "2026-07"`; `_active_pids` is default-deny when identity cannot be verified.

- [ ] **Step 1: Add failing missing-create-time and fixture tests**

```python
def test_pid_with_record_start_is_rejected_when_live_start_unknown(discovery) -> None:
    discovery.process_started_at = lambda _pid: None
    assert discovery._active_pids({"conversation-1"}) == {}

def test_current_metadata_fixtures(discovery_from_current_fixtures) -> None:
    tasks = discovery_from_current_fixtures.discover({"conversation-1"})
    assert tasks[0].state.status is TaskStatus.COMPLETED
```

- [ ] **Step 2: Prove strict identity test fails**

Run: `uv run --extra dev pytest tests/test_codex_discovery.py -q`

Expected: current implementation incorrectly accepts the PID.

- [ ] **Step 3: Split and tighten process matching**

```python
if isinstance(record_started, int):
    if process_started is None:
        continue
    process_matches = abs(process_started - record_started) <= 60_000
else:
    process_matches = True
```

Add the compatibility constant next to the parser class and fixture records containing metadata only, with no prompt/response text.

- [ ] **Step 4: Run discovery parser tests**

Run: `uv run --extra dev pytest tests/test_codex_discovery.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit PID and parser contract**

```bash
git add src/aacc/codex_discovery.py tests/test_codex_discovery.py tests/fixtures/codex
git commit -m "fix: verify Codex process identity"
```

### Task 2: Discovery health state and warning banner

**Files:**
- Modify: `src/aacc/discovery_service.py`
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/app.py`
- Modify: `tests/test_discovery_service.py`
- Modify: `tests/test_gui.py`

**Interfaces:**
- Produces: frozen `DiscoveryHealth(degraded, consecutive_failures, consecutive_successes, diagnostic_id, summary, last_failure_at, last_success_at)` and `subscribe_health(callback) -> unsubscribe`.
- Produces: `CodexDiscoveryError`, raised immediately when an existing required index is unreadable; a genuinely absent index remains an empty first-run catalog.
- GUI consumes health through `discovery_health_received = Signal(object)`.

- [ ] **Step 1: Add failing degradation, recovery, cooldown, and banner tests**

```python
def test_health_degrades_after_three_failures_and_recovers_after_two_successes(service) -> None:
    service.discovery.discover.side_effect = [OSError("broken"), OSError("broken"),
                                              OSError("broken"), [], []]
    for _ in range(3):
        service.poll_safely()
    assert service.health().degraded
    service.poll_safely(); assert service.health().degraded
    service.poll_safely(); assert not service.health().degraded

def test_existing_unreadable_index_degrades_immediately(service) -> None:
    service.discovery.discover.side_effect = CodexDiscoveryError("session index unreadable")
    service.poll_safely()
    assert service.health().degraded
```

The GUI test emits a degraded snapshot and asserts a visible `#discoveryWarning`, an 80-character-or-shorter summary, and clipboard diagnostics containing the diagnostic ID but not `traceback`.

- [ ] **Step 2: Prove service and GUI tests fail**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_discovery_service.py tests/test_gui.py -q`

Expected: health interfaces and warning banner are absent.

- [ ] **Step 3: Implement redacted health snapshots and safe polling**

```python
@dataclass(frozen=True)
class DiscoveryHealth:
    degraded: bool = False
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    diagnostic_id: str = ""
    summary: str = ""
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None
```

`CodexLocalDiscovery._sessions` raises `CodexDiscoveryError` when the index exists but cannot be read, while a missing path remains an empty catalog. `_run` calls `poll_safely`; errors are logged through `aacc.discovery` with a stable hash diagnostic ID and 60-second identical-error cooldown. `CodexDiscoveryError` degrades immediately; other failures use the three-failure threshold. Failure keeps current tasks. `MainWindow` renders a yellow frame above cards and copies only counters, timestamps, diagnostic ID, redacted class/message, and log path.

- [ ] **Step 4: Run discovery/GUI tests**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_discovery_service.py tests/test_gui.py -q`

Expected: all tests pass; two successful polls hide the banner.

- [ ] **Step 5: Commit discovery observability**

```bash
git add src/aacc/discovery_service.py src/aacc/gui.py src/aacc/app.py tests/test_discovery_service.py tests/test_gui.py
git commit -m "feat: expose Codex discovery health"
```

### Task 3: Accessibility guidance and hotkey recovery

**Files:**
- Create: `src/aacc/accessibility.py`
- Modify: `src/aacc/automation.py`
- Modify: `src/aacc/hotkeys.py`
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/app.py`
- Create: `tests/test_accessibility.py`
- Modify: `tests/test_automation.py`
- Modify: `tests/test_hotkeys.py`
- Modify: `tests/test_gui.py`

**Interfaces:**
- Produces: `is_accessibility_trusted(prompt: bool = False) -> bool`, `open_accessibility_settings() -> None`, and `GlobalHotkeys.available: bool`.
- Modifies: `MacAutomation._ensure_injection()` checks an injected `Callable[[], bool]`, so both GUI and API commands are rejected before System Events injection when trust is missing.

- [ ] **Step 1: Add failing platform and disabled-tap tests**

```python
def test_disabled_event_tap_is_reenabled(quartz, hotkeys) -> None:
    event = object()
    result = hotkeys._callback(None, quartz.kCGEventTapDisabledByTimeout, event, None)
    quartz.CGEventTapEnable.assert_called_once_with(hotkeys._tap, True)
    assert result is event
```

Add GUI tests proving missing trust disables injection commands, shows a permission message once, and the button invokes the injected settings opener. Add an automation test asserting `send_key`, `send_text`, and `start_voice` raise `AutomationError("Accessibility permission is required")` without calling the subprocess runner.

- [ ] **Step 2: Prove Accessibility tests fail**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_accessibility.py tests/test_automation.py tests/test_hotkeys.py tests/test_gui.py -q`

Expected: module and recovery callback are absent.

- [ ] **Step 3: Implement trust check, settings deep link, and recovery**

```python
def is_accessibility_trusted(prompt: bool = False) -> bool:
    import Quartz
    options = {Quartz.kAXTrustedCheckOptionPrompt: prompt}
    return bool(Quartz.AXIsProcessTrustedWithOptions(options))

def open_accessibility_settings() -> None:
    subprocess.run(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                   check=False, timeout=5)
```

Store the event tap on `GlobalHotkeys`; re-enable it on both disabled-by-timeout and disabled-by-user-input event types. App startup passes trust state and opener into `MainWindow`; settings exposes an actionable permission row and marks hotkeys unavailable. `MacAutomation` receives `accessibility_trusted=is_accessibility_trusted` and checks it inside `_ensure_injection`, covering API calls as well as GUI actions.

- [ ] **Step 4: Run Accessibility/hotkey/GUI tests**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_accessibility.py tests/test_automation.py tests/test_hotkeys.py tests/test_gui.py -q`

Expected: all tests pass with Quartz mocked.

- [ ] **Step 5: Commit macOS permission guidance**

```bash
git add src/aacc/accessibility.py src/aacc/automation.py src/aacc/hotkeys.py src/aacc/gui.py src/aacc/app.py tests/test_accessibility.py tests/test_automation.py tests/test_hotkeys.py tests/test_gui.py
git commit -m "feat: guide Accessibility permission setup"
```

### Task 4: Single instance, wrapper signal cleanup, and adapter sentinel

**Files:**
- Create: `src/aacc/instance_guard.py`
- Modify: `src/aacc/app.py`
- Modify: `src/aacc/run_wrapper.py`
- Modify: `src/aacc/adapters.py`
- Create: `tests/test_instance_guard.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_adapters.py`

**Interfaces:**
- Produces: `InstanceGuard.acquire() -> bool`, `InstanceGuard.close() -> None`, `terminate_process(process, timeout=3.0) -> None`.

- [ ] **Step 1: Add failing lock, signal, and disconnect tests**

```python
async def test_disconnect_unblocks_events(adapter) -> None:
    await adapter.connect()
    consumer = asyncio.create_task(anext(adapter.events()))
    await adapter.disconnect()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(consumer, timeout=0.1)
```

Add a same-lock-path test where the second `InstanceGuard.acquire()` returns `False`, and a fake `Popen` test asserting terminate, 3-second wait, kill after timeout, and final reap.

- [ ] **Step 2: Prove lifecycle tests fail**

Run: `uv run --extra dev pytest tests/test_instance_guard.py tests/test_app.py tests/test_cli.py tests/test_adapters.py -q`

Expected: instance module missing, signal cleanup absent, adapter consumer times out.

- [ ] **Step 3: Implement flock, cooperative signals, and queue sentinel**

```python
class InstanceGuard:
    def acquire(self) -> bool:
        self._handle = self.path.open("a+")
        try:
            fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            self._handle.close()
            return False
```

Acquire before Runtime construction; a second launch runs `/usr/bin/open -b com.aacc.controlcenter` with a five-second timeout and exits zero. The wrapper signal handler only sets a `threading.Event`; the main wait loop terminates the held `Popen`, waits three seconds, kills on timeout, and reaps. The adapter queue accepts `TaskState | object`; `disconnect` enqueues a private sentinel and `events` returns when it is received.

- [ ] **Step 4: Run process/adapter tests**

Run: `uv run --extra dev pytest tests/test_instance_guard.py tests/test_app.py tests/test_cli.py tests/test_adapters.py -q`

Expected: all tests pass, including the timeout/kill fallback.

- [ ] **Step 5: Commit desktop lifecycle hardening**

```bash
git add src/aacc/instance_guard.py src/aacc/app.py src/aacc/run_wrapper.py src/aacc/adapters.py tests/test_instance_guard.py tests/test_app.py tests/test_cli.py tests/test_adapters.py
git commit -m "fix: harden desktop process lifecycle"
```
