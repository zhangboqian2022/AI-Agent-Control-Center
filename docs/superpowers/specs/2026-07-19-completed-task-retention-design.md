# Completed Codex Task Retention Design

## Goal

Keep automatically discovered Codex tasks visible after their current turn completes. A completed task must retain its green result light until the user explicitly removes it, while a later verified run of a removed task must make it appear again.

## Monitoring model

The Codex discovery service will maintain three persistent user-owned sets in addition to its transient verified-active set:

- **Manual IDs**: non-running tasks explicitly selected by the user.
- **Retained IDs**: automatically discovered tasks that have been shown and must remain monitored after they stop being active.
- **Muted IDs**: tasks explicitly removed by the user; they are excluded while inactive.

On each poll, verified-active tasks are merged into the retained set before discovery. The monitored set is `(manual ∪ retained ∪ verified-active) − muted`, except a verified-active task is always allowed back in and removed from the muted set. This restores a task after a new real run without treating historical session events as activity.

The service preserves the last terminal `TaskState` in the existing task manager. Thus completed, failed, cancelled and stopped tasks remain visible until explicit removal. No time-based removal is introduced.

## Panel layout and actions

The panel will continue to be a compact, always-on-top card list. It gains:

- A compact summary row with running, retained-terminal and visible task counts.
- A **Running** group first, ordered by recent activity.
- A **Completed / retained** group below it. Completed is green; error, cancelled and stopped keep their established status colours.
- A small accessible `×` button in each card’s upper-right corner. It removes that task from the panel and monitoring preferences. The button stops click propagation so it cannot focus the task.
- A matching **Remove from panel** context-menu action.
- A **Clear retained tasks** command at the retained-group heading, protected by a confirmation dialog. It removes only non-running Codex tasks, never active tasks or non-Codex configured tasks.
- A last-activity timestamp in each expanded card.

The existing large status light, compact mode, agent visibility options, task selector, tray behavior, window position and always-on-top preference remain unchanged.

## Persistence and migration

`codex_retained_tasks` and `codex_muted_tasks` are stored in QSettings. Existing manual selections remain manual. An existing user’s already visible completed task is retained on the first refresh so an upgrade cannot hide it.

Removing a task adds it to muted IDs and removes it from manual and retained IDs. A later verified active signal removes that mute, adds the task to retained IDs and shows it again. User-initiated selection continues to remove a mute.

## Error handling and privacy

All task identity and state remain local. The feature uses the existing safe metadata-only discovery path and does not read prompts, conversation content, code or commands. A failed discovery poll keeps the last retained state rather than clearing cards. Missing states are ignored safely during grouping.

## Verification

Automated tests will cover automatic retention after terminal status, individual removal, automatic reappearance after fresh activity, clear-retained safety, ordering/grouping, timestamp rendering and QSettings persistence. Existing full tests, linting, type checks, packaging build, signature verification and DMG verification will be run before release. Chinese and English README, user guides and changelog entries will be updated with the interaction model.
