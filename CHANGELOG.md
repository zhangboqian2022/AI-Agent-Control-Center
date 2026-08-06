# Changelog

[中文版本](CHANGELOG.zh-CN.md)

## 1.4.5-rc.3 — 2026-08-06

[Bilingual release notes](docs/release-notes-1.4.5rc3.md)

- [Feat] Qwen quota now survives the Aliyun session expiry (~5.5 hours after a session copy): with `qwen_auto_session_recopy: true` in `config.yaml`, a hidden refresh that hits the console's inline "not logged in" banner rebuilds AACC's Chrome profile from the daily Chrome's minimal session set (Cookies via online backup, Local State, Preferences, Local/Session Storage — never Login Data) and retries within the same tick, so the bar keeps showing quota without a visible re-login. The replaced managed profile is quarantined as `.qwen-chrome-profile.pre-dailycopy-*` (pruned to the 3 newest). The flag defaults to off: enable it only on machines that already use the daily-session copy flow.
- [Fix] The Qwen logged-out path is no longer silent: detecting an expired session now logs a WARNING (previously the bar flipped back to "click to authorize" with no trace in the log), and skipped refreshes leave a debug line.

## 1.4.5-rc.2 — 2026-08-04

[Bilingual release notes](docs/release-notes-1.4.5rc2.md)

- [Fix] Bailian (Qwen Code) sign-in now completes on macOS: the session drives a real Google Chrome through CDP (the same paradigm Windows already uses with Edge) — one visible Chrome window opens for the Aliyun login (RAM entry included). Background quota refreshes then run every 15 minutes inside a headed-but-hidden Chrome: Aliyun's risk control voids session tickets presented by headless browsers, so AACC launches Chrome through `open -g -n` (no focus steal, no Dock bounce), pushes the window off-screen via CDP, and masks `navigator.webdriver` plus the off-screen coordinates before the page loads. Cookies stay in AACC's own Chrome profile directory; AACC never sees the account password. When Chrome is not installed the previous native web view remains as fallback.
- [Fix] An expired Bailian session no longer loops on stale data: the logged-out console stays on the workspace origin and renders an inline login banner, which the extractor now classifies as unauthorized — the bar flips back to "click to authorize" and a fresh visible login can start.
- [Fix] The token-plan bar no longer flips to "authorized" with a fake 0% reading: usage snippets without any rendered percentage (the anonymous/login page repeats the window labels in marketing copy) are treated as signed-out instead of quota data.
- [Fix] Fractional usage is preserved end-to-end: values such as 0.04% now render as "0.04%" instead of 0%, and the 5-hour window reset no longer absorbs the "7 天" wording from the neighbouring window.
- [Fix] OpenCode and Qwen quota refreshes now reload the workspace page on every tick (previously the extraction script re-read the stale DOM, so values never changed); Qwen ticks every 15 minutes, OpenCode every 5; Kimi refresh is unchanged.
- [Fix] The Windows quota service no longer imports the not-yet-existing `aacc.qwen_edge_session` module; Windows follows the native web-view path until a dedicated Edge-CDP session lands.
- [Note] `websocket-client` is now a cross-platform dependency (CDP transport on macOS).

## 1.4.5-rc.1 — 2026-08-04

[Bilingual release notes](docs/release-notes-1.4.5rc1.md)

- [Feat] Qwen Code (Bailian token-plan) quota is now shown alongside Kimi and OpenCode. AACC keeps an embedded web-view of the Aliyun Bailian personal token-plan page; once you sign in once, the cookie is cached in AACC's private directory and the 5-hour / 7-day windows refresh every 5 minutes by reading the rendered page text. AACC never sees or stores the account password. Add `qwen_quota_enabled` (default on) and `qwen_workspace_url` (default bailian personal page) to `config.yaml`.

## 1.4.4-rc.6 — 2026-08-03

[Bilingual release notes](docs/release-notes-1.4.4rc6.md)

- [Fix] Stale-state expiry now covers app restarts: rc.5 scanned only the in-memory task table, so zombie run-states whose sessions are no longer discovered (and therefore never re-registered after a restart) still escaped expiry. The sweep now reads persisted states straight from the store and normalizes them through the state machine, notifying subscribers the same way.

