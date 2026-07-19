# Codex Active Auto-monitoring Design

## Goal

Show currently active local Codex tasks without asking the user to find and add them first, while preserving an explicit and durable way to hide tasks they do not want to observe.

## Evidence and root cause

The current UI persists only a manual `codex_selected_tasks` set. The discovery service passes that set directly to `discover`, so an unselected active conversation is never registered or displayed. The local index can contain duplicate rows for one conversation. In addition, a historical `task_started` event is currently treated as running indefinitely even when its timestamp is months old and no PID or recent file activity exists.

## Monitoring model

There are three independent sets:

- **Manual selections:** inactive or completed conversations the user wants to retain on the panel.
- **Active auto-selections:** up to four recent, verified active conversations found from safe local metadata. They disappear when no longer active.
- **Muted auto-selections:** conversations the user unchecked; they remain hidden from automatic monitoring until the user restores automatic detection.

The visible and polled set is `(manual ∪ active_auto) − muted`. Inactive, unselected conversations remain only in the selector catalog and are not registered or state-polled.

## Active evidence

An active candidate requires one of:

1. a verified live PID associated with the conversation;
2. a recent session-file modification inside the configured activity window; or
3. a recent `task_started` event inside that window.

A `task_complete` event remains terminal evidence even if the file was modified recently. A stale `task_started` event produces `UNKNOWN`, not `RUNNING`. Session-index rows are deduplicated by conversation ID, retaining the most recent metadata row.

## Interface

The settings selector presents checked automatic tasks with the label **Auto monitoring · running** and places them before regular catalog entries. Its explanatory copy tells the user that unchecking one pauses automatic monitoring for that conversation; **Restore automatic detection** clears these suppressions. The settings button shows total selected count and automatic-running count.

## Safety and limits

Discovery scans only IDs, titles, timestamps, session event labels, file modification times, and PID records. It never reads prompts, code, commands, or conversation bodies. Automatic registration is capped at four tasks to avoid a crowded panel; the user can still manually select other tasks.

## Verification

Tests cover stale-event rejection, duplicate index deduplication, active candidate selection, mute persistence, automatic visibility, and selector labels. Full project tests, linting, typing, and the installed UI are checked before packaging.
