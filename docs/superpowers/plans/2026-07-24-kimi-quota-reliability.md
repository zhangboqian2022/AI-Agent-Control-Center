# Kimi Quota Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate Kimi credential-generation races, close every HTTP client, recover safely from OAuth failures, and distinguish real zero quota from unknown or stale data.

**Architecture:** A `CredentialStore` provides snapshot/generation/fingerprint conditional commits. `QuotaService` coordinates state and active OAuth flow IDs around that store without holding locks during network requests. Kimi quota parsing returns optional windows plus an explicit status, and the existing quota bar preserves last-known values while marking failures stale.

**Tech Stack:** Python 3.12, PySide6, httpx, pytest, pytest-qt.

## Global Constraints

- Keep `kimi-credentials.json` AACC-owned, atomically replaced, mode `0600`, and separate from Kimi CLI credentials.
- Never hold a state or credential lock across an HTTP request or OAuth sleep.
- Never display an unknown or malformed quota window as a real `0%`.
- Catch `Exception`, never `BaseException`, at daemon-thread boundaries.
- All behavior changes begin with a failing test.

---

### Task 1: Credential snapshot and conditional-commit store

**Files:**
- Create: `src/aacc/credential_store.py`
- Create: `tests/test_credential_store.py`

**Interfaces:**
- Consumes: `load_credentials`, `save_credentials`, and `clear_credentials` from `aacc.kimi_oauth`.
- Produces: `CredentialSnapshot`, `CredentialStore.snapshot()`, `invalidate()`, `replace()`, `replace_if_current()`, `clear_if_current()`, and `is_current()`.

- [ ] **Step 1: Write failing store tests**

```python
def test_late_replace_is_rejected_after_invalidate(tmp_path):
    store = CredentialStore(tmp_path)
    store.replace({"auth_method": "api_key", "api_key": "old"})
    old = store.snapshot()
    store.invalidate()
    assert store.replace_if_current(old, {"auth_method": "api_key", "api_key": "late"}) is None
    assert load_credentials(tmp_path)["api_key"] == "old"


def test_external_disk_change_invalidates_snapshot(tmp_path):
    store = CredentialStore(tmp_path)
    store.replace({"auth_method": "api_key", "api_key": "old"})
    old = store.snapshot()
    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "external"})
    assert not store.is_current(old)
    assert store.clear_if_current(old) is False
```

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/python -m pytest tests/test_credential_store.py -q`
Expected: FAIL because `aacc.credential_store` does not exist.

- [ ] **Step 3: Implement the store**

```python
@dataclass(frozen=True)
class CredentialSnapshot:
    generation: int
    fingerprint: str
    credentials: dict[str, Any] | None


class CredentialStore:
    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._lock = threading.RLock()
        self._generation = 0
        current = load_credentials(config_dir)
        self._fingerprint = self._digest(current)

    def snapshot(self) -> CredentialSnapshot:
        with self._lock:
            current = load_credentials(self._config_dir)
            digest = self._digest(current)
            if digest != self._fingerprint:
                self._generation += 1
                self._fingerprint = digest
            return CredentialSnapshot(
                self._generation,
                self._fingerprint,
                copy.deepcopy(current),
            )

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1

    def replace(self, data: dict[str, Any]) -> CredentialSnapshot:
        with self._lock:
            save_credentials(self._config_dir, data)
            self._generation += 1
            self._fingerprint = self._digest(data)
            return self.snapshot()

    def replace_if_current(
        self, expected: CredentialSnapshot, data: dict[str, Any]
    ) -> CredentialSnapshot | None:
        with self._lock:
            if not self._matches(expected):
                return None
            save_credentials(self._config_dir, data)
            self._generation += 1
            self._fingerprint = self._digest(data)
            return self.snapshot()
```

Implement `_matches`, `clear_if_current`, `is_current`, and `_digest` with canonical
`json.dumps(..., sort_keys=True, separators=(",", ":"))` and SHA-256. The `None`
sentinel must hash differently from `{}`.

- [ ] **Step 4: Run store tests**

Run: `.venv/bin/python -m pytest tests/test_credential_store.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aacc/credential_store.py tests/test_credential_store.py
git commit -m "fix: add conditional Kimi credential store"
```

### Task 2: Linearize QuotaService credential and OAuth state

**Files:**
- Modify: `src/aacc/quota_service.py`
- Modify: `tests/test_quota_service.py`

**Interfaces:**
- Consumes: `CredentialStore` and `CredentialSnapshot` from Task 1.
- Produces: race-safe `begin_oauth`, `set_api_key`, `logout`, `_poll_once`, `_access_token`, and `_oauth_flow`.

- [ ] **Step 1: Add deterministic interleaving tests**

Add tests using `threading.Event` barriers:

```python
def test_delayed_refresh_cannot_overwrite_new_oauth_credentials(qapp, tmp_path):
    refresh_started = threading.Event()
    release_refresh = threading.Event()
    # The refresh handler blocks after receiving the old refresh token.
    # Start refresh_now(), then begin OAuth, complete OAuth, and finally release refresh.
    # Assert the final saved access token is the OAuth token, not "late-refresh".


