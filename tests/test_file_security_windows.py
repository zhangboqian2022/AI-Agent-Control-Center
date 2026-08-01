from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aacc.config import default_config
from aacc.file_security import FileProtectionError, protect_file
from aacc.file_security_windows import (
    ADMINISTRATORS_SID,
    CONTAINER_INHERIT_ACE,
    FILE_ALL_ACCESS,
    OBJECT_INHERIT_ACE,
    SYSTEM_SID,
    WindowsSecurityApi,
    protect_windows_path,
    replace_windows_file,
)
from aacc.persistence import StateStore


class FakeWindowsSecurityApi:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.replacements: list[tuple[Path, tuple[object, ...], bool]] = []
        self.entries: tuple[tuple[object, int, int], ...] = ()
        self.verified = False

    def current_user_sid(self) -> object:
        return "CURRENT"

    def convert_sid(self, value: str) -> object:
        return value

    def replace_dacl(self, path: Path, entries: tuple[tuple[object, int, int], ...]) -> None:
        if self.error is not None:
            raise self.error
        self.entries = entries
        directory = bool(entries[0][2])
        self.replacements.append((path, tuple(entry[0] for entry in entries), directory))

    def verify_dacl(
        self,
        path: Path,
        expected_sids: tuple[object, ...],
        *,
        directory: bool,
    ) -> None:
        assert self.replacements[-1] == (path, expected_sids, directory)
        self.verified = True


def test_windows_atomic_replace_rejects_different_parent_paths(tmp_path: Path) -> None:
    source = tmp_path / "one" / "temporary"
    target = tmp_path / "two" / "target"

    with pytest.raises(FileProtectionError, match="same directory"):
        replace_windows_file(source, target)


@pytest.mark.skipif(sys.platform == "win32", reason="native helper is available on Windows")
def test_windows_atomic_replace_does_not_fallback_on_non_windows(tmp_path: Path) -> None:
    source = tmp_path / "temporary"
    target = tmp_path / "target"
    source.write_text("secret", encoding="utf-8")

    with pytest.raises(FileProtectionError, match="unavailable"):
        replace_windows_file(source, target)

    assert source.read_text(encoding="utf-8") == "secret"
    assert not target.exists()


def test_windows_atomic_replace_native_contract_retains_handle_and_buffer() -> None:
    from aacc import file_security_windows

    source = inspect.getsource(file_security_windows.replace_windows_file)
    assert "source_handle: int | None = None" in source
    assert "native_source_handle = wintypes.HANDLE(source_handle)" in source
    assert "ctypes.sizeof(FileRenameInfo) + len(file_name)" in source
    assert "os.replace(" not in source


def test_windows_file_acl_is_replaced_with_exact_protected_allowlist(
    tmp_path: Path,
) -> None:
    api = FakeWindowsSecurityApi()
    path = tmp_path / "配置 secret.yaml"
    path.write_text("", encoding="utf-8")

    protect_windows_path(path, api=api)

    assert api.replacements == [
        (
            path,
            ("CURRENT", SYSTEM_SID, ADMINISTRATORS_SID),
            False,
        )
    ]
    assert api.entries == (
        ("CURRENT", FILE_ALL_ACCESS, 0),
        (SYSTEM_SID, FILE_ALL_ACCESS, 0),
        (ADMINISTRATORS_SID, FILE_ALL_ACCESS, 0),
    )
    assert api.verified


def test_windows_directory_acl_uses_inheritable_entries(tmp_path: Path) -> None:
    api = FakeWindowsSecurityApi()

    protect_windows_path(tmp_path, directory=True, api=api)

    inheritance = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
    assert api.replacements[0][2] is True
    assert {entry[2] for entry in api.entries} == {inheritance}


def test_windows_acl_error_is_sanitized(tmp_path: Path) -> None:
    api = FakeWindowsSecurityApi(error=OSError(r"C:\secret token=abc"))

    with pytest.raises(FileProtectionError) as exc:
        protect_windows_path(tmp_path / "secret", api=api)

    assert "secret" not in str(exc.value)
    assert "abc" not in str(exc.value)
    assert "OSError" in str(exc.value)


