# Completed Codex Task Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep completed Codex tasks visible with their terminal light until the user removes them, and restore a removed task after a later verified run.

**Architecture:** `CodexDiscoveryService` owns automatic, retained and muted session IDs. `MainWindow` persists those sets and renders running cards before retained terminal cards. The existing `TaskManager` remains the terminal-state source.

**Tech Stack:** Python 3.13, PySide6, QSettings, pytest-qt, PyInstaller and GitHub Releases.

## Global Constraints

- Keep the existing metadata-only Codex discovery path; never read prompts, responses, code or commands.
- Retain terminal tasks indefinitely with no timed cleanup.
- Fresh verified activity always cancels a prior mute and returns the task.
- Preserve manual selections, compact mode, filters and always-on-top behavior.

---

### Task 1: Retained monitoring state

**Files:**
- Modify: `src/aacc/discovery_service.py`
- Test: `tests/test_discovery_service.py`

**Interfaces:** Produces `set_monitoring_preferences(manual_ids, retained_ids, muted_ids)`, `retained_ids()` and `remove_task(session_id)`.

- [ ] **Step 1: Write the failing retention test**

```python
def test_active_task_is_retained_and_reappears_after_removal(tmp_path: Path) -> None:
    discovery = StubDiscovery([task("auto")])
    discovery.active_ids = {"auto"}
    service = CodexDiscoveryService(TaskManager(tmp_path / "aacc.db"), discovery=discovery)
    service.poll_once()
    discovery.active_ids = set()
    service.poll_once()
    assert service.retained_ids() == {"auto"}
    service.remove_task("auto")
    service.poll_once()
    assert discovery.selected_ids == set()
    discovery.active_ids = {"auto"}
    service.poll_once()
    assert discovery.selected_ids == {"auto"}
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_discovery_service.py -q`

Expected: FAIL because retained-state APIs do not exist.

- [ ] **Step 3: Implement the transition**

```python
self._retained_ids: set[str] = set()

def poll_once(self) -> int:
    active = self.discovery.active_session_ids()
    with self._selection_lock:
        self._auto_active_ids = set(active)
        self._muted_ids -= active
        self._retained_ids |= active
        selected_ids = (self._manual_ids | self._retained_ids | active) - self._muted_ids
```

`remove_task` removes an ID from manual and retained sets, then adds it to muted IDs.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_discovery_service.py -q`

Expected: PASS.

```bash
git add src/aacc/discovery_service.py tests/test_discovery_service.py
git commit -m "feat: retain completed Codex tasks"
```

### Task 2: Persist preferences and remove individual cards

**Files:**
- Modify: `src/aacc/app.py`
- Modify: `src/aacc/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:** Consumes the Task 1 three-set callback. Produces QSettings key `codex_retained_tasks`, `MainWindow.remove_codex_task(task_id)` and `TaskCard.remove_requested`.

- [ ] **Step 1: Write failing GUI coverage**

```python
def test_completed_auto_task_remains_visible_until_remove(tmp_path: Path, qtbot: object) -> None:
    auto_ids = {"kept"}
    window, manager = build_window(tmp_path, qtbot, codex_auto_active_ids=lambda: set(auto_ids))
    window.set_codex_monitoring_preferences(set(), {"kept"}, set())
    assert "codex:kept" in window.cards
    window.remove_codex_task("codex:kept")
    assert "kept" in window.codex_muted_ids
    assert "codex:kept" not in window.cards
```

- [ ] **Step 2: Run test and verify failure**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui.py -q`

Expected: FAIL because retained preferences and removal do not exist.

- [ ] **Step 3: Implement persistent controls**

```python
class TaskCard(QFrame):
    remove_requested = Signal(str)

def remove_codex_task(self, task_id: str) -> None:
    session_id = task_id.removeprefix("codex:")
    self.codex_manual_ids.discard(session_id)
    self.codex_retained_ids.discard(session_id)
    self.codex_muted_ids.add(session_id)
    self._save_codex_monitoring_preferences()
    self.sync_cards()
