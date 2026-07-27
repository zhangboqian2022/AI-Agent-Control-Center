# Kimi Web Session Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cached Kimi membership login fail closed across restarts, refresh web and Kimi Code quota from one five-minute cycle, recover from hung WebView requests, and never fabricate a MONTH reset.

**Architecture:** Keep the native system `QWebView`, but persist an AACC-owned protected boolean gate that decides whether the native session may be reused. Make `KimiWebQuotaService` the single 300-second coordinator for native web and Kimi Code refresh, add generation-checked JavaScript and Python deadlines, and age-gate Kimi Code fallback values.

**Tech Stack:** Python 3.12+, PySide6 Qt WebView, Qt signals/timers, atomic protected JSON, pytest/pytest-qt.

## Global Constraints

- Do not bundle Qt WebEngine/Chromium.
- Do not persist a website bearer token, cookie, password, account name, or quota value in the AACC gate file.
- The gate file uses atomic replacement and the existing exact `protect_file` / `protect_directory` behavior.
- Explicit logout writes `reuse_native_session=false` before asynchronous WebView cleanup starts.
- Automatic web refresh performs no navigation or request while the gate is false.
- One timer callback triggers both web membership and Kimi Code refresh every `300_000` milliseconds.
- JavaScript requests abort after 15 seconds; Python releases a stuck refresh after 25 seconds.
- Kimi Code fallback older than 330 seconds is not merged into a fresh web snapshot.
- MONTH reset accepts only `subscriptionBalance.expireTime` or `subscriptionBalance.resetTime`.
- Codex remains WEEK-only and the Kimi row order remains `5H`, `WEEK`, `MONTH`.
- Formal `v1.4.2` remains blocked on macOS and Windows native-session/logout manual checks.

---

### Task 1: Persist the AACC native-session reuse gate

**Files:**
- Create: `src/aacc/kimi_web_login_state.py`
- Create: `tests/test_kimi_web_login_state.py`
- Modify: `src/aacc/kimi_web_session.py`
- Modify: `tests/test_kimi_web_session.py`

**Interfaces:**
- Produces: `KimiWebLoginStateStore(config_dir: Path)`
- Produces: `KimiWebLoginStateStore.may_reuse() -> bool`
- Produces: `KimiWebLoginStateStore.set_may_reuse(value: bool) -> None`
- Consumed by: `KimiWebSession`

- [ ] **Step 1: Add RED store and lifecycle tests**

Add tests with these exact behaviors:

```python
def test_gate_defaults_false_and_survives_new_store_instance(tmp_path):
    first = KimiWebLoginStateStore(tmp_path)
    assert first.may_reuse() is False
    first.set_may_reuse(True)
    assert KimiWebLoginStateStore(tmp_path).may_reuse() is True


def test_corrupt_gate_fails_closed(tmp_path):
    path = tmp_path / "kimi-web-session-state.json"
    path.write_text("{", encoding="utf-8")
    assert KimiWebLoginStateStore(tmp_path).may_reuse() is False
```

Extend fake-session tests to prove a successful quota bridge writes `true`,
an unauthorized bridge writes `false`, and `refresh()` does not call
`setUrl()` or `runJavaScript()` when the gate is false.

- [ ] **Step 2: Verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_kimi_web_login_state.py tests/test_kimi_web_session.py -q
```

Expected: collection fails because `aacc.kimi_web_login_state` is absent.

- [ ] **Step 3: Implement the protected atomic store**

Use schema:

```json
{"version":1,"reuse_native_session":false}
```

Write to a sibling temporary file with UTF-8 JSON, call `protect_file` on the
open file/path according to the existing credential-store pattern, use
`os.replace`, and re-protect the final path. Reject symlinks and any object
whose version/boolean type is invalid. Reads fail closed without logging file
content or paths.

- [ ] **Step 4: Gate session refresh and bridge state**

`KimiWebSession.__init__` accepts
`login_state: KimiWebLoginStateStore | None = None`. `refresh()` returns
without WebView navigation when `may_reuse()` is false. `open_login()` marks
only a login attempt; it does not set the gate true. A successful `quota`
bridge sets true before emitting `login_state_changed(True)`. An
`unauthorized` bridge sets false before emitting false.

- [ ] **Step 5: Verify GREEN and quality**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_kimi_web_login_state.py tests/test_kimi_web_session.py -q
.venv/bin/ruff check src/aacc/kimi_web_login_state.py \
  src/aacc/kimi_web_session.py tests/test_kimi_web_login_state.py \
  tests/test_kimi_web_session.py
.venv/bin/mypy src/aacc
```

- [ ] **Step 6: Commit**

```bash
git add src/aacc/kimi_web_login_state.py src/aacc/kimi_web_session.py \
  tests/test_kimi_web_login_state.py tests/test_kimi_web_session.py
git commit -m "fix: gate cached Kimi web sessions"
```

---

### Task 2: Make logout deterministic and WebView refresh bounded

