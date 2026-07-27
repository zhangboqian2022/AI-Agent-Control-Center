# Kimi Web Quota and Readable Quota Bars Implementation Plan

**Status: Superseded by
`2026-07-27-kimi-web-session-correction.md`.**

Do not execute this plan. Qt's native `QWebView` does not expose a configurable
profile path, HTTP-cache clearing API, or persistent-storage clearing API. Use
the correction plan for the protected reuse gate, bounded correct-origin
cleanup, one five-minute coordinator, request deadlines, fallback freshness,
and trustworthy MONTH reset rules. The task list below is retained only as a
historical record of the initial implementation.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Historical goal:** Enlarge both quota strips and display Kimi 5H, WEEK, and
MONTH from membership metadata. Current session and refresh behavior is
defined only by the correction plan above.

**Architecture:** Keep `QuotaService` as the Kimi Code fallback and add a GUI-thread `KimiWebQuotaService` around Qt's native system `QWebView`. Parse the web Connect responses in a pure module, merge sources deterministically, and expose one `KimiQuota` stream to the existing bar.

**Tech Stack:** Python 3.12+, PySide6/Qt WebView (WKWebView/WebView2), httpx, pytest/pytest-qt, PyInstaller.

## Global Constraints

- Use TDD: every production behavior starts with a failing test.
- Support both macOS and Windows.
- For native website-session reuse, do not copy the Kimi password, cookies, or
  website bearer token into AACC's protected reuse gate. Kimi Code OAuth
  credentials remain separately protected.
- Poll web membership metadata every 300 seconds while AACC is running.
- Clear cached web and Kimi Code authorization only on explicit logout or server rejection.
- Preserve the existing A-format row order: Codex WEEK; Kimi 5H, WEEK, MONTH.

---

### Task 1: Quota strip readability

**Files:**
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/styles.qss`
- Test: `tests/test_quota_bar.py`
- Test: `tests/test_codex_quota_bar.py`

**Interfaces:**
- Consumes: existing `_QuotaMetricRow` and `_add_quota_metric_row`.
- Produces: readable metric rows with a 36 px percentage column and 7 px progress bar.

- [ ] **Step 1: Write failing GUI geometry and font tests**

Add assertions that `quotaPercent` uses at least 11 px, `quotaReset` at least
10 px, percentage labels reserve the width of `100%`, and progress bars have a
fixed height of 7 px.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_quota_bar.py tests/test_codex_quota_bar.py -q`

Expected: failures report the current 9 px fonts, 30 px percentage column, and
5 px progress bar.

- [ ] **Step 3: Implement the minimum layout and QSS changes**

Set percentage width to 36 px, progress height to 7 px, metric horizontal
spacing to 7 px, and QSS sizes to summary 12 px, period 10 px, percentage
11 px, reset 10 px.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: all focused tests pass.

### Task 2: Pure Kimi web quota parser and merge policy

**Files:**
- Create: `src/aacc/kimi_web_quota.py`
- Create: `tests/test_kimi_web_quota.py`

**Interfaces:**
- Consumes: `KimiQuota`, `QuotaDetail`, and `QuotaStatus` from
  `aacc.kimi_quota`.
- Produces:
  `parse_membership_quota(stats: object, subscription: object, *, now: datetime) -> KimiQuota`
  and
  `merge_kimi_quota(web: KimiQuota | None, code: KimiQuota | None) -> KimiQuota`.

- [ ] **Step 1: Write failing parser tests**

Use a fixture with `ratelimitCode5h`, `ratelimitCode7d`,
`subscriptionBalance.amountUsedRatio`, `expireTime`, and
`nextBillingTime`. Assert ratios are rounded to display percentages and all
resets are timezone-aware.

- [ ] **Step 2: Run the parser tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_kimi_web_quota.py -q`

Expected: import failure because `aacc.kimi_web_quota` does not exist.

- [ ] **Step 3: Implement defensive parsing**

Accept numeric ratios in either 0–1 or 0–100 form, reject non-finite/out-of-range
values, accept seconds/milliseconds/ISO timestamps, and return PARTIAL rather
than inventing data.

- [ ] **Step 4: Add failing merge tests**

Assert web values win for all known rows, Kimi Code fills only missing 5H/WEEK,
MONTH is never fabricated from Kimi Code, and the newest non-null `fetched_at`
is retained.

- [ ] **Step 5: Implement the merge and verify GREEN**

Run the command from Step 2. Expected: all parser and merge tests pass.

### Task 3: Native Kimi web session and page bridge (historical)

**Files:**
- Create: `src/aacc/kimi_web_session.py`
- Create: `tests/test_kimi_web_session.py`
- Modify: `AACC.spec`
- Modify: `AACC-windows.spec`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces `KimiWebSession(QObject)` with signals
  `login_state_changed(bool)`, `quota_received(object, object)`, and
  `error_occurred(str)`, plus methods `open_login(parent)`, `refresh()`, and
  `logout()`.
- The session owns a native `QWebView`; the operating system's per-application
  WebView store owns its first-party website session. AACC owns only the
  protected reuse decision described by the correction plan.

- [ ] **Step 1: Write failing bridge tests with fake page/profile objects**

This historical step is invalid and must not be executed: native `QWebView`
does not provide the profile/cache/storage APIs it assumed. The correction
plan instead tests a protected reuse decision, exact-origin bounded cleanup,
refresh generations, and logout that fails closed across restart.

- [ ] **Step 2: Run tests and verify RED**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_kimi_web_session.py -q`

