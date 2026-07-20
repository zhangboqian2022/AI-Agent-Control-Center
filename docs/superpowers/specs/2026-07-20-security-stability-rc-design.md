# AACC 1.3.0 RC Security and Stability Design

## Release decision

The next public artifact is `v1.3.0-rc.1`, not a stable release. Source, tests, bilingual documentation, and an ad-hoc-signed DMG may be published as a GitHub prerelease. Stable `v1.3.0` remains blocked until a Developer ID Application identity and Apple notarization credentials are available.

AACC supports macOS 13 or newer. The repository must not claim macOS 12 compatibility.

## Review disposition

The implementation accepts verified findings: unsafe configuration permissions and missing token repair, automation transaction races and GUI-thread blocking, duplicate state history and broken lifecycle timestamps, silent discovery failures, weak PID identity fallback, incomplete automation exception normalization, dynamic AppleScript text interpolation, wrapper signal cleanup, missing Accessibility guidance, oldest-first limited history, source-bound CLI installation, lack of single-instance protection, adapter disconnect blocking, and incomplete release engineering.

It rejects unsupported remedies: separate focus/input locks, ASCII-only input, ACL/xattr inheritance, `atexit` guarantees after `SIGKILL`, the claimed `None` subtraction TypeError, and the claim that osascript has no timeout.

## 1. Secure configuration service

`config.py` becomes the only configuration persistence boundary.

- Add `config_version: 1` to `AppConfig` and an explicit migration registry.
- A token is valid only when it is at least 32 printable, non-whitespace characters and is not a documented placeholder.
- Missing or invalid tokens are replaced with `secrets.token_urlsafe(32)` during load.
- Reject a configuration path that is a symbolic link.
- Create the application-support directory with mode `0700` and correct its mode on every load.
- Save YAML through a same-directory temporary file created as `0600`; flush, `fsync`, `os.replace`, chmod the final file to `0600`, then fsync the directory.
- Correct an existing regular configuration file to `0600` even when its contents do not change.
- The SQLite file is also forced to `0600`.

Token rotation is local-GUI-only in this RC. The settings dialog asks for confirmation, calls the configuration service directly, atomically persists a new token, updates the mutable in-memory config used by the API, and copies the new token once. No remote rotate endpoint and no grace period are added.

## 2. State lifecycle and bounded history

Replace boolean-only acceptance with `StateMachine.transition(current, candidate) -> TaskState | None`.

- Preserve `started_at` across active, thinking, waiting, warning, and paused states in the same run.
- Set a fresh `started_at` only when entering a new run from idle or a terminal state.
- Preserve `started_at` and set `finished_at` when entering a terminal state.
- Treat states as semantic duplicates when status, message, source, confidence, PID, session ID, metadata, and source-event timestamp are equal. A duplicate returns `None` and performs no database write or notification.
- A heartbeat with a newer observation time updates the current-state row at most once per minute without appending history.
- A business-state change updates current state and appends history.

`StateStore` adds a descending task/history index, `busy_timeout`, recent-N history selection presented oldest-to-newest, and bounded retention: at most 1,000 history entries per task and no entries older than 30 days. Cleanup runs at initialization and after bounded batches of writes.

## 3. Transactional asynchronous automation

Automation uses one bounded `AutomationExecutor` queue with one worker thread. A complete `focus → delay → input` operation is one transaction. Queue capacity is 32; overflow produces `AutomationBusyError` instead of unbounded growth.

`MacAutomation` also uses one `threading.RLock` around public operations so direct callers remain safe. Nested focus uses a private unlocked helper to avoid deadlock. Focus and injection are never protected by separate locks.

- GUI actions submit and return immediately. A Qt signal delivers success or failure and keeps the event loop responsive.
- API sync endpoints submit and wait on a Future with a bounded total timeout.
- osascript subprocess timeout remains five seconds and is configurable from 2–15 seconds.
- `TimeoutExpired` and `OSError` become sanitized `AutomationError` messages.
- Text is passed as argv to a fixed AppleScript handler, never interpolated into script source. NUL is rejected; Chinese, emoji, quotes, slashes, CR/LF/TAB, hostile-looking literals, and the existing 2,000-character boundary remain supported.
- Logs include operation kind, target bundle ID, elapsed time, and outcome, but never text payloads.

## 4. Discovery health and GUI warning

`CodexDiscoveryService` exposes an immutable `DiscoveryHealth` snapshot and subscription callback.

- Log unexpected poll failures with a redacted diagnostic ID.
- Mark degraded after three consecutive failures; an unreadable required index may degrade immediately.
- Clear degraded state only after two consecutive successful polls.
- Deduplicate identical error logs for 60 seconds.
- Preserve the last known task state while degraded.

The panel shows a yellow warning banner containing an 80-character sanitized summary and a **Copy diagnostics** button. Clipboard diagnostics contain health counters, diagnostic ID, timestamps, log path, and redacted exception class/message—not a raw traceback or conversation content. The banner clears after recovery.

## 5. Remaining high-priority fixes

- Reject a Codex PID when the process record contains a start time but live process start time cannot be verified.
- `aacc-run` handles `SIGINT` and `SIGTERM` by setting a stop flag, terminating the held `Popen`, waiting up to three seconds, then killing and reaping it. Documentation explicitly excludes `SIGKILL` guarantees.
- Add an `fcntl.flock` single-instance guard before Runtime creation. A second launch activates the existing bundle and exits successfully.
- Detect Accessibility trust before enabling global hotkeys or injection actions. The GUI explains the missing permission, can open the correct System Settings pane, and reports hotkeys unavailable. Disabled event taps are re-enabled in the callback.
- Separate source build dependencies from the stable CLI runtime. The source installer may use dev dependencies for test/build, but installs runtime-only CLI dependencies into `~/Library/Application Support/AACC/runtime`; links never point into the repository `.venv`.
- `BaseAgentAdapter.disconnect()` places a sentinel in the queue so an awaiting `events()` consumer exits immediately.

## 6. Selected delivery improvements

- `build_dmg.sh` supports `SKIP_BUILD=1`.
- Extract QSS to packaged `src/aacc/styles.qss`; no hot reload in this RC.
- Extend logging redaction for quoted/JSON-style token, password, secret, and bearer values while leaving business metadata unchanged.
- Build scripts accept `AACC_CODESIGN_IDENTITY` and `AACC_NOTARY_PROFILE`. Without both, the build is explicitly an ad-hoc RC; with both, hardened-runtime signing, notarization, stapling, `codesign`, and `spctl` verification are required.
- Add a manual macOS 13/14/15/26 integration checklist and `integration` pytest marker. Hardware/version claims are limited to environments actually tested.
- Add a Codex metadata parser compatibility identifier plus current-format fixtures.
- Add `KNOWN_LIMITATIONS.md` in English and Chinese.

## Testing and acceptance

Every verified P0/P1 behavior receives a regression test written before implementation. Modified executable lines must reach at least 90% focused coverage; full-suite coverage is reported separately.

Acceptance requires:

1. Full pytest, Ruff, mypy, and diff checks pass.
2. Ten concurrent automation submissions preserve transaction order; a hanging runner does not block the Qt event loop.
3. Repeating one discovered state does not grow history; active duration does not reset.
4. Invalid/empty tokens self-heal and config/database permissions are `0600`.
5. Three poll failures show a banner and two successes clear it.
6. The app bundle, DMG checksum, ad-hoc signature, local API, and clean replacement install verify on this Mac.
7. GitHub receives source and a prerelease `v1.3.0-rc.1`; no stable/notarized claim is made.