**Files:**
- Modify: `src/aacc/kimi_web_session.py`
- Modify: `src/aacc/kimi_web_quota_service.py`
- Modify: `tests/test_kimi_web_session.py`
- Modify: `tests/test_kimi_web_quota_service.py`

**Interfaces:**
- Produces: generation-checked `KimiWebSession.refresh()`
- Produces: `_finish_logout_cleanup(generation: int) -> None`
- Produces: `_refresh_watchdog_fired(generation: int) -> None`

- [ ] **Step 1: Add RED ordering, timeout, and late-result tests**

Tests must prove:

1. `logout()` calls `set_may_reuse(False)` before the first WebView cleanup
   call.
2. Logout on a non-Kimi origin navigates to `KIMI_MEMBERSHIP_URL`, clears
   `localStorage`/`sessionStorage` only after that origin loads, deletes
   cookies, and never re-enables the gate.
3. If the cleanup callback never arrives, the gate remains false.
4. `membership_fetch_script()` contains `AbortController` and `15000`.
5. A 25-second Python watchdog clears `_refreshing`, emits one sanitized
   timeout error, and a later `refresh()` starts a new generation.
6. A bridge payload from an older generation is ignored.

- [ ] **Step 2: Verify RED**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_kimi_web_session.py tests/test_kimi_web_quota_service.py -q
```

- [ ] **Step 3: Implement two-layer logout**

Synchronously set the gate false and invalidate the current refresh generation.
If the current origin is not `www.kimi.com`, navigate there with a
`_logout_after_load` flag. At the correct origin run:

```javascript
try {
  localStorage.clear();
  sessionStorage.clear();
  return true;
} catch (_) {
  return false;
}
```

Use its callback only for cleanup completion; correctness never depends on the
callback. Delete cookies and keep the gate false. A bounded Qt timer may end the
cleanup state, but must not change authorization.

- [ ] **Step 4: Add request generations and two deadlines**

The bridge payload includes an integer `generation`. Both membership fetches
share one `AbortController`; `setTimeout(() => controller.abort(), 15000)`
guarantees a JavaScript completion path. A single-shot Python `QTimer` keyed by
generation fires after 25 seconds, clears `_refreshing`, and emits
`"Kimi 会员额度刷新超时"`. `_handle_bridge` ignores generation values that do
not match the active generation.

- [ ] **Step 5: Verify GREEN and commit**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_kimi_web_session.py tests/test_kimi_web_quota_service.py -q
.venv/bin/ruff check src/aacc/kimi_web_session.py \
  src/aacc/kimi_web_quota_service.py tests/test_kimi_web_session.py \
  tests/test_kimi_web_quota_service.py
.venv/bin/mypy src/aacc
git add src/aacc/kimi_web_session.py src/aacc/kimi_web_quota_service.py \
  tests/test_kimi_web_session.py tests/test_kimi_web_quota_service.py
git commit -m "fix: bound Kimi web session cleanup"
```

---

### Task 3: Drive both Kimi sources from one five-minute cycle

**Files:**
- Modify: `src/aacc/quota_service.py`
- Modify: `src/aacc/kimi_web_quota_service.py`
- Modify: `src/aacc/app.py`
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/kimi_web_quota.py`
- Modify: `tests/test_quota_service.py`
- Modify: `tests/test_kimi_web_quota_service.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_gui_quota_wiring.py`
- Modify: `tests/test_kimi_web_quota.py`

**Interfaces:**
- Produces: `QuotaService(..., externally_scheduled: bool = False)`
- Produces: `QuotaService.set_externally_scheduled(enabled: bool) -> None`
- Produces: `KimiWebQuotaService.set_fallback_refresh(callback: Callable[[], None]) -> None`
- Produces: `merge_kimi_quota(web, code, *, now=None, fallback_max_age_seconds=330.0)`

- [ ] **Step 1: Add RED scheduling and freshness tests**

Tests must assert:

```python
service = QuotaService(tmp_path, version="test", externally_scheduled=True)
service.start()
# no autonomous fetch until refresh_now()
```

For the web service, one `start()` cycle and one timer timeout must each call
the fallback callback exactly once and the native session refresh exactly
once. Manual GUI refresh must call the same service cycle, not invoke two
services independently.

Add merge tests where a web MONTH fetched at `NOW` and Kimi Code 5H/WEEK fetched
331 seconds earlier produce no fallback windows, while 330 seconds is accepted.

- [ ] **Step 2: Verify RED**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_quota_service.py tests/test_kimi_web_quota_service.py \
  tests/test_app.py tests/test_gui_quota_wiring.py tests/test_kimi_web_quota.py -q
```

- [ ] **Step 3: Implement external scheduling**

When `externally_scheduled=True`, `QuotaService._run()` waits for `_wake` before
the first poll and after every poll; it has no interval clock. `stop()` remains
bounded independently of the 300-second interval.

`KimiWebQuotaService.refresh_now()` calls the registered fallback callback and
then the native session refresh from the same GUI-thread invocation. Its
`start()` and timer timeout both use `refresh_now()`.

