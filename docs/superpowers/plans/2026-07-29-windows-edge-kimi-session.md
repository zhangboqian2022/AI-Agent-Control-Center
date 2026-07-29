# Windows Edge Kimi Persistent Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unreliable embedded WebView2 Kimi login on Windows with an AACC-owned, persistent Microsoft Edge session that refreshes 5H, WEEK, and MONTH quota every five minutes.

**Architecture:** Keep `KimiWebQuotaService` and the macOS `KimiWebSession` intact, but select a new `KimiEdgeSession` on Windows. The Windows session owns one protected Edge profile, drives a short-lived Edge process through loopback CDP from a worker thread, evaluates the existing same-origin membership request in the page, and returns only quota JSON through Qt signals.

**Tech Stack:** Python 3.12, PySide6 signals/QObject, `httpx`, `websocket-client`, Microsoft Edge CDP, PyInstaller, Inno Setup, pytest, Ruff, mypy.

## Global Constraints

- Profile path is exactly `%LOCALAPPDATA%\AACC\kimi-edge-profile`.
- Never read, import, or modify the user's normal Edge profile.
- Reuse the dedicated login until manual logout, Kimi authentication expiry, or a security failure; do not impose a seven-day expiry.
- Keep cookies and access tokens inside Edge page context; never return, persist, or log them in Python.
- Bind CDP to loopback with a random port discovered from `DevToolsActivePort`.
- Preserve the existing 300,000 ms refresh interval and refresh 5H, WEEK, and MONTH together.
- Keep the existing native web-session implementation on macOS.
- Do not claim the Windows 10/11 real-machine release gate is complete.

---

## File Structure

- Create `src/aacc/kimi_edge_cdp.py`: pure Edge discovery, profile validation, managed-process lifecycle, CDP request/response transport, and quota evaluation.
- Create `src/aacc/kimi_edge_session.py`: Qt-facing asynchronous session implementing the existing web-session protocol.
- Create `tests/test_kimi_edge_cdp.py`: focused unit tests for discovery, launch, CDP parsing, redaction, and safe cleanup.
- Create `tests/test_kimi_edge_session.py`: Qt signal tests for login, refresh, logout, single-flight, cancellation, and persistence.
- Modify `src/aacc/kimi_web_quota_service.py`: platform-specific session factory.
- Modify `src/aacc/app.py`: do not initialize or run the native WebView2 smoke path on Windows.
- Modify `src/aacc/i18n.py`: Windows Edge-specific login/status/error wording.
- Modify `pyproject.toml` and `uv.lock`: add the Windows-only synchronous WebSocket transport.
- Modify `AACC-windows.spec`: package the CDP transport and remove the QtWebView hidden import.
- Modify `installer/AACC.iss` and `scripts/build_windows_installer.ps1`: stop bundling and requiring WebView2 Runtime.
- Modify `scripts/test_windows_package.ps1`: replace native WebView smoke assertions with managed Edge profile and application-start checks.
- Modify `tests/test_kimi_web_quota_service.py`, `tests/test_packaging.py`, and relevant app tests: enforce the new platform and packaging contracts.
- Modify `README.md`, `README.zh-CN.md`, `docs/user-guide.en.md`, `docs/user-guide.md`, `docs/release-notes-1.4.2.md`, `CHANGELOG.md`, `CHANGELOG.zh-CN.md`, and both Windows verification checklists: document the Edge login flow and persistent isolated profile.

---

### Task 1: Edge Discovery, Owned Profile, and Launch Contract

