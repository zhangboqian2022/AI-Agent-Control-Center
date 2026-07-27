# AACC 1.4.2 Windows Stable Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a non-elevated Windows Setup whose frozen and installed AACC builds start reliably, protect credentials with native exact ACLs, and isolate Codex child processes behind a static Job Object broker.

**Architecture:** Replace `whoami`/`icacls` with a Windows-only `win32security` adapter. Compile a fixed-purpose static `aacc-spawn.exe` that sanitizes only its own DLL environment and owns the Codex cmd/Node process tree. Package the onedir pair with Inno Setup and make frozen first launch, installed first launch, reinstall, graceful shutdown, and uninstall mandatory Windows CI stages.

**Tech Stack:** Python 3.12+, PySide6, pywin32 312, PyInstaller 6.x onedir, MSVC x64 C++17, Win32 Job Objects, Inno Setup 6.7.1, PowerShell, GitHub Actions `windows-2025`.

## Global Constraints

- Windows support is Windows 10/11 x64 only.
- Windows Setup is per-user: `PrivilegesRequired=lowest`, defaulting to `%LocalAppData%\Programs\AACC`.
- Runtime dependency is exactly `pywin32==312; sys_platform == 'win32'`.
- Sensitive-file DACL contains only current user, Local System, and built-in Administrators, with full control and inheritance disabled.
- The frozen runtime must never execute `whoami.exe`, `icacls.exe`, or `taskkill.exe`.
- The broker only launches the configured absolute Codex executable with `app-server --stdio`; it is not a general command runner.
- The broker is x64, `/MT` static CRT, protocol 1, and lives beside `AACC.exe`.
- Upgrade/uninstall waits up to 20 seconds for graceful shutdown and never force-kills AACC.
- Setup, broker, and AACC remain unsigned in 1.4.2; docs must retain the SmartScreen warning.
- `%APPDATA%\AACC` survives reinstall and uninstall.
- All new behavior is test-first; full pytest, Ruff, strict mypy, dependency audit, macOS build, and Windows package smoke must pass before merge.
- Formal `v1.4.2` remains blocked on the real Windows 10/11 checklist and separate-account denial test.

---

## File Map

- `src/aacc/file_security.py`: cross-platform atomic-protection facade.
- `src/aacc/file_security_windows.py`: exact Windows DACL creation and verification.
- `src/aacc/windows_broker.py`: locate and invoke the fixed-purpose broker.
- `native/aacc_spawn/aacc_spawn.cpp`: static process broker and Job Object lifecycle.
- `native/aacc_spawn/aacc_spawn.rc.in`: product/protocol version resource template.
- `scripts/build_spawn_broker.ps1`: locate MSVC, compile broker, and verify dependencies.
- `src/aacc/codex_app_server.py`: use broker command on packaged Windows and remove `taskkill`.
- `src/aacc/win32.py`: exact-window/message/process-wait wrappers for upgrade shutdown.
- `src/aacc/shutdown_windows.py`: Qt native shutdown listener and control client.
- `src/aacc/app.py`: early control-command handling and friendly fail-closed startup.
- `installer/AACC.iss`: per-user Inno Setup definition.
- `scripts/build_windows_installer.ps1`: verify ISCC 6.7.1 and build Setup/SHA-256.
- `scripts/test_windows_package.ps1`: frozen/install/reinstall/uninstall smoke orchestration.
- `.github/workflows/ci.yml`: pin Windows 2025 and run product-level gates.
- `scripts/capture_panel_screenshot.py`: deterministic privacy-safe UI fixture.
- `docs/images/panel-overview.png`: regenerated final demo screenshot.

---

### Task 1: Replace Windows command-line ACLs with exact native DACLs

**Files:**
- Create: `src/aacc/file_security_windows.py`
- Create: `tests/test_file_security_windows.py`
- Modify: `src/aacc/file_security.py`
- Modify: `src/aacc/config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `AACC-windows.spec`
- Modify: `tests/test_file_security.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: `protect_windows_path(path: Path, *, directory: bool = False, api: WindowsSecurityApi | None = None) -> None`
- Produces: `WindowsSecurityApi.current_user_sid() -> object`
- Produces: `WindowsSecurityApi.replace_dacl(path: Path, entries: tuple[tuple[object, int, int], ...]) -> None`
- Produces: `WindowsSecurityApi.verify_dacl(path: Path, expected_sids: tuple[object, ...], *, directory: bool) -> None`
- Consumed by: `file_security.protect_file()` and `file_security.protect_directory()`

- [ ] **Step 1: Add failing native-ACL contract tests**

```python
def test_windows_file_acl_is_replaced_with_exact_protected_allowlist(tmp_path):
    api = FakeWindowsSecurityApi()
    path = tmp_path / "配置 secret.yaml"
    path.write_text("", encoding="utf-8")

    protect_windows_path(path, api=api)

    assert api.replacements == [
        (
            path,
            ("CURRENT", "S-1-5-18", "S-1-5-32-544"),
            False,
        )
    ]
    assert api.verified


def test_windows_directory_acl_uses_inheritable_entries(tmp_path):
    api = FakeWindowsSecurityApi()
    protect_windows_path(tmp_path, directory=True, api=api)
    assert api.replacements[0][2] is True


def test_windows_acl_error_is_sanitized(tmp_path):
    api = FakeWindowsSecurityApi(error=OSError("C:\\secret token=abc"))
    with pytest.raises(FileProtectionError) as exc:
        protect_windows_path(tmp_path / "secret", api=api)
    assert "secret" not in str(exc.value)
    assert "abc" not in str(exc.value)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_file_security_windows.py tests/test_file_security.py tests/test_config.py -q
```

Expected: collection fails because `aacc.file_security_windows` does not exist.

- [ ] **Step 3: Add the pinned Windows dependency**

Add to `project.dependencies`:

```toml
"pywin32==312; sys_platform == 'win32'",
```

