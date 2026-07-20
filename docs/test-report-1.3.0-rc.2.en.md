# AACC v1.3.0-rc.2 Test Report

Test date: 2026-07-20

Platform: macOS 26.5.2 (Build 25F84), Apple Silicon arm64

Build runtime: uv-managed CPython 3.13.11

## Automated quality gates

- `uv run ruff check .`: passed with no lint errors.
- `uv run ruff format --check .`: passed; all 44 files are formatted.
- `uv run mypy src/aacc`: passed; 23 source files have no type errors.
- `uv run pytest -q`: passed; all 195 tests passed.
- The installer reran the complete suite before the real installation: all 195 tests passed.
- Codex discovery/service selection: 39 tests passed, covering the five-second default, private fixed summaries, real approval events, integer timestamps, long-session start recovery, partial concurrent lines, truncate-and-rewrite invalidation, bounded oversized-line scanning, waiting-task priority, completion events, and discovery health.
- State-machine/model selection: 16 tests passed, covering one run across short turns and waiting states, terminal freeze, and reset when a terminal task first reappears as running or waiting.
- Complete GUI selection: 25 tests passed, covering horizontal cards, long-title elision, selection, removal, adaptive height, the 80% cap, and internal scrolling.
- Privacy regression tests confirm that raw command arguments are neither classified nor displayed; only explicit safe categories and fixed tool event names can produce an activity label.

## Build and installation verification

- App: `/Users/zhangboqian/Applications/AACC.app`, approximately 110 MB.
- Bundle versions: `CFBundleShortVersionString=1.3.0-rc.2`, `CFBundleVersion=3`.
- `codesign --verify --deep --strict`: passed; the current build uses an ad-hoc signature.
- DMG: `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.2.dmg`, approximately 50 MB.
- `hdiutil verify`: passed; the disk image checksum is valid.
- SHA-256: `36e4918357e08b8456f0037cfab0f01355d14937cc32dbae4c2494ce94f954b6`.
- The installer built the `aacc_control_center-1.3.0rc2` wheel, created a production-dependency runtime, and replaced the prior installation.
- One AACC process remained after installation, using approximately 49 MB RSS at the stable check.
- The local health endpoint returned `{"status":"ok","version":"1.3.0rc2"}`.
- `aacc doctor`: config, SQLite, and the local API all passed.
- Config and SQLite both have mode `0600` (`-rw-------`).

## Real macOS UI verification

- AACC discovered and displayed two running Codex tasks at the same time.
- Each card showed a small `CODEX` badge, a larger task title, the large left status light, an `HH:MM:SS` timer, and a one-line activity label.
- Observed labels included “executing command,” “inspecting code,” and “editing code”; raw command and conversation text were not displayed.
- Clicking `×` on one running card changed the visible count from two to one and immediately shortened the window vertically.
- Using “Restore automatic detection” in the task selector restored two visible tasks and automatically grew the window; the pre-test monitoring selection was restored.
- Automated testing with a simulated 500 px available screen confirmed a 400 px cap and internal scrolling beyond the cap.

## Known limitations

- This build has no Developer ID signature and no Apple notarization. It is a GitHub RC prerelease only.
- rc.2 automatically discovers Codex only. Claude Code, Cursor, Kimi Code, and other tools require their own adapters to report real state.
- Accessibility permission was disabled on this Mac during the test, so global hotkeys and keyboard injection were not repeated manually; the app displayed the correct permission guidance.
