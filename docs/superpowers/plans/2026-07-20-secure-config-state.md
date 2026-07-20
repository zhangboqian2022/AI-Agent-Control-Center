# Secure Configuration and State Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make credentials and state persistence secure, crash-safe, semantically deduplicated, and bounded.

**Architecture:** `aacc.config` owns all YAML persistence and token rotation. `StateMachine.transition` normalizes accepted candidates before `TaskManager` asks `StateStore` to persist either a business change or a throttled heartbeat.

**Tech Stack:** Python 3.12+, Pydantic 2, PyYAML, SQLite, pytest.

## Global Constraints

- Target release is `v1.3.0-rc.1`; supported operating systems are macOS 13 or newer.
- Use same-directory atomic replacement, never follow a configuration-file symlink, and force app data/config/database modes to `0700`/`0600`.
- Generate credentials only with `secrets.token_urlsafe(32)` and never log or persist a placeholder token.
- State history keeps no more than 1,000 rows per task and no rows older than 30 days.
- Every behavior change starts with a failing regression test; focused modified-line coverage must be at least 90%.

---

### Task 1: Versioned atomic configuration service

**Files:**
- Modify: `src/aacc/models.py`
- Modify: `src/aacc/config.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Produces: `CURRENT_CONFIG_VERSION: int`, `is_valid_token(value: str) -> bool`, `save_config(path: Path, config: AppConfig) -> None`, `rotate_api_token(path: Path, config: AppConfig) -> str`.
- `load_config(path: Path) -> AppConfig` remains the public load entrypoint and migrates version `0` mappings to version `1`.

- [ ] **Step 1: Add failing validation, migration, permission, symlink, atomicity, and rotation tests**

```python
def test_load_repairs_empty_token_and_permissions(config_path: Path) -> None:
    config_path.write_text("app:\n  api:\n    token: ''\n", encoding="utf-8")
    config = load_config(config_path)
    assert is_valid_token(config.app.api.token)
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert yaml.safe_load(config_path.read_text())["config_version"] == 1

def test_load_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "config.yaml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        load_config(link)

def test_rotate_token_updates_same_object_and_disk(config_path: Path) -> None:
    config = create_default_config(config_path)
    old = config.app.api.token
    new = rotate_api_token(config_path, config)
    assert new != old
    assert config.app.api.token == new
    assert load_config(config_path).app.api.token == new
```

- [ ] **Step 2: Prove the new tests fail**

Run: `uv run --extra dev pytest tests/test_config.py tests/test_models.py -q`

Expected: failures for missing `config_version`, `save_config`, token repair, symlink rejection, and rotation.

- [ ] **Step 3: Implement strict persistence and migration**

```python
CURRENT_CONFIG_VERSION = 1

def is_valid_token(value: str) -> bool:
    return len(value) >= 32 and not value.isspace() and value.isprintable() and value not in {
        "change-me", "replace-me", "your-token-here"
    }

def save_config(path: Path, config: AppConfig) -> None:
    _reject_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, raw_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config.model_dump(mode="json"), handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
```

`load_config` validates/migrates the mapping, repairs an invalid token, calls `save_config` when repair/migration is needed, and chmods a valid existing regular file. `AppConfig` gains `config_version: int = 1`.

- [ ] **Step 4: Run focused tests and coverage**

Run: `uv run --extra dev pytest tests/test_config.py tests/test_models.py --cov=aacc.config --cov=aacc.models --cov-report=term-missing -q`

Expected: all tests pass and changed executable lines are at least 90% covered.

- [ ] **Step 5: Commit secure configuration**

```bash
git add src/aacc/models.py src/aacc/config.py tests/test_config.py tests/test_models.py
git commit -m "fix: harden configuration persistence"
```

### Task 2: Lifecycle-preserving semantic transitions

**Files:**
- Modify: `src/aacc/state_machine.py`
- Modify: `src/aacc/task_manager.py`
- Modify: `tests/test_state_machine.py`
- Modify: `tests/test_task_manager.py`

**Interfaces:**
- Produces: `StateMachine.transition(current: TaskState | None, candidate: TaskState) -> TaskState | None`.
- Produces: `StateMachine.heartbeat_due(current: TaskState, candidate: TaskState) -> bool`, true only for a semantic duplicate observed at least 60 seconds later.
- Consumes: `StateStore.update(state, *, append_history: bool = True)` from Task 3.

- [ ] **Step 1: Add failing lifecycle and duplicate tests**

```python
def test_transition_preserves_run_start_and_finishes() -> None:
    started = TaskState.new("task-1", "RUNNING", source="codex_local")
    waiting = TaskState.new("task-1", "WAITING_INPUT", source="codex_local")
    accepted = StateMachine.transition(started, waiting)
    assert accepted is not None and accepted.started_at == started.started_at
    completed = StateMachine.transition(accepted, TaskState.new("task-1", "COMPLETED"))
    assert completed is not None
    assert completed.started_at == started.started_at
    assert completed.finished_at is not None