**Files:**
- Create: `src/aacc/kimi_edge_cdp.py`
- Create: `tests/test_kimi_edge_cdp.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `EdgeSessionError(category: KimiWebErrorCategory)`.
- Produces: `edge_profile_path(local_app_data: Path) -> Path`.
- Produces: `find_edge_executable(*, environ: Mapping[str, str], registry_reader: Callable[[str, str], str | None]) -> Path`.
- Produces: `validate_owned_profile(profile: Path, local_app_data: Path) -> None`.
- Produces: `EdgeLaunchSpec(executable: Path, arguments: tuple[str, ...], profile: Path)`.
- Produces: `build_edge_launch(executable: Path, profile: Path, *, visible: bool) -> EdgeLaunchSpec`.

- [ ] **Step 1: Write failing path and discovery tests**

```python
def test_edge_profile_is_isolated_under_local_app_data(tmp_path: Path) -> None:
    assert edge_profile_path(tmp_path) == tmp_path / "AACC" / "kimi-edge-profile"


def test_edge_discovery_accepts_only_existing_explicit_msedge_paths(tmp_path: Path) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")
    found = find_edge_executable(
        environ={"PROGRAMFILES(X86)": str(tmp_path)},
        registry_reader=lambda _key, _name: None,
    )
    assert found == edge


def test_profile_validation_rejects_reparse_point(tmp_path: Path, monkeypatch) -> None:
    profile = edge_profile_path(tmp_path)
    profile.mkdir(parents=True)
    monkeypatch.setattr(
        kimi_edge_cdp,
        "_is_reparse_point",
        lambda candidate: candidate == profile,
    )
    with pytest.raises(EdgeSessionError) as raised:
        validate_owned_profile(profile, tmp_path)
    assert raised.value.category is KimiWebErrorCategory.PROFILE_UNSAFE
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_kimi_edge_cdp.py -q`  
Expected: collection fails because `aacc.kimi_edge_cdp` does not exist.

- [ ] **Step 3: Implement the minimal discovery and launch types**

```python
@dataclass(frozen=True)
class EdgeLaunchSpec:
    executable: Path
    arguments: tuple[str, ...]
    profile: Path


def edge_profile_path(local_app_data: Path) -> Path:
    return local_app_data / "AACC" / "kimi-edge-profile"


def build_edge_launch(executable: Path, profile: Path, *, visible: bool) -> EdgeLaunchSpec:
    arguments = (
        f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        *(() if visible else ("--headless=new", "--disable-gpu")),
        f"--app={KIMI_MEMBERSHIP_URL}",
    )
    return EdgeLaunchSpec(executable, arguments, profile)
```

Implement discovery using only the App Paths registry value and explicit
`PROGRAMFILES`, `PROGRAMFILES(X86)`, and `LOCALAPPDATA` Edge installation
locations. Reject a non-file, a leaf name other than `msedge.exe`, and any
reparse-point candidate. Create/protect the profile with
`protect_directory(profile, platform="win32")`.

- [ ] **Step 4: Add launch-contract tests**

```python
def test_background_launch_uses_random_loopback_cdp_and_dedicated_profile(tmp_path: Path) -> None:
    spec = build_edge_launch(Path("C:/Edge/msedge.exe"), tmp_path, visible=False)
    assert "--remote-debugging-address=127.0.0.1" in spec.arguments
    assert "--remote-debugging-port=0" in spec.arguments
    assert "--headless=new" in spec.arguments
    assert all("Default" not in argument for argument in spec.arguments)
    assert spec.arguments[-1] == f"--app={KIMI_MEMBERSHIP_URL}"
```

- [ ] **Step 5: Add the Windows-only transport dependency and verify GREEN**

Add:

```toml
"websocket-client>=1.8,<2; sys_platform == 'win32'",
```

Run: `uv lock`  
Run: `.venv/bin/python -m pytest tests/test_kimi_edge_cdp.py -q`  
Expected: all Task 1 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/aacc/kimi_edge_cdp.py tests/test_kimi_edge_cdp.py pyproject.toml uv.lock
git commit -m "feat: add isolated Edge session foundation"
```

---

### Task 2: CDP Transport and Same-Origin Quota Query

**Files:**
- Modify: `src/aacc/kimi_edge_cdp.py`
- Modify: `src/aacc/kimi_web_session.py`
- Modify: `tests/test_kimi_edge_cdp.py`
- Modify: `tests/test_kimi_web_session.py`

