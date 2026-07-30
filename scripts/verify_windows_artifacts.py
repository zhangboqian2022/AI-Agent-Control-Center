from __future__ import annotations

import argparse
import hashlib
import re
import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

CHECKSUM_PATTERN = re.compile(
    rb"(?P<digest>[0-9a-f]{64})  (?P<name>AACC-\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?-Setup\.exe)\n"
)
EXPECTED_ROOT = {"AACC.exe", "aacc-spawn.exe", "_internal"}
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(setup_path: Path, checksum_path: Path) -> None:
    checksum = checksum_path.read_bytes()
    match = CHECKSUM_PATTERN.fullmatch(checksum)
    if match is None:
        raise ValueError("checksum contract failed")
    if match.group("name").decode("ascii") != setup_path.name:
        raise ValueError("checksum filename mismatch")
    if setup_path.stat().st_size < 1024 * 1024:
        raise ValueError("Setup is unexpectedly small")
    if _sha256_file(setup_path) != match.group("digest").decode("ascii"):
        raise ValueError("Setup checksum mismatch")


def _safe_member_path(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        raise ValueError("ZIP member path encoding is unsafe")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("ZIP member path is unsafe")
    if any(part in ("", ".") for part in path.parts):
        raise ValueError("ZIP member path is not normalized")
    if any(part.rstrip(" .") != part for part in path.parts):
        raise ValueError("ZIP member path is ambiguous on Windows")
    return path


def _windows_path_key(path: PurePosixPath) -> str:
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def verify_portable(portable_path: Path, built_root: Path) -> None:
    built_files: dict[str, str] = {}
    built_directories: set[str] = set()
    built_keys: set[str] = set()
    for path in built_root.rglob("*"):
        if _is_reparse(path):
            raise ValueError("built onedir contains a reparse point")
        relative = PurePosixPath(path.relative_to(built_root).as_posix())
        key = _windows_path_key(relative)
        if key in built_keys:
            raise ValueError("built onedir contains a Windows path collision")
        built_keys.add(key)
        if path.is_dir():
            built_directories.add(relative.as_posix())
        elif path.is_file():
            built_files[relative.as_posix()] = _sha256_file(path)
        else:
            raise ValueError("built onedir contains an unsupported entry")
    built_entries = {PurePosixPath(name).parts[0] for name in (*built_files, *built_directories)}
    if built_entries != EXPECTED_ROOT:
        raise ValueError("built onedir root contract failed")

    archive_files: dict[str, str] = {}
    archive_directories: set[str] = set()
    seen: set[PurePosixPath] = set()
    seen_windows_keys: set[str] = set()
    with zipfile.ZipFile(portable_path) as archive:
        for info in archive.infolist():
            member = _safe_member_path(info.filename.rstrip("/"))
            if member in seen:
                raise ValueError("duplicate ZIP member")
            seen.add(member)
            windows_key = _windows_path_key(member)
            if windows_key in seen_windows_keys:
                raise ValueError("ZIP contains a Windows path collision")
            seen_windows_keys.add(windows_key)
            if member.parts[0] != "AACC":
                raise ValueError("unexpected ZIP top-level entry")
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode):
                raise ValueError("ZIP symlink is forbidden")
            if file_type and not (
                (info.is_dir() and file_type == stat.S_IFDIR)
                or (not info.is_dir() and file_type == stat.S_IFREG)
            ):
                raise ValueError("ZIP member type is unsupported")
            if len(member.parts) == 1:
                if not info.is_dir():
                    raise ValueError("AACC ZIP root must be a directory")
                continue
            relative = PurePosixPath(*member.parts[1:])
            if info.is_dir():
                archive_directories.add(relative.as_posix())
                continue
            if relative.parts[0] not in EXPECTED_ROOT:
                raise ValueError("unexpected ZIP root payload")
            archive_files[relative.as_posix()] = _sha256_bytes(archive.read(info))
            for parent in relative.parents:
                if parent != PurePosixPath("."):
                    archive_directories.add(parent.as_posix())

    if set(archive_files) != set(built_files):
        raise ValueError("ZIP and built onedir manifests differ")
    if archive_directories != built_directories:
        raise ValueError("ZIP and built onedir directory manifests differ")
    if archive_files != built_files:
        raise ValueError("ZIP and built onedir hashes differ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", dest="setup_path", type=Path, required=True)
    parser.add_argument("--checksum", dest="checksum_path", type=Path, required=True)
    parser.add_argument("--portable", dest="portable_path", type=Path, required=True)
    parser.add_argument("--built-root", dest="built_root", type=Path, required=True)
    arguments = parser.parse_args()
    verify_checksum(arguments.setup_path, arguments.checksum_path)
    verify_portable(arguments.portable_path, arguments.built_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