Regenerate the lock:

```bash
uv lock
```

Expected: `uv.lock` contains `pywin32` build 312 under the Windows marker.

- [ ] **Step 4: Implement the narrow Windows adapter**

Use this public shape:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from aacc.file_security import FileProtectionError

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"


class SecurityApi(Protocol):
    def current_user_sid(self) -> Any: ...
    def convert_sid(self, value: str) -> Any: ...
    def replace_dacl(
        self, path: Path, entries: tuple[tuple[Any, int, int], ...]
    ) -> None: ...
    def verify_dacl(
        self, path: Path, expected_sids: tuple[Any, ...], *, directory: bool
    ) -> None: ...


def protect_windows_path(
    path: Path,
    *,
    directory: bool = False,
    api: SecurityApi | None = None,
) -> None:
    resolved = api or WindowsSecurityApi()
    try:
        user = resolved.current_user_sid()
        system = resolved.convert_sid(SYSTEM_SID)
        administrators = resolved.convert_sid(ADMINISTRATORS_SID)
        flags = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if directory else 0
        entries = tuple(
            (sid, FILE_ALL_ACCESS, flags)
            for sid in (user, system, administrators)
        )
        resolved.replace_dacl(path, entries)
        resolved.verify_dacl(
            path, (user, system, administrators), directory=directory
        )
    except Exception as error:
        if isinstance(error, FileProtectionError):
            raise
        raise FileProtectionError(
            f"Windows credential protection failed ({type(error).__name__})"
        ) from None
```

`WindowsSecurityApi` lazily imports `win32api`, `win32con`,
`win32security`, `ntsecuritycon`, and `pywintypes`. It obtains `TokenUser`,
constructs a fresh `win32security.ACL`, calls `SetNamedSecurityInfo` with
`DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION`, and reads
the descriptor back. Verification rejects inheritance, deny ACEs, unexpected
SIDs, duplicate SIDs, and masks other than `FILE_ALL_ACCESS`.

- [ ] **Step 5: Route the facade and remove external commands**

`file_security.py` becomes:

```python
def protect_file(path, *, descriptor=None, platform=sys.platform) -> None:
    if platform == "win32":
        from aacc.file_security_windows import protect_windows_path
        protect_windows_path(path)
        return
    if descriptor is not None:
        cast(Any, os).fchmod(descriptor, 0o600)
    else:
        os.chmod(path, 0o600)


def protect_directory(path, *, platform=sys.platform) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if platform == "win32":
        from aacc.file_security_windows import protect_windows_path
        protect_windows_path(path, directory=True)
    else:
        os.chmod(path, 0o700)
```

Delete `_current_windows_user_sid`, `RunCommand`, and every runtime reference
to `whoami` and `icacls`.

In `load_config()`, replace the Windows re-publication branch with a direct
`protect_file(path, platform=sys.platform)` verification/repair.

- [ ] **Step 6: Add Windows real-ACL integration tests**

```python
@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ACL APIs")
def test_real_windows_acl_is_exact_and_protected(tmp_path):
    path = tmp_path / "目录 with spaces" / "配置.yaml"
    path.parent.mkdir()
    path.write_text("secret", encoding="utf-8")
    protect_file(path)
    snapshot = read_windows_acl(path)
    assert snapshot.protected
    assert snapshot.allow_sids == {
        current_user_sid_string(),
        "S-1-5-18",
        "S-1-5-32-544",
    }
    assert snapshot.deny_sids == set()
    assert not snapshot.inherited
```

Seed an Everyone explicit read ACE through the test API, protect again, and
assert it disappears.

- [ ] **Step 7: Make PyInstaller collection explicit**

Add these Windows hidden imports:

```python
"win32api",
"win32con",
"win32security",
"ntsecuritycon",
"pywintypes",
```

Extend packaging tests to require these modules and the ABI-matching
`pywintypes{major}{minor}.dll` payload. The dependency release remains pinned
to pywin32 312; the DLL suffix follows the active supported Python ABI (for
example, `312` for Python 3.12 and `313` for Python 3.13).

- [ ] **Step 8: Run the ACL slice and quality checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_file_security.py tests/test_file_security_windows.py tests/test_config.py tests/test_packaging.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
```

Expected: all pass on macOS, with real Windows tests skipped.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock AACC-windows.spec src/aacc/file_security.py \
  src/aacc/file_security_windows.py src/aacc/config.py \
  tests/test_file_security.py tests/test_file_security_windows.py \
  tests/test_config.py tests/test_packaging.py
git commit -m "fix: protect Windows credentials with native ACLs"
```

---

### Task 2: Add friendly fail-closed startup errors

**Files:**
- Modify: `src/aacc/app.py`
- Modify: `AACC-windows.spec`
- Modify: `tests/test_app.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `FileProtectionError`
- Produces: `_show_startup_security_error(data_dir: Path, error: FileProtectionError) -> int`
- Produces: `_create_qapplication() -> QApplication`

- [ ] **Step 1: Add failing startup-error tests**

```python
def test_security_failure_shows_sanitized_dialog_and_returns_nonzero(
    tmp_path, monkeypatch
):
    shown = []
    monkeypatch.setattr(app_module, "_create_qapplication", FakeApplication)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, title, text: shown.append((title, text)),
    )
    monkeypatch.setattr(
        app_module,
        "build_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FileProtectionError("token=C:\\secret")
        ),
    )
    result = app_module._run_application(
        tmp_path / "config.yaml", tmp_path / "aacc.db", tmp_path
    )
    assert result == 1
    assert shown
    assert "token" not in shown[0][1]
    assert str(tmp_path / "logs" / "app.log") in shown[0][1]
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_app.py -q
```

Expected: failure because `_create_qapplication` and the caught path do not
exist.

- [ ] **Step 3: Create Qt before runtime construction and catch only known startup security errors**

