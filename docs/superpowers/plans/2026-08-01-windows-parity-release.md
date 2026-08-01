# AACC 1.4.3 Windows Parity and Formal Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secure Windows OpenCode quota support, prove cross-platform parity contracts, and publish verified macOS and Windows `1.4.3` release assets.

**Architecture:** Keep the existing macOS `OpenCodeWebSession` unchanged. Add an OpenCode-specific Edge/CDP session on Windows with its own profile, state file, target allowlist, payload sanitizer, and worker lifecycle. Select the session in the quota service, keep GUI/service interfaces platform-neutral, and extend CI with explicit hosted-server compatibility contracts for Windows 10 and 11 labels.

**Tech Stack:** Python 3.12+, PySide6, httpx, websocket-client, psutil, pytest, PyInstaller, PowerShell, GitHub Actions, uv.

## Global Constraints

- Do not read, persist, or log browser cookies, tokens, prompts, replies, tool commands, or reasoning content.
- Windows owns only `%LOCALAPPDATA%/AACC/opencode-edge-profile`; Kimi's profile and the user's normal Edge profile remain untouched.
- Windows uses Edge/CDP because `AACC-windows.spec` must continue excluding `PySide6.QtWebView`.
- Hosted Windows Server 2022/2025 is compatibility evidence only; never claim real consumer Windows 10/11 verification.
- TDD: every behavior change starts with a failing focused test, then the smallest implementation.
- Version order is `src/aacc/__init__.py` → `pyproject.toml` → `uv.lock` → bilingual changelog/release notes.
- Stage only files belonging to the task; preserve the pre-existing dirty `.superpowers/sdd/*` and `AGENTS.md` files.
- No formal tag/release until local checks, required CI jobs, artifact checksums, and release metadata all pass.

---

### Task 1: Add the OpenCode Edge/CDP boundary

**Files:**
- Create: `src/aacc/opencode_edge_cdp.py`
- Create: `tests/test_opencode_edge_cdp.py`

**Interfaces:**
- Produces `OpenCodeEdgeSessionError`, `OpenCodeEdgeUnauthorizedError`, `OpenCodeEdgeCancelledError`, `OpenCodeEdgeLaunchSpec`, `opencode_edge_profile_path()`, `build_opencode_edge_launch()`, `select_opencode_target()`, `opencode_dom_extract_expression()`, `parse_opencode_edge_payload()`, and `ManagedOpenCodeEdgeOperation.run(visible, cancel)`.
- Reuses only tested loopback/CDP primitives from `aacc.kimi_edge_cdp` where their hard-coded Kimi URL/payload rules are not involved; do not import Kimi profile paths or share its profile.

- [ ] **Step 1: Write failing security and expression tests.**

  Cover the exact profile path, `--user-data-dir`, `--app=<validated workspace URL>`, headless flags, no shell invocation, target rejection for foreign pages/non-loopback WebSockets, extraction of exactly three percentage windows, unauthorized/page-timeout payloads, and rejection of booleans/out-of-range/non-finite values.

- [ ] **Step 2: Run the focused tests and verify they fail.**

  Run: `.venv/bin/python -m pytest tests/test_opencode_edge_cdp.py -q`

  Expected: collection failure because `aacc.opencode_edge_cdp` does not exist.

- [ ] **Step 3: Implement the bounded CDP module.**

  Use a separate profile path ending in `opencode-edge-profile`. Launch only an absolute Edge executable with `--user-data-dir`, loopback remote debugging, no-first-run flags, `--app=<workspace_url>`, and optional `--headless=new --disable-gpu`. Accept only page targets whose URL host is `opencode.ai`, whose WebSocket is `ws://127.0.0.1:<expected-port>/devtools/page/...`, and whose query/fragment are empty. Evaluate a Promise-based DOM expression that returns a small dictionary with `kind` and the three allowed usage windows. Convert invalid/foreign payloads into allowlisted OpenCode errors before they reach the Qt layer.

- [ ] **Step 4: Run focused tests and static checks.**

  Run: `.venv/bin/python -m pytest tests/test_opencode_edge_cdp.py -q`

  Expected: all new boundary tests pass. Then run `.venv/bin/ruff check src/aacc/opencode_edge_cdp.py tests/test_opencode_edge_cdp.py`.

- [ ] **Step 5: Commit the boundary.**

  ```bash
  git add -- src/aacc/opencode_edge_cdp.py tests/test_opencode_edge_cdp.py
  git commit -m "feat: add OpenCode Edge quota boundary"
  ```

### Task 2: Add the Windows OpenCode Qt session and site-specific reuse state

