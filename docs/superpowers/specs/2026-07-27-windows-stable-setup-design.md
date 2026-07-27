# AACC 1.4.2 Windows Stable Setup Design

**Date:** 2026-07-27

**Target release:** 1.4.2

**Platforms:** Windows 10/11 x64; macOS behavior must remain unchanged

## Goal

Replace the current Windows portable-only delivery with a per-user
`AACC-1.4.2-Setup.exe`, eliminate the packaged application's dependency on
`whoami.exe`, `icacls.exe`, and `taskkill.exe`, and make a real frozen first
launch plus installed first launch mandatory CI gates.

The Windows application must install without elevation, upgrade in place,
shut down cleanly, preserve the user's AACC data, and fail closed with a
readable error if sensitive-file protection cannot be established.

## Incident and Root Cause

The first 1.4.2 Windows package failed on a real machine while creating
`config.yaml`:

```text
aacc.file_security.FileProtectionError:
Windows file permission update timed out
```

The traceback reaches the second `protect_file()` call in `save_config()`.
`whoami.exe` had already completed; the `icacls.exe` child showed an
application error and did not exit before the five-second timeout.

The packaged onedir bundle contains UCRT, API-MS, Python, Qt, and OpenSSL
DLLs. PyInstaller sets the frozen process DLL search directory to the bundle
directory with `SetDllDirectoryW`, and Windows propagates that setting to
child processes. This is the high-confidence mechanism behind the observed
failure, although the design does not depend on it being the only possible
cause: removing the security-critical external commands eliminates the whole
failure class.

The existing CI did not detect the incident because its real ACL test runs
under ordinary Python. After PyInstaller builds `AACC.exe`, CI only checks the
archive and output paths; it never launches the frozen product.

## Expert Review Outcome

Three independent reviews covered Windows security, installation/build
engineering, and release reliability. Their second-round consensus is:

- Use `pywin32`/`win32security` to replace the file DACL in process.
- Do not temporarily reset and restore the AACC process DLL search path.
- Do not permanently reset the AACC process DLL search path; delayed Qt,
  WebView, image, TLS, and pywin32 DLL loads make that a broad regression
  surface.
- Use a small statically linked native broker for the unavoidable Codex
  app-server child process.
- Replace `taskkill.exe` with broker-owned Job Object lifetime management.
- Use Inno Setup for a per-user, non-elevated installer.
- Treat frozen first launch, installed first launch, exact ACL verification,
  broker process-tree cleanup, reinstall, and uninstall as `main` merge
  blockers.
- Keep Windows 10/11 standard-user, second-account denial, real Kimi login,
  real Codex query, SmartScreen, tray, hotkey, and long-running checks as
  formal Release blockers.

## Architecture

### 1. Native Windows file protection

`src/aacc/file_security.py` retains the cross-platform public interface and
delegates Windows work to a narrow Windows-only adapter.

Windows uses the pinned runtime dependency:

```toml
"pywin32==312; sys_platform == 'win32'"
```

The adapter:

1. Gets the current process `TokenUser` SID.
2. Builds a new DACL containing only:
   - the current user SID: full control;
   - Local System (`S-1-5-18`): full control;
   - built-in Administrators (`S-1-5-32-544`): full control.
3. Calls `SetNamedSecurityInfo` for `SE_FILE_OBJECT` with
   `DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION`.
4. Reads the DACL back and verifies that it is protected and contains exactly
   the expected allow entries, with no inherited or deny entries.
5. Converts all platform exceptions into a sanitized `FileProtectionError`
   that contains no path, user name, SID, token, or file content.

The implementation replaces the complete DACL. It never appends grants and
therefore removes inherited entries and legacy explicit entries such as
Everyone, Users, or Authenticated Users.

Atomic writing remains:

```text
create empty temporary file
→ establish exact protected DACL
→ write and fsync the secret
→ verify the exact protected DACL
→ atomically replace the target
```

