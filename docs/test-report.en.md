# AACC v1.3.0-rc.1 Test Report

[中文版本](test-report.md) · [Back to README](../README.md)

Validation environment: Apple Silicon macOS 26.5.2 (build 25F84), Python 3.13.11, and PySide6 6.11.1.

Run the current validation suite with:

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
uv run --extra dev mypy src
```

## Measured results

- `pytest`: 163 passed, 0 failed.
- Full-package line coverage: 83%. Of 666 executable lines changed from release baseline `6167267`, 600 are covered and 66 are missing, producing reproducible 90% changed-line coverage and meeting the release gate. Run `uv run pytest --cov=src/aacc --cov-report=xml:coverage.xml -q`, then `uvx diff-cover coverage.xml --compare-branch=61672673d326796ad2631a0a59a39b8e5545ce45 --fail-under=90`.
- Ruff: zero findings. Strict mypy: 23 source modules passed.
- Wheel: `aacc/styles.qss` is present. The installer exports production dependencies from `uv.lock` and installs the local wheel with `--no-deps`; the isolated runtime contains no pytest, mypy, Ruff, or PyInstaller.
- App: installed at `~/Applications/AACC.app`, version `1.3.0-rc.1`, 110 MB, ad-hoc deep-signature strict verification passed, and the process remained running at about 71 MB RSS.
- DMG: `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg`, 49 MB, `hdiutil verify` passed, SHA-256 `10f6c4fed8ee4fff4cf3b9fb708fd7197e8647adb8ddd6e771808f2a3fedd9f3`.
- Installed config and SQLite modes are both `0600`; local health returned `1.3.0rc1`; `aacc doctor` passed; a second launch kept one process.
- Live Codex smoke testing detected this running task and a separate completed task. Sanitized 2026-07 current-format index/session fixtures verify running and completed event parsing. Completed-task retention remains covered by automated Qt/discovery regression tests.

This machine has no Developer ID Application identity or configured notary profile. The artifact is therefore explicitly an ad-hoc GitHub prerelease, not a notarized stable release. macOS 13/14/15 rows remain untested on real hardware.