def test_delayed_401_cannot_clear_new_api_key(qapp, tmp_path):
    quota_started = threading.Event()
    release_quota = threading.Event()
    # Start a quota request with old credentials, set a new API key while blocked,
    # return 401, and assert the new API key remains on disk and state is authorized.


def test_two_threads_begin_only_one_oauth_flow(qapp, tmp_path, monkeypatch):
    started = 0
    # Patch the flow target to count starts, synchronize two begin_oauth calls,
    # and assert started == 1 and state == STATE_PENDING.
```

- [ ] **Step 2: Verify each race test fails against v1.4.0**

Run: `.venv/bin/python -m pytest tests/test_quota_service.py -k "delayed or two_threads" -q`
Expected: at least one stale write/clear assertion fails and double begin can start twice.

- [ ] **Step 3: Integrate generation and flow IDs**

Add:

```python
@dataclass(frozen=True)
class AccessGrant:
    token: str
    snapshot: CredentialSnapshot


self._credentials = CredentialStore(config_dir)
self._active_flow_id: str | None = None
```

Under `_state_lock`, `begin_oauth()` must atomically check pending, invalidate old
snapshots, allocate `uuid.uuid4().hex`, clear cancellation, set pending, and start one
thread with that flow ID. `_poll_once()` must atomically check state and capture a
credential snapshot. All refresh saves, 401 clears, state changes, and quota emissions
must verify the originating snapshot is still current. `_oauth_flow(flow_id, snapshot)`
must commit only when both the flow ID and snapshot remain current.

- [ ] **Step 4: Cover API Key and logout interleavings**

```python
def test_api_key_wins_over_late_oauth(qapp, tmp_path):
    # Block token return, call set_api_key("sk-new"), release token,
    # and assert auth_method == "api_key".


def test_logout_wins_over_late_oauth(qapp, tmp_path):
    # Block token return, call logout(), release token,
    # and assert credentials are absent and state is unauthorized.
```

- [ ] **Step 5: Run the service race suite**

Run: `.venv/bin/python -m pytest tests/test_quota_service.py -q`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aacc/quota_service.py tests/test_quota_service.py
git commit -m "fix: guard Kimi auth with credential generations"
```

### Task 3: Close clients and recover OAuth threads

**Files:**
- Modify: `src/aacc/kimi_oauth.py`
- Modify: `src/aacc/quota_service.py`
- Modify: `tests/test_kimi_oauth.py`
- Modify: `tests/test_quota_service.py`

**Interfaces:**
- Consumes: race-safe flow finalizer from Task 2.
- Produces: bounded OAuth deadline, deterministic client closure, and one terminal OAuth signal.

- [ ] **Step 1: Add deadline, close, and persistence-failure tests**

```python
def test_poll_deadline_uses_shorter_device_expiry(monkeypatch):
    authorization = _auth()
    authorization = replace(authorization, expires_in_seconds=30)
    clock = FakeClock()
    with pytest.raises(KimiOAuthError, match="超时"):
        poll_device_token(client, authorization, now=clock.now, sleep=clock.sleep)
    assert clock.value == pytest.approx(30)


def test_oauth_save_oserror_exits_pending_once(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("aacc.credential_store.save_credentials", Mock(side_effect=OSError("disk")))
    # Complete device flow and assert one failed oauth_finished signal and non-pending state.


def test_every_created_client_is_closed(qapp, tmp_path):
    clients: list[TrackingClient] = []
    # Exercise success, 401, OAuth cancellation, and HTTP error.
    assert all(client.close_calls == 1 for client in clients)
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/test_kimi_oauth.py tests/test_quota_service.py -k "deadline or closed or oserror" -q`
Expected: fixed 15-minute deadline, zero close calls, or pending state causes failures.

- [ ] **Step 3: Implement lifecycle guarantees**

Change `poll_device_token` to:

```python
deadline = now() + min(float(authorization.expires_in_seconds), POLL_TIMEOUT_SECONDS)
```

Use `with self._client_factory() as client:` in both polling and OAuth paths. Route
success, cancellation, known OAuth/HTTP errors, and unexpected `Exception` through the
flow-ID-aware finalizer. Log unexpected failures with `logger.exception`.

- [ ] **Step 4: Add an FD regression test where supported**

```python
@pytest.mark.skipif(not hasattr(psutil.Process(), "num_fds"), reason="macOS FD API required")
def test_no_fd_growth_over_poll_cycles(tmp_path):
    before = psutil.Process().num_fds()
    # Run 200 direct poll cycles with cache reset and a MockTransport client.
    after = psutil.Process().num_fds()
    assert after <= before + 3
```

- [ ] **Step 5: Run focused and full Kimi tests**

