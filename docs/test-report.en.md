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

- `pytest`: 150 passed, 0 failed.
- Full-package line coverage: 81%. Security-critical focused modules measured 92–100% for configuration (94%), models (99%), persistence (96%), state machine (94%), task manager (93%), automation (98%), automation executor (96%), discovery service (92%), and redaction (100%). Platform entry loops account for most uncovered lines.
- Ruff: zero findings. Strict mypy: 23 source modules passed.
- Wheel: `aacc/styles.qss` is present; the isolated installed runtime contains no pytest, mypy, Ruff, or PyInstaller.
- App: installed at `~/Applications/AACC.app`, version `1.3.0-rc.1`, 110 MB, ad-hoc deep-signature strict verification passed, and the process remained running at about 71 MB RSS.
- DMG: `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg`, 49 MB, `hdiutil verify` passed, SHA-256 `35069897f340c4c0da9f1c9c3380e1d888152c53986ab68345b563556b15278f`.
- Installed config and SQLite modes are both `0600`; local health returned `1.3.0rc1`; `aacc doctor` passed; a second launch kept one process.
- Live Codex smoke test detected this running task and a separate completed task. Completed-task retention remains covered by automated Qt/discovery regression tests.

This machine has no Developer ID Application identity or configured notary profile. The artifact is therefore explicitly an ad-hoc GitHub prerelease, not a notarized stable release. macOS 13/14/15 rows remain untested on real hardware.
