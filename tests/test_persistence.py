import sqlite3
import stat
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

import aacc.persistence as persistence_module
from aacc.config import default_config
from aacc.file_security import FileProtectionError
from aacc.models import TaskState, TaskStatus
from aacc.persistence import HISTORY_CLEANUP_INTERVAL_SECONDS, StateStore


def test_state_survives_store_reopen_and_history_is_ordered(tmp_path: Path) -> None:
    path = tmp_path / "aacc.db"
    store = StateStore(path)
    store.initialize(default_config().tasks)
    store.update(TaskState.new("task-1", "running", message="one", source="api"))
    store.update(TaskState.new("task-1", "completed", message="two", source="api"))
    store.close()

    reopened = StateStore(path)
    reopened.initialize(default_config().tasks)
    assert reopened.get("task-1").status is TaskStatus.COMPLETED
    assert [item.message for item in reopened.history("task-1")] == ["one", "two"]
    reopened.close()


def test_initialize_creates_idle_state_for_each_configured_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    assert len(store.list()) == 4
    assert all(item.status is TaskStatus.IDLE for item in store.list())
    store.close()


def test_expired_history_cleanup_is_throttled_between_updates(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    cleanups: list[bool] = []
    store._delete_expired_history = lambda: cleanups.append(True)  # type: ignore[method-assign]

    store.update(TaskState.new("task-1", "running", source="api"))
    store.update(TaskState.new("task-1", "running", message="again", source="api"))
    assert cleanups == []

    # Force the throttle window to elapse relative to *now*: setting the
    # timestamp to 0.0 only works when machine uptime exceeds the interval
    # (fresh CI runners are younger than one hour).
    store._last_history_cleanup = time.monotonic() - HISTORY_CLEANUP_INTERVAL_SECONDS - 1
    store.update(TaskState.new("task-1", "completed", source="api"))
    assert cleanups == [True]
    store.close()


def test_state_history_created_at_is_indexed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    indexes = {
        row[1] for row in store._connection.execute("PRAGMA index_list('state_history')").fetchall()
    }
    assert "idx_state_history_created_at" in indexes
    store.close()


def test_history_returns_recent_rows_oldest_to_newest(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    for index in range(5):
        store.update(TaskState.new("task-1", "running", message=str(index), source="api"))

    assert [item.message for item in store.history("task-1", 2)] == ["3", "4"]
    store.close()


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced on Windows"
)
def test_database_is_private(tmp_path: Path) -> None:
    path = tmp_path / "aacc.db"
    store = StateStore(path)
    store.initialize(default_config().tasks)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    store.close()


def test_database_and_existing_sidecars_use_shared_file_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "aacc.db"
    protected: list[Path] = []
    monkeypatch.setattr(
        persistence_module,
        "protect_file",
        lambda candidate: protected.append(candidate),
        raising=False,
    )

    store = StateStore(path)
    store.initialize(default_config().tasks)

    sidecars = [Path(f"{path}-wal"), Path(f"{path}-shm")]
    assert all(sidecar.exists() for sidecar in sidecars)
    assert set([path, *sidecars]).issubset(protected)
    store.close()


def test_database_protection_failure_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeConnection:
        row_factory: object | None = None

        def __init__(self) -> None:
            self.closed = False

        def execute(self, _statement: str) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection()
    path = tmp_path / "aacc.db"
    path.touch()

    def fail_protection(_path: Path) -> None:
        raise FileProtectionError("safe database protection failure")

    monkeypatch.setattr(persistence_module.sqlite3, "connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(
        persistence_module,
        "protect_file",
        fail_protection,
        raising=False,
    )

    with pytest.raises(FileProtectionError, match="safe database protection failure"):
        StateStore(path)
    assert connection.closed


class _ConnectionProxy:
    def __init__(self, connection: sqlite3.Connection, *, close_raises: bool = False) -> None:
        self._connection = connection
        self.close_raises = close_raises
        self.closed = False

    def __enter__(self) -> "_ConnectionProxy":
        self._connection.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        return self._connection.__exit__(*args)

    def execute(self, *args: object) -> sqlite3.Cursor:
        return self._connection.execute(*args)

    def close(self) -> None:
        self.closed = True
        self._connection.close()
        if self.close_raises:
            raise RuntimeError("unsafe close detail")


@pytest.mark.parametrize("sidecar_suffix", ["-wal", "-shm"])
def test_initialize_closes_connection_when_sidecar_protection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sidecar_suffix: str,
) -> None:
    path = tmp_path / "aacc.db"
    store = StateStore(path)
    connection = _ConnectionProxy(store._connection)
    store._connection = connection  # type: ignore[assignment]
    protected_while_present: list[Path] = []

    def protect(candidate: Path) -> None:
        assert candidate.exists()
        protected_while_present.append(candidate)
        if candidate == Path(f"{path}{sidecar_suffix}"):
            raise FileProtectionError("safe sidecar protection failure")

    monkeypatch.setattr(persistence_module, "protect_file", protect)

    with pytest.raises(FileProtectionError, match="safe sidecar protection failure"):
        store.initialize(default_config().tasks)

    assert Path(f"{path}{sidecar_suffix}") in protected_while_present
    assert connection.closed