```python
def _create_qapplication() -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setApplicationName("AACC")
    app.setOrganizationName("AACC")
    app.setQuitOnLastWindowClosed(False)
    return app


def _show_startup_security_error(data_dir: Path, error: FileProtectionError) -> int:
    category = type(error).__name__
    _logger.critical("Startup credential protection failed: %s", category)
    QMessageBox.critical(
        None,
        "AACC 启动失败 / Startup failed",
        "AACC 无法保护本机凭据文件，因此没有保存新的凭据。\n"
        "AACC could not protect its local credential file, so no new "
        "credential was saved.\n\n"
        f"日志 / Log: {data_dir / 'logs' / 'app.log'}\n"
        f"诊断 / Diagnostic: STARTUP-ACL-{category}",
    )
    return 1
```

Initialize logging and Qt before `build_runtime`, catch
`FileProtectionError`, and return the friendly path. Do not catch unrelated
programming errors.

- [ ] **Step 4: Disable raw windowed traceback fallback**

Set `disable_windowed_traceback=True` only after the known startup errors have
a visible tested dialog.

- [ ] **Step 5: Run and commit**

```bash
.venv/bin/python -m pytest tests/test_app.py tests/test_packaging.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
git add src/aacc/app.py AACC-windows.spec tests/test_app.py tests/test_packaging.py
git commit -m "fix: show safe Windows startup errors"
```

---

### Task 3: Build the fixed-purpose static Codex broker

**Files:**
- Create: `native/aacc_spawn/aacc_spawn.cpp`
- Create: `native/aacc_spawn/aacc_spawn.rc.in`
- Create: `scripts/build_spawn_broker.ps1`
- Create: `tests/native/fake_codex_server.py`
- Create: `tests/native/fake_codex.cmd`
- Create: `tests/native/spawn_descendant.py`
- Create: `tests/test_spawn_broker_contract.py`
- Modify: `scripts/build_windows.ps1`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: `build/native/aacc-spawn.exe`
- Broker CLI: `--protocol 1 --parent-pid PID --bundle-dir DIR --codex PATH`
- Broker version CLI: `--version` prints `protocol=1 product=1.4.2`
- Exit stages: 10 arguments, 11 DLL reset, 12 environment, 20 job create,
  21 job configure, 22 target create, 23 job assign, 24 resume, 25 wait

- [ ] **Step 1: Add failing packaging and broker contract tests**

```python
def test_windows_build_compiles_static_spawn_broker():
    script = (ROOT / "scripts" / "build_spawn_broker.ps1").read_text()
    assert "/MT" in script
    assert "/W4" in script
    assert "/WX" in script
    assert "dumpbin" in script.lower()
    assert "VCRUNTIME" in script
    assert "ucrtbase" in script


def test_broker_source_is_fixed_to_codex_app_server():
    source = (ROOT / "native" / "aacc_spawn" / "aacc_spawn.cpp").read_text()
    assert 'L"app-server"' in source
    assert 'L"--stdio"' in source
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in source
    assert "CREATE_SUSPENDED" in source
    assert "AssignProcessToJobObject" in source
    assert "SetDllDirectoryW(nullptr)" in source
    assert "taskkill" not in source.lower()
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_spawn_broker_contract.py tests/test_packaging.py -q
```

Expected: missing native source and build script.

- [ ] **Step 3: Implement strict argument parsing and fixed protocol**

The C++ entry accepts only:

```cpp
struct Options {
    DWORD parent_pid;
    std::wstring bundle_dir;
    std::wstring codex_path;
};

// Accepted commands:
// aacc-spawn.exe --version
// aacc-spawn.exe --protocol 1 --parent-pid N
//   --bundle-dir ABSOLUTE --codex ABSOLUTE
```

Reject unknown flags, duplicate flags, non-decimal PID, relative paths,
directories, non-`.exe`/`.cmd`/`.bat` targets, and protocol values other than
1 with exit stage 10.

- [ ] **Step 4: Implement isolated environment and target creation**

Core order:

```cpp
if (!SetDllDirectoryW(nullptr)) return Fail(11, GetLastError());
EnvironmentBlock env = BuildSanitizedEnvironment(options.bundle_dir);
UniqueHandle parent(OpenProcess(SYNCHRONIZE, FALSE, options.parent_pid));
UniqueHandle job(CreateJobObjectW(nullptr, nullptr));
ConfigureKillOnClose(job.get());
TargetCommand command = BuildCodexCommand(options.codex_path);
SuspendedProcess target = CreateSuspendedWithStdio(command, env);
if (!AssignProcessToJobObject(job.get(), target.process.get())) {
    TerminateProcess(target.process.get(), 23);
    return Fail(23, GetLastError());
}
if (ResumeThread(target.thread.get()) == static_cast<DWORD>(-1)) {
    TerminateJobObject(job.get(), 24);
    return Fail(24, GetLastError());
}
return WaitForParentOrTarget(parent.get(), target.process.get(), job.get());
```

Use RAII handle wrappers. `STARTUPINFOEXW` supplies a
`PROC_THREAD_ATTRIBUTE_HANDLE_LIST` containing only stdin, stdout, and a NUL
stderr handle. Job handles are non-inheritable.

For scripts, resolve absolute System32 `cmd.exe` and build `/D /S /C` with a
dedicated quoting routine. For `.exe`, set `lpApplicationName` explicitly.
The target arguments are always fixed to `app-server --stdio`.

- [ ] **Step 5: Implement fixed diagnostics**

```cpp
int Fail(int stage, DWORD error) {
    fwprintf(stderr, L"AACC_BROKER_ERROR stage=%d win32=%lu\n", stage, error);
    return stage;
}
```

Never write target path, bundle path, environment, account name, or command
line to stderr.

- [ ] **Step 6: Build with MSVC and enforce dependency allowlist**

`build_spawn_broker.ps1`:

1. reads version with `uv version --short`;
2. enumerates all `vswhere` instances as UTF-8 JSON through bounded async
   stdout/stderr reads, explicitly expands the PowerShell 5.1 JSON result,
   rejects non-local candidate paths, sorts and de-duplicates candidates by
   normalized path and installation version, then initializes each x64
   developer environment in isolation; it selects only a candidate whose
   `VCToolsInstallDir` is beneath that candidate and whose parsed `cl.exe`,
   `link.exe`, and `dumpbin.exe` are beneath it, while `rc.exe` is beneath a
   local `WindowsSdkDir`;
3. renders the `.rc` file with product version and protocol 1;
4. compiles with `/std:c++17 /O2 /MT /GS /guard:cf /W4 /WX /DUNICODE
   /D_UNICODE`;
5. links with `/DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA`;
6. runs `dumpbin /HEADERS` and requires x64;
7. runs `dumpbin /DEPENDENTS`;
8. rejects `VCRUNTIME`, `MSVCP`, `ucrtbase`, Python, Qt, and third-party DLLs;
9. writes `build/native/aacc-spawn.exe`.

- [ ] **Step 7: Add real Windows broker integration in PowerShell**

The Windows-only test launches fake `.cmd` and `.exe` targets through the
broker, writes JSON-RPC on stdin, verifies output and exit code, then launches
the descendant fixture, terminates the broker, and polls all recorded PIDs
until none exist.

Include directories named:

```text
临时 AACC &(broker)
```

Run the normal probe 20 times and compare broker/child process counts before
and after.

- [ ] **Step 8: Copy broker into the onedir root**

After PyInstaller:

```powershell
Copy-Item "build\native\aacc-spawn.exe" "dist\AACC\aacc-spawn.exe" -Force
$rootFiles = Get-ChildItem "dist\AACC" | Select-Object -ExpandProperty Name
if (@($rootFiles | Sort-Object) -join "," -ne "_internal,AACC.exe,aacc-spawn.exe") {
    throw "unexpected Windows package root"
}
```

- [ ] **Step 9: Run hosted Windows tests via CI after pushing this task**

Expected on Windows:

- broker compiles with zero warnings;
- dependency allowlist passes;
- JSON stdio round-trip passes;
- broker termination removes the complete descendant tree.

- [ ] **Step 10: Commit**

```bash
git add native/aacc_spawn scripts/build_spawn_broker.ps1 \
  scripts/build_windows.ps1 tests/native tests/test_spawn_broker_contract.py \
  tests/test_packaging.py
git commit -m "feat: isolate Windows Codex processes"
```

---

### Task 4: Route packaged Codex app-server calls through the broker

**Files:**
- Create: `src/aacc/windows_broker.py`
- Create: `tests/test_windows_broker.py`
- Modify: `src/aacc/codex_app_server.py`
- Modify: `src/aacc/app.py`
- Modify: `tests/test_codex_app_server.py`
- Modify: `tests/test_app.py`
- Modify: `AACC-windows.spec`

**Interfaces:**
- Produces: `BrokerCommand(args: tuple[str, ...], creationflags: int)`
- Produces: `build_broker_command(codex: Path, *, parent_pid: int, executable: Path, bundle_dir: Path) -> BrokerCommand`
- Consumed by: `CodexAppServerReader(command_factory=...)`

- [ ] **Step 1: Add failing command-routing tests**

```python
def test_frozen_windows_reader_uses_absolute_broker(tmp_path):
    broker = tmp_path / "AACC" / "aacc-spawn.exe"
    broker.parent.mkdir()
    broker.write_bytes(b"MZ")
    codex = tmp_path / "用户 & tools" / "codex.cmd"
    command = build_broker_command(
        codex,
        parent_pid=42,
        executable=broker.parent / "AACC.exe",
        bundle_dir=broker.parent / "_internal",
    )
    assert command.args == (
        str(broker),
        "--protocol", "1",
        "--parent-pid", "42",
        "--bundle-dir", str(broker.parent / "_internal"),
        "--codex", str(codex),
    )


def test_reader_reaps_broker_without_taskkill(fake_process):
    reader = CodexAppServerReader(
        Path("codex.cmd"),
        command_factory=lambda _path: BrokerCommand(("broker",), 0),
    )
    reader._reap(fake_process)
    assert fake_process.terminate_calls == 1
    assert fake_process.kill_calls == 0
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_windows_broker.py tests/test_codex_app_server.py -q
```

Expected: `aacc.windows_broker` missing and old test still expects `taskkill`.

- [ ] **Step 3: Implement broker path and command construction**

```python
@dataclass(frozen=True)
class BrokerCommand:
    args: tuple[str, ...]
    creationflags: int = WINDOWS_PROCESS_CREATION_FLAGS


def packaged_broker_path() -> Path | None:
    override = os.environ.get("AACC_SPAWN_BROKER_PATH")
    if override:
        return Path(override).resolve()
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("aacc-spawn.exe")
    return None
```

`build_broker_command` verifies absolute regular broker and Codex paths. It
uses `sys._MEIPASS` only as the `--bundle-dir` value and never searches PATH.

- [ ] **Step 4: Inject a process command factory into the reader**

```python
ProcessCommandFactory = Callable[[Path], BrokerCommand]

class CodexAppServerReader:
    def __init__(..., command_factory: ProcessCommandFactory | None = None):
        self._command_factory = command_factory

    def _process_command(self) -> BrokerCommand:
        if self._command_factory is None:
            return BrokerCommand(
                (str(self._executable), "app-server", "--stdio"),
                WINDOWS_PROCESS_CREATION_FLAGS if self._platform == "win32" else 0,
            )
        return self._command_factory(self._executable)
```

Use `command.args` and `command.creationflags` in `Popen`. `_reap()` closes
stdin, terminates the broker if running, waits, then kills only the broker if
needed. Delete `RunCommand`, `_run`, and every `taskkill` branch.

