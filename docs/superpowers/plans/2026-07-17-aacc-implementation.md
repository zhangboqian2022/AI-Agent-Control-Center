# AACC V1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a directly usable macOS floating control center for four AI coding-agent tasks with persistent status, local API/CLI control, safe focusing/input, adapters, hotkeys, and app packaging.

**Architecture:** A typed Python package separates normalized task state from API/CLI, macOS automation, agent adapters, and PySide6 UI. SQLite is the source of runtime truth, YAML defines user configuration, and UI/API communicate through a thread-safe manager and event subscriptions.

**Tech Stack:** Python 3.12+, PySide6, FastAPI, Uvicorn, Pydantic 2, PyYAML, SQLite, psutil, pytest, httpx, PyInstaller.

## Global Constraints

- Target platform is macOS 13 or newer on Apple Silicon or Intel.
- API binds only to `127.0.0.1` and uses a generated bearer token.
- No `shell=True`, arbitrary command API, screen-coordinate automation, or secret logging.
- Unknown third-party behavior must degrade to `UNKNOWN`, `WARNING`, manual state, or configurable patterns.
- Default UI contains four tasks and all core agent differences remain behind adapters.
- Production code follows a failing-test-first cycle.

---

### Task 1: Project foundation, models, config, and state machine

**Files:**
- Create: `pyproject.toml`, `src/aacc/constants.py`, `src/aacc/models.py`, `src/aacc/config.py`, `src/aacc/state_machine.py`
- Test: `tests/test_models.py`, `tests/test_config.py`, `tests/test_state_machine.py`

**Interfaces:**
- Produces: `TaskStatus`, `TaskConfig`, `TaskState`, `AppConfig`, `load_config(path)`, `create_default_config(path)`, `StateMachine.accept(current, candidate)`.

- [ ] Write tests proving enum parsing, four default tasks, random token generation, invalid regex rejection, manual-source priority, confidence priority, and terminal-to-active restart rules.
- [ ] Run `uv run pytest tests/test_models.py tests/test_config.py tests/test_state_machine.py -q`; expect failures because modules do not exist.
- [ ] Implement the minimal typed models, YAML loader/default writer, and deterministic state transition policy.
- [ ] Re-run the three test files; expect all tests to pass.

### Task 2: SQLite persistence, manager, redaction, and logging

**Files:**
- Create: `src/aacc/persistence.py`, `src/aacc/task_manager.py`, `src/aacc/security.py`, `src/aacc/logging_setup.py`
- Test: `tests/test_persistence.py`, `tests/test_task_manager.py`, `tests/test_security.py`

**Interfaces:**
- Consumes: `TaskConfig`, `TaskState`, `StateMachine.accept`.
- Produces: `StateStore.initialize/get/list/update/history`, `TaskManager.get/list/update/reset/subscribe`, `redact(value)`.

- [ ] Write tests for persistence across reopen, ordered history, subscriber callbacks, rejected stale-low-confidence state, reset behavior, and common API-key/password/token redaction.
- [ ] Run the task tests and observe missing-module failures.
- [ ] Implement transactional SQLite storage, a lock-protected manager, callbacks, and structured rotating logs.
- [ ] Run all task tests and expect green.

### Task 3: Authenticated localhost API and CLI