def test_initialize_preserves_protection_error_when_connection_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "aacc.db"
    store = StateStore(path)
    connection = _ConnectionProxy(store._connection, close_raises=True)
    store._connection = connection  # type: ignore[assignment]

    def fail_wal_protection(candidate: Path) -> None:
        if candidate == Path(f"{path}-wal"):
            raise FileProtectionError("safe original protection failure")

    monkeypatch.setattr(persistence_module, "protect_file", fail_wal_protection)

    with pytest.raises(FileProtectionError, match="safe original protection failure") as exc:
        store.initialize(default_config().tasks)

    assert "unsafe close detail" not in str(exc.value)
    assert connection.closed


def test_update_closes_connection_and_preserves_sidecar_protection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "aacc.db"
    store = StateStore(path)
    store.initialize(default_config().tasks)
    connection = _ConnectionProxy(store._connection, close_raises=True)
    store._connection = connection  # type: ignore[assignment]

    def fail_wal_protection(candidate: Path) -> None:
        if candidate == Path(f"{path}-wal"):
            raise FileProtectionError("safe update protection failure")

    monkeypatch.setattr(persistence_module, "protect_file", fail_wal_protection)

    with pytest.raises(FileProtectionError, match="safe update protection failure") as exc:
        store.update(TaskState.new("task-1", "running", source="api"))

    assert "unsafe close detail" not in str(exc.value)
    assert connection.closed
    assert not store._initialized


def test_read_before_initialize_does_not_create_wal_sidecars(tmp_path: Path) -> None:
    path = tmp_path / "aacc.db"
    initialized = StateStore(path)
    initialized.initialize(default_config().tasks)
    initialized.close()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()

    uninitialized = StateStore(path)
    with pytest.raises(RuntimeError, match="not initialized"):
        uninitialized.get("task-1")

    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()
    uninitialized.close()


def test_heartbeat_updates_current_without_growing_history(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    current = store.update(TaskState.new("task-1", "running", message="working", source="api"))
    heartbeat = current.model_copy(update={"updated_at": current.updated_at + timedelta(minutes=1)})

    store.heartbeat(heartbeat)

    assert store.get("task-1").updated_at == heartbeat.updated_at
    assert len(store.history("task-1")) == 1
    store.close()


def test_history_retains_at_most_one_thousand_rows_per_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    for index in range(1_001):
        store.update(TaskState.new("task-1", "running", message=str(index), source="api"))

    history = store.history("task-1", 1_000)
    assert len(history) == 1_000
    assert history[0].message == "1"
    assert history[-1].message == "1000"
    store.close()


def test_locked_operation_retries_three_times(tmp_path: Path) -> None:
    delays: list[float] = []
    store = StateStore(tmp_path / "aacc.db", sleeper=delays.append)
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise sqlite3.OperationalError("database is locked")
        return "saved"

    assert store._retry_locked(operation) == "saved"
    assert delays == [0.05, 0.1, 0.2]
    store.close()