`QuotaService.set_externally_scheduled()` is valid only before `start()` and
selects the worker-loop policy without replacing the worker thread. After both
services are constructed in `build_runtime`, call
`quota_service.set_externally_scheduled(True)` and register its `refresh_now`
callback with the web service. If the web service is disabled, the Kimi Code
service retains its existing independent 300-second polling.

- [ ] **Step 4: Age-gate merge results**

`merge_kimi_quota` compares the Kimi Code `fetched_at` against `now` (defaulting
to the web timestamp or current UTC time). It fills 5H/WEEK only when the age
is between zero and 330 seconds. An absent timestamp is not eligible to fill a
fresh web snapshot. Preserve the existing rule that MONTH never comes from
Kimi Code.

- [ ] **Step 5: Verify GREEN and commit**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_quota_service.py tests/test_kimi_web_quota_service.py \
  tests/test_app.py tests/test_gui_quota_wiring.py tests/test_kimi_web_quota.py -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
git add src/aacc/quota_service.py src/aacc/kimi_web_quota_service.py \
  src/aacc/app.py src/aacc/gui.py src/aacc/kimi_web_quota.py \
  tests/test_quota_service.py tests/test_kimi_web_quota_service.py \
  tests/test_app.py tests/test_gui_quota_wiring.py tests/test_kimi_web_quota.py
git commit -m "fix: synchronize Kimi quota refresh cycles"
```

---

### Task 4: Remove fabricated MONTH resets and correct documentation

**Files:**
- Modify: `src/aacc/kimi_web_quota.py`
- Modify: `tests/test_kimi_web_quota.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/release-notes-1.4.2.md`
- Modify: `docs/superpowers/specs/2026-07-27-kimi-web-quota-readable-bars-design.md`
- Modify: `docs/windows-verification-checklist.en.md`
- Modify: `docs/windows-verification-checklist.zh-CN.md`
- Test: `tests/test_release_docs.py`

**Interfaces:**
- Keeps: `parse_membership_quota(stats, subscription, *, now) -> KimiQuota`

- [ ] **Step 1: Add the RED annual-renewal regression**

```python
def test_monthly_reset_does_not_fall_back_to_annual_billing_time():
    result = parse_membership_quota(
        {"subscriptionBalance": {"amountUsedRatio": 0.31}},
        {
            "subscription": {
                "status": "SUBSCRIPTION_STATUS_ACTIVE",
                "nextBillingTime": "2027-07-20T13:28:47Z",
            }
        },
        now=NOW,
    )
    assert result.monthly is not None
    assert result.monthly.percentage == 31
    assert result.monthly.reset_at is None
```

- [ ] **Step 2: Verify RED, then remove billing fallbacks**

Run the single test and confirm it fails with a 2027 reset. Delete both
`nextBillingTime` fallback branches. Keep only the balance object's
`expireTime`/`resetTime`.

- [ ] **Step 3: Correct user-facing claims**

The old design is marked superseded by
`2026-07-27-kimi-web-session-correction-design.md`. User docs must say:

- the OS native per-application WebView store retains the first-party session;
- AACC stores a protected reuse decision, not cookies or a password;
- explicit logout disables reuse synchronously and attempts bounded native
  site-data cleanup;
- a quota lookup is metadata-only and consumes no generation tokens;
- both sources start in the same five-minute cycle;
- missing trustworthy reset time displays `--`;
- native persistence/logout still requires macOS and Windows manual sign-off.

- [ ] **Step 4: Run focused and full verification**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_kimi_web_quota.py tests/test_release_docs.py \
  tests/test_quota_bar.py tests/test_codex_quota_bar.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
git diff --check
```

- [ ] **Step 5: Commit**

```bash
git add src/aacc/kimi_web_quota.py tests/test_kimi_web_quota.py \
  README.md README.zh-CN.md docs/user-guide.en.md docs/user-guide.md \
  docs/release-notes-1.4.2.md \
  docs/superpowers/specs/2026-07-27-kimi-web-quota-readable-bars-design.md \
  docs/windows-verification-checklist.en.md \
  docs/windows-verification-checklist.zh-CN.md tests/test_release_docs.py
git commit -m "docs: correct Kimi web session guarantees"
```

---

### Task 5: Independent review and native release gates

**Files:**
- Modify: `.superpowers/sdd/progress.md`
- Modify: `docs/superpowers/plans/2026-07-27-windows-stable-setup-handoff.md`

- [ ] **Step 1: Request task-scoped review**

One reviewer checks gate/logout security and native WebView lifecycle. A second
checks timer/thread coordination, freshness, and MONTH reset semantics. Fix all
Critical and Important findings before proceeding.

- [ ] **Step 2: Re-run complete automated verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
git diff --check
```

- [ ] **Step 3: Keep formal release blocked**

Record automated evidence, but leave these manual items unchecked:

- macOS restart retains an authorized native Kimi session;
- Windows 10/11 restart retains an authorized native Kimi session;
- explicit logout followed by restart stays logged out on both platforms;
- five-minute cycles update web 5H/WEEK/MONTH and Kimi Code fallback together.

Do not create or move `v1.4.2` until those items and the Windows
separate-account ACL check are signed off.
