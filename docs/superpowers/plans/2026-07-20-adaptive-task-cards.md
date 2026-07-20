# Adaptive Task Cards and Live Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship AACC v1.3.0-rc.2 with five-second Codex discovery, privacy-safe live activity labels, whole-run timers, compact horizontal cards, and automatic window height.

**Architecture:** Extend `CodexLocalDiscovery` to return a fixed activity label derived from bounded session-tail metadata, while `StateMachine` remains the single owner of run lifecycle timestamps. Rebuild `TaskCard` as a compact horizontal component and add a queued adaptive-height calculation to `MainWindow`, capped at 80% of the active screen. Release metadata and bilingual documentation advance to rc.2 only after behavior and packaging checks pass.

**Tech Stack:** Python 3.12+, PySide6, Pydantic, pytest/pytest-qt, Ruff, mypy, uv, PyInstaller, macOS hdiutil.

## Global Constraints

- Codex discovery interval is exactly 5.0 seconds by default; GUI timer refresh remains 1.0 second.
- Activity labels are deterministic fixed strings no longer than 18 Chinese characters.
- Prompt text, assistant response text, file content, command arguments, and credentials are never copied into `TaskState.message`.
- Automatic discovery remains Codex-only in rc.2; other tools require configured adapters.
- Window height is capped at 80% of the current screen's available geometry.
- Completed tasks remain visible until explicit removal.
- A terminal-to-active transition starts a new timer; active/waiting heartbeats preserve the current timer.
- Public version is `v1.3.0-rc.2`; Python package version is `1.3.0rc2`.

---

### Task 1: Five-Second Discovery and Privacy-Safe Activity Labels

**Files:**
- Modify: `tests/test_codex_discovery.py`
- Modify: `tests/test_discovery_service.py`
- Modify: `src/aacc/codex_discovery.py`
- Modify: `src/aacc/discovery_service.py`
- Modify: `tests/fixtures/codex/rollout-fixture-running-0001.jsonl`

**Interfaces:**
- Produces: `SessionSignal(status: TaskStatus, observed_at: datetime, message: str)`.
- Produces: `CodexLocalDiscovery._activity_message(item: dict[str, Any]) -> str | None`.
- Consumed by: `CodexLocalDiscovery.discover()` to set `TaskState.message`.

- [ ] **Step 1: Write failing discovery tests**

Add parameterized cases that create bounded JSONL tails for `patch_apply_end`, safe custom-tool names, test/build command categories, unknown events, malformed JSON, and `task_complete`. Assert only fixed labels such as `正在修改代码`, `正在运行测试`, `正在构建程序`, `正在查询资料`, `正在检查代码`, `正在分析任务`, and `已完成` appear. Include sentinel prompt, response, command, and file strings and assert none is present in the resulting state message.

- [ ] **Step 2: Write the failing default-interval test**

Instantiate `CodexDiscoveryService` with a stub discovery and assert `service.interval_seconds == 5.0`.

- [ ] **Step 3: Verify the new tests fail for the intended reasons**

Run:

```bash
uv run pytest tests/test_codex_discovery.py tests/test_discovery_service.py -q
```

Expected: activity-label expectations and the 5.0-second default fail against the rc.1 implementation.

- [ ] **Step 4: Implement bounded metadata classification**

Extend `SessionSignal` with `message`. Scan at most the existing 256 KiB tail in reverse, prioritize the most recent terminal event, and classify recognized event/tool categories into constants. Do not assign raw payload values to `message`. Return `正在分析任务` for a recent active session without a recognized activity and `已完成` for completion.

- [ ] **Step 5: Change the service default interval**

Change `CodexDiscoveryService(..., interval_seconds: float = 5.0)` while retaining the minimum interval guard for explicit callers.

- [ ] **Step 6: Verify green and commit**

Run:

```bash
uv run pytest tests/test_codex_discovery.py tests/test_discovery_service.py -q
uv run ruff check src/aacc/codex_discovery.py src/aacc/discovery_service.py tests/test_codex_discovery.py tests/test_discovery_service.py
```

Commit `feat: add privacy-safe Codex activity summaries`.

### Task 2: Whole-Run Timer Semantics and Formatting

**Files:**
- Modify: `tests/test_state_machine.py`
- Modify: `tests/test_gui.py`
- Modify: `src/aacc/gui.py`

**Interfaces:**
- Preserves: `StateMachine.transition(current, candidate) -> TaskState | None`.
- Produces: `_elapsed(state: TaskState, now: datetime | None = None) -> str` returning `HH:MM:SS`.
- Produces: `_elapsed_label(state: TaskState, now: datetime | None = None) -> str`.

- [ ] **Step 1: Add failing lifecycle and display tests**

Cover repeated active heartbeat/message transitions, active-to-waiting-to-active, terminal freeze, and terminal-to-active reset with explicit timestamps. Add GUI helper tests for `00:18:42`, `01:26:08`, and terminal text `总用时 01:26:08`.

- [ ] **Step 2: Verify the formatting tests fail**

Run:

```bash
uv run pytest tests/test_state_machine.py tests/test_gui.py -q
```

Expected: rc.1 omits the `00:` hours field and terminal prefix.

- [ ] **Step 3: Implement stable labels without GUI-owned lifecycle state**

Keep run timestamps owned by `StateMachine`. Make `_elapsed` always return `HH:MM:SS`; add `_elapsed_label` to prefix terminal states with `总用时 `. Ensure `TaskCard.set_state()` uses this label.

- [ ] **Step 4: Verify green and commit**

Run:

```bash
uv run pytest tests/test_state_machine.py tests/test_gui.py -q
```

Commit `fix: track and display whole task runs`.

### Task 3: Compact Horizontal Task Card

**Files:**
- Modify: `tests/test_gui.py`
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/styles.qss`

**Interfaces:**
- Preserves: `TaskCard.action_requested`, `TaskCard.remove_requested`, `TaskCard.details`, and context-menu behavior.
- Produces visible widgets: `agent_label`, `status_label`, `name_label`, `timer_label`, `message_label`, `dot`.

- [ ] **Step 1: Add failing hierarchy tests**

Assert the tool badge is smaller than the task name, the name is a single prominent line, the status and timer live inside the right-side details container, the status light is fixed near 60 px, and an expanded card's size hint is no more than 110 px tall. Preserve the existing remove-button and compact-mode assertions.

- [ ] **Step 2: Verify the layout test fails**

Run:

```bash
uv run pytest tests/test_gui.py -q
```

Expected: the rc.1 vertical card hierarchy exceeds the new height and typography expectations.

- [ ] **Step 3: Rebuild the card layout and stylesheet**

Use a root horizontal layout: status light, right-side details, and optional `×`. Put a small tool/status row above the larger task name, then timer and one-line activity below. Remove the always-visible updated-time row and retain it in the tooltip. Update QSS object styles and reduce margins/spacing to target about 100 px.

- [ ] **Step 4: Verify green and commit**

Run:

```bash
uv run pytest tests/test_gui.py -q
uv run ruff check src/aacc/gui.py tests/test_gui.py
```

Commit `feat: flatten agent task cards`.

### Task 4: Adaptive Window Height and Internal Scrolling

**Files:**
- Modify: `tests/test_gui.py`
- Modify: `src/aacc/gui.py`

**Interfaces:**
- Produces: `MainWindow._schedule_adaptive_resize() -> None`.
- Produces: `MainWindow._resize_to_card_content() -> None`.
- Produces: `MainWindow._available_screen_height() -> int`.

- [ ] **Step 1: Add failing adaptive-height tests**

Create windows with zero, one, three, and enough cards to exceed a stubbed screen cap. Process queued Qt events and assert height grows and shrinks, never exceeds `int(available_height * 0.8)`, switches the vertical scrollbar policy at the cap, and keeps `x()`/`y()` unchanged during height-only resizing.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/test_gui.py -q
```