Expected: module import failure.

- [ ] **Step 3: Implement the minimum WebEngine bridge**

Use `QWebView` on the Qt GUI thread, bridge only the two JSON response objects
through an encoded page-title signal, and redact all errors.

- [ ] **Step 4: Add and pass packaging tests**

Assert both specs collect `PySide6.QtWebView` and do not collect Qt WebEngine.
Run:
`.venv/bin/python -m pytest tests/test_kimi_web_session.py tests/test_packaging.py -q`.

### Task 4: Five-minute web service and source fallback

**Files:**
- Create: `src/aacc/kimi_web_quota_service.py`
- Create: `tests/test_kimi_web_quota_service.py`
- Modify: `src/aacc/quota_service.py`
- Modify: `tests/test_quota_service.py`

**Interfaces:**
- Produces `KimiWebQuotaService(QObject)` with a 300,000 ms timer and signals
  `quota_updated(object)`, `login_state_changed(bool)`, and
  `error_occurred(str)`.
- Consumes `KimiWebSession` and the pure parser from Task 2.

- [ ] **Step 1: Write failing timer, single-flight, and stale-value tests**

Assert startup triggers one refresh, periodic refresh uses 300 seconds, repeated
manual clicks coalesce, and an error emits stale state without erasing the last
good quota.

- [ ] **Step 2: Run tests and verify RED**

Run:
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_kimi_web_quota_service.py -q`

Expected: module import failure.

- [ ] **Step 3: Implement the service and verify GREEN**

Use `QTimer`, a pending flag, and one last-success snapshot. Run the command
from Step 2 and expect all tests to pass.

- [ ] **Step 4: Reduce Kimi Code polling to fallback cadence**

Write a failing `QuotaService` test expecting the default interval to be 300
seconds, update the default, and run
`.venv/bin/python -m pytest tests/test_quota_service.py -q`.

### Task 5: Runtime wiring, cached login UI, and unified logout

**Files:**
- Modify: `src/aacc/app.py`
- Modify: `src/aacc/gui.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_gui_quota_wiring.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- `MainWindow` receives the web quota service callbacks alongside the existing
  Kimi Code callbacks.
- `kimi_logout()` clears both services.

- [ ] **Step 1: Write failing runtime and GUI wiring tests**

Assert runtime creates the web service when Kimi quota is enabled, clicking an
unauthorized MONTH row opens web login, a web update controls all three rows,
and unified logout clears both credentials.

- [ ] **Step 2: Run focused tests and verify RED**

Run:
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_app.py tests/test_gui_quota_wiring.py -q`

Expected: constructor/wiring assertions fail.

- [ ] **Step 3: Implement wiring and copy**

Keep Kimi Code device authorization available as fallback, label the second
state “Kimi 会员网页登录”, and explain that no account password is stored.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all focused tests pass.

### Task 6: Full verification and visual acceptance

**Files:**
- Modify if needed: `scripts/capture_panel_screenshot.py`

**Interfaces:**
- Produces a verified macOS build and a Windows-buildable source tree.

- [ ] **Step 1: Run all automated gates**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
```

Expected: all commands exit 0.

- [ ] **Step 2: Capture and inspect the default-width panel**

Render the quota panel with 18%, 72%, and 31% fixture values. Verify `18%`
cannot be mistaken for `10%`, no label overlaps, and all reset timestamps fit.

- [ ] **Step 3: Build macOS and inspect packaging**

Run `scripts/build_app.sh`, verify the app with
`codesign --verify --deep --strict dist/AACC.app`, and launch the built app.

- [ ] **Step 4: Record Windows manual gate**

Run `scripts/build_windows.ps1` on Windows 10/11 and execute the existing
Windows verification checklist, including web login, persistence across
restart, five-minute refresh, and unified logout.