## 1.4.4-rc.5 — 2026-08-03

[Bilingual release notes](docs/release-notes-1.4.4rc5.md)

- [Fix] Stale discovered run-states now expire: sessions outside the discovery window (unselected or beyond the result limit) never received fresh candidates, so an outdated RUNNING/WAITING state could persist in storage indefinitely. Each poll round now normalizes any discovered run-state unseen for over an hour to UNKNOWN ("长时间未更新") through the regular state machine; genuinely active tasks heartbeat every minute and are unaffected.

## 1.4.4-rc.4 — 2026-08-03

[Bilingual release notes](docs/release-notes-1.4.4rc4.md)

- [Fix] Codex Desktop subagent threads (rollouts whose session metadata marks `source.subagent.thread_spawn`, forked inside a parent conversation) no longer surface as separate task cards in the panel or the task picker; only the parent conversation is discovered. User-visible forks with a plain string `source` are kept.
- [Fix] Kimi CLI cards no longer turn green between turns of an ongoing conversation: a finished turn now reports idle (grey) while the Kimi process lives, and green "turn completed" is reserved for process exit. Kimi Desktop task monitoring keeps per-turn completion (green at turn end).

## 1.4.4-rc.3 — 2026-08-03

[Bilingual release notes](docs/release-notes-1.4.4rc3.md)

- [Fix] OpenCode cards no longer report a false yellow "waiting for approval": a `pending` tool part only means the call was created but not started (arguments may still be streaming), and opencode does not persist permission requests, so pending is never inferred as an approval wait. Fresh pending parts report running; stale pending parts resolve through the generic stalled-session path (waiting input while the process lives, stopped after exit). This also removes the 0.97-confidence latch that suppressed the blue running state for up to 300 seconds after work resumed.

## 1.4.4-rc.2 — 2026-08-02

[Bilingual release notes](docs/release-notes-1.4.4rc2.md)

- [Security] OAuth query parameters never reach logs; `/send-text` is rate-limited (10/10s → 429); API metadata is bounded and unknown `source` normalization logs a warning.
- [Fix] OpenCode step-aware inference: a running tool inside the current step is no longer shadowed by a newer text part, and step-end signals only turn a session green after 90 s of inactivity; single-digit `usagePercent` values (e.g. 1) are no longer scaled to 100; the unreadable-cwd liveness fallback only applies to sessions without a known work directory; DOM extraction retries use an independent attempt counter.
- [Lifecycle] `aacc-run` and the macOS Codex app-server reap process groups on POSIX; the state machine's same-source override is bounded by staleness or a confidence gap.
- [Stability] Adapter polling isolates per-adapter failures and shares one process snapshot per round.
- [Build] `install.sh` builds the new runtime before removing the old one; `build_dmg.sh` emits the `.dmg.sha256` sidecar; `AACC-windows.spec` derives its root from the spec location.
- [Docs] Least-privilege deployment guidance in SECURITY.md; Windows release-note wording corrected to foreground window-handle re-check only.

## 1.4.4-rc.1 — 2026-08-01

[Bilingual release notes](docs/release-notes-1.4.4rc1.md)

- [Fix] Make OpenCode and Kimi terminal states truthful: approval, failure, cancellation, explicit completion, and process disappearance no longer collapse into a misleading green or blue state.
- [Stability] Reject stale terminal restarts, allow fresh process evidence to replace the initial idle baseline, and stop repeated runtime task registration from reinitializing SQLite.
- [Integration] Wire configured non-native Agent adapters into a conservative process-level discovery service; process disappearance is STOPPED and never claimed as agent-specific completion.
- [Security] Require unique desktop targets and verify the target immediately before input injection on both macOS and Windows; Windows re-checks the foreground window handle (process identity is not re-verified) while macOS checks the frontmost application/window identity. Ambiguous or changed targets fail closed.
- [Security] Anchor POSIX config replacement to an opened parent directory, disable proxy inheritance for loopback status calls, escape CR/LF in AppleScript strings, and avoid absolute Daimon paths in INFO logs.
- [UI] Label OpenCode's rolling quota as `ROLLING` (Chinese: `滚动`) instead of incorrectly presenting it as Kimi's `5H` window.
- [Build] Align macOS RC artifact naming with the public `1.4.4-rc.1` version, keep Windows Setup on PEP 440 `1.4.4rc1`, and replace the hard-coded macOS bundle build number.

