import stat
from pathlib import Path

from aacc.instance_guard import InstanceGuard


def test_only_one_guard_can_hold_lock_and_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "aacc.lock"
    first = InstanceGuard(path)
    second = InstanceGuard(path)

    assert first.acquire()
    assert not second.acquire()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    first.close()
    assert second.acquire()
    second.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    guard = InstanceGuard(tmp_path / "aacc.lock")
    guard.close()
    assert guard.acquire()
    guard.close()
    guard.close()
