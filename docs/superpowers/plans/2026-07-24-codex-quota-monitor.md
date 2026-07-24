# Codex Quota Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the current Codex weekly usage limit in AACC using only privacy-safe local rate-limit metadata.

**Architecture:** `CodexQuotaReader` scans a bounded tail of the newest local Codex session and validates rate-limit windows by duration. `CodexQuotaService` polls the reader on a daemon thread and emits Qt signals. A separate read-only Codex quota bar sits above the existing Kimi quota bar.

**Tech Stack:** Python 3.12, PySide6, pytest, pytest-qt.

## Global Constraints

- Read only `event_msg.payload.type == "token_count"` and `payload.rate_limits`.
- Never expose prompt, response, command, tool input, or arbitrary event data.
- Read at most 262144 bytes from a candidate session tail.
- Recognize only the current 10080-minute weekly window by `window_minutes`, not field position.
- Ignore legacy 300-minute windows because current Codex no longer exposes a five-hour limit.
- Treat expired, malformed, missing, or out-of-range windows as unknown.

---

### Task 1: Parse bounded Codex rate-limit metadata

**Files:**
- Create: `src/aacc/codex_quota.py`
- Create: `tests/test_codex_quota.py`

**Interfaces:**
- Produces: `CodexQuotaWindow`, `CodexQuotaSnapshot`, `CodexQuotaStatus`, `parse_rate_limits()`, and `CodexQuotaReader.read_latest()`.

- [ ] **Step 1: Add parser privacy and schema tests**

```python
def test_parses_weekly_window_by_duration():
    item = token_count(primary=(27, 10080), secondary=None)
    snapshot = parse_rate_limits(item, now=NOW)
    assert snapshot.weekly.used_percent == 27


def test_weekly_in_secondary_parses_and_legacy_short_window_is_ignored():
    item = token_count(primary=(18, 300), secondary=(27, 10080))
    snapshot = parse_rate_limits(item, now=NOW)
    assert snapshot.weekly.used_percent == 27
    assert not hasattr(snapshot, "five_hour")


def test_private_fields_are_never_retained():
    item = token_count(primary=(27, 10080), secondary=None)
    item["payload"]["private_prompt"] = "secret-sentinel"
    assert "secret-sentinel" not in repr(parse_rate_limits(item, now=NOW))
```

- [ ] **Step 2: Verify parser tests fail**

Run: `.venv/bin/python -m pytest tests/test_codex_quota.py -q`  
Expected: FAIL because `aacc.codex_quota` does not exist.

- [ ] **Step 3: Implement strict immutable models**

```python
class CodexQuotaStatus(StrEnum):
    OK = "ok"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CodexQuotaWindow:
    used_percent: int
    window_minutes: int
    resets_at: datetime


@dataclass(frozen=True)
class CodexQuotaSnapshot:
    weekly: CodexQuotaWindow | None
    observed_at: datetime
    status: CodexQuotaStatus
    plan_type: str | None = None
```

Reject booleans, NaN, values outside 0–100, nonpositive reset timestamps, and windows
whose reset is not later than `now`. Truncate a valid string plan type to 32 characters.

- [ ] **Step 4: Add bounded-tail reader tests**

Create fixture files containing a large private prefix, valid token_count event, malformed
half-line, and a newer unrelated event. Assert only the valid rate-limit snapshot returns.
Add an all-malformed case that returns an UNKNOWN snapshot without private strings.

- [ ] **Step 5: Implement `CodexQuotaReader`**

Choose the newest `.jsonl` by modification time, seek to
`max(0, size - MAX_CODEX_QUOTA_TAIL_BYTES)`, discard a partial first line when the seek
offset is nonzero, and inspect complete lines in reverse. Return the first valid rate-limit
snapshot; otherwise return UNKNOWN with the file modification time.

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_codex_quota.py -q`  
Expected: all tests PASS.

```bash
git add src/aacc/codex_quota.py tests/test_codex_quota.py
git commit -m "feat: read local Codex quota metadata"
```

### Task 2: Poll Codex quota without blocking Qt

**Files:**
- Create: `src/aacc/codex_quota_service.py`
- Create: `tests/test_codex_quota_service.py`

**Interfaces:**
- Consumes: `CodexQuotaReader.read_latest()`.
- Produces: `CodexQuotaService.start()`, `stop()`, `refresh_now()`, `quota_updated`, and `error_occurred`.

- [ ] **Step 1: Add service lifecycle tests**

```python
def test_refresh_emits_snapshot(qapp):
    reader = FakeReader([SNAPSHOT])
    service = CodexQuotaService(reader, interval_seconds=60)
    received = QSignalSpy(service.quota_updated)
    service.refresh_now()
    assert wait_for(lambda: len(received) == 1)