Any protection or verification failure preserves the old target and deletes
the temporary file. Unchanged Windows configuration files no longer need to
be republished on every application launch once their exact DACL verifies.

The default `%APPDATA%\AACC` directory receives a protected inheritable DACL
for the same three principals. Individual secret files still receive and
verify their own exact non-inherited DACL so custom paths and legacy data do
not rely only on directory inheritance.

### 2. Fixed-purpose native spawn broker

The packaged GUI must still launch the installed Codex app server. Starting
`codex.cmd`, `cmd.exe`, Node, or `taskkill.exe` directly from the PyInstaller
process would retain the same frozen DLL-search risk.

The solution is a fixed-purpose native executable:

```text
AACC.exe
  └─ aacc-spawn.exe
       ├─ restores its own standard DLL search path
       ├─ creates a KILL_ON_JOB_CLOSE Job Object
       └─ suspended cmd.exe → codex.cmd → node/codex
```

`aacc-spawn.exe` is not a general shell or command execution API. Protocol
version 1 only starts the already-authorized absolute Codex executable with
`app-server --stdio`.

The broker:

- is implemented as one focused C++17 translation unit at
  `native/aacc_spawn/aacc_spawn.cpp`, plus a version-resource file;
- is built for x64 with MSVC using `/MT /O2 /GS /guard:cf /W4 /WX` and linker
  ASLR, DEP, and high-entropy VA flags;
- lives beside `AACC.exe` at `{app}\aacc-spawn.exe`, not inside `_internal`;
- imports only reviewed Windows system DLLs and dynamically depends on no
  UCRT, VCRUNTIME, MSVCP, Python, Qt, or third-party DLL;
- immediately calls and verifies `SetDllDirectoryW(NULL)` in its own process;
- receives the parent AACC PID, the bundle directory, and a structured
  absolute Codex path;
- builds a new Unicode child environment and removes only normalized `PATH`
  entries rooted in the PyInstaller bundle;
- resolves `%SystemRoot%\System32\cmd.exe` with `GetSystemDirectoryW`;
- uses `/D /S /C` for `.cmd`/`.bat` targets and a reviewed Windows quoting
  implementation instead of concatenating untrusted shell text;
- creates a non-inheritable Job Object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`;
- creates the target with `CREATE_SUSPENDED | CREATE_NO_WINDOW |
  CREATE_UNICODE_ENVIRONMENT`, assigns it to the Job, then resumes it;
- terminates a still-suspended target if Job assignment fails and never
  degrades to `taskkill` or an uncontained child;
- uses `STARTUPINFOEX` with a handle allowlist for stdin, stdout, and the
  child's NUL stderr only;
- passes stdin/stdout through without parsing JSON;
- waits for either AACC or the target to exit and closes the Job on every
  return and error path;
- returns the target exit code on normal completion;
- reports only a fixed error stage plus numeric Win32 error code.

The fixed command-line protocol is:

```text
aacc-spawn.exe --protocol 1 --parent-pid <decimal-pid> \
  --bundle-dir <absolute-directory> --codex <absolute-executable-or-cmd>