## 1.4.3 — 2026-08-01

[Bilingual release notes](docs/release-notes-1.4.3.md)

- [Feature] OpenCode Go-plan quota bar on macOS and Windows: macOS uses a native web view; Windows uses an isolated AACC-owned Edge profile and a strict CDP boundary. Both extract only rendered rolling/weekly/monthly quota from the configured /go workspace page.
- [Feature] OpenCode CLI task discovery: read-only polling of the local opencode SQLite database infers per-session status (pending permission → waiting approval; active streaming → running; finished turn → completed; stale + process alive → waiting input; process gone → completed) with the existing circular status lights. Windows resolves its native database locations and binds terminal focus to the session work directory.
- [Fix] A finished opencode turn (step-finish / tool completed) now shows the green completed state immediately instead of staying blue.
- [Fix] If the opencode process disappears after a forced stop, the matching session now leaves the blue running state immediately instead of waiting for the activity timeout.
- [Fix] OpenCode Edge login, quota parsing, profile cleanup, and target selection are isolated from Kimi and fail closed on foreign pages, unsafe CDP endpoints, malformed payloads, or expired authorization.
- [Delivery] Formal 1.4.3 release ships verified macOS DMG and Windows Setup assets. GitHub Actions adds Windows 10/11 compatibility-contract jobs; these execute on hosted Windows Server and do not claim consumer hardware validation.

## 1.4.3-rc.2 — 2026-07-31

[Bilingual release notes](docs/release-notes-1.4.3rc2.md)

- [Fix] On Windows, a single 401 from an expired Kimi access token no longer permanently disables background quota refresh: headless Edge refreshes now retry inside a bounded 60-second grace window, giving kimi.com's own token renewal time to recover; a session that stays unauthorized for the whole window still requires a new login.
- [UX] Restoring the panel (unhide / un-minimize) now triggers an immediate Kimi quota catch-up refresh, throttled to once per 60 seconds, on both platforms.
- [Delivery] Windows-only version increment; the macOS build remains at 1.4.3-rc.1 and no new macOS artifact is published for rc.2.

## 1.4.3-rc.1 — 2026-07-30

[Bilingual release notes](docs/release-notes-1.4.3rc1.md)

- [Fix] On Windows, launching a second copy now finds the existing panel by its real window title (`AI Agent Control Center`, via the shared `AACC_WINDOW_TITLE` constant) and brings it to the foreground; the previous `"AACC"` substring never matched.
- [Security] Extend sink-level log redaction to cover `device_code`, `user_code`, `api_key`, and `apikey` fields as defense in depth; the global `RedactingFormatter` remains the single redaction point.
- [Diagnostics] Log one INFO entry listing every probed candidate path when no Kimi Desktop daimon root exists, so the silently disabled discovery source becomes traceable.
- [Docs] Declare in KNOWN_LIMITATIONS (bilingual) that hosted Windows Server CI does not equal consumer Windows 10/11 verification, and add a CI assertion keeping the bilingual entry counts aligned.
- [Build] Accept PEP 440 prerelease versions (`a`/`b`/`rc` suffixes) in the Windows broker and installer build scripts; numeric-only VERSIONINFO fields now receive a dedicated `MyAppVersionInfo` triplet while prerelease suffixes remain in display and artifact names.

## 1.4.2 — 2026-07-29

[Bilingual release notes](docs/release-notes-1.4.2.md)

