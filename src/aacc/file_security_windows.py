from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol

from aacc.file_security import FileProtectionError

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
OBJECT_INHERIT_ACE = 0x1
CONTAINER_INHERIT_ACE = 0x2
FILE_ALL_ACCESS = 0x001F01FF


class SecurityApi(Protocol):
    def current_user_sid(self) -> Any: ...

    def convert_sid(self, value: str) -> Any: ...

    def replace_dacl(self, path: Path, entries: tuple[tuple[Any, int, int], ...]) -> None: ...

    def verify_dacl(
        self, path: Path, expected_sids: tuple[Any, ...], *, directory: bool
    ) -> None: ...


class WindowsSecurityApi:
    """Narrow, lazily loaded adapter around the Windows security APIs."""

    def __init__(self) -> None:
        self._win32api: Any = importlib.import_module("win32api")
        self._win32con: Any = importlib.import_module("win32con")
        self._win32security: Any = importlib.import_module("win32security")
        self._ntsecuritycon: Any = importlib.import_module("ntsecuritycon")
        self._pywintypes: Any = importlib.import_module("pywintypes")

    def current_user_sid(self) -> Any:
        token = self._win32security.OpenProcessToken(
            self._win32api.GetCurrentProcess(),
            self._win32con.TOKEN_QUERY,
        )
        try:
            return self._win32security.GetTokenInformation(
                token,
                self._win32security.TokenUser,
            )[0]
        finally:
            close = getattr(token, "Close", None)
            if close is not None:
                close()

    def convert_sid(self, value: str) -> Any:
        return self._win32security.ConvertStringSidToSid(value)

    def replace_dacl(
        self,
        path: Path,
        entries: tuple[tuple[Any, int, int], ...],
    ) -> None:
        dacl = self._win32security.ACL()
        for sid, mask, flags in entries:
            dacl.AddAccessAllowedAceEx(
                self._win32security.ACL_REVISION,
                flags,
                mask,
                sid,
            )
        security_information = (
            self._win32security.DACL_SECURITY_INFORMATION
            | self._win32security.PROTECTED_DACL_SECURITY_INFORMATION
        )
        self._win32security.SetNamedSecurityInfo(
            str(path),
            self._win32security.SE_FILE_OBJECT,
            security_information,
            None,
            None,
            dacl,
            None,
        )

    def verify_dacl(
        self,
        path: Path,
        expected_sids: tuple[Any, ...],
        *,
        directory: bool,
    ) -> None:
        descriptor = self._win32security.GetNamedSecurityInfo(
            str(path),
            self._win32security.SE_FILE_OBJECT,
            self._win32security.DACL_SECURITY_INFORMATION,
        )
        control, _revision = descriptor.GetSecurityDescriptorControl()
        if not control & self._win32security.SE_DACL_PROTECTED:
            raise FileProtectionError("Windows ACL verification failed")
        dacl = descriptor.GetSecurityDescriptorDacl()
        if dacl is None:
            raise FileProtectionError("Windows ACL verification failed")

        expected = {self._win32security.ConvertSidToStringSid(sid) for sid in expected_sids}
        expected_flags = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if directory else 0
        seen: set[str] = set()
        for index in range(dacl.GetAceCount()):
            try:
                ace = dacl.GetAce(index)
            except NotImplementedError:
                raise FileProtectionError("Windows ACL verification failed") from None
            if len(ace) != 3:
                raise FileProtectionError("Windows ACL verification failed")
            (ace_type, flags), mask, sid = ace
            sid_string = self._win32security.ConvertSidToStringSid(sid)
            if (
                ace_type != self._win32security.ACCESS_ALLOWED_ACE_TYPE
                or flags & self._win32security.INHERITED_ACE
                or flags != expected_flags
                or mask != self._ntsecuritycon.FILE_ALL_ACCESS
                or sid_string not in expected
                or sid_string in seen
            ):
                raise FileProtectionError("Windows ACL verification failed")
            seen.add(sid_string)
        if seen != expected:
            raise FileProtectionError("Windows ACL verification failed")


def protect_windows_path(
    path: Path,
    *,
    directory: bool = False,
    api: SecurityApi | None = None,
) -> None:
    resolved = api or WindowsSecurityApi()
    try:
        user = resolved.current_user_sid()
        system = resolved.convert_sid(SYSTEM_SID)
        administrators = resolved.convert_sid(ADMINISTRATORS_SID)
        flags = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if directory else 0
        entries = tuple((sid, FILE_ALL_ACCESS, flags) for sid in (user, system, administrators))
        resolved.replace_dacl(path, entries)
        resolved.verify_dacl(
            path,
            (user, system, administrators),
            directory=directory,
        )
    except Exception as error:
        if isinstance(error, FileProtectionError):
            raise
        raise FileProtectionError(
            f"Windows credential protection failed ({type(error).__name__})"
        ) from None