```

There is no trailing arbitrary-command field. The broker itself appends
`app-server --stdio`.

The Python reader starts the broker by its absolute packaged path. Frozen
builds resolve it beside `sys.executable`; Windows source development resolves
an explicit `AACC_SPAWN_BROKER_PATH` or the build-script output. A missing or
incompatible broker produces an honest UNKNOWN/local-session fallback and
never falls back to direct frozen process launch. Ordinary non-Windows source
development keeps its existing direct process path.

The existing `taskkill.exe` cleanup path is removed. Terminating the broker or
closing its Job reliably terminates the complete cmd/Node/Codex descendant
tree.

### 3. Graceful shutdown for upgrade and uninstall

Setup must not kill the GUI while SQLite or credentials may be writing.
`WM_CLOSE` is unsuitable because closing the AACC window hides it to the tray.

The packaged executable gains:

```text
AACC.exe --shutdown-for-update
```

A dedicated Qt native event filter receives a registered
`AACC.ShutdownForUpdate.v1` Windows message. The control invocation finds the
exact AACC window, validates that the target PID belongs to `AACC.exe`, posts
the registered message, and waits up to 20 seconds for the process to exit.
The main instance quits through its normal Qt path so hotkeys, services,
broker processes, the API thread, and SQLite close normally.

No-instance is success. Send failure or timeout is a non-zero result. Setup
then stops and asks the user to exit AACC from the tray; it never force-kills
the application.

### 4. Inno Setup package

The primary Windows artifact becomes:

```text
AACC-1.4.2-Setup.exe
AACC-1.4.2-Setup.exe.sha256
```

The portable ZIP remains a CI/debugging artifact rather than the primary
download.

Installer contract:

- Inno Setup 6.7.1, checked explicitly before compilation.
- The compiler bootstrap uses the immutable `innosetup-6.7.1.exe` release and
  verifies SHA-256
  `4d11e8050b6185e0d49bd9e8cc661a7a59f44959a621d31d11033124c4e8a7b0`
  before executing a downloaded or cached installer.
- `PrivilegesRequired=lowest`.
- `UsePreviousAppDir=yes` and `UninstallLogMode=append`; the stable App ID
  owns per-user upgrades without allowing a command-line/admin override.
- Windows x64 only with `x64compatible`.
- Stable App ID: `{C174E242-E193-5863-8A46-F16152875173}`.
- Default directory: `{localappdata}\Programs\AACC`.
- Payload root contains exactly:
  - `AACC.exe`;
  - `aacc-spawn.exe`;
  - `_internal`.
- Start Menu shortcut is always created.
- Desktop shortcut is offered but unchecked by default.
- No login/startup item is added.
- The final page offers Launch AACC, skipped for silent installation.
- `CloseApplications=no` and `RestartApplications=no`; Inno Restart Manager
  never closes or restarts AACC on the installer's behalf.
- Upgrade calls `--shutdown-for-update` with an exact 25-second control
  timeout, then lets Inno replace `_internal` before the two root executables.
- `[InstallDelete]` is not used: it would remove files outside Inno's native
  per-file rollback journal. A generated exact `_internal` manifest instead
  removes stale entries after file commit with three bounded retries.
- `PrepareToInstall` reads the exact `{app}\_internal` root attributes through
  `GetFileAttributesW` and rejects every root reparse point before `[Files]`
  can traverse it. The installer never deletes or enumerates a junction
  target.
- Manifest load/validation failure or an undeletable stale file, directory, or
  reparse entry makes Setup return non-zero. Post-commit cleanup makes no
  rollback claim; `GetCustomSetupExitCode` reports the incomplete state as
  exit `9`, and no explicit staging/backup/swap implementation is claimed.
- It never recursively deletes `{app}` during upgrade.
- Uninstall uses the same graceful shutdown path and removes the installed
  program, shortcuts, and uninstall registration.
- `%APPDATA%\AACC` is never listed in `UninstallDelete`; AACC-owned
  configuration, database, task history, protected reuse decision, and Kimi
  Code OAuth credentials survive upgrade, reinstall, and uninstall. The
  operating system owns the native WebView store separately, so there is no
  claim that Setup preserves or removes the Kimi website session.
- The unsigned installer honestly retains the Windows Unknown
  publisher/SmartScreen warning.
- Silent smoke adds `/NOCLOSEAPPLICATIONS`,
  `/NOFORCECLOSEAPPLICATIONS`, and `/NORESTARTAPPLICATIONS`. Shutdown failure
  aborts before mutation; a locked-file reinstall fault must prove that the
  prior payload is restored and still starts. An independent observer must
  first see a chosen metadata file change from known old bytes to packaged
  bytes, and the installer log provides auxiliary rollback evidence.
- Setup and uninstaller `/LOG` paths contain Chinese characters, spaces,
  `&() %! []`; every invocation must create a non-empty file at the exact
  requested path.
- Setup/checksum/ZIP candidates are isolated from all `if: always()`
  diagnostics and become uploadable only after strict verification. Saved
  product executables used by smoke stay in the same non-uploaded candidate
  tree; diagnostic fixtures cannot use product artifact names.

The build script reads version `1.4.2` from `pyproject.toml` via
`uv version --short`. The workflow uses both supported hosted Windows images,
bootstraps the hash-pinned Inno compiler when an explicit trusted
`AACC_ISCC_PATH` is not supplied, verifies version 6.7.1, and logs only the
compiler version and a sanitized source category.

When Windows signing becomes available, the order is broker, AACC and other
inner binaries, then Setup, all with a timestamp. Signing is not claimed by
1.4.2.

### 5. Friendly fail-closed startup

Configuration security still fails closed. A failure no longer escapes as a
raw windowed PyInstaller traceback.

The application creates the Qt application early enough to show a bilingual
startup error that says:

- AACC could not protect its local credential file;
- no new credential was saved;
- where the sanitized `app.log` can be found;
- the user should close AACC and retry or report the diagnostic code.

The UI never shows the path to a temporary secret, a SID, a token, or raw
subprocess/platform output. The error is logged at CRITICAL with a stable
diagnostic category and sanitized numeric Windows error.

## Automated Verification

### Cross-platform source checks

- Full pytest suite.
- Ruff check and format.
- Strict mypy.
- Changed-line coverage at least 90%.
- Locked dependency audit on macOS and Windows, including `pywin32==312`.
- Existing macOS build and signing verification remain green.

### Windows native and ACL checks

- Exact DACL construction with fake Windows API boundaries.
- Real Windows temporary file and directory DACL integration.
- Current user, System, and Administrators are the complete explicit allow
  set.
- No inheritance, deny, Everyone, Users, Authenticated Users, or Owner Rights
  ACE.
- Unicode, spaces, long paths, idempotent protection, and legacy weak ACL
  tightening.
- Protection failure leaves the old target unchanged and does not leak secret
  values into logs or exceptions.
- The frozen Windows runtime contains no `whoami`, `icacls`, or `taskkill`
  execution path.

### Broker checks

- Compile with warnings as errors.
- Verify x64 PE and protocol/product version.
- `dumpbin /DEPENDENTS` enforces a reviewed system-DLL allowlist and explicitly
  rejects dynamic CRT and third-party DLLs.
- Test executable and command-script targets with Unicode, spaces, `&`, and
  parentheses.
- Verify stdin/stdout JSON pass-through, large output, target exit-code
  propagation, and fixed sanitized broker errors.
- Verify the target remains suspended and is terminated when Job assignment
  fails.
- Kill the broker and confirm root, child, and grandchild PIDs all disappear.
- Force AACC to exit and confirm the broker and complete target tree exit.
- Repeat at least 20 Codex probes and check for orphan processes and sustained
  handle growth.

### Frozen and installed product checks

Frozen checks use isolated `AACC_CONFIG_PATH` and `AACC_DATABASE_PATH`.
Installed checks instead isolate the user profile and exercise AACC's real
default `%APPDATA%\AACC` structure.

1. Build the onedir application and broker.
2. Start the real frozen `AACC.exe`.
3. Wait for config, database, and log creation.
4. Require the process to remain alive for at least 20 seconds.
5. Verify protected DACLs down to exact SID, ACE type/count, full-control
   mask, inheritance and propagation flags using the test process, not AACC.
6. Point Codex discovery to a controlled `fake-codex.cmd`.
7. Complete a real JSON-RPC initialize/rate-limits exchange through the
   broker.
8. Run the timeout descendant-tree test.
9. Shut down via `AACC.exe --shutdown-for-update`.
10. Compile Setup.
11. Silent-install with logging.
12. Verify installed files, HKCU uninstall registration, Start Menu shortcut,
    and the absence of a default desktop shortcut.
13. Repeat frozen first launch, ACL, broker, Codex, and graceful-shutdown
    checks from the installed directory.
14. Cover a stopped and running legacy portable that does not understand the
    shutdown protocol; Setup must never hang or mutate on refusal.
15. Snapshot install state, lock root `AACC.exe`, independently observe an
    already-replaced metadata file, and prove native rollback restores it.
16. Lock a stale `_internal` file and require non-zero post-commit cleanup;
    release it, reinstall, and verify the exact manifest plus stable
    config/credential hashes and the preserved database smoke row.
17. Silent-uninstall while AACC is running; separately prove shutdown failure
    aborts without mutation.
18. Verify graceful exit, removal of the two executables, `_internal`,
    shortcuts, and uninstall registration.
19. Verify `%APPDATA%\AACC` and the marker remain.
20. Both hosted legs run build and product smoke under Windows PowerShell 5.1.
    Only after their serial DAG passes does the final job revalidate
    checksum/ZIP structure, copy candidates into the safe output tree, and
    upload Setup, SHA-256, and portable ZIP. Always-uploaded diagnostics never
    contain those primary artifacts.

Any frozen first-launch exit, ACL mismatch, broker dependency mismatch,
orphan descendant, failed graceful shutdown, stale program payload, or missing
artifact blocks merging into `main`.

Hosted Windows Server evidence is labelled as such. It does not replace the
Windows 10/11 consumer, standard-user, second-account, SmartScreen, or real
Codex/Kimi manual release gates below.

## Manual Release Gates

These checks do not block merging the fully automated repair into `main`, but
they block tag and formal Release `v1.4.2`:

- Windows 10 x64 and Windows 11 x64 clean install as standard,
  non-administrator users.
- Windows 11 running upgrade/reinstall and running uninstall.
- Chinese user name and paths containing spaces.
- A second unprivileged local account is denied access to `config.yaml` and
  `kimi-credentials.json`.
- Real Kimi login, restart persistence, five-minute `5H`/`WEEK`/`MONTH`
  refresh, logout, and credential ACL verification.
- Real installed Codex executable live weekly-quota query with no active task.
- Codex timeout, offline recovery, sleep/wake, and no orphan Node/Codex
  processes.
- Discovery, focus, input, voice, global hotkeys, tray restore, and quota rows.
- Defender, SmartScreen, and unsigned-publisher behavior recorded.
- At least 30 minutes continuous operation followed by clean quit and restart.

The completed evidence is recorded in the bilingual Windows verification
checklist and linked from the Release notes.

## Documentation and Demo Image

The GitHub documentation image is generated from the real Qt UI with
synthetic, privacy-safe fixture data. It is not an AI approximation and never
uses a real account or task.

The final image must show:

- Codex with one `WEEK` row;
- Kimi in `5H`, `WEEK`, `MONTH` order;
- readable percentages, progress bars, and absolute local reset date/time;
- representative running, waiting, completed, and error task cards;
- the final 420-pixel panel width with no overlap or clipping.

`scripts/capture_panel_screenshot.py` is the source of the screenshot at
`docs/images/panel-overview.png`. Both README files add a caption stating that
the values are demonstration data. The image is regenerated only after the
final UI code and visually inspected before GitHub synchronization.

README, bilingual CHANGELOG, user guides, Windows verification checklists, and
1.4.2 Release notes are updated to:

- make Setup the primary Windows installation path;
- explain per-user installation, upgrade, uninstall, and retained AppData;
- describe the unsigned SmartScreen warning honestly;
- record the native ACL and broker security model;
- distinguish hosted CI evidence from real Windows 10/11 evidence;
- avoid claiming formal `v1.4.2` publication until every manual Release gate
  is complete.

## Non-goals

- No all-users or administrator installation.
- No MSI, MSIX, Store package, auto-updater, or login-startup entry.
- No general-purpose command execution through the broker.
- No deletion of user data during uninstall.
- No claim that Setup removes SmartScreen without a signing certificate.
- No formal `v1.4.2` Release before the real-machine gates pass.