**Interfaces:**
- Consumes: `EdgeLaunchSpec`.
- Produces: `CdpConnection.evaluate(expression: str) -> object`.
- Produces: `ManagedEdgeOperation.run(*, visible: bool, cancel: Event) -> EdgeQuotaResult`.
- Produces: `EdgeQuotaResult(stats: object, subscription: object)`.
- Produces: `membership_fetch_expression(generation: int) -> str`, shared by native WebView and CDP.

- [ ] **Step 1: Write failing endpoint and response tests**

```python
def test_devtools_endpoint_uses_active_port_and_loopback(tmp_path: Path) -> None:
    (tmp_path / "DevToolsActivePort").write_text("43127\n/devtools/browser/id\n")
    endpoint = read_devtools_endpoint(tmp_path)
    assert endpoint.http_origin == "http://127.0.0.1:43127"
    assert endpoint.browser_websocket == "ws://127.0.0.1:43127/devtools/browser/id"


def test_cdp_evaluate_ignores_events_and_matches_request_id() -> None:
    socket = FakeSocket(
        [
            '{"method":"Runtime.consoleAPICalled","params":{}}',
            '{"id":1,"result":{"result":{"value":{"kind":"quota"}}}}',
        ]
    )
    connection = CdpConnection(socket=socket)
    assert connection.evaluate("1 + 1") == {"kind": "quota"}
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_kimi_edge_cdp.py -q`  
Expected: fails because `read_devtools_endpoint` and `CdpConnection` are absent.

- [ ] **Step 3: Implement bounded CDP transport**

Implement:

```python
@dataclass(frozen=True)
class DevToolsEndpoint:
    http_origin: str
    browser_websocket: str


class CdpConnection:
    def evaluate(self, expression: str) -> object:
        response = self._request(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        remote = response["result"]["result"]
        if "exceptionDetails" in response["result"]:
            raise EdgeSessionError(KimiWebErrorCategory.REFRESH_FAILED)
        return remote.get("value")
```

The transport must:

- use `httpx.Client(trust_env=False)` for loopback `/json/list`;
- use `websocket.create_connection(..., timeout=...)`;
- reject non-loopback WebSocket URLs;
- cap JSON messages at 4 MiB;
- match monotonically increasing request IDs while ignoring CDP events;
- convert every external exception to a fixed `KimiWebErrorCategory`;
- never include a raw response, URL query, page value, or token in exceptions/logs.

- [ ] **Step 4: Convert the existing script into a shared promise expression**

Write a failing assertion in `tests/test_kimi_web_session.py`:

```python
def test_membership_expression_returns_payload_without_exporting_token() -> None:
    script = membership_fetch_expression(7)
    assert "return await Promise.all" in script
    assert "localStorage.getItem('access_token')" in script
    assert "Authorization" in script
    assert "console.log" not in script
```

Refactor `membership_fetch_script()` to wrap the shared expression and preserve
the existing title bridge. Existing native WebView tests must remain green.

- [ ] **Step 5: Implement managed operation and verify GREEN**

`ManagedEdgeOperation.run()` must:

1. validate and protect the profile;
2. remove a stale `DevToolsActivePort` regular file;
3. start Edge with `subprocess.Popen([executable, *arguments])`;
4. poll for the active-port file with a monotonic 15-second deadline and
   cancellation checks;
5. select the `www.kimi.com` page target from `/json/list`;
6. evaluate the shared membership expression;
7. classify `quota`, `unauthorized`, and `error` payloads;
8. request `Browser.close`, wait up to five seconds, then terminate only its
   own process if still alive;
9. return only `EdgeQuotaResult`.