- [UX] Add live Chinese/English switching for the complete macOS and Windows UI, immediately without a restart. First launch follows the system language, explicit selection persists, and switching does not refresh quotas or change monitored tasks or login state; compact mode remains in Settings and the tray menu.
- [Quota] Render Codex as one larger `WEEK` row and Kimi as `5H`, `WEEK`, and `MONTH`, with full local reset date/time in each available row. Refresh Kimi’s cached membership session every five minutes without consuming model tokens.
- [Windows] Add the per-user, non-elevated `AACC-1.4.2-Setup.exe`; it installs under `%LocalAppData%\Programs\AACC`, supports graceful in-place upgrade/uninstall, and preserves `%APPDATA%\AACC`.
- [Windows] Replace the unreliable embedded login with Microsoft Edge and an isolated AACC-owned Edge profile. The Kimi session survives app and PC restarts until you sign out or Kimi expires it; normal Edge data is never read.
- [Security] Replace `whoami.exe`/`icacls.exe` file protection with exact native protected DACLs. Route packaged Codex read-only app-server processes through a fixed-purpose static broker and remove `taskkill.exe`.
- [Delivery] Add Windows Server 2022/2025 frozen/install/reinstall/pre-mutation locked-target refusal/uninstall smoke gates and publish macOS DMG and Windows Setup assets with checksums.
- [Evidence] Hosted CI passed; consumer Windows 10/11 and separate-account manual verification remain explicitly unclaimed.

## 1.4.1 — 2026-07-24

[Bilingual release notes](docs/release-notes-1.4.1.md)

- [Feature] Add a read-only Codex weekly quota strip from bounded local structured metadata. Only the current 10080-minute weekly window is accepted; legacy shorter windows are ignored and no five-hour Codex limit is displayed.
- [Security] Serialize Kimi credential writes with generation/fingerprint checks so delayed refresh, OAuth, logout, and API-key changes cannot overwrite a newer credential generation.
- [Stability] Skip Kimi polling while authorization is pending, close every HTTP client deterministically, bound device polling to 15 minutes, cancel OAuth on every dialog-close path, recover safely from unexpected or persistence errors, and give the read-only Kimi Desktop catalog a five-second SQLite busy timeout.
- [Honesty] Distinguish unknown, partial, and stale Kimi quota data instead of silently rendering malformed API responses as zero usage.
- [Delivery] Make locked formatting, typing, tests, 90% changed-line coverage, and a non-empty blocking dependency audit mandatory; retain the JSON audit report and add a release-asset verifier.

## 1.4.0 — 2026-07-24

- [Feature] Add Kimi account quota monitoring: weekly / 5-hour quota and booster balance in the panel header, via official device authorization or API key.
- [Feature] Kimi Code task cards now show a token usage row: cumulative input/output, cache hit rate, and median generation speed (incremental wire tailing).
- [Fix] The 5-hour quota window was not recognized when the API spells the window unit `TIME_UNIT_MINUTE`, so the 5-hour bar always showed 0%; window-unit matching now accepts the live spellings and rejects non-minute units.

## 1.4.0-rc.2 — 2026-07-24

- [Fix] The 5-hour quota window was not recognized when the API spells the window unit `TIME_UNIT_MINUTE`, so the 5-hour bar always showed 0%; window-unit matching now accepts the live spellings and rejects non-minute units.

## 1.4.0-rc.1 — 2026-07-24

- [Feature] Add Kimi account quota monitoring: weekly / 5-hour quota and booster balance in the panel header, via official device authorization or API key.
- [Feature] Kimi Code task cards now show a token usage row: cumulative input/output, cache hit rate, and median generation speed (incremental wire tailing).

## 1.3.3-rc.1 — 2026-07-22

- [Security] `save_config` now rejects a symlinked configuration directory (defense in depth alongside the existing config-file symlink check).
- [Stability] Card removal goes through a single dispatch funnel that logs an ERROR for task ids with an unknown brand prefix instead of silently ignoring them.
- [Performance] Process-liveness probes (Kimi Code / Kimi Desktop discovery) cache the matching PID and only rescan the process tree after it dies or changes identity, instead of walking the whole tree every poll.
- [Delivery] CI now runs a non-blocking `pip-audit` dependency vulnerability scan.
- [Docs] Clarified that the Kimi Desktop catalog is opened `mode=ro` deliberately, not `immutable=1` (WAL freshness), updated the discovery-service count and the signing wording in the docs, and added a TCC contingency note for future daimon data-path changes.

## 1.3.2 — 2026-07-22