- [ ] **Step 5: Wire the product factory**

On packaged Windows, `_default_codex_quota_service_factory` requires the
absolute broker and passes a broker command factory. If the broker is missing,
log a sanitized warning and build only the local-session fallback; never
direct-spawn Codex from the frozen app.

On macOS, preserve the direct app-server reader. On Windows source
development, require `AACC_SPAWN_BROKER_PATH` for live quota; otherwise use
the local fallback.

- [ ] **Step 6: Extend archive/root checks**

Require `aacc.windows_broker` in the archive and
`dist\AACC\aacc-spawn.exe` at the package root.

- [ ] **Step 7: Run tests and commit**

```bash
.venv/bin/python -m pytest tests/test_windows_broker.py \
  tests/test_codex_app_server.py tests/test_app.py tests/test_packaging.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
git add src/aacc/windows_broker.py src/aacc/codex_app_server.py src/aacc/app.py \
  tests/test_windows_broker.py tests/test_codex_app_server.py tests/test_app.py \
  tests/test_packaging.py AACC-windows.spec
git commit -m "fix: broker Windows Codex quota processes"
```

---

### Task 5: Add graceful shutdown for Setup upgrades

**Files:**
- Create: `src/aacc/shutdown_windows.py`
- Create: `tests/test_shutdown_windows.py`
- Modify: `src/aacc/win32.py`
- Modify: `src/aacc/app.py`
- Modify: `tests/test_win32.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Produces: `request_shutdown_for_update(timeout_ms: int = 20_000, win32_module: object | None = None) -> int`
- Produces: `WindowsShutdownListener.start(qt_app, window) -> None`
- Produces: `WindowsShutdownListener.stop() -> None`

- [ ] **Step 1: Add failing shutdown listener/client tests**

```python
def test_shutdown_message_quits_through_window(qtbot):
    window = FakeWindow()
    api = FakeWin32ShutdownApi()
    listener = WindowsShutdownListener(win32_module=api)
    listener.start(QApplication.instance(), window)
    listener.dispatch_message(api.shutdown_message)
    QCoreApplication.processEvents()
    assert window.quit_calls == 1


def test_shutdown_client_waits_for_exact_aacc_process():
    api = FakeWin32ShutdownApi(exact_window=100, pid=200, image="AACC.exe")
    assert request_shutdown_for_update(win32_module=api) == 0
    assert api.posted == [(100, api.shutdown_message)]
    assert api.waited == [(200, 20_000)]
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_shutdown_windows.py tests/test_win32.py tests/test_app.py -q
```

- [ ] **Step 3: Add exact Win32 wrappers**

Extend `win32.py` with injectable wrappers for:

```python
register_window_message(name: str) -> int
find_exact_window(title: str) -> int | None
window_process_id(hwnd: int) -> int
process_image_name(pid: int) -> str
post_message(hwnd: int, message: int) -> None
wait_for_process_exit(pid: int, timeout_ms: int) -> bool
```

Set `argtypes`/`restype` for every new ctypes call. `find_exact_window`
compares full titles, and the client additionally requires the executable
basename `AACC.exe`.

- [ ] **Step 4: Implement the independent Qt native event filter**

Use `RegisterWindowMessageW("AACC.ShutdownForUpdate.v1")`. The filter posts:

```python
QTimer.singleShot(0, self._window.quit_application)
```

It is installed independently from the hotkey event filter and remains active
even if global hotkeys fail.

- [ ] **Step 5: Handle the control command before config and instance lock**

At the start of `main()`:

```python
if sys.platform == "win32" and sys.argv[1:] == ["--shutdown-for-update"]:
    return request_shutdown_for_update()
```

Install the listener immediately after `MainWindow` construction. Stop it in
the same cleanup closure that closes services and SQLite.

- [ ] **Step 6: Run and commit**

```bash
.venv/bin/python -m pytest tests/test_shutdown_windows.py tests/test_win32.py tests/test_app.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
git add src/aacc/shutdown_windows.py src/aacc/win32.py src/aacc/app.py \
  tests/test_shutdown_windows.py tests/test_win32.py tests/test_app.py
git commit -m "feat: gracefully stop AACC for Windows updates"
```

---

### Task 6: Build the per-user Inno Setup package

**Files:**
- Create: `installer/AACC.iss`
- Create: `scripts/build_windows_installer.ps1`
- Create: `tests/test_windows_installer_contract.py`
- Modify: `scripts/build_windows.ps1`
- Modify: `tests/test_packaging.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `dist/AACC/{AACC.exe,aacc-spawn.exe,_internal}`
- Produces: `dist/installer/AACC-1.4.2-Setup.exe`
- Produces: `dist/installer/AACC-1.4.2-Setup.exe.sha256`

- [ ] **Step 1: Add failing installer contract tests**

```python
def test_inno_setup_is_per_user_and_upgrade_stable():
    text = (ROOT / "installer" / "AACC.iss").read_text()
    assert "PrivilegesRequired=lowest" in text
    assert "DefaultDirName={localappdata}\\Programs\\AACC" in text
    assert "UsePreviousAppDir=yes" in text
    assert "UninstallLogMode=append" in text
    assert "ArchitecturesAllowed=x64compatible" in text
    assert "C174E242-E193-5863-8A46-F16152875173" in text
    assert "CloseApplications=no" in text
    assert "RestartApplications=no" in text
    assert "--shutdown-for-update" in text
    assert "taskkill" not in text.lower()
    assert "terminateprocess" not in text.lower()
    assert "stop-process" not in text.lower()
    assert "wm_close" not in text.lower()
    assert "{userappdata}\\AACC" not in text.split("[UninstallDelete]")[-1]
    assert 'Name: "{app}\\_internal"' in text
    assert 'Name: "{app}\\*"' not in text


def test_windows_installer_build_pins_iscc():
    text = (ROOT / "scripts" / "build_windows_installer.ps1").read_text()
    assert "6.7.1" in text
    assert "4d11e8050b6185e0d49bd9e8cc661a7a59f44959a621d31d11033124c4e8a7b0" in text
    assert "AACC_ISCC_PATH" in text
    assert "innosetup-6.7.1.exe" in text
    assert "uv version --short" in text
    assert "Get-FileHash" in text
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_windows_installer_contract.py tests/test_packaging.py -q
```