Run: `.venv/bin/python -m pytest tests/test_kimi_oauth.py tests/test_quota_service.py -q`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aacc/kimi_oauth.py src/aacc/quota_service.py tests/test_kimi_oauth.py tests/test_quota_service.py
git commit -m "fix: bound and close Kimi auth requests"
```

### Task 4: Honest Kimi quota states

**Files:**
- Modify: `src/aacc/kimi_quota.py`
- Modify: `src/aacc/quota_service.py`
- Modify: `src/aacc/gui.py`
- Modify: `tests/test_kimi_quota.py`
- Modify: `tests/test_quota_bar.py`
- Modify: `tests/test_gui_quota_wiring.py`

**Interfaces:**
- Produces: `QuotaStatus`, optional Kimi quota windows, `fetched_at`, and stale UI behavior.

- [ ] **Step 1: Replace zero-fallback tests with unknown/partial tests**

```python
def test_missing_sections_are_unknown():
    quota = parse_quota({})
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.weekly is None
    assert quota.five_hour is None


def test_explicit_zero_is_not_unknown():
    quota = parse_quota({"usage": {"limit": 0, "used": 0, "remaining": 0}})
    assert quota.weekly is not None
    assert quota.weekly.percentage == 0


def test_one_valid_window_is_partial():
    quota = parse_quota({"usage": {"limit": 100, "used": 10}})
    assert quota.status is QuotaStatus.PARTIAL
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/test_kimi_quota.py -q`
Expected: current parser returns all-zero `QuotaDetail` values.

- [ ] **Step 3: Implement optional windows and status**

Add `QuotaStatus(StrEnum)` with `OK`, `PARTIAL`, `UNKNOWN`, and `STALE`. Make
`_make_detail(raw) -> QuotaDetail | None`; require explicit valid numeric fields and
preserve explicit zero. Derive status from weekly/five-hour availability. Store
`fetched_at` when `fetch_quota()` succeeds.

- [ ] **Step 4: Add stale UI tests**

```python
def test_refresh_error_preserves_values_and_marks_stale(qapp):
    bar = QuotaBar()
    bar.show_quota(make_quota())
    bar.show_error("network")
    assert "数据过期" in bar.summary_label.text()
    assert "42%" in bar.weekly_label.text()
    assert "network" in bar.toolTip()


def test_partial_quota_uses_dashes(qapp):
    bar = QuotaBar()
    bar.show_quota(make_quota(five_hour=None))
    assert "5h --" == bar.five_hour_label.text()
```

- [ ] **Step 5: Update UI and service emission**

Render optional windows with `--`; preserve the last successful widget values on refresh
errors; display the last success timestamp in the tooltip. Do not mutate a valid snapshot
into STALE in the parser—STALE is a service/UI observation.

- [ ] **Step 6: Run Kimi parser and GUI tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_kimi_quota.py tests/test_quota_bar.py tests/test_gui_quota_wiring.py -q`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aacc/kimi_quota.py src/aacc/quota_service.py src/aacc/gui.py tests/test_kimi_quota.py tests/test_quota_bar.py tests/test_gui_quota_wiring.py
git commit -m "fix: distinguish unknown Kimi quota data"
```

### Task 5: Unify OAuth dialog cancellation

**Files:**
- Modify: `src/aacc/gui.py`
- Modify: `tests/test_gui_quota_wiring.py`
- Modify: `tests/test_quota_bar.py`

**Interfaces:**
- Produces: `KimiOAuthDialog.cancel_once()` and `finish_and_close()`.

- [ ] **Step 1: Add close, Esc, and programmatic-finish tests**

```python
def test_oauth_dialog_x_cancels_once(qtbot):
    dialog = KimiOAuthDialog()
    cancelled: list[bool] = []
    dialog.cancelled.connect(lambda: cancelled.append(True))
    dialog.show()
    dialog.close()
    assert cancelled == [True]


def test_oauth_dialog_success_close_does_not_cancel(qtbot):
    dialog = KimiOAuthDialog()
    cancelled: list[bool] = []
    dialog.cancelled.connect(lambda: cancelled.append(True))
    dialog.finish_and_close()
    assert cancelled == []
```

- [ ] **Step 2: Verify tests fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_gui_quota_wiring.py -k "dialog" -q`
Expected: X emits nothing and `finish_and_close` is absent.

- [ ] **Step 3: Implement idempotent cancellation**

Track `_cancel_emitted` and `_finishing`. `cancel_once()` emits once. Override
`reject()` and `closeEvent()` to call it unless `_finishing`; `finish_and_close()` sets
`_finishing` before `close()`. Update `MainWindow._on_oauth_finished()` to use the
non-cancelling close path.

- [ ] **Step 4: Replace deprecated mouse event construction**

Use:

```python
QTest.mouseClick(bar, Qt.MouseButton.LeftButton)
```

in `tests/test_quota_bar.py`.

- [ ] **Step 5: Run GUI tests and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_quota_bar.py tests/test_gui_quota_wiring.py -q`
Expected: all tests PASS without the QMouseEvent constructor warning.

```bash
git add src/aacc/gui.py tests/test_gui_quota_wiring.py tests/test_quota_bar.py
git commit -m "fix: cancel Kimi OAuth on every close path"
```

