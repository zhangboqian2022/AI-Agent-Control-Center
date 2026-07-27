# AACC User Guide

[中文版本](user-guide.md) · [Back to README](../README.md)

## Panel controls

Clicking a card selects it and leaves AACC visible. Double-clicking focuses the configured target and starts macOS dictation. The context menu contains **Switch to task**, allowed-key actions, manual state controls, reset, and copy-task-info. The top arrow switches compact/full mode, the gear opens settings, and the dash hides the panel to the menu bar. Drag the panel to move it and use the lower-right corner to resize it.

## Codex task discovery

AACC refreshes local Codex metadata every five seconds. Up to four recently verified running tasks are automatically checked, displayed, and monitored. When a monitored task reaches a terminal state, it remains in the **Completed · retained until removed** section instead of disappearing. Use the card’s `×`, **Remove from panel** in its context menu, or confirmed **Clear retained tasks** to stop monitoring it. A removed task reappears automatically if it later has verified new activity. Open settings and choose **Choose Codex tasks to monitor** to add a non-running task manually or uncheck an automatic task to mute it. **Restore automatic detection** removes these mutes.

Every five seconds, AACC checks task ID, title, update time, session-file modification time, turn events, PID records, and a bounded recent tool-event category. It may inspect command category markers to distinguish tests and builds, but never copies raw conversation, prompt, response, code, command, credential, or file content into the UI, history, or logs. `task_complete` means the turn completed and wins over recent file activity. `task_started` counts as running only with recent activity; a stale start event becomes unknown. A verified matching process or recent session write can also establish running state.

The panel starts near the top-right of the main display and remembers its position. **Always on top** persists your preference; **Dock to desktop top right** restores the default placement. Codex does not currently expose a reliable public API for jumping to an exact task.

If Codex metadata polling repeatedly fails, a yellow banner appears without discarding the last-known task states. **Copy diagnostics** copies a sanitized ID, counters, timestamps, and log path. The banner clears after two healthy polls.

## Codex weekly quota

The Codex quota strip first starts the locally installed Codex `app-server` and calls only its read-only `account/rateLimits/read` method with the account already configured in Codex. It does not submit a prompt, start a task, or initiate login. If that path is unavailable, AACC falls back to the structured `rate_limits` object from bounded tails of recent local session files. It accepts only a future 10080-minute weekly window and intentionally ignores legacy shorter windows, so there is no five-hour Codex field. Missing, expired, or changed metadata is shown as unavailable instead of zero usage. Click the strip to refresh.

Kimi shows `5H`, `WEEK`, and `MONTH`. Sign in to the Kimi membership website inside AACC to cache an isolated web session and refresh all three rows together every five minutes. Kimi Code can fill a temporarily missing `5H` or `WEEK`, but never invents `MONTH`. Quota lookups are metadata-only requests and consume no model tokens.

## macOS DMG

The published stable installer remains `AACC-1.4.1.dmg`. Building the current 1.4.2 source with `./scripts/build_dmg.sh` creates a versioned 1.4.2 DMG candidate; it is not a formal Release asset until the 1.4.2 gates close. Open the matching DMG and drag `AACC.app` to Applications. The local build is self-signed and not notarized. Compare `shasum -a 256 <file>.dmg` with its matching `.sha256` before using **Open Anyway**. If that standard path still fails, `xattr -cr /Applications/AACC.app` is the last-resort local quarantine removal.

## Windows Setup candidate

The primary Windows 1.4.2 candidate is `AACC-1.4.2-Setup.exe`; ordinary users do not need Python or `uv`. Verify `AACC-1.4.2-Setup.exe.sha256`, then run Setup. It is a per-user installation without administrator elevation and defaults to `%LocalAppData%\Programs\AACC`. Setup creates a Start Menu shortcut, offers an unchecked desktop shortcut, and does not add a startup entry.

Running the same Setup upgrades the existing per-user copy after a bounded graceful shutdown. Uninstall removes the installed program, Start Menu entry, optional desktop shortcut, and uninstall registration. Upgrade and uninstall preserve `%APPDATA%\AACC`, including configuration, task history, database, and the cached Kimi membership session. Use AACC’s explicit Kimi logout to remove the cached session.

The 1.4.2 candidate is unsigned. Windows may show Unknown publisher or SmartScreen; verify the checksum before choosing **More info → Run anyway**. Sensitive configuration, credential, database, WAL, and SHM files receive an exact native protected DACL for only the current user, Local System, and Administrators. The packaged Codex read-only query uses a fixed-purpose broker beside `AACC.exe` so the app never shells out to `icacls.exe`, `whoami.exe`, or `taskkill.exe`.

## Terminal and iTerm2 binding

Set a unique stable window title for each task, such as `AACC-TASK-1`. Use `terminal.type: terminal_app` and `app_bundle_id: com.apple.Terminal` for Terminal, or `terminal.type: iterm2` for iTerm2. To focus Codex App or another desktop app, use `terminal.type: mac_app` and its bundle identifier.

## Status sources

The most reliable integration is an agent hook that calls the local API. Without a hook, use `aacc-run` to report process start, running, and exit; exit code 0 produces `STOPPED`, not a fabricated business completion. `aacc status` can update state manually. Manual state takes priority and can be replaced by new automatic state after five minutes.

## Global shortcuts

F13–F16 focus tasks 1–4; F17 sends Enter; F18/F19 send `1`/`2`; F20 starts dictation. Karabiner-Elements or keyboard firmware can map physical keys to these function keys. Global listening and key injection require macOS Accessibility permission; AACC offers a direct System Settings link when it is missing. Set `keyboard_injection: false` to disable input actions completely.

Use **Settings → Reset API credentials** to replace the localhost API token. The old token becomes invalid immediately and the new token is copied once.

## Launch at login

The installer does not change Login Items. Add `~/Applications/AACC.app` yourself in **System Settings → General → Login Items** and remove it there at any time.