- [ ] **Step 3: Implement the Inno contract**

Key setup entries:

```ini
#ifndef MyAppVersion
  #error MyAppVersion must be supplied by ISCC
#endif

[Setup]
AppId={{C174E242-E193-5863-8A46-F16152875173}
AppName=AACC
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\AACC
PrivilegesRequired=lowest
UsePreviousAppDir=yes
UninstallLogMode=append
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
RestartApplications=no
OutputDir=..\dist\installer
OutputBaseFilename=AACC-{#MyAppVersion}-Setup
UninstallFilesDir={app}\uninstall

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\AACC\AACC.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\AACC\aacc-spawn.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\AACC\_internal\*"; DestDir: "{app}\_internal"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
```

Add Start Menu and unchecked desktop-icon tasks. `[Run]` launches AACC with
`postinstall nowait skipifsilent`.

When the existing `{app}\AACC.exe` exists, `PrepareToInstall` and
`InitializeUninstall` call only that executable with
`--shutdown-for-update` through Inno `Exec(..., ewWaitUntilTerminated,
ResultCode)`. An `Exec` failure or any non-zero result aborts the install or
uninstall with a bilingual message telling the user to exit AACC from the
tray and retry. `CloseApplications=no` keeps Restart Manager from bypassing
this contract. Never use `taskkill`, `WM_CLOSE`, PowerShell process control,
`/FORCECLOSEAPPLICATIONS`, or any forced termination path.

Validate `MyAppVersion` against the project's restricted numeric version
grammar before passing it to ISPP. Keep `Filename`, `Parameters`, and
`WorkingDir` as separate `Exec` arguments. The installer must not delete
`{app}` recursively or mention `{userappdata}\AACC`, `config.yaml`,
`aacc.db`, or `kimi-credentials.json` in deletion sections or Pascal code.

- [ ] **Step 4: Implement the installer build script**

The script:

1. validates the onedir root contract;
2. reads the package version;
3. resolves `AACC_ISCC_PATH`; otherwise downloads the immutable
   `innosetup-6.7.1.exe` release from
   `https://github.com/jrsoftware/issrc/releases/download/is-6_7_1/innosetup-6.7.1.exe`
   into a versioned cache;
4. verifies SHA-256
   `4d11e8050b6185e0d49bd9e8cc661a7a59f44959a621d31d11033124c4e8a7b0`
   before executing either a cached or newly downloaded installer, requires a
   valid Authenticode signature, installs it into a task-local directory, and
   requires product/file version 6.7.1;
5. resolves all paths with literal-path APIs, removes only the expected stale
   versioned Setup/checksum, validates the onedir root contains exactly
   `AACC.exe`, `aacc-spawn.exe`, and `_internal`, and checks every external
   command exit code;
6. invokes:

```powershell
& $IsccPath "/DMyAppVersion=$Version" $IssPath
```

7. requires exactly one expected Setup leaf with a plausible non-zero size;
8. writes a lowercase SHA-256 plus two spaces and filename plus LF to an
   ASCII/UTF-8-without-BOM `.sha256`, rereads it, and verifies the digest.

- [ ] **Step 5: Chain Setup after the normal Windows build**

`build_windows.ps1` builds dependencies, broker, PyInstaller onedir, then
Setup unless `AACC_SKIP_INSTALLER=1`.

- [ ] **Step 6: Run static tests and commit**

```bash
.venv/bin/python -m pytest tests/test_windows_installer_contract.py tests/test_packaging.py -q
.venv/bin/ruff check src tests
git add installer/AACC.iss scripts/build_windows_installer.ps1 \
  scripts/build_windows.ps1 tests/test_windows_installer_contract.py \
  tests/test_packaging.py .gitignore
git commit -m "feat: add per-user Windows setup"
```

---

### Task 7: Add frozen and installed Windows product smoke tests

**Files:**
- Create: `scripts/test_windows_package.ps1`
- Create: `tests/windows/fake_codex_server.py`
- Create: `tests/windows/fake-codex.cmd`
- Create: `tests/windows/fake_codex_timeout.py`
- Create: `tests/windows/fake_legacy_aacc.cpp`
- Create: `tests/windows/lock_payload.cpp`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: onedir, Setup, broker, fake Codex fixtures
- Produces: `build/windows-smoke/{frozen,installed,reinstall,uninstall}` logs
- Produces: CI artifacts for Setup, SHA-256, portable ZIP, audit, and smoke logs

- [ ] **Step 1: Add failing workflow contract tests**

```python
def test_ci_runs_real_windows_product_smoke():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "windows-2022" in workflow
    assert "windows-2025-vs2026" in workflow
    assert "scripts/test_windows_package.ps1" in workflow
    assert "AACC-*-Setup.exe" in workflow
    assert "windows-smoke" in workflow
    assert "windows-latest" not in workflow
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_packaging.py -q
```

- [ ] **Step 3: Implement frozen first-launch orchestration**

PowerShell creates an isolated directory and environment whose name is built
from character codes and includes Unicode, spaces, `&()`, `%`, `!`, and
brackets. Use literal-path APIs throughout. Cover frozen config/database,
fake Codex, Setup copy, `/LOG=`, PID, checksum, and ZIP paths:

```powershell
$env:AACC_CONFIG_PATH = Join-Path $SmokeRoot "frozen\AACC\config.yaml"
$env:AACC_DATABASE_PATH = Join-Path $SmokeRoot "frozen\AACC\aacc.db"
$env:AACC_CODEX_EXECUTABLE = $FakeCodexCmd
$env:QT_QPA_PLATFORM = "offscreen"
```

Start `dist\AACC\AACC.exe`, wait up to 30 seconds for config, database, log,
and fake-Codex marker, then require the process to remain alive for another
20 seconds.

Use PowerShell/.NET ACL APIs to require a protected DACL with no deny or
inherited ACEs, exactly one full-control Allow ACE for current user,
`S-1-5-18`, and `S-1-5-32-544`, and no other SID. File ACEs have no
inheritance flags; directory ACEs have the exact container/object inheritance
and propagation contract. Validate the protected directory, config, database,
and a credentials fixture created through the application path.

Invoke `AACC.exe --shutdown-for-update`, require exit 0, and require the main
PID to exit within 20 seconds.

- [ ] **Step 4: Implement timeout and orphan-tree checks**

The timeout fake writes root/child/grandchild PID, full image path, and
creation-time identities, then blocks. After the reader timeout, poll those
exact identities for up to 10 seconds and fail if any remains; never assert
that all global `cmd.exe` or Python processes disappeared. Compare
`aacc-spawn.exe` against a baseline PID/path set. Repeat the normal probe 20
times and prove each owned tree is gone after the probe, AACC exit, reinstall,
and uninstall.

Every external Setup, uninstaller, fixture, and AACC control process has an
outer harness deadline. Product logic never force-kills AACC. After a harness
deadline, cleanup may terminate only test-owned processes and must still fail
the smoke.

- [ ] **Step 5: Implement silent install/reinstall/uninstall checks**

Install:

```powershell
& $SetupPath /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- `
  /NOCLOSEAPPLICATIONS /NOFORCECLOSEAPPLICATIONS /NORESTARTAPPLICATIONS `
  "/LOG=$InstallLog"
```

Assert installed executables, `_internal`, HKCU uninstall entry, Start Menu
shortcut, and no default desktop shortcut. Also require no HKLM uninstall
entry, no Program Files payload, and clean pre-install state. Repeat first
launch, exact ACL, fake Codex, timeout, and graceful shutdown from the
installed path using the isolated default `%APPDATA%\AACC` structure rather
than redirecting installed config/database to arbitrary paths.

Write `%APPDATA%\AACC\preserve-me.txt`, reinstall the same Setup while AACC is
running, and require graceful shutdown plus preserved marker/config.

Uninstall silently while AACC is running. Wait for uninstaller self-removal,
then require program files, shortcuts, and HKCU uninstall registration gone
while AppData and the marker remain.

Exercise install, reinstall, and uninstall failure paths. A shutdown timeout
or non-zero control result must abort before payload/registry/data mutation.
Add a compiled legacy-AACC fixture that owns the exact window title but
ignores `--shutdown-for-update`. Cover an unregistered old portable while
stopped, while running, and a same-name executable without the shutdown-v1
capability. Setup must complete safely or return non-zero within the outer
deadline and must never hang or mutate on refusal.

Before fault injection, snapshot the complete `{app}` tree as relative path,
size, and SHA-256 plus uninstall registry, shortcut, and AppData manifests.
Add `_internal\rollback-sentinel.bin`, then use an independent native locker
with `FileShare.None` to lock a replaceable payload and signal readiness.
Require non-zero Setup exit, exact restoration of every manifest entry,
continued presence of the rollback sentinel, no staging/backup residue or
pending-reboot replacement, successful restart/broker exchange/shutdown of
the old payload, and unchanged `%APPDATA%\AACC`. Release the lock and perform
a successful reinstall; the sentinel must then be removed. If Inno does not
restore files removed by `[InstallDelete]`, replace that approach with an
explicit staging/backup/swap rollback before release.

For install-over-legacy, reinstall, and uninstall, separately inject control
timeout and non-zero results. After every refusal compare the complete
payload, HKCU registry, shortcuts, and AppData snapshots. A successful
uninstall is not complete until the uninstaller clone exits, self-removes,
and the directory/registry/shortcuts are gone within a deadline.

- [ ] **Step 6: Pin and extend the hosted workflow**

Both `windows-2022` and `windows-2025-vs2026` compile the broker and
PyInstaller onedir and run frozen first-launch, exact ACL, broker exchange,
timeout, and owned-process-tree cleanup. Set `AACC_SKIP_INSTALLER=1` on
`windows-2022`; only `windows-2025-vs2026` bootstraps hash-pinned Inno 6.7.1,
builds the primary Setup, and runs fresh install, running reinstall, failure
rollback, and running uninstall:

```yaml
- name: Build Windows app and Setup
  if: matrix.os == 'windows-2025-vs2026'
  shell: pwsh
  run: ./scripts/build_windows.ps1

- name: Smoke frozen and installed Windows product
  if: matrix.os == 'windows-2025-vs2026'
  shell: pwsh
  run: ./scripts/test_windows_package.ps1
```

After both matrix legs succeed, a separate final artifact job downloads the
verified outputs. It revalidates the Setup checksum as lowercase 64 hex, two
spaces, exact filename, LF, and no BOM. It rejects ZIP absolute paths, `..`,
extra top-level entries, or a root other than `AACC/` containing exactly
`AACC.exe`, `aacc-spawn.exe`, and `_internal`, then compares critical hashes
with the built onedir. Only this job uploads Setup, SHA-256, and portable debug
ZIP with `if-no-files-found: error`; it must not use `always()` or `failure()`.
Upload broker dependency output, ACL/audit reports, installer logs, and smoke
logs separately for diagnosis with `if: always()`. A failed smoke or either
failed runner must never publish a primary Setup artifact.

- [ ] **Step 7: Push and inspect the hosted Windows result**

Required evidence:

- source suite and real native ACL pass;
- broker build/import allowlist passes;
- both runner frozen launches and the installed first launch survive;
- fake Codex exchange and process-tree cleanup pass;
- legacy refusal is bounded; reinstall, rollback, and uninstall pass;
- Setup and SHA-256 artifacts are non-empty.
- the run is labelled hosted Windows Server evidence, not Windows 10/11
  consumer or separate-account validation.

- [ ] **Step 8: Commit**

```bash
git add scripts/test_windows_package.ps1 tests/windows \
  .github/workflows/ci.yml tests/test_packaging.py
git commit -m "ci: smoke Windows setup end to end"
```

---

### Task 8: Regenerate the real demo image and update release documentation

**Files:**
- Modify: `scripts/capture_panel_screenshot.py`
- Modify: `docs/images/panel-overview.png`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.zh-CN.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/windows-verification-checklist.en.md`
- Modify: `docs/windows-verification-checklist.zh-CN.md`
- Modify: `docs/release-notes-1.4.2.md`
- Modify: `AGENTS.md`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: deterministic 420-pixel privacy-safe screenshot
- Produces: bilingual Windows Setup and release-gate documentation

- [ ] **Step 1: Add failing documentation contracts**

```python
def test_readmes_show_current_demo_and_windows_setup():
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text()
        assert "AACC-1.4.2-Setup.exe" in text
        assert "demo data" in text.lower() or "演示数据" in text
        assert "AACC-1.4.2-windows-x64.zip" not in text


def test_release_docs_do_not_claim_unverified_windows_release():
    text = (ROOT / "docs/release-notes-1.4.2.md").read_text()
    assert "AACC-1.4.2-Setup.exe" in text
    assert "Windows 10" in text and "Windows 11" in text
    assert "尚未完成" in text or "not yet complete" in text
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_packaging.py -q
```

- [ ] **Step 3: Make the screenshot fixture deterministic**

Use a fixed demonstration observation time and values:

```python
DEMO_NOW = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
CODEX_WEEK = 17
KIMI_5H = 30
KIMI_WEEK = 72
KIMI_MONTH = 31
```

Reset dates are future values relative to `DEMO_NOW`. Keep Codex as one
`WEEK` row and Kimi as `5H`, `WEEK`, `MONTH`. Render at exactly 420 pixels and
assert the resulting image size is 420×640 or the final deterministic content
height selected by the real window.

- [ ] **Step 4: Generate and visually inspect the image**

```bash
.venv/bin/python scripts/capture_panel_screenshot.py docs/images/panel-overview.png
```

Inspect `docs/images/panel-overview.png` and require:

- no percentage/reset overlap;
- complete local dates and times;
- correct Kimi row order;
- no clipped task text;
- no real user/account/path data.

- [ ] **Step 5: Update bilingual release docs**

Document:

- Setup as the primary Windows download;
- current-user install path and no admin prompt;
- Start Menu, optional desktop shortcut, upgrade, uninstall, and preserved
  AppData/Kimi session;
- unsigned SmartScreen limitation;
- native DACL and fixed-purpose broker;
- exact distinction between hosted CI and real Windows 10/11 validation;
- manual Release blockers still unchecked;
- DMG remains the macOS artifact.

Add an immediate caption below the README image:

```markdown
_Illustrative UI with synthetic demo data; no real account or task data._
```

and:

```markdown
_使用合成演示数据生成的界面示意图，不含真实账户或任务数据。_
```

- [ ] **Step 6: Run docs/package tests and commit**

```bash
.venv/bin/python -m pytest tests/test_packaging.py -q
git diff --check
git add scripts/capture_panel_screenshot.py docs/images/panel-overview.png \
  README.md README.zh-CN.md CHANGELOG.md CHANGELOG.zh-CN.md \
  docs/user-guide.md docs/user-guide.en.md \
  docs/windows-verification-checklist.en.md \
  docs/windows-verification-checklist.zh-CN.md \
  docs/release-notes-1.4.2.md AGENTS.md tests/test_packaging.py
git commit -m "docs: prepare Windows setup release"
```

---

### Task 9: Final automated verification and merge gate

**Files:**
- Modify only when verification finds a documented defect.

**Interfaces:**
- Consumes: every previous task
- Produces: evidence for merge readiness; does not claim formal Release readiness

- [ ] **Step 1: Run the full local verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 2: Build and verify macOS remains unchanged**

```bash
scripts/build_app.sh
codesign --verify --deep --strict dist/AACC.app
```

Expected: build and signature verification exit 0.

- [ ] **Step 3: Push and wait for hosted CI**

Required Windows evidence is the complete product smoke sequence from Task 7.
Required macOS evidence is lint, format, strict mypy, tests, changed-line
coverage, dependency audit, and native app build.

- [ ] **Step 4: Run an independent final code review**

Review the complete diff from `origin/main` to `HEAD`. Critical and Important
findings must be fixed and the full verification rerun.

- [ ] **Step 5: Update release notes with exact automated evidence**

Record exact test counts, changed-line coverage, audit result, macOS build,
Windows Setup size/SHA-256, and hosted Actions URL. Keep every unexecuted
real-machine checkbox visibly unchecked.

- [ ] **Step 6: Commit verification evidence**

```bash
git add docs/release-notes-1.4.2.md AGENTS.md
git commit -m "docs: record Windows setup verification"
```

- [ ] **Step 7: Merge eligibility decision**

Merge into `main` only when:

- current branch is clean and pushed;
- latest macOS and Windows jobs are green;
- frozen and installed first launch passed;
- no runtime `whoami`/`icacls`/`taskkill` path remains;
- ACL allowlist is exact;
- no broker/Codex descendants remain after timeout or AACC exit;
- Setup install/reinstall/uninstall passes;
- final review has no unresolved Critical or Important issue.

Do not create tag or formal Release `v1.4.2` until the manual Windows 10/11
and separate-account gates in the design are signed.
