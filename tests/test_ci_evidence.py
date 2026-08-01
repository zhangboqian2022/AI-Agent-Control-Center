import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_ci_evidence_script_writes_commit_bound_provenance(tmp_path: Path) -> None:
    output = tmp_path / "ci-evidence.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "write_ci_evidence.py"),
            "--output",
            str(output),
            "--commit",
            "abc123",
            "--ref",
            "refs/tags/v1.4.4-rc.1",
            "--run-id",
            "42",
            "--runner",
            "windows-2025-vs2026",
            "--version",
            "1.4.4rc1",
        ],
        check=True,
        cwd=tmp_path,
    )

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence == {
        "schema_version": 1,
        "repository": "",
        "commit": "abc123",
        "ref": "refs/tags/v1.4.4-rc.1",
        "run_id": "42",
        "run_attempt": "",
        "runner": "windows-2025-vs2026",
        "job_status": "",
        "version": "1.4.4rc1",
        "artifacts": {
            "coverage": "coverage.xml",
            "junit": "test-results-windows-2025-vs2026.xml",
            "pip_audit": "pip-audit-windows-2025-vs2026.json",
            "diff_cover": None,
        },
        "commands": {
            "tests": (
                "uv run pytest --cov=src/aacc --cov-report=xml -ra -vv --tb=long "
                "--junitxml=test-results-windows-2025-vs2026.xml"
            ),
            "pip_audit": (
                "uv export --locked --extra dev --no-emit-project --format requirements-txt "
                "--output-file pip-audit-requirements.txt && "
                "uv run pip-audit --requirement pip-audit-requirements.txt --no-deps "
                "--disable-pip --format=json --output=pip-audit-windows-2025-vs2026.json"
            ),
            "diff_cover": None,
        },
        "files": {
            "coverage": {"path": "coverage.xml", "exists": False, "bytes": 0, "sha256": None},
            "junit": {
                "path": "test-results-windows-2025-vs2026.xml",
                "exists": False,
                "bytes": 0,
                "sha256": None,
            },
            "pip_audit": {
                "path": "pip-audit-windows-2025-vs2026.json",
                "exists": False,
                "bytes": 0,
                "sha256": None,
            },
            "diff_cover": None,
        },
    }


def test_ci_workflow_publishes_commit_bound_release_evidence_inputs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "write_ci_evidence.py" in workflow
    assert "ci-evidence-${{ matrix.os }}.json" in workflow
    assert "release-evidence-inputs-${{ matrix.os }}" in workflow
    assert "diff-cover-${{ matrix.os }}.txt" in workflow
    assert "coverage.xml" in workflow
    assert "pip-audit-${{ matrix.os }}.json" in workflow