def test_windows_acl_preserves_already_sanitized_protection_error(tmp_path: Path) -> None:
    api = FakeWindowsSecurityApi(error=FileProtectionError("safe failure"))

    with pytest.raises(FileProtectionError, match="safe failure"):
        protect_windows_path(tmp_path, api=api)


class FakeAcl:
    def __init__(self, aces: list[tuple[Any, ...]] | None = None) -> None:
        self.aces = list(aces or [])

    def AddAccessAllowedAceEx(self, _revision: int, flags: int, mask: int, sid: object) -> None:
        self.aces.append(((0, flags), mask, sid))

    def GetAceCount(self) -> int:
        return len(self.aces)

    def GetAce(self, index: int) -> tuple[Any, ...]:
        return self.aces[index]


class FakeDescriptor:
    def __init__(self, acl: FakeAcl, *, protected: bool = True) -> None:
        self.acl = acl
        self.protected = protected

    def GetSecurityDescriptorDacl(self) -> FakeAcl:
        return self.acl

    def GetSecurityDescriptorControl(self) -> tuple[int, int]:
        return (0x1000 if self.protected else 0, 1)


@dataclass
class NativeApiHarness:
    api: WindowsSecurityApi
    security: Any


def _native_api(
    monkeypatch: pytest.MonkeyPatch,
    aces: list[tuple[Any, ...]] | None = None,
    *,
    protected: bool = True,
) -> NativeApiHarness:
    security = SimpleNamespace(
        ACL=lambda: FakeAcl(),
        ACL_REVISION=2,
        ACCESS_ALLOWED_ACE_TYPE=0,
        ACCESS_DENIED_ACE_TYPE=1,
        INHERITED_ACE=0x10,
        SE_DACL_PROTECTED=0x1000,
        TokenUser=1,
        SE_FILE_OBJECT=1,
        DACL_SECURITY_INFORMATION=0x4,
        PROTECTED_DACL_SECURITY_INFORMATION=0x80000000,
        OpenProcessToken=lambda _process, _access: "TOKEN",
        GetTokenInformation=lambda _token, _kind: ("CURRENT",),
        ConvertStringSidToSid=lambda value: value,
        ConvertSidToStringSid=lambda value: str(value),
    )
    descriptor = FakeDescriptor(FakeAcl(aces), protected=protected)
    security.GetNamedSecurityInfo = lambda *_args: descriptor
    security.set_calls = []

    def set_named_security_info(*args: object) -> None:
        security.set_calls.append(args)

    security.SetNamedSecurityInfo = set_named_security_info
    modules = {
        "win32api": SimpleNamespace(GetCurrentProcess=lambda: "PROCESS"),
        "win32con": SimpleNamespace(TOKEN_QUERY=0x8),
        "win32security": security,
        "ntsecuritycon": SimpleNamespace(FILE_ALL_ACCESS=FILE_ALL_ACCESS),
        "pywintypes": SimpleNamespace(),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return NativeApiHarness(WindowsSecurityApi(), security)


def test_native_adapter_replaces_dacl_with_protected_security_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _native_api(monkeypatch)
    entries = (
        ("CURRENT", FILE_ALL_ACCESS, 0),
        (SYSTEM_SID, FILE_ALL_ACCESS, 0),
        (ADMINISTRATORS_SID, FILE_ALL_ACCESS, 0),
    )

    harness.api.replace_dacl(tmp_path, entries)

    (call,) = harness.security.set_calls
    assert call[:3] == (
        str(tmp_path),
        harness.security.SE_FILE_OBJECT,
        harness.security.DACL_SECURITY_INFORMATION
        | harness.security.PROTECTED_DACL_SECURITY_INFORMATION,
    )
    assert call[3:5] == (None, None)
    assert call[5].aces == [
        ((0, 0), FILE_ALL_ACCESS, "CURRENT"),
        ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
        ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
    ]
    assert call[6] is None


@pytest.mark.parametrize(
    ("aces", "protected", "directory"),
    [
        (
            [
                ((0, 0x10), FILE_ALL_ACCESS, "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
            ],
            True,
            False,
        ),
        (
            [
                ((1, 0), FILE_ALL_ACCESS, "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
            ],
            True,
            False,
        ),
        (
            [
                ((5, 0), FILE_ALL_ACCESS, "OBJECT", "INHERITED_OBJECT", "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
            ],
            True,
            False,
        ),
        (
            [
                ((0, 0), FILE_ALL_ACCESS, "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, "S-1-1-0"),
            ],
            True,
            False,
        ),
        (
            [
                ((0, 0), FILE_ALL_ACCESS, "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
            ],
            True,
            False,
        ),
        (
            [
                ((0, 0), FILE_ALL_ACCESS - 1, "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
            ],
            True,
            False,
        ),
        (
            [
                ((0, 0), FILE_ALL_ACCESS, "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
            ],
            False,
            False,
        ),
        (
            [
                ((0, 0), FILE_ALL_ACCESS, "CURRENT"),
                ((0, 0), FILE_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), FILE_ALL_ACCESS, ADMINISTRATORS_SID),
            ],
            True,
            True,
        ),
    ],
)
def test_native_adapter_rejects_non_exact_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    aces: list[tuple[Any, ...]],
    protected: bool,
    directory: bool,
) -> None:
    harness = _native_api(monkeypatch, aces, protected=protected)

    with pytest.raises(FileProtectionError, match="verification"):
        harness.api.verify_dacl(
            tmp_path,
            ("CURRENT", SYSTEM_SID, ADMINISTRATORS_SID),
            directory=directory,
        )


@dataclass(frozen=True)
class AclSnapshot:
    protected: bool
    allow_sids: set[str]
    deny_sids: set[str]
    inherited: bool


def _read_windows_acl(path: Path) -> AclSnapshot:
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    control, _revision = descriptor.GetSecurityDescriptorControl()
    allow_sids: set[str] = set()
    deny_sids: set[str] = set()
    inherited = False
    for index in range(dacl.GetAceCount()):
        (ace_type, flags), _mask, sid = dacl.GetAce(index)
        sid_string = win32security.ConvertSidToStringSid(sid)
        inherited = inherited or bool(flags & win32security.INHERITED_ACE)
        if ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE:
            allow_sids.add(sid_string)
        elif ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
            deny_sids.add(sid_string)
    return AclSnapshot(
        protected=bool(control & win32security.SE_DACL_PROTECTED),
        allow_sids=allow_sids,
        deny_sids=deny_sids,
        inherited=inherited,
    )


def _current_user_sid_string() -> str:
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    return win32security.ConvertSidToStringSid(sid)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ACL APIs")
def test_real_windows_acl_is_exact_and_protected(tmp_path: Path) -> None:
    path = tmp_path / "目录 with spaces" / "配置.yaml"
    path.parent.mkdir()
    path.write_text("secret", encoding="utf-8")

    protect_file(path)

    snapshot = _read_windows_acl(path)
    assert snapshot.protected
    assert snapshot.allow_sids == {
        _current_user_sid_string(),
        SYSTEM_SID,
        ADMINISTRATORS_SID,
    }
    assert snapshot.deny_sids == set()
    assert not snapshot.inherited


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ACL APIs")
def test_real_windows_acl_removes_unrelated_explicit_ace(tmp_path: Path) -> None:
    import ntsecuritycon
    import win32security

    path = tmp_path / "config.yaml"
    path.write_text("secret", encoding="utf-8")
    protect_file(path)
    descriptor = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    everyone = win32security.ConvertStringSidToSid("S-1-1-0")
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        0,
        ntsecuritycon.FILE_GENERIC_READ,
        everyone,
    )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )

    protect_file(path)

    assert "S-1-1-0" not in _read_windows_acl(path).allow_sids


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ACL APIs")
def test_real_windows_database_and_wal_sidecars_have_exact_protected_acls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "database with spaces" / "aacc.db"
    store = StateStore(path)
    try:
        store.initialize(default_config().tasks)
        candidates = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        assert all(candidate.exists() for candidate in candidates)

        expected_sids = {
            _current_user_sid_string(),
            SYSTEM_SID,
            ADMINISTRATORS_SID,
        }
        for candidate in candidates:
            snapshot = _read_windows_acl(candidate)
            assert snapshot.protected
            assert snapshot.allow_sids == expected_sids
            assert snapshot.deny_sids == set()
            assert not snapshot.inherited
    finally:
        store.close()