def test_reader_error_does_not_kill_poll_thread(qapp):
    reader = FakeReader([OSError("busy"), SNAPSHOT])
    service = CodexQuotaService(reader, interval_seconds=0.2)
    service.start()
    assert wait_for(lambda: reader.calls >= 2)
    service.stop()
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/test_codex_quota_service.py -q`  
Expected: FAIL because the service module does not exist.

- [ ] **Step 3: Implement the service**

Mirror the existing quota service thread discipline with `_stop`, `_wake`, and a
nonblocking `_poll_lock`. `refresh_now()` starts a one-shot daemon thread if `start()` was
never called. Catch `Exception` at `_poll_guarded`, log a warning, and emit a sanitized
error string. Default interval is 10 seconds.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_codex_quota_service.py -q`  
Expected: all tests PASS.

```bash
git add src/aacc/codex_quota_service.py tests/test_codex_quota_service.py
git commit -m "feat: poll Codex quota metadata"
```

### Task 3: Wire runtime and the Codex quota bar

**Files:**
- Modify: `src/aacc/app.py`
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/models.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_gui_quota_wiring.py`
- Create: `tests/test_codex_quota_bar.py`

**Interfaces:**
- Consumes: `CodexQuotaService` signals and `CodexQuotaSnapshot`.
- Produces: `CodexQuotaBar` and `AppSettings.codex_quota_enabled`.

- [ ] **Step 1: Add runtime wiring tests**

```python
def test_runtime_creates_codex_quota_service_by_default(tmp_path):
    runtime = build_runtime(config_path, database_path)
    try:
        assert runtime.codex_quota_service is not None
    finally:
        runtime.close()


def test_runtime_can_disable_codex_quota_service(tmp_path):
    # Write app.codex_quota_enabled: false and assert the service is None.
```

- [ ] **Step 2: Add bar state tests**

```python
def test_codex_bar_shows_windows(qapp):
    bar = CodexQuotaBar()
    bar.show_quota(SNAPSHOT)
    assert bar.weekly_label.text() == "周 27%"


def test_codex_bar_unknown_and_stale_states(qapp):
    bar = CodexQuotaBar()
    bar.show_quota(replace(SNAPSHOT, weekly=None, status=CodexQuotaStatus.UNKNOWN))
    assert bar.weekly_label.text() == "周 --"
    bar.show_error("read failed")
    assert "数据过期" in bar.summary_label.text()
```

- [ ] **Step 3: Verify tests fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_app.py tests/test_codex_quota_bar.py -q`  
Expected: missing service field, setting, and widget failures.

- [ ] **Step 4: Add runtime ownership**

Add `codex_quota_service` to `Runtime`, create it from the default
`~/.codex/sessions` reader when enabled, start it beside the Kimi quota service, and stop
it before discovery services in `Runtime.close()`.

- [ ] **Step 5: Add the independent GUI bar**

Insert `CodexQuotaBar` immediately before the Kimi `QuotaBar`. Connect click to
`refresh_now`, quota snapshots to `show_quota`, and errors to `show_error`. UNKNOWN uses
`--`; errors preserve prior values and mark the summary stale. Tooltip includes reset
countdowns, observation time, and plan type.

- [ ] **Step 6: Run GUI/runtime tests and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_app.py tests/test_codex_quota_bar.py tests/test_gui_quota_wiring.py -q`  
Expected: all tests PASS.

```bash
git add src/aacc/app.py src/aacc/gui.py src/aacc/models.py tests/test_app.py tests/test_gui_quota_wiring.py tests/test_codex_quota_bar.py
git commit -m "feat: show Codex weekly quota"
```

### Task 4: Document Codex quota privacy and behavior

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/user-guide.md`
- Modify: `KNOWN_LIMITATIONS.md`
- Modify: `KNOWN_LIMITATIONS.zh-CN.md`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Documents the feature delivered by Tasks 1–3.

- [ ] **Step 1: Add documentation assertions**

Assert both READMEs mention Codex weekly quota, local structured
`token_count.rate_limits`, and that no prompt/response content is read for quota.

- [ ] **Step 2: Verify documentation tests fail**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`  
Expected: new content assertions fail.

- [ ] **Step 3: Update bilingual documentation**

Document that values update only after Codex emits rate-limit metadata, expired windows
show `--`, and clicking the Codex quota bar rescans local metadata. State that this is a
local observation, not an OpenAI billing API.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`  
Expected: all tests PASS.

```bash
git add README.md README.zh-CN.md docs/user-guide.en.md docs/user-guide.md KNOWN_LIMITATIONS.md KNOWN_LIMITATIONS.zh-CN.md tests/test_packaging.py
git commit -m "docs: explain local Codex quota monitoring"
```