Run: `.venv/bin/python -m pytest tests/test_kimi_edge_cdp.py tests/test_kimi_web_session.py -q`  
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/aacc/kimi_edge_cdp.py src/aacc/kimi_web_session.py tests/test_kimi_edge_cdp.py tests/test_kimi_web_session.py
git commit -m "feat: query Kimi quota through managed Edge"
```

---

### Task 3: Qt Session, Persistence, Refresh, and Logout

**Files:**
- Create: `src/aacc/kimi_edge_session.py`
- Create: `tests/test_kimi_edge_session.py`
- Modify: `src/aacc/kimi_web_quota_service.py`
- Modify: `tests/test_kimi_web_quota_service.py`

**Interfaces:**
- Consumes: `ManagedEdgeOperation.run(visible, cancel)`.
- Produces: `KimiEdgeSession(QObject)` with the exact `_WebSessionLike` methods and signals.
- Produces: `create_web_session(config_dir: Path, parent: QObject, language_manager: LanguageManager, platform: str = sys.platform) -> _WebSessionLike`.

- [ ] **Step 1: Write failing platform selection tests**

```python
def test_windows_selects_edge_session(qapp, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_quota_service.sys, "platform", "win32")
    monkeypatch.setattr(web_quota_service, "KimiEdgeSession", FakeEdgeSession)
    service = KimiWebQuotaService(tmp_path)
    service.open_login()
    assert isinstance(service._session, FakeEdgeSession)


def test_macos_keeps_native_web_session(qapp, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_quota_service.sys, "platform", "darwin")
    monkeypatch.setattr(web_quota_service, "KimiWebSession", FakeNativeSession)
    service = KimiWebQuotaService(tmp_path)
    service.open_login()
    assert isinstance(service._session, FakeNativeSession)
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/python -m pytest tests/test_kimi_web_quota_service.py -q`  
Expected: Windows test fails because the service always constructs `KimiWebSession`.

- [ ] **Step 3: Implement the platform factory**

Use a lazy Windows import so macOS never imports `websocket-client`:

```python
def create_web_session(..., platform: str = sys.platform) -> _WebSessionLike:
    if platform == "win32":
        from aacc.kimi_edge_session import KimiEdgeSession
        return KimiEdgeSession(config_dir, parent, language_manager=language_manager)
    return KimiWebSession(config_dir, parent, language_manager=language_manager)
```

- [ ] **Step 4: Write failing session lifecycle tests**

```python
def test_refresh_reuses_profile_without_opening_visible_window(qapp, session) -> None:
    session.login_state.set_may_reuse(True)
    session.refresh()
    session.worker.complete_quota(STATS, SUBSCRIPTION)
    assert session.worker.calls == [False]


def test_open_login_uses_visible_edge_and_persists_success(qapp, session) -> None:
    session.open_login()
    session.worker.complete_quota(STATS, SUBSCRIPTION)
    assert session.worker.calls == [True]
    assert session.login_state.may_reuse() is True


def test_expired_session_revokes_reuse_and_emits_signed_out(qapp, session) -> None:
    session.login_state.set_may_reuse(True)
    session.refresh()
    session.worker.complete_error(KimiWebErrorCategory.UNAUTHORIZED)
    assert session.login_state.may_reuse() is False
    assert session.login_states == [False]
```

- [ ] **Step 5: Implement asynchronous `KimiEdgeSession`**

The class uses one daemon `threading.Thread` per accepted operation and an
`Event` cancellation token. It never blocks the Qt thread. `_busy` enforces
single-flight. Each completion captures a generation and is ignored if stale.

Required behavior:

- `open_login()` starts `visible=True` even when reuse is currently false;
- `refresh()` starts `visible=False` only when `may_reuse()` is true;
- quota success persists reuse, emits `login_state_changed(True)`, then
  `quota_received(stats, subscription)`;
- unauthorized persists false and emits `login_state_changed(False)`;
- transient failures keep reuse and emit only the sanitized category;
- `close()` cancels and invalidates the generation;
- all logs contain fixed stages/categories only.

- [ ] **Step 6: Write failing logout and safe cleanup tests**

```python
def test_logout_revokes_reuse_before_profile_cleanup(qapp, session) -> None:
    session.login_state.set_may_reuse(True)
    session.logout()
    assert session.login_state.may_reuse() is False
    assert session.worker.cleanup_calls == [session.profile]