**Files:**
- Modify: `src/aacc/kimi_web_login_state.py`
- Create: `src/aacc/opencode_edge_session.py`
- Create: `tests/test_opencode_edge_session.py`
- Modify: `tests/test_kimi_web_login_state.py` if present, otherwise add the state-file assertions to `tests/test_kimi_edge_session.py`

**Interfaces:**
- `KimiWebLoginStateStore(config_dir, state_file_name="kimi-web-session-state.json")` preserves existing Kimi behavior and permits a validated OpenCode filename.
- `OpenCodeEdgeSession` exposes `login_state_changed`, `quota_received`, `error_occurred`, `set_workspace_url()`, `open_login()`, `refresh()`, `logout()`, `close()`, and `retranslate_ui()` matching `_WebSessionLike`.

- [ ] **Step 1: Write failing state isolation and lifecycle tests.**

  Verify OpenCode writes only `opencode-web-session-state.json`, login uses visible Edge, refresh uses headless Edge only after reuse permission, unauthorized refresh revokes permission, transient errors preserve permission, cancellation does not delete a live profile, logout revokes permission before cleaning the exact OpenCode profile, and close is idempotent.

- [ ] **Step 2: Run focused tests and verify the expected failure.**

  Run: `.venv/bin/python -m pytest tests/test_opencode_edge_session.py -q`

  Expected: import failure for the new session.

- [ ] **Step 3: Implement state-file parameterization and the Qt wrapper.**

  Keep the default Kimi filename unchanged. Build the OpenCode session around `ManagedOpenCodeEdgeOperation`, use a worker thread with generation/cancellation guards, map only `OpenCodeQuotaErrorCategory` values, and preserve the existing login/reuse semantics. Treat Edge launch failure, unsafe profile, malformed page result, and timeout as sanitized categories; never emit exception text.

- [ ] **Step 4: Run focused tests plus Kimi regression tests.**

  Run: `.venv/bin/python -m pytest tests/test_opencode_edge_session.py tests/test_kimi_edge_session.py tests/test_kimi_web_login_state.py -q`

  Expected: all pass, with Kimi behavior unchanged.

- [ ] **Step 5: Commit the session layer.**

  ```bash
  git add -- src/aacc/kimi_web_login_state.py src/aacc/opencode_edge_session.py tests/test_opencode_edge_session.py tests/test_kimi_edge_session.py tests/test_kimi_web_login_state.py
  git commit -m "feat: add Windows OpenCode quota session"
  ```

### Task 3: Select the Windows session and freeze it into the package

