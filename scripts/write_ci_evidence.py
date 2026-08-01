"""Write a small, commit-bound manifest for CI release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tomllib
from pathlib import Path


def environment(primary: str, fallback: str) -> str:
    return os.environ.get(primary) or os.environ.get(fallback, "")


def project_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def file_evidence(name: str) -> dict[str, object]:
    path = Path(name)
    if not path.is_file():
        return {"path": name, "exists": False, "bytes": 0, "sha256": None}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": name,
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": digest,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--repository",
        default=environment("GITHUB_REPOSITORY", "AACC_CI_REPOSITORY"),
    )
    parser.add_argument("--commit", default=environment("GITHUB_SHA", "AACC_CI_COMMIT"))
    parser.add_argument("--ref", default=environment("GITHUB_REF", "AACC_CI_REF"))
    parser.add_argument("--run-id", default=environment("GITHUB_RUN_ID", "AACC_CI_RUN_ID"))
    parser.add_argument(
        "--run-attempt",
        default=environment("GITHUB_RUN_ATTEMPT", "AACC_CI_RUN_ATTEMPT"),
    )
    parser.add_argument("--runner", default=environment("AACC_CI_RUNNER", "RUNNER_LABELS"))
    parser.add_argument("--version", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suffix = args.runner or "unknown-runner"
    macos_runner = suffix.startswith("macos-")
    artifacts = {
        "coverage": "coverage.xml",
        "junit": f"test-results-{suffix}.xml",
        "pip_audit": f"pip-audit-{suffix}.json",
        "diff_cover": f"diff-cover-{suffix}.txt" if macos_runner else None,
    }
    commands = {
        "tests": (
            "uv run pytest --cov=src/aacc --cov-report=xml -ra -vv --tb=long "
            f"--junitxml={artifacts['junit']}"
        ),
        "pip_audit": (
            "uv export --locked --extra dev --no-emit-project --format requirements-txt "
            "--output-file pip-audit-requirements.txt && "
            "uv run pip-audit --requirement pip-audit-requirements.txt --no-deps "
            f"--disable-pip --format=json --output={artifacts['pip_audit']}"
        ),
    }
    if macos_runner:
        commands["diff_cover"] = (
            "uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=90 "
            f"| tee {artifacts['diff_cover']}"
        )
    else:
        commands["diff_cover"] = None
    evidence = {
        "schema_version": 1,
        "repository": args.repository,
        "commit": args.commit,
        "ref": args.ref,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "runner": args.runner,
        "job_status": environment("AACC_CI_JOB_STATUS", "GITHUB_JOB_STATUS"),
        "version": args.version or project_version(),
        "artifacts": artifacts,
        "commands": commands,
        "files": {
            key: file_evidence(name) if name is not None else None
            for key, name in artifacts.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