**Files:**
- Create: `src/aacc/api.py`, `src/aacc/cli.py`, `src/aacc/run_wrapper.py`
- Test: `tests/test_api.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `AppConfig`, `TaskManager`.
- Produces: `create_api(config, manager) -> FastAPI`, `aacc` CLI entry point, `aacc-run` wrapper entry point.

- [ ] Write API tests for health, bearer rejection, task list, valid state update, invalid task/status/key/text length, and loopback-only config; write CLI parser/doctor tests.
- [ ] Run API/CLI tests and confirm expected missing-interface failures.
- [ ] Implement validated endpoints, JSON errors, CLI HTTP client, doctor checks, and lifecycle wrapper without arbitrary shell execution.
- [ ] Run API/CLI tests and the full suite.

### Task 4: Safe macOS focus, keyboard, voice, and hotkeys

**Files:**
- Create: `src/aacc/automation.py`, `src/aacc/hotkeys.py`
- Test: `tests/test_automation.py`, `tests/test_hotkeys.py`

**Interfaces:**
- Consumes: task terminal binding and app configuration.
- Produces: `MacAutomation.focus`, `send_key`, `send_text`, `start_voice`, `GlobalHotkeys.start/stop`.

- [ ] Write tests for AppleScript quoting, bundle/window targeting, strict key whitelist, injection-disabled behavior, focus-before-send ordering, and F13-F20 parsing.
- [ ] Run tests and verify they fail for absent production interfaces.
- [ ] Implement timeout-bounded `osascript`/`open` calls, System Events input, voice shortcut, and Quartz event-tap hotkeys with graceful permission fallback.
- [ ] Run automation/hotkey and full tests.

### Task 5: Agent adapter registry and conservative parsing

**Files:**
- Create: `src/aacc/adapters.py`
- Test: `tests/test_adapters.py`

**Interfaces:**
- Produces: `BaseAgentAdapter`, `GenericCLIAdapter.classify(line)`, `ProcessAdapter.detect`, `AdapterRegistry.create(task)`.

- [ ] Write tests for ANSI removal, maximum line length, invalid regex, Codex/Claude/Kimi defaults, generic Z Code configuration, and ambiguous lines returning no fabricated state.
- [ ] Run the test and observe failure due to missing adapters.
- [ ] Implement base/registry, conservative regex classification, process detection, and specialized presets.
- [ ] Run adapter and full tests.

### Task 6: PySide6 floating GUI and menu bar

**Files:**
- Create: `src/aacc/gui.py`, `src/aacc/app.py`, `src/aacc/__main__.py`, `src/aacc/__init__.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: `TaskManager`, `MacAutomation`, `GlobalHotkeys`.
- Produces: `TaskCard`, `MainWindow`, `AACCApplication`, `main()`.

- [ ] Write offscreen Qt tests for four cards, status color mapping, compact toggle, card refresh, menu actions, and geometry setting keys.
- [ ] Run GUI tests and verify missing-interface failures.
- [ ] Implement a polished translucent always-on-top panel, cards, pulse animation, context menus, tray icon, settings persistence, and nonblocking manager refresh.
- [ ] Run GUI and full tests under `QT_QPA_PLATFORM=offscreen`.

### Task 7: Packaging, scripts, example configuration, and documentation

**Files:**
- Create: `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/build_app.sh`, `scripts/start.sh`, `examples/config.example.yaml`, `README.md`, `CHANGELOG.md`, `LICENSE`, `docs/user-guide.md`, `docs/adapter-development.md`, `docs/troubleshooting.md`, `docs/test-report.md`, `AI-Agent-Control-Center-Specification.md`.
- Test: `tests/test_packaging.py`

**Interfaces:**
- Produces: one-command installer, launcher, uninstall flow, PyInstaller `.app`, user/developer docs.

- [ ] Write tests that assert executable scripts, valid example configuration, registered console entry points, required documentation, and no placeholder markers.
- [ ] Run packaging tests and confirm failure for missing artifacts.
- [ ] Add repeatable uv/pip installation, app build, launch scripts, examples, permissions instructions, known limitations, and copy the authoritative product specification.
- [ ] Run packaging and full tests.

### Task 8: End-to-end verification and usable local install

**Files:**
- Modify only files required by failures found during verification.

**Interfaces:**
- Validates all earlier interfaces together.

- [ ] Run `uv sync --all-extras` and record successful dependency resolution.
- [ ] Run `QT_QPA_PLATFORM=offscreen uv run pytest -q --cov=aacc --cov-report=term-missing`; require zero failures and at least 80% core coverage.
- [ ] Run `uv run ruff check .` and `uv run mypy src/aacc`; require zero errors.
- [ ] Start the API, run authenticated CLI list/status/show/doctor commands, and verify persisted state after restart.
- [ ] Run `scripts/build_app.sh`; require `dist/AACC.app` to exist and pass `codesign --verify` when ad-hoc signing is available.
- [ ] Launch `dist/AACC.app`, verify the process remains alive, then leave the app ready for the user with default configuration created.