**Files:**
- Modify: `src/aacc/opencode_web_quota_service.py`
- Modify: `src/aacc/app.py`
- Modify: `AACC-windows.spec`
- Modify: `tests/test_opencode_web_quota_service.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- `_create_native_web_session()` remains the macOS path.
- `_ensure_session()` selects `OpenCodeEdgeSession` only when `sys.platform == "win32"`.
- `_default_opencode_web_quota_service_factory()` returns a configured service on Windows whenever `opencode_workspace_url` is configured, just as on macOS.

- [ ] **Step 1: Write failing factory and frozen-module tests.**

  Monkeypatch `sys.platform` to `win32` and assert the service creates an OpenCode Edge session, assert `build_runtime` can retain the service on Windows, and assert the Windows spec includes `aacc.opencode_edge_cdp` and `aacc.opencode_edge_session` while excluding `PySide6.QtWebView`.

- [ ] **Step 2: Run the focused tests and verify they fail.**

  Run: `.venv/bin/python -m pytest tests/test_opencode_web_quota_service.py tests/test_app.py tests/test_packaging.py -q`

  Expected: the Windows factory/module assertions fail against the current `None`/missing hidden-import behavior.

- [ ] **Step 3: Implement platform selection and packaging inclusion.**

  Import `sys` only where required, choose the Edge session in the service, remove the Windows `return None` guard from the app factory, and add the two dynamic modules to `AACC-windows.spec` hidden imports. Do not add QtWebView to the Windows spec.

- [ ] **Step 4: Run focused tests and full relevant regressions.**

  Run: `.venv/bin/python -m pytest tests/test_opencode_web_quota_service.py tests/test_app.py tests/test_packaging.py tests/test_gui_quota_wiring.py -q`

- [ ] **Step 5: Commit the platform integration.**

  ```bash
  git add -- src/aacc/opencode_web_quota_service.py src/aacc/app.py AACC-windows.spec tests/test_opencode_web_quota_service.py tests/test_app.py tests/test_packaging.py
  git commit -m "fix: enable OpenCode quota on Windows"
  ```

### Task 3A: Pass discovered Codex work directories into Windows targeting

**Files:**
- Modify: `src/aacc/codex_discovery.py`
- Modify: `tests/test_codex_discovery.py`

**Interfaces:**
- `CodexLocalDiscovery.discover()` passes the already-sanitized `session_work_dirs[conversation_id]` into `_default_terminal_config()` so Windows Terminal receives the same work-directory title used by Kimi Code.

- [ ] **Step 1: Add the failing regression assertion.**

  Extend the existing Windows discovery fixture with a session whose metadata contains a work directory and assert `task.config.terminal.window_title` equals that directory's basename.

- [ ] **Step 2: Run the focused test and verify it fails.**

  Run: `.venv/bin/python -m pytest tests/test_codex_discovery.py::test_discover_uses_windows_work_dir_for_terminal_title -q`

  Expected: failure because the current call uses `_default_terminal_config()` without the work directory.

- [ ] **Step 3: Pass the directory into the terminal factory.**

  Change only the Codex `TaskConfig` construction to call `_default_terminal_config(session_work_dirs.get(conversation_id))`; keep metadata allowlisting and macOS bundle targeting unchanged.

- [ ] **Step 4: Run Codex discovery regressions.**

  Run: `.venv/bin/python -m pytest tests/test_codex_discovery.py -q`

- [ ] **Step 5: Commit the Windows focus fix.**

  ```bash
  git add -- src/aacc/codex_discovery.py tests/test_codex_discovery.py
  git commit -m "fix: target Codex Windows terminals by work directory"
  ```

### Task 4: Add explicit hosted compatibility contracts for Windows 10/11 labels

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `scripts/test_windows_compatibility_contract.ps1`
- Create: `tests/test_windows_compatibility_contract.py`
- Modify: `KNOWN_LIMITATIONS.md`
- Modify: `KNOWN_LIMITATIONS.zh-CN.md`

**Interfaces:**
- PowerShell contract accepts `-Target windows-10|windows-11`, checks the current runner is Windows, validates the target label, and prints the required “hosted Windows Server compatibility evidence only” statement.

- [ ] **Step 1: Write failing contract tests.**

  Validate the PowerShell target allowlist, the explicit server-evidence wording, and the workflow matrix containing exactly `windows-10` and `windows-11` labels without pretending `runs-on` is a consumer OS.

- [ ] **Step 2: Run focused tests and verify the expected failure.**

  Run: `.venv/bin/python -m pytest tests/test_windows_compatibility_contract.py -q`

  Expected: missing script/workflow contract failure.

- [ ] **Step 3: Implement the contract job.**

  Add a non-duplicative `windows-consumer-compatibility-contract` matrix on the available Windows runner. Run platform automation, Edge/OpenCode boundary, installer contract, and Windows broker tests under each label, then execute the PowerShell assertion. Keep the existing full quality/frozen/package jobs unchanged as the primary evidence. Make the quality Test step diagnostic (`PYTHONFAULTHANDLER=1`, `pytest -ra -vv --tb=long --junitxml=...`) and upload the JUnit/coverage/pytest diagnostic files with `if: always()` so a native Windows exit cannot again hide the failing test behind a 31-dot log.

- [ ] **Step 4: Run focused tests and lint the script.**

  Run: `.venv/bin/python -m pytest tests/test_windows_compatibility_contract.py -q` and `.venv/bin/ruff check tests/test_windows_compatibility_contract.py`.

- [ ] **Step 5: Commit CI evidence boundaries.**

  ```bash
  git add -- .github/workflows/ci.yml scripts/test_windows_compatibility_contract.ps1 tests/test_windows_compatibility_contract.py KNOWN_LIMITATIONS.md KNOWN_LIMITATIONS.zh-CN.md
  git commit -m "ci: add Windows consumer compatibility contracts"
  ```

### Task 5: Advance to formal 1.4.3 and update bilingual release documentation

**Files:**
- Modify: `src/aacc/__init__.py`
- Modify: `pyproject.toml`
- Regenerate: `uv.lock`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.zh-CN.md`
- Create: `docs/release-notes-1.4.3.md`
- Modify: `README.md`, `README.zh-CN.md`, `docs/user-guide.md`, `docs/user-guide.en.md`
- Modify: `docs/windows-verification-checklist.en.md`, `docs/windows-verification-checklist.zh-CN.md`
- Modify: any current release-doc tests that intentionally assert rc.3 wording

