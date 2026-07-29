# AACC User Guide

[中文版本](user-guide.md) · [Back to README](../README.md)

## Panel controls

Clicking a card selects it and leaves AACC visible. Double-clicking focuses the configured target and starts the platform voice input: macOS dictation on Mac or Win+H on Windows. The context menu contains **Switch to task**, manual state controls, reset, rename, and copy-task-info. The header `EN`/`中` action provides live Chinese/English switching for the complete UI, the gear opens Settings, the dash hides the panel, and the power button exits AACC completely. On Windows, left-click the system tray icon to show/hide AACC; right-click for the persistent menu and choose **Quit AACC** to stop its background services. The language button shows the destination language: `EN` while the UI is Chinese and `中` while it is English.

On first launch, AACC uses Chinese for a Chinese system language and English for every other system language. A selection made with the header button persists across restarts on both macOS and Windows. The switch only retranslates retained UI state: it does not refresh quotas or change monitored tasks or login state. Compact mode remains in Settings and the tray menu; changing language leaves that state unchanged. Drag the panel to move it and use the lower-right corner to resize it.

## Codex task discovery

AACC refreshes local Codex metadata every five seconds. Up to four recently verified running tasks are automatically checked, displayed, and monitored. When a monitored task reaches a terminal state, it remains in the **Completed · retained until removed** section instead of disappearing. Use the card’s `×`, **Remove from panel** in its context menu, or confirmed **Clear retained tasks** to stop monitoring it. A removed task reappears automatically if it later has verified new activity. Open settings and choose **Choose Codex tasks to monitor** to add a non-running task manually or uncheck an automatic task to mute it. **Restore automatic detection** removes these mutes.

Every five seconds, AACC checks task ID, title, update time, session-file modification time, turn events, PID records, and a bounded recent tool-event category. It may inspect command category markers to distinguish tests and builds, but never copies raw conversation, prompt, response, code, command, credential, or file content into the UI, history, or logs. `task_complete` means the turn completed and `turn_aborted` means it was cancelled; both win over earlier tool activity and file modification time. `task_started` counts as running only with recent activity; a stale start event becomes unknown. A verified matching process or recent session write can also establish running state.

The panel starts near the top-right of the main display and remembers its position. **Always on top** persists your preference; **Dock to desktop top right** restores the default placement. Codex does not currently expose a reliable public API for jumping to an exact task.

If Codex metadata polling repeatedly fails, a yellow banner appears without discarding the last-known task states. **Copy diagnostics** copies a sanitized ID, counters, timestamps, and log path. The banner clears after two healthy polls.

## Codex weekly quota

The Codex quota strip first starts the locally installed Codex `app-server` and calls only its read-only `account/rateLimits/read` method with the account already configured in Codex. It does not submit a prompt, start a task, or initiate login. AACC rediscovers the safe live source every 60 seconds, so ChatGPT/Codex opened or restarted after AACC can synchronize without an AACC restart. If that path is unavailable, AACC falls back to the structured `rate_limits` object from bounded tails of recent local session files. It accepts only a future 10080-minute weekly window and intentionally ignores legacy shorter windows, so there is no five-hour Codex field. Missing, expired, or changed metadata is shown as unavailable instead of zero usage. Click the strip to refresh immediately.

Kimi shows `5H`, `WEEK`, and `MONTH`. On Windows, the first sign-in opens Microsoft Edge with an isolated AACC-owned Edge profile at `%LOCALAPPDATA%\AACC\kimi-edge-profile`; AACC never reads the normal Edge profile. The dedicated session survives AACC and PC restarts until you sign out, Kimi expires it, or a security check fails. macOS keeps its native per-application web session. AACC stores a protected reuse decision and does not copy cookies, passwords, a website bearer token, account names, or quota values into configuration. Kimi Code OAuth credentials are stored separately under AACC credential protection. One coordinator starts the website source and Kimi Code fallback in the same five-minute cycle; Kimi Code can fill a fresh, temporarily missing `5H` or `WEEK`, but never invents `MONTH`. Quota lookups are metadata-only, send no prompt, and use no generation tokens. A known percentage without a trustworthy reset time remains visible while its reset displays `--`.