def test_semantic_duplicate_returns_none() -> None:
    current = TaskState.new("task-1", "RUNNING", message="working", source="codex_local")
    candidate = current.model_copy(update={"updated_at": current.updated_at})
    assert StateMachine.transition(current, candidate) is None

def test_duplicate_becomes_heartbeat_only_after_one_minute() -> None:
    current = TaskState.new("task-1", "RUNNING", message="working", source="codex_local")
    early = current.model_copy(update={"updated_at": current.updated_at + timedelta(seconds=59)})
    due = current.model_copy(update={"updated_at": current.updated_at + timedelta(seconds=60)})
    assert not StateMachine.heartbeat_due(current, early)
    assert StateMachine.heartbeat_due(current, due)
```

- [ ] **Step 2: Prove lifecycle tests fail**

Run: `uv run --extra dev pytest tests/test_state_machine.py tests/test_task_manager.py -q`

Expected: failures because `transition` does not exist and repeated registration writes history.

- [ ] **Step 3: Implement normalized transitions and no-op updates**

```python
@classmethod
def transition(cls, current: TaskState | None, candidate: TaskState) -> TaskState | None:
    if current is not None and not cls.accept(current, candidate):
        return None
    normalized = cls._apply_lifecycle(current, candidate)
    if current is not None and cls._semantic_key(current) == cls._semantic_key(normalized):
        return None
    return normalized
```

`_semantic_key` includes status, message, source, confidence, PID, session ID, metadata, and the source-event timestamp stored in metadata. `_apply_lifecycle` preserves start time through nonterminal states, starts a new run after idle/terminal, and carries start time into terminal states. When `transition` returns `None`, `TaskManager.update` calls `StateMachine.heartbeat_due`; a due heartbeat updates only `current_states` with the candidate observation time and does not notify subscribers, while an early duplicate performs no write.

- [ ] **Step 4: Run state-machine and manager tests**

Run: `uv run --extra dev pytest tests/test_state_machine.py tests/test_task_manager.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit lifecycle changes**

```bash
git add src/aacc/state_machine.py src/aacc/task_manager.py tests/test_state_machine.py tests/test_task_manager.py
git commit -m "fix: preserve task lifecycle transitions"
```

### Task 3: Bounded resilient SQLite history

**Files:**
- Modify: `src/aacc/persistence.py`
- Modify: `tests/test_persistence.py`
- Modify: `tests/test_task_manager.py`

**Interfaces:**
- Produces: `StateStore.update(state: TaskState, *, append_history: bool = True) -> TaskState` and `StateStore.heartbeat(state: TaskState) -> TaskState`.
- `history(task_id, limit)` returns the most recent `limit` entries in chronological order.

- [ ] **Step 1: Add failing database-mode, retry, recency, and retention tests**

```python
def test_history_returns_recent_rows_oldest_to_newest(store: StateStore) -> None:
    for index in range(5):
        store.update(TaskState.new("task-1", "RUNNING", message=str(index)))
    assert [item.message for item in store.history("task-1", 2)] == ["3", "4"]

def test_database_is_private(database_path: Path, tasks: list[TaskConfig]) -> None:
    store = StateStore(database_path)
    store.initialize(tasks)
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
```

