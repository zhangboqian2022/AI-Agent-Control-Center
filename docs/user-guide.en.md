# AACC User Guide

[中文版本](user-guide.md) · [Back to README](../README.md)

## Panel controls

Clicking a card selects it and leaves AACC visible. Double-clicking focuses the configured target and starts macOS dictation. The context menu contains **Switch to task**, allowed-key actions, manual state controls, reset, and copy-task-info. The top arrow switches compact/full mode, the gear opens settings, and the dash hides the panel to the menu bar. Drag the panel to move it and use the lower-right corner to resize it.

## Codex task discovery

AACC refreshes the local Codex task index every two seconds. Open settings and choose **Choose Codex tasks to monitor**; only checked tasks are shown, receive state writes, and are polled. Unchecked tasks remain available in the selector but are not monitored.

For selected sessions, AACC reads only task ID, title, update time, session-file modification time, turn events, and PID records. It does not read conversation bodies, prompts, code, or commands. `task_started` means the active Codex turn is running and `task_complete` means it has completed; completion wins over recent file activity. Without an explicit event, a recent update (within 90 seconds) or matching process may show running; otherwise the honest state is unknown.

The panel starts near the top-right of the main display and remembers its position. **Always on top** persists your preference; **Dock to desktop top right** restores the default placement. Codex does not currently expose a reliable public API for jumping to an exact task.

## DMG installation

Run `./scripts/build_dmg.sh` to create `AACC-1.0.0.dmg` on the desktop. Open it and drag `AACC.app` to Applications. The public release build is ad-hoc signed and not notarized.

## Terminal and iTerm2 binding

Set a unique stable window title for each task, such as `AACC-TASK-1`. Use `terminal.type: terminal_app` and `app_bundle_id: com.apple.Terminal` for Terminal, or `terminal.type: iterm2` for iTerm2. To focus Codex App or another desktop app, use `terminal.type: mac_app` and its bundle identifier.

## Status sources

The most reliable integration is an agent hook that calls the local API. Without a hook, use `aacc-run` to report process start, running, and exit; exit code 0 produces `STOPPED`, not a fabricated business completion. `aacc status` can update state manually. Manual state takes priority and can be replaced by new automatic state after five minutes.

## Global shortcuts

F13–F16 focus tasks 1–4; F17 sends Enter; F18/F19 send `1`/`2`; F20 starts dictation. Karabiner-Elements or keyboard firmware can map physical keys to these function keys. Global listening and key injection require macOS Accessibility permission; set `keyboard_injection: false` to disable input actions completely.

## Launch at login

The 1.0 installer does not change Login Items. Add `~/Applications/AACC.app` yourself in **System Settings → General → Login Items** and remove it there at any time.
