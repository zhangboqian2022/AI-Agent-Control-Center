# AACC User Guide

[中文版本](user-guide.md) · [Back to README](../README.md)

## Panel controls

Clicking a card selects it and leaves AACC visible. Double-clicking focuses the configured target and starts macOS dictation. The context menu contains **Switch to task**, allowed-key actions, manual state controls, reset, and copy-task-info. The top arrow switches compact/full mode, the gear opens settings, and the dash hides the panel to the menu bar. Drag the panel to move it and use the lower-right corner to resize it.

## Codex task discovery

AACC refreshes local Codex metadata every two seconds. Up to four recently verified running tasks are automatically checked, displayed, and monitored. When a monitored task reaches a terminal state, it remains in the **Completed · retained until removed** section instead of disappearing. Use the card’s `×`, **Remove from panel** in its context menu, or confirmed **Clear retained tasks** to stop monitoring it. A removed task reappears automatically if it later has verified new activity. Open settings and choose **Choose Codex tasks to monitor** to add a non-running task manually or uncheck an automatic task to mute it. **Restore automatic detection** removes these mutes.

For selected sessions, AACC reads only task ID, title, update time, session-file modification time, turn events, and PID records. It does not read conversation bodies, prompts, code, or commands. `task_complete` means the turn completed and wins over recent file activity. `task_started` counts as running only with recent activity; a stale start event becomes unknown. A verified matching process or recent session write can also establish running state.

The panel starts near the top-right of the main display and remembers its position. **Always on top** persists your preference; **Dock to desktop top right** restores the default placement. Codex does not currently expose a reliable public API for jumping to an exact task.

If Codex metadata polling repeatedly fails, a yellow banner appears without discarding the last-known task states. **Copy diagnostics** copies a sanitized ID, counters, timestamps, and log path. The banner clears after two healthy polls.

## DMG installation

Run `./scripts/build_dmg.sh` to create `AACC-1.3.0-rc.1.dmg` on the desktop. Open it and drag `AACC.app` to Applications. This RC is ad-hoc signed and not notarized; verify the published SHA-256 before using **Open Anyway**.

## Terminal and iTerm2 binding

Set a unique stable window title for each task, such as `AACC-TASK-1`. Use `terminal.type: terminal_app` and `app_bundle_id: com.apple.Terminal` for Terminal, or `terminal.type: iterm2` for iTerm2. To focus Codex App or another desktop app, use `terminal.type: mac_app` and its bundle identifier.

## Status sources

The most reliable integration is an agent hook that calls the local API. Without a hook, use `aacc-run` to report process start, running, and exit; exit code 0 produces `STOPPED`, not a fabricated business completion. `aacc status` can update state manually. Manual state takes priority and can be replaced by new automatic state after five minutes.

## Global shortcuts

F13–F16 focus tasks 1–4; F17 sends Enter; F18/F19 send `1`/`2`; F20 starts dictation. Karabiner-Elements or keyboard firmware can map physical keys to these function keys. Global listening and key injection require macOS Accessibility permission; AACC offers a direct System Settings link when it is missing. Set `keyboard_injection: false` to disable input actions completely.

Use **Settings → Reset API credentials** to replace the localhost API token. The old token becomes invalid immediately and the new token is copied once.

## Launch at login

The installer does not change Login Items. Add `~/Applications/AACC.app` yourself in **System Settings → General → Login Items** and remove it there at any time.