def test_cleanup_refuses_path_outside_owned_root(tmp_path: Path) -> None:
    with pytest.raises(EdgeSessionError):
        clear_owned_profile(tmp_path / "personal-edge", tmp_path)
```

- [ ] **Step 7: Implement logout cleanup and verify GREEN**

`clear_owned_profile()` validates the exact expected path, rejects reparse
points, renames it inside `%LOCALAPPDATA%\AACC` to a random quarantine name,
and removes that quarantine. Failure returns `False` from `logout()` and emits
the existing partial-logout category. It never follows an unsafe path.

Run: `.venv/bin/python -m pytest tests/test_kimi_edge_session.py tests/test_kimi_web_quota_service.py -q`  
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/aacc/kimi_edge_session.py src/aacc/kimi_web_quota_service.py tests/test_kimi_edge_session.py tests/test_kimi_web_quota_service.py
git commit -m "feat: persist Kimi login in managed Edge"
```

---

### Task 4: Windows Startup, UI Copy, and Packaging

**Files:**
- Modify: `src/aacc/app.py`
- Modify: `src/aacc/i18n.py`
- Modify: `AACC-windows.spec`
- Modify: `installer/AACC.iss`
- Modify: `scripts/build_windows_installer.ps1`
- Modify: `scripts/test_windows_package.ps1`
- Modify: `tests/test_app.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `KimiEdgeSession`.
- Produces: Windows application startup with no Qt WebView2 initialization or installer prerequisite.

- [ ] **Step 1: Write failing startup and packaging contract tests**

```python
def test_windows_startup_does_not_initialize_native_webview(monkeypatch) -> None:
    monkeypatch.setattr(app.sys, "platform", "win32")
    initialize = Mock()
    monkeypatch.setattr(app, "initialize_native_webview", initialize)
    app.initialize_web_quota_backend(DATA_DIR)
    initialize.assert_not_called()


def test_windows_installer_no_longer_bundles_webview2() -> None:
    script = (ROOT / "scripts/build_windows_installer.ps1").read_text()
    installer = (ROOT / "installer/AACC.iss").read_text()
    assert "MicrosoftEdgeWebview2Setup.exe" not in script
    assert "EnsureWebView2Runtime" not in installer
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/python -m pytest tests/test_app.py tests/test_packaging.py -q`  
Expected: fails because Windows still initializes and packages WebView2.

- [ ] **Step 3: Remove the Windows WebView2 dependency**

- Guard native initialization and `--smoke-native-webview` to non-Windows.
- Remove `PySide6.QtWebView` from `AACC-windows.spec`.
- Add `websocket` to Windows hidden imports if PyInstaller analysis does not
  include it through the lazy import.
- Remove WebView2 bootstrap download, trust validation, installer source,
  registry detection, install gate, and related temporary files.
- Replace the package smoke's native WebView test with assertions that the
  installed app starts without WebView2 and that the protected
  `%LOCALAPPDATA%\AACC\kimi-edge-profile` path contract is present.

- [ ] **Step 4: Add Windows Edge UI translations**

Add fixed keys:

```python
"kimi.edge_starting": "正在使用 AACC 专用 Edge 会话打开 Kimi 登录页面…",
"kimi.edge_explanation": "请在打开的 Edge 窗口登录 Kimi。该独立会话会安全保留，登录成功后自动关闭窗口并同步 5H、WEEK 和 MONTH。",
"kimi.edge_not_found": "未找到 Microsoft Edge，无法打开 Kimi 登录页面",
```

and exact English equivalents. Do not alter the macOS native WebView copy.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_app.py tests/test_packaging.py tests/test_kimi_edge_session.py -q`  
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/aacc/app.py src/aacc/i18n.py AACC-windows.spec installer/AACC.iss scripts/build_windows_installer.ps1 scripts/test_windows_package.ps1 tests/test_app.py tests/test_packaging.py
git commit -m "fix: replace Windows WebView2 login with Edge"
```

---

### Task 5: Documentation and Focused Verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/release-notes-1.4.2.md`
- Modify: `docs/windows-verification-checklist.en.md`
- Modify: `docs/windows-verification-checklist.zh-CN.md`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.zh-CN.md`

**Interfaces:**
- Produces: accurate bilingual user and release documentation.

- [ ] **Step 1: Write failing documentation assertions**

Replace WebView2-specific assertions in `tests/test_packaging.py` with:

```python
for document in english_docs:
    text = normalized(document)
    assert "AACC-owned Edge profile" in text
    assert "until you sign out" in text

