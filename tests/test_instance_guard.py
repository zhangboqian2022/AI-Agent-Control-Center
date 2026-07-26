import stat
import sys
import types
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


def test_acquire_uses_msvcrt_on_windows(tmp_path, monkeypatch) -> None:
    import aacc.instance_guard as guard_mod

    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[tuple[int, int, int]] = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 0

        @staticmethod
        def locking(fd: int, mode: int, nbytes: int) -> None:
            calls.append((fd, mode, nbytes))

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    guard = guard_mod.InstanceGuard(tmp_path / "aacc.lock")
    assert guard.acquire() is True
    assert calls and calls[0][1] == FakeMsvcrt.LK_NBLCK
    guard.close()
    assert calls[-1][1] == FakeMsvcrt.LK_UNLCK


def test_acquire_windows_conflict_returns_false(tmp_path, monkeypatch) -> None:
    import aacc.instance_guard as guard_mod

    monkeypatch.setattr(sys, "platform", "win32")

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 0

        @staticmethod
        def locking(fd: int, mode: int, nbytes: int) -> None:
            raise OSError("locked")

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    assert guard_mod.InstanceGuard(tmp_path / "aacc.lock").acquire() is False


def test_activate_existing_instance_windows(tmp_path, monkeypatch) -> None:
    import aacc.instance_guard as guard_mod

    monkeypatch.setattr(sys, "platform", "win32")
    focused: list[int] = []
    fake_win32 = types.SimpleNamespace(
        find_window_by_title=lambda title: 42 if title == "AACC" else None,
        focus_window=lambda hwnd: focused.append(hwnd) or True,
    )
    # aacc.win32 is importable off-Windows now, so ``from aacc import win32``
    # resolves via the cached package attribute; patch that attribute.
    monkeypatch.setattr("aacc.win32", fake_win32, raising=False)
    guard_mod.activate_existing_instance()
    assert focused == [42]
