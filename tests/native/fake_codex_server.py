from __future__ import annotations

import json
import os
import sys
from typing import Any


def _bundle_in_path() -> bool:
    bundle = os.environ.get("AACC_TEST_BUNDLE_DIR")
    if not bundle:
        return False
    normalized_bundle = os.path.normcase(os.path.abspath(bundle))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        normalized_entry = os.path.normcase(os.path.abspath(entry or os.curdir))
        if normalized_entry == normalized_bundle:
            return True
        if normalized_entry.startswith(normalized_bundle + os.sep):
            return True
    return False


def _preserved_path_present() -> bool:
    expected = os.environ.get("AACC_TEST_PRESERVED_PATH_ENTRY")
    if not expected:
        return False
    normalized_expected = os.path.normcase(os.path.abspath(expected))
    return any(
        os.path.normcase(os.path.abspath(entry or os.curdir)) == normalized_expected
        for entry in os.environ.get("PATH", "").split(os.pathsep)
    )


def _broker_target_matches_expected() -> bool:
    return os.environ.get("AACC_BROKER_CODEX_TARGET") == os.environ.get(
        "AACC_TEST_EXPECTED_CODEX_TARGET"
    )


def main() -> int:
    if sys.argv[1:] != ["app-server", "--stdio"]:
        return 90

    line = sys.stdin.buffer.readline()
    if not line:
        return 91
    try:
        request: Any = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 92

    response = {
        "pid": os.getpid(),
        "args": sys.argv[1:],
        "request": request,
        "bundle_in_path": _bundle_in_path(),
        "preserved_path_present": _preserved_path_present(),
        "broker_target_matches_expected": _broker_target_matches_expected(),
    }
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return int(os.environ.get("AACC_TEST_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