## macOS DMG

The published stable installer is `AACC-1.4.2.dmg`. Open it and drag `AACC.app` to Applications. The community build is ad-hoc signed and not notarized. Compare `shasum -a 256 AACC-1.4.2.dmg` with its matching `.sha256` before using **Open Anyway**. If that standard path still fails, `xattr -cr /Applications/AACC.app` is the last-resort local quarantine removal.

## Windows Setup

The primary Windows 1.4.2 installer is `AACC-1.4.2-Setup.exe`; ordinary users do not need Python or `uv`. Verify `AACC-1.4.2-Setup.exe.sha256`, then run Setup. It is a per-user installation without administrator elevation and defaults to `%LocalAppData%\Programs\AACC`. Setup creates a Start Menu shortcut, offers an unchecked desktop shortcut, and does not add a startup entry.

Running the same Setup upgrades the existing per-user copy after a bounded graceful shutdown. Uninstall removes the installed program, Start Menu entry, optional desktop shortcut, and uninstall registration. Upgrade and uninstall preserve AACC-owned configuration, task history, database, credentials, and the protected reuse decision under `%APPDATA%\AACC`.

Windows Kimi login uses the installed Microsoft Edge browser; Setup does not install a separate browser runtime. Click **Sign in to Kimi with dedicated Edge**, complete login in the visible managed window, and AACC closes it after the three quota windows are received. Later five-minute refreshes reuse the AACC-owned Edge profile in background mode until you sign out or Kimi expires the session. Explicit AACC logout disables reuse first and removes only `%LOCALAPPDATA%\AACC\kimi-edge-profile`.

The 1.4.2 Windows build is unsigned. Windows may show Unknown publisher or SmartScreen; verify the checksum before choosing **More info → Run anyway**. Sensitive configuration, credential, database, WAL, and SHM files receive an exact native protected DACL for only the current user, Local System, and Administrators. The packaged Codex read-only query uses a fixed-purpose broker beside `AACC.exe` so the app never shells out to `icacls.exe`, `whoami.exe`, or `taskkill.exe`. Hosted Windows Server tests passed, but consumer Windows 10/11 manual verification remains a documented evidence boundary.

## Terminal and iTerm2 binding

Set a unique stable window title for each task, such as `AACC-TASK-1`. Use `terminal.type: terminal_app` and `app_bundle_id: com.apple.Terminal` for Terminal, or `terminal.type: iterm2` for iTerm2. To focus Codex App or another desktop app, use `terminal.type: mac_app` and its bundle identifier.

## Status sources

The most reliable integration is an agent hook that calls the local API. Without a hook, use `aacc-run` to report process start, running, and exit; exit code 0 produces `STOPPED`, not a fabricated business completion. `aacc status` can update state manually. Manual state takes priority and can be replaced by new automatic state after five minutes.

## Global shortcuts

F13–F16 focus tasks 1–4; F17 sends Enter; F18/F19 send `1`/`2`; F20 starts macOS dictation or Windows Win+H voice input. Karabiner-Elements, an Fn layer, or keyboard firmware can map physical keys to these function keys. On macOS, global listening and key injection require Accessibility permission, and AACC offers a direct System Settings link when it is missing. Windows uses native global-hotkey and window APIs and requires no Accessibility permission. Set `keyboard_injection: false` to disable input actions completely on either platform.

Use **Settings → Reset API credentials** to replace the localhost API token. The old token becomes invalid immediately and the new token is copied once.

## Launch at login

On macOS, the installer does not change Login Items. Add `~/Applications/AACC.app` yourself in **System Settings → General → Login Items** and remove it there at any time. On Windows, Setup adds no startup entry; launch AACC from the Start Menu or manage a separate startup shortcut yourself.
