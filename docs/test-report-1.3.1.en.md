# AACC v1.3.1 Test Report

Test date: 2026-07-22

Platform: macOS (Apple Silicon arm64)

Build environment: uv-managed CPython 3.13

## Automated quality gates

- `.venv/bin/ruff check src tests`: passed, no lint errors.
- `.venv/bin/mypy src/aacc`: passed, 25 source files with no type errors.
- `.venv/bin/python -m pytest -q`: all 284 tests passed (15 new in this release).
- Version consistency: packaging tests assert `pyproject.toml`, `aacc.__version__`, build-script defaults, and README/user-guide download links match exactly.
- Security regression: the public placeholder token in the shipped example config is rejected by prefix and rotated automatically; legitimate high-entropy tokens are unaffected.

## Build and install verification

- App: `~/Applications/AACC.app`, `CFBundleShortVersionString=1.3.1`.
- Signing: stable self-signed "AACC Local Development" identity; `codesign --verify --deep --strict` passed.
  Hardened runtime is enabled only for Developer ID identities (self-signed identities have no Team ID; enabling it crashes the app at launch — regression-verified).
- DMG: `~/Desktop/AACC-1.3.1.dmg`, `hdiutil verify` passed.
- SHA-256: `c748a726441334ba24d3537050ce6a7c4b32fa176808910db9f516da8a231df9`.
- Post-install process liveness confirmed (`pgrep`); the running instance is quit unconditionally before overwrite.

## Real macOS UI verification

- Panel restored from minimized via the tray menu; restored from hidden via Dock icon / Cmd-Tab (reproduced with a real-Cocoa script and confirmed on-device).
- Kimi Code cards show the working-directory name next to the status (e.g. `· codelight`) with the full path in the tooltip — confirmed on-device.
- Kimi Desktop agent conversations verified end to end with a data-level simulation: an injected in-progress conversation is judged RUNNING and auto-added to the panel; appending `usage.record` flips it to "turn completed". Simulation data was removed afterwards.
- Accessibility: hotkeys start within 5 seconds of granting permission and stop on revocation; the guidance dialog stays hidden once "do not show again" is checked (including a test-isolation fix for shared settings).
- The credential-rotation dialog no longer writes the clipboard automatically; copying requires clicking "复制" (Copy).

## Known limitations

- This build is self-signed locally and not notarized by Apple; the first launch requires approval under Privacy & Security.
- Kimi Desktop's Chat tab is a kimi.com web shell; conversations live in the cloud with no local data source, so they cannot be monitored.
- Global hotkeys bind F13–F20, which standard Mac keyboards do not have; an extended keyboard or key remapping is required.