- [Security] Placeholder-shaped API tokens are now rejected by prefix (`change-me`, `replace-`, `your-token`, `placeholder`), and the shipped example config leaves the token empty — loading it always rotates to a fresh random credential instead of running with a public constant.
- [UX] Hiding an agent brand now persists across restarts: the new-brand visibility seeding runs once via a migration key instead of force re-adding Kimi brands on every launch.
- [API] `/api/v1/reload-config` now returns 501 Not Implemented instead of a misleading 200.
- [Stability] `aacc doctor` and the app resolve the runtime database path through one shared helper, so diagnostics check the file the app actually mounts.
- [UX] Rotating the API token no longer writes the clipboard automatically; the new token is shown in a dialog and copied only when the user clicks Copy.
- [Performance] Expired state-history cleanup is throttled to once per hour with an index on `created_at`, and the panel skips its per-second card-layout rebuild when grouping and order are unchanged.
- [Stability] Task-state subscriber failures are logged instead of silently swallowed.
- [Docs] Fixed the README claim that the installer runs tests (they are opt-in via `AACC_RUN_TESTS=1`) and added the 1.3.1 test report.

## 1.3.1 — 2026-07-22

- [UX] Switching to a task now restores its minimized windows (both terminal and macOS app targets), so the focused window actually appears on screen ready for typing.
- [UX] Removed the voice-input and key-injection actions from the card context menu; the underlying automation remains available through global hotkeys.

## 1.3.0 — 2026-07-22

- [UX] Kimi Code cards now show the session's working-directory name next to the status (full path in the tooltip), making it easy to tell concurrent projects apart.
- [UX] Fixed the panel failing to reopen after being minimized: clicking the tray icon now restores the minimized window instead of hiding it, and clicking the Dock icon (or Cmd-Tabbing back to the app) brings back a hidden panel like other Mac apps.
- [UX] Accessibility permission changes take effect within seconds while the app is running — global hotkeys start as soon as permission is granted (no restart needed) and stop when it is revoked — and the permission guidance dialog now offers a "do not show again" checkbox.
- [Delivery] The build signs with the stable self-signed "AACC Local Development" identity when present in the keychain, so macOS accessibility grants survive rebuilds and upgrades; it falls back to ad-hoc signing otherwise.

## 1.3.0-rc.6 — 2026-07-21

- [Feature] Added Kimi Desktop (Kimi.app) monitoring: conversations are discovered from the daimon runtime's local read-only catalog, agent conversations reuse the Kimi Code turn analysis for full running/waiting/completed status, chats show simplified generating/idle states, and cards focus Kimi.app the same way Codex cards focus Codex.app.
- [UX] Added Kimi Desktop task selection, retention, and muting to the panel and settings, and merged discovery-health reporting across all three monitored brands.
- [Stability] Read the Kimi Desktop catalog through the sqlite WAL so conversations created while the app is running are discovered immediately, and dropped an ambiguous process-name fallback in the Kimi.app liveness check.

## 1.3.0-rc.5 — 2026-07-21

- [Stability] Fixed Kimi sessions dropping to idle during long in-turn silences (slow LLM responses, long tool calls, or context summarization): a turn still in progress now keeps the running status within a bounded active-turn window (default 30 minutes) instead of going idle after 90 seconds without file activity; sessions past the window still fall back to idle so crashed sessions cannot show running forever.
- [UX] Added an About dialog (ⓘ in the panel header) showing the running version and the matching DMG installer name.

## 1.3.0-rc.4 — 2026-07-20

- [Stability] Changed Kimi wire completion scanning to a bounded full-line reverse scan, and updated privacy wording to state that sensitive prompt and response bodies are never stored, displayed, or logged.
- [UX] Restored a running task removed with `×` automatically when it starts running again, so it returns to the monitored panel.
- [UX] Added custom task-card renaming, persisted per task id.
- [Delivery] Fixed the installer: it no longer depends on `python3`, unconditionally quits the old instance before replacement, skips tests by default (enable with `AACC_RUN_TESTS=1`), and the uninstaller is aware of `AACC_INSTALL_ROOT`.
- [UX] The panel now surfaces Kimi discovery health alongside Codex discovery health.
- [Docs] Corrected the security-model wording in SECURITY.md.
- [Delivery] Added a CI workflow running lint, type checks, and tests on macOS.

## 1.3.0-rc.3 — 2026-07-20