Expected: rc.1 keeps the restored/manual outer height after card changes.

- [ ] **Step 3: Implement queued adaptive resizing**

After card synchronization and compact-mode changes, queue one resize. Calculate non-scroll chrome plus the cards container size hint, clamp to the screen cap, set the scrollbar policy deterministically, and resize only the height. Run a second queued measurement when visibility/layout changes need Qt to settle. Do not persist adaptive height as authoritative state.

- [ ] **Step 4: Verify green and commit**

Run:

```bash
uv run pytest tests/test_gui.py -q
```

Commit `feat: resize the panel with monitored tasks`.

### Task 5: rc.2 Versioning, Documentation, and Regression Suite

**Files:**
- Modify: `src/aacc/__init__.py`
- Modify: `pyproject.toml`
- Modify: `scripts/build_app.sh`
- Modify: `scripts/build_dmg.sh`
- Modify: `scripts/install.sh`
- Modify: `tests/test_api.py`
- Modify: `tests/test_packaging.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/user-guide.en.md`

**Interfaces:**
- Produces Python version `1.3.0rc2` and artifact label `1.3.0-rc.2`.
- Produces desktop artifact `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.2.dmg`.

- [ ] **Step 1: Update failing version assertions first**

Change API and packaging tests to expect `1.3.0rc2`, `AACC-1.3.0-rc.2.dmg`, and rc.2 build defaults.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/test_api.py tests/test_packaging.py -q
```

Expected: rc.1 version sources fail the rc.2 expectations.

- [ ] **Step 3: Advance version sources and bilingual docs**

Update package/build/install references. Add a changelog section describing adaptive cards, five-second discovery, privacy-safe summaries, and timer restart/freeze behavior. Update bilingual README and user-guide download/build references.

- [ ] **Step 4: Run full static and test verification**

Run:

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/aacc
uv run pytest -q
```

Commit `release: prepare AACC v1.3.0-rc.2` only after all four commands pass.

### Task 6: Build, Install, Visual Check, and GitHub Publication

**Files:**
- Create from observed results: `docs/test-report-1.3.0-rc.2.md`
- Create from observed results: `docs/test-report-1.3.0-rc.2.en.md`

**Interfaces:**
- Produces verified ad-hoc-signed app and DMG.
- Produces GitHub branch, pull request/merge, annotated tag, and prerelease asset.

- [ ] **Step 1: Build and verify the DMG**

Run:

```bash
AACC_VERSION=1.3.0-rc.2 ./scripts/build_dmg.sh
hdiutil verify /Users/zhangboqian/Desktop/AACC-1.3.0-rc.2.dmg
shasum -a 256 /Users/zhangboqian/Desktop/AACC-1.3.0-rc.2.dmg
```

- [ ] **Step 2: Install and launch the rc.2 app**

Replace the prior user-level installation using the project's install/build workflow, launch `AACC.app`, verify a single process, health version `1.3.0rc2`, config/database mode `0600`, and `aacc doctor` success.

- [ ] **Step 3: Perform the macOS UI smoke check**

Verify at least two monitored Codex tasks produce distinct cards, tool badges and task names are readable, activity changes appear on the five-second cadence, completion freezes total time, restart resets it, `×` shrinks the window, new activity grows it, and the window caps at 80% with scrolling.

- [ ] **Step 4: Record observed results and commit**

Write only observed versions, sizes, checksum, signing results, health output, and UI limitations into both test reports. Commit `docs: record AACC rc2 release verification`.

- [ ] **Step 5: Publish through GitHub**

Push `codex/aacc-v1-3-rc2`, open a PR against `main`, merge only after checks pass, create annotated tag `v1.3.0-rc.2` on the merged main commit, and create a GitHub prerelease with `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.2.dmg` plus its SHA-256. Do not publish the stale rc.1 draft.
