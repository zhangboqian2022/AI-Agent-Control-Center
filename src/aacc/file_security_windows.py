from __future__ import annotations

import importlib
import ntpath
import os
from pathlib import Path
from typing import Any, Protocol, cast

from aacc.file_security import FileProtectionError

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
OBJECT_INHERIT_ACE = 0x1
CONTAINER_INHERIT_ACE = 0x2
FILE_ALL_ACCESS = 0x001F01FF


def _native_handle_value(handle: Any) -> int | None:
    value = getattr(handle, "value", handle)
    return None if value is None else int(value)


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


def open_windows_replaceable_text(path: Path) -> Any:
    """Open a sensitive temporary file with DELETE/share-delete semantics.

    The default Python/MSVCRT text open does not promise DELETE access on the
    underlying handle. Windows therefore cannot publish that still-open file
    with ``FILE_RENAME_INFO``. This adapter keeps the handle usable by Python
    while granting the native rename rights explicitly.
    """
    if os.name != "nt":
        raise FileProtectionError("Windows replaceable file access is unavailable on this platform")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    msvcrt_api: Any = msvcrt
    os_api: Any = cast(Any, os)

    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise FileProtectionError("Windows replaceable file access is unavailable on this platform")
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_delete = 0x00010000
    file_share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    invalid_handle = ctypes.c_void_p(-1).value

    native_handle = kernel32.CreateFileW(
        os.fspath(path),
        generic_read | generic_write | file_delete,
        file_share_all,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    native_value = _native_handle_value(native_handle)
    if native_value in (None, -1, invalid_handle):
        raise FileProtectionError("Windows replaceable file access failed")
    try:
        descriptor = msvcrt_api.open_osfhandle(
            native_value,
            os.O_RDWR | os_api.O_BINARY,
        )
    except Exception:
        kernel32.CloseHandle(native_handle)
        raise FileProtectionError("Windows replaceable file access failed") from None
    try:
        return os.fdopen(descriptor, "w", encoding="utf-8")
    except Exception:
        os.close(descriptor)
        raise FileProtectionError("Windows replaceable file access failed") from None


def replace_windows_file(
    source: Path,
    target: Path,
    *,
    source_handle: int | None = None,
) -> None:
    """Replace a sibling file through an opened Windows directory handle.

    ``os.replace`` resolves both paths afresh.  On Windows that leaves a
    same-user writer exposed to a directory-reparse-point swap between the
    protection checks and publication.  ``FILE_RENAME_INFO.RootDirectory``
    makes the final rename relative to the directory handle that was opened
    with reparse-point traversal disabled.
    """
    source_parent = os.path.normcase(os.path.abspath(os.fspath(source.parent)))
    target_parent = os.path.normcase(os.path.abspath(os.fspath(target.parent)))
    if source_parent != target_parent:
        raise FileProtectionError("Windows atomic replacement requires files in the same directory")
    source_name = source.name
    target_name = target.name
    if (
        not source_name
        or not target_name
        or any(separator in source_name or separator in target_name for separator in ("/", "\\"))
    ):
        raise FileProtectionError("Windows atomic replacement requires simple file names")
    if os.name != "nt":
        raise FileProtectionError("Windows atomic replacement is unavailable on this platform")

    import ctypes
    from ctypes import wintypes

    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise FileProtectionError("Windows atomic replacement is unavailable on this platform")
    kernel32 = win_dll("kernel32", use_last_error=True)
    invalid_handle = ctypes.c_void_p(-1).value
    file_share_all = 0x00000001 | 0x00000002 | 0x00000004
    file_read_attributes = 0x00000080
    file_list_directory = 0x00000001
    file_add_file = 0x00000002
    file_delete = 0x00010000
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    open_existing = 3
    file_attribute_reparse_point = 0x00000400
    file_rename_info = 3
    file_attribute_tag_info = 9

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetLongPathNameW.restype = wintypes.DWORD

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        ]

    class ByHandleFileInfo(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    handle_alignment = ctypes.alignment(wintypes.HANDLE)

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", wintypes.BOOLEAN),
            ("_padding", ctypes.c_ubyte * (handle_alignment - 1)),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]

    def fail() -> FileProtectionError:
        return FileProtectionError(
            f"Windows atomic replacement failed (winerror={cast(Any, ctypes).get_last_error()})"
        )

    def open_handle(path: Path, access: int, flags: int) -> Any:
        handle = kernel32.CreateFileW(
            os.fspath(path),
            access,
            file_share_all,
            None,
            open_existing,
            flags,
            None,
        )
        if _native_handle_value(handle) in (None, -1, invalid_handle):
            raise fail()
        return handle

    def reject_reparse(handle: Any) -> None:
        attributes = FileAttributeTagInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            file_attribute_tag_info,
            ctypes.byref(attributes),
            ctypes.sizeof(attributes),
        ):
            raise fail()
        if attributes.file_attributes & file_attribute_reparse_point:
            raise FileProtectionError("Windows atomic replacement rejected a reparse point")

    def file_identity(handle: Any) -> tuple[int, int, int]:
        information = ByHandleFileInfo()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise fail()
        return (
            int(information.volume_serial_number),
            int(information.file_index_high),
            int(information.file_index_low),
        )

    def final_path(handle: Any) -> str:
        capacity = 260
        for _ in range(3):
            buffer = ctypes.create_unicode_buffer(capacity)
            length = kernel32.GetFinalPathNameByHandleW(handle, buffer, capacity, 0)
            if length == 0:
                raise fail()
            if length < capacity:
                return buffer.value
            capacity = length + 1
        raise fail()

    def long_path(path: str) -> str:
        required = kernel32.GetLongPathNameW(path, None, 0)
        if required == 0:
            raise fail()
        for _ in range(3):
            buffer = ctypes.create_unicode_buffer(required + 1)
            length = kernel32.GetLongPathNameW(path, buffer, len(buffer))
            if length == 0:
                raise fail()
            if length < len(buffer):
                return buffer.value
            required = length
        raise fail()

    def comparable_path(path: str) -> str:
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[8:]
        elif path.startswith("\\\\?\\"):
            path = path[4:]
        return ntpath.normcase(ntpath.normpath(path))

    parent_handle: Any = None
    source_handle_owned = True
    native_source_handle: Any = None
    try:
        parent_handle = open_handle(
            target.parent,
            file_list_directory | file_add_file | file_read_attributes | file_delete,
            file_flag_backup_semantics | file_flag_open_reparse_point,
        )
        reject_reparse(parent_handle)
        parent_final = comparable_path(final_path(parent_handle))
        expected_parent = comparable_path(long_path(os.path.abspath(os.fspath(target.parent))))
        if parent_final != expected_parent:
            raise FileProtectionError("Windows atomic replacement rejected a redirected directory")

        source_handle_identity: tuple[int, int, int] | None = None
        if source_handle is not None:
            borrowed_source_handle = wintypes.HANDLE(source_handle)
            reject_reparse(borrowed_source_handle)
            source_handle_identity = file_identity(borrowed_source_handle)
        # Python's file handle is intentionally retained by the caller, but it
        # commonly lacks DELETE access required by FILE_RENAME_INFO. Re-open
        # the exact sibling with DELETE access and bind it to the original
        # handle's identity before publishing.
        native_source_handle = open_handle(
            source,
            file_read_attributes | file_delete,
            file_flag_open_reparse_point,
        )
        reject_reparse(native_source_handle)
        if (
            source_handle_identity is not None
            and file_identity(native_source_handle) != source_handle_identity
        ):
            raise FileProtectionError("Windows atomic replacement source handle identity changed")
        source_final = comparable_path(final_path(native_source_handle))
        if ntpath.dirname(source_final) != parent_final:
            raise FileProtectionError(
                "Windows atomic replacement source is outside the target directory"
            )

        file_name = target_name.encode("utf-16-le")
        name_offset = FileRenameInfo.file_name.offset
        buffer = ctypes.create_string_buffer(ctypes.sizeof(FileRenameInfo) + len(file_name))
        rename_info = ctypes.cast(buffer, ctypes.POINTER(FileRenameInfo)).contents
        rename_info.replace_if_exists = 1
        rename_info.root_directory = parent_handle
        rename_info.file_name_length = len(file_name)
        ctypes.memmove(
            ctypes.addressof(buffer) + name_offset,
            file_name,
            len(file_name),
        )
        if not kernel32.SetFileInformationByHandle(
            native_source_handle,
            file_rename_info,
            ctypes.byref(buffer),
            len(buffer),
        ):
            raise fail()
    except FileProtectionError:
        raise
    except Exception:
        raise fail() from None
    finally:
        if source_handle_owned and native_source_handle is not None:
            kernel32.CloseHandle(native_source_handle)
        if parent_handle is not None:
            kernel32.CloseHandle(parent_handle)