- [Stability] Added Kimi Code local session discovery with running/idle/completed status lights and wire-tail turn-completion detection that inspects event types only, never prompt or response content.
- [UX] Added Kimi task selection and monitoring preferences to the panel, mirroring the Codex auto-monitoring, retention, and muting behavior.
- [Delivery] Version housekeeping for the 1.3.0-rc.3 prerelease across the package, build scripts, and packaging tests.

## 1.3.0-rc.2 — 2026-07-20

- [UX] Rebuilt task cards as a compact horizontal hierarchy with a large left status light, small agent badge, prominent task name, raised whole-run timer, and one-line activity summary.
- [UX] Made the floating window grow and shrink with visible tasks, capped it at 80% of the current screen's available height, and enabled internal scrolling only beyond that cap.
- [Stability] Changed Codex discovery to a five-second cadence and added fixed privacy-safe activity labels for analysis, code edits, tests, builds, inspection, searches, command execution, and completion.
- [Stability] Preserved one timer across short active/waiting turns, froze terminal total duration, and reset timing only when a terminal task starts a new run.
- [Stability] Recognized current Codex input, command, patch, and permission approval events; recovered cold-start run times beyond the activity tail with cached incremental metadata scans; and prioritized waiting tasks with other active work.
- [Privacy] Activity classification never copies prompt, response, command, credential, or file-content payloads into task messages, logs, or the panel.
- [Stability] Stopped GUI refresh timers before shutdown and made task-manager closure idempotent, preventing a queued Qt refresh from touching a closed SQLite connection.

## 1.3.0-rc.1 — 2026-07-20

- [Security] Made configuration writes atomic, repaired invalid API tokens, rejected whitespace-bearing credentials, enforced private config/database permissions, added local credential rotation, strengthened log redaction, and removed AppleScript text interpolation.
- [Stability] Serialized complete desktop automation transactions in a bounded worker, cancelled timed-out queued/input operations before delayed injection, kept Qt responsive, preserved task run timestamps, suppressed duplicate history, bounded SQLite retention, and exposed recoverable Codex discovery health.
- [Stability] Added verified PID identity, single-instance locking, cooperative wrapper process cleanup, Accessibility guidance, event-tap recovery, and immediate adapter disconnect wake-up.
- [Breaking] The source installer now places command-line tools in a runtime-only environment under Application Support instead of linking the repository `.venv`.
- [Delivery] Added packaged QSS, lockfile-reproducible production installs, sanitized current-format Codex fixtures, build reuse, paired Developer ID/notarization validation, and explicit ad-hoc prerelease labeling.

## 1.2.0 — 2026-07-19

- Retained terminal Codex cards until explicit removal, so completed green lights no longer disappear automatically.
- Added per-card `×` removal, a matching context-menu action, and confirmation-backed bulk cleanup for retained terminal tasks.
- Added running/retained grouping, compact task counts, and last-activity time to improve scanning.
- Restored a removed task automatically when a later verified Codex run is detected.

## 1.1.0 — 2026-07-19

- Added automatic monitoring for up to four recent verified Codex tasks, persistent task muting, stale-start protection, and duplicate-index cleanup.
- Added a larger, 5× status light for faster desktop scanning.
- Added selected-task-only local Codex discovery and completion-event precedence.
- Added English-first and Chinese documentation, open-source governance, and public repository metadata.

## 1.0.0 — 2026-07-17

- Added a frameless, transparent, draggable and resizable macOS floating panel with optional always-on-top behavior.
- Added a menu-bar entry, compact mode, remembered position and opacity, state animation, and completion/error notifications.
- Added a unified state machine, SQLite state history, YAML configuration, and secret redaction.
- Added a random-token localhost API, `aacc` CLI, and `aacc-run` lifecycle wrapper.
- Added Terminal.app, iTerm2, Codex App, and generic bundle-ID focus targets.
- Added F13–F20 global shortcuts, allowlisted keyboard injection, and macOS dictation triggering.
- Added Generic CLI, Codex CLI, Claude Code, Kimi Code, and Codex App adapter presets.
- Added automated tests, installation, recoverable uninstall, PyInstaller `.app` builds, a DMG release artifact, and documentation.