- [ ] **Step 1: Add failing formal-version assertions.**

  Update/add tests that require Python/PEP 440 version `1.4.3`, public version `1.4.3`, matching bilingual changelog headings, `docs/release-notes-1.4.3.md`, download links for `v1.4.3`, four release assets, OpenCode quota terminology as “quota/额度”, and both-platform wording where the Windows Edge session is now supported.

- [ ] **Step 2: Run packaging/release-doc tests to establish the failure.**

  Run: `.venv/bin/python -m pytest tests/test_packaging.py tests/test_release_docs.py -q`

  Expected: current rc.3/version/link assertions fail.

- [ ] **Step 3: Change version sources in the required order and regenerate the lock.**

  Edit `__version__` and `pyproject.toml` to `1.4.3`, run `uv lock`, then update bilingual docs with the actual feature list and the explicit Win10/11 evidence boundary. Preserve old rc notes and do not replace old GitHub assets.

- [ ] **Step 4: Run formal documentation tests and inspect links.**

  Run: `.venv/bin/python -m pytest tests/test_packaging.py tests/test_release_docs.py -q` and `git diff --check`.

- [ ] **Step 5: Commit the formal release metadata.**

  ```bash
  git add -- src/aacc/__init__.py pyproject.toml uv.lock CHANGELOG.md CHANGELOG.zh-CN.md docs/release-notes-1.4.3.md README.md README.zh-CN.md docs/user-guide.md docs/user-guide.en.md docs/windows-verification-checklist.en.md docs/windows-verification-checklist.zh-CN.md tests/test_packaging.py tests/test_release_docs.py
  git commit -m "release: prepare AACC 1.4.3"
  ```

### Task 6: Review, verify, build, and publish

**Files/Artifacts:**
- Modify only if review finds an actionable issue.
- Build: `dist/AACC.app`, `dist/AACC-1.4.3.dmg`, CI `AACC-1.4.3-Setup.exe`, and their SHA-256 files.

- [ ] **Step 1: Request an independent code review of the full diff.**

  Review from the design commit `099d4b1` to the release-preparation commit must specifically inspect profile isolation, CDP allowlists, cancellation/cleanup, platform factory wiring, Windows frozen imports, and honest CI evidence. Fix Critical/Important findings and re-run focused tests.

- [ ] **Step 2: Run the complete local verification gate.**

  ```bash
  .venv/bin/python -m pytest -q
  .venv/bin/ruff check src tests scripts/capture_panel_screenshot.py
  .venv/bin/ruff format --check src tests scripts/capture_panel_screenshot.py
  .venv/bin/mypy src/aacc
  git diff --check 099d4b1..HEAD
  ```

  Expected: zero test failures, zero lint/format/type errors, and no whitespace errors.

- [ ] **Step 3: Build and verify the macOS DMG.**

  Run `AACC_VERSION=1.4.3 AACC_DMG_OUTPUT_DIR=/Users/zhangboqian/Desktop/codelight/dist ./scripts/build_dmg.sh`, verify `hdiutil verify`, `codesign --verify --deep --strict dist/AACC.app`, Info.plist version `1.4.3`, and a SHA-256 file named `AACC-1.4.3.dmg.sha256`. Install with `SKIP_BUILD=1 AACC_RUN_TESTS=0 ./scripts/install.sh --no-launch`, replacing the current Mac app only after the DMG/app checks pass.

- [ ] **Step 4: Push the release branch and wait for GitHub Actions.**

  Push `codex/release-1.4.3`, inspect all workflow runs, and wait until quality, both Windows package jobs, and the Windows compatibility-contract matrix are successful. Do not publish on partial or stale results.

- [ ] **Step 5: Download and verify the Windows Setup artifact.**

  Download the `AACC-Windows-Setup` artifact from the successful release-branch run, verify the Setup SHA-256 with `scripts/verify_windows_artifacts.py`/PowerShell checks, and rename/copy only the verified `AACC-1.4.3-Setup.exe` and `.sha256` into the release staging directory. Record the exact run URL and runner labels.

- [ ] **Step 6: Create and verify formal GitHub release `v1.4.3`.**

  Create a non-draft, non-prerelease release from the verified commit with the bilingual `docs/release-notes-1.4.3.md`, upload exactly the four required assets, run `scripts/verify_release.sh 1.4.3`, and confirm the release is Latest. Never delete or overwrite prior rc releases.

- [ ] **Step 7: Re-run post-publication verification and hand off.**

  Re-run `scripts/verify_release.sh 1.4.3`, inspect GitHub asset sizes/URLs and checksums, confirm the locally installed Mac app reports `1.4.3`, and report the release URL plus the precise hosted-server/consumer-Windows evidence boundary.