Add a controlled fake connection test that raises `sqlite3.OperationalError("database is locked")` twice and succeeds on the third call; assert sleeps of `0.05` and `0.1` seconds.

- [ ] **Step 2: Prove persistence tests fail**

Run: `uv run --extra dev pytest tests/test_persistence.py tests/test_task_manager.py -q`

Expected: recent-history and private-mode assertions fail.

- [ ] **Step 3: Implement busy timeout, exponential retry, index, heartbeat, and cleanup**

```python
RETRY_DELAYS = (0.05, 0.1, 0.2)
MAX_HISTORY_PER_TASK = 1_000
HISTORY_DAYS = 30

def _retry_locked(self, operation: Callable[[], T]) -> T:
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or delay is None:
                raise
            self._sleep(delay)
    raise AssertionError("unreachable")
```

Set `PRAGMA busy_timeout=3000`, create `idx_state_history_task_id_id`, chmod the database after connection, select recent rows through a descending subquery and reverse them for presentation, delete rows older than 30 days, and retain only the newest 1,000 IDs per task. A heartbeat only updates `current_states`.

- [ ] **Step 4: Run focused tests and the full state slice**

Run: `uv run --extra dev pytest tests/test_persistence.py tests/test_state_machine.py tests/test_task_manager.py -q`

Expected: all tests pass; 1,001 inserts retain exactly 1,000 history entries.

- [ ] **Step 5: Commit persistence hardening**

```bash
git add src/aacc/persistence.py tests/test_persistence.py tests/test_task_manager.py
git commit -m "fix: bound and harden state history"
```

### Task 4: Local GUI credential rotation

**Files:**
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/app.py`
- Modify: `tests/test_gui.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `rotate_api_token(path: Path, config: AppConfig) -> str` from Task 1.
- Produces: settings action `MainWindow.rotate_credentials()`; no HTTP rotation endpoint is added.

- [ ] **Step 1: Add failing confirmation, cancellation, and live-token tests**

```python
def test_rotate_credentials_updates_live_api_config_and_clipboard(qtbot, window, monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    old = window.config.app.api.token
    window.rotate_credentials()
    assert window.config.app.api.token != old
    assert QGuiApplication.clipboard().text() == window.config.app.api.token

def test_cancel_rotation_keeps_token(window, monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Cancel)
    old = window.config.app.api.token
    window.rotate_credentials()
    assert window.config.app.api.token == old
```

The API test reuses the same mutable `AppConfig`, rotates it through the injected callback, and proves the old bearer token returns 401 while the new token succeeds.

- [ ] **Step 2: Prove GUI rotation tests fail**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_gui.py tests/test_app.py tests/test_api.py -q`

Expected: `rotate_credentials` and its settings button are absent.

- [ ] **Step 3: Inject and expose the local rotation callback**

```python
self._rotate_api_token = rotate_api_token_callback

def rotate_credentials(self) -> None:
    answer = QMessageBox.question(self, "重置凭证", "旧凭证会立即失效，是否继续？",
                                  QMessageBox.Cancel | QMessageBox.Yes,
                                  QMessageBox.Cancel)
    if answer != QMessageBox.Yes:
        return
    token = self._rotate_api_token()
    QGuiApplication.clipboard().setText(token)
    QMessageBox.information(self, "凭证已重置", "新凭证已复制；旧凭证已失效。")
```

`build_runtime` stores `config_path`; `MainWindow` receives `lambda: rotate_api_token(runtime.config_path, runtime.config)`. Settings adds a **重置 API 凭证** button. Do not add `/api/v1/auth/rotate` and do not keep the old token.

- [ ] **Step 4: Run config/GUI/API tests**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_config.py tests/test_gui.py tests/test_app.py tests/test_api.py -q`

Expected: all tests pass; permissions remain `0600` after rotation.

- [ ] **Step 5: Commit local token rotation**

```bash
git add src/aacc/gui.py src/aacc/app.py tests/test_gui.py tests/test_app.py tests/test_api.py
git commit -m "feat: add local credential rotation"
```
