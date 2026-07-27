from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--response", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--request-id", required=True, type=int)
    parser.add_argument("--pid-file")
    args = parser.parse_args()

    try:
        response = json.loads(Path(args.response).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 3
    try:
        payload = Path(args.payload).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return 2

    if (
        not isinstance(response, dict)
        or response.get("args") != ["app-server", "--stdio"]
        or response.get("bundle_in_path") is not False
        or response.get("preserved_path_present") is not True
        or response.get("broker_target_matches_expected") is not True
        or type(response.get("pid")) is not int
        or response["pid"] <= 0
    ):
        return 4
    request = response.get("request")
    if (
        not isinstance(request, dict)
        or request.get("id") != args.request_id
        or request.get("method") != "account/rateLimits/read"
        or request.get("payload") != payload
    ):
        return 4
    if args.pid_file:
        try:
            Path(args.pid_file).write_text(str(response["pid"]), encoding="ascii")
        except OSError:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