for document in chinese_docs:
    text = normalized(document)
    assert "AACC 专用 Edge 配置" in text
    assert "手动退出" in text
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`  
Expected: fails because the documents still describe WebView2.

- [ ] **Step 3: Update bilingual documentation**

Document:

- the visible first-login Edge window;
- automatic close after successful login;
- persistent reuse across app/PC restarts until explicit logout or expiry;
- isolated AACC profile and no access to the normal Edge profile;
- five-minute 5H/WEEK/MONTH metadata refresh without model-token use;
- manual logout cleanup;
- truthful Windows 10/11 release-gate status.

Remove claims that the Setup installs or requires Evergreen WebView2 Runtime.

- [ ] **Step 4: Run focused verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_kimi_edge_cdp.py tests/test_kimi_edge_session.py tests/test_kimi_web_quota_service.py tests/test_kimi_web_session.py tests/test_app.py tests/test_packaging.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
git diff --check
```

Expected: every command exits 0 with no warnings introduced by this change.

- [ ] **Step 5: Commit**

```bash
git add README.md README.zh-CN.md docs/user-guide.en.md docs/user-guide.md docs/release-notes-1.4.2.md docs/windows-verification-checklist.en.md docs/windows-verification-checklist.zh-CN.md CHANGELOG.md CHANGELOG.zh-CN.md tests/test_packaging.py
git commit -m "docs: explain persistent Edge Kimi login"
```

---

### Task 6: One Windows Candidate Build and Desktop Delivery

**Files:**
- Modify only if the single Windows build reveals a tested packaging defect.

**Interfaces:**
- Produces: `AACC-1.4.2-Setup.exe` and `AACC-1.4.2-Setup.exe.sha256`.

- [ ] **Step 1: Push the implementation once**

Run:

```bash
git status --short
git push origin main
```

Expected: clean worktree and successful push.

- [ ] **Step 2: Run one Windows packaging workflow**

Use the existing GitHub Actions Windows package job. Do not run duplicate
branch and main smoke cycles. The workflow must build the frozen application,
compile the current-user Inno Setup package, and upload the Setup plus SHA-256.

- [ ] **Step 3: Inspect only the required build evidence**

Confirm:

- focused tests/static checks are green;
- `AACC.exe` starts on the Windows runner;
- Setup installs and launches;
- artifact contains exactly one Setup and its checksum;
- no WebView2 bootstrapper is present.

- [ ] **Step 4: Download and verify the Setup**

Download the artifact, verify its filename, nonzero size, SHA-256 sidecar, PE
header, and expected installer version. Copy the verified files to:

```text
/Users/zhangboqian/Desktop/AACC-1.4.2-Setup.exe
/Users/zhangboqian/Desktop/AACC-1.4.2-Setup.exe.sha256
```

- [ ] **Step 5: Report the candidate boundary**

Provide the exact SHA-256 and state that this is a Windows 1.4.2 candidate for
direct installation. Do not create `v1.4.2` or a formal Latest release until
the documented Windows 10/11 manual gate is signed off.