```

Add an accessible `×` button to the card top row and a matching **从面板移除** context-menu action. Load/save `codex_retained_tasks` and pass all three sets through `app.py`.

- [ ] **Step 4: Run tests and commit**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui.py -q`

Expected: PASS.

```bash
git add src/aacc/app.py src/aacc/gui.py tests/test_gui.py
git commit -m "feat: add Codex task removal control"
```

### Task 3: Group cards and protect batch cleanup

**Files:**
- Modify: `src/aacc/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:** Consumes retained IDs and `remove_codex_task`. Produces `clear_retained_tasks()` and test helper `card_order()`.

- [ ] **Step 1: Write failing grouping coverage**

```python
def test_codex_cards_are_grouped_running_before_retained_terminal(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    window.set_codex_monitoring_preferences(set(), {"finished", "running"}, set())
    manager.update(TaskState.new("codex:finished", "COMPLETED", source="codex_local"))
    manager.update(TaskState.new("codex:running", "RUNNING", source="codex_local"))
    window.refresh()
    assert window.card_order() == ["codex:running", "codex:finished"]
```

- [ ] **Step 2: Run test and verify failure**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui.py -q`

Expected: FAIL because grouped ordering does not exist.

- [ ] **Step 3: Implement grouped layout**

Use terminal statuses `{COMPLETED, ERROR, CANCELLED, STOPPED}`. Render summary counts, **运行中** before active cards and **已完成 · 保留直到移除** before terminal cards. The terminal heading has a confirmation-backed **全部清除**. It only removes terminal `codex:` cards. Show local last-activity time in expanded cards.

- [ ] **Step 4: Run tests and commit**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui.py -q`

Expected: PASS.

```bash
git add src/aacc/gui.py tests/test_gui.py
git commit -m "feat: group retained Codex task cards"
```

### Task 4: Document, package and publish 1.2.0

**Files:**
- Modify: `README.md`, `README.zh-CN.md`, `docs/user-guide.en.md`, `docs/user-guide.md`
- Modify: `CHANGELOG.md`, `CHANGELOG.zh-CN.md`
- Modify: `pyproject.toml`, `uv.lock`, `src/aacc/__init__.py`, `scripts/build_app.sh`, `scripts/build_dmg.sh`
- Test: `tests/test_packaging.py`, `tests/test_api.py`

**Interfaces:** Produces a version-consistent `AACC-1.2.0.dmg` and bilingual user documentation.

- [ ] **Step 1: Write failing version assertions**

```python
def test_release_version_is_consistent_across_project_and_build_scripts() -> None:
    assert __version__ == "1.2.0"
    assert 'version = "1.2.0"' in Path("pyproject.toml").read_text()
    assert "AACC-1.2.0.dmg" in Path("scripts/build_dmg.sh").read_text()
```

- [ ] **Step 2: Run test and verify failure**

Run: `uv run pytest tests/test_packaging.py tests/test_api.py -q`

Expected: FAIL while version sources are 1.1.0.

- [ ] **Step 3: Update release documentation and sources**

Set release sources to `1.2.0`. Document that terminal cards remain until `×`, context-menu removal or confirmed **全部清除**, and that verified re-runs return automatically.

- [ ] **Step 4: Run full verification, build and publish**

Run:

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
./scripts/build_dmg.sh
/usr/bin/codesign --verify --deep --strict dist/AACC.app
/usr/bin/hdiutil verify /Users/zhangboqian/Desktop/AACC-1.2.0.dmg
git push origin HEAD:main
gh release create v1.2.0 /Users/zhangboqian/Desktop/AACC-1.2.0.dmg --repo zhangboqian2022/AI-Agent-Control-Center --target main --title "AACC 1.2.0" --notes "Completed-task retention and controls."
```

Expected: checks pass, artifacts validate, and GitHub has a public v1.2.0 release with the DMG asset.
