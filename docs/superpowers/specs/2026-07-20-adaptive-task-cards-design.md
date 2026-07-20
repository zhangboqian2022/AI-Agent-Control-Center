# AACC Adaptive Task Cards and Live Activity Design

**Status:** Approved design candidate for v1.3.0-rc.2

**Date:** 2026-07-20

**Author:** zhangboqian <zhangboqian@hotmail.com>

## Goal

Make the floating AACC panel easier to scan when several coding agents are active. Cards must be shorter, identify both the tool and the task, show a stable whole-run timer, provide a privacy-preserving short activity description, and resize the window automatically as monitored tasks appear or are removed.

## Confirmed Product Decisions

- Use the refined **A · compact horizontal** card layout.
- Keep the large status light on the left so it remains visible at a distance.
- Show the tool name (`Codex`, `Claude Code`, `Cursor`, or an adapter-defined name) as a small badge.
- Show the task name as the largest text on the card.
- Put status and elapsed time on the right side of the light and move the timer upward.
- Target approximately 100 px per expanded task card.
- Poll Codex discovery every **5 seconds**. The existing GUI clock may continue refreshing once per second so the timer remains smooth.
- AACC automatically discovers Codex tasks. Other tools appear only when an installed or configured adapter reports them; rc.2 does not claim automatic discovery for unsupported tools.

## Card Information Hierarchy

Each expanded card contains:

1. A 56–60 px colored status light on the left.
2. A small uppercase tool badge and short localized status.
3. A prominent, single-line task name with ellipsis when the window is narrow.
4. A one-line elapsed-time label and a short activity summary.
5. An `×` removal control for removable discovered tasks.

Terminal states use the label `总用时 HH:MM:SS`. Active states use `HH:MM:SS`. The detailed timestamp remains available through the card tooltip or context menu instead of increasing card height.

## Live Activity Summary

Codex discovery will inspect only the safe tail metadata required to classify recent activity. It must not copy prompt text, assistant response text, command arguments, file contents, credentials, or arbitrary tool payloads into the card.

The classifier uses deterministic short labels so it is fast and does not hallucinate:

| Observed activity | Card summary |
| --- | --- |
| Patch or file-write event | `正在修改代码` |
| Test command classification | `正在运行测试` |
| Build/package command classification | `正在构建程序` |
| Search or browser event | `正在查询资料` |
| File-read or inspection event | `正在检查代码` |
| Task active without a recognized tool event | `正在分析任务` |
| Waiting-input or approval event | `等待你的确认` |
| Task completion event | `已完成` |

Command classification may examine only an executable or normalized command category already present in event metadata. The displayed summary is selected from the fixed table and limited to 18 Chinese characters. Unknown, malformed, oversized, or future event formats fall back to `正在分析任务` without interrupting discovery.

With a five-second discovery poll and a one-second GUI refresh, a new Codex activity label should normally appear within one to six seconds after the local event is written.

## Whole-Run Timer Semantics

A run begins when a task transitions from a terminal/non-active state into an active state. The run start time is preserved across short Codex turns and across `RUNNING`, `WAITING_INPUT`, and `WAITING_APPROVAL` transitions.

- While active, elapsed time is `now - run_started_at`.
- On completion, failure, cancellation, or stop, the timer freezes at `run_finished_at - run_started_at`.
- A completed card remains visible until the user presses `×` or otherwise removes it.
- If a retained terminal task becomes active again, AACC starts a new run and resets the timer to zero.
- Discovery heartbeats and message-only changes never reset the timer.

This behavior belongs in state reconciliation, not in GUI-only bookkeeping, so persisted state and all views agree.

## Adaptive Window Height

After card creation, removal, visibility changes, compact-mode changes, and screen changes, the main window recalculates its desired height.

1. Measure the non-scrollable chrome plus the visible card content height.
2. Clamp the desired height between the existing minimum usable height and **80% of the available height of the screen containing the window**.
3. When content is below the cap, hide the vertical scrollbar and shrink or grow the outer window to fit.
4. When content exceeds the cap, keep the outer window at the cap and enable internal vertical scrolling.
5. Preserve the current top-right docking edge while changing only the vertical size; do not jump to another display.
6. Coalesce bursts of task updates into one queued resize and animate only if it does not interfere with window dragging.

Saved geometry may restore horizontal position and width, but stale saved height must not override adaptive height after cards synchronize.

## Failure Handling and Compatibility

- Unrecognized Codex event formats degrade to generic running/completed messages.
- A malformed or temporarily unreadable session tail must not remove cards or freeze the discovery thread; existing discovery health warnings remain authoritative.
- Unsupported tools keep using the `message` supplied by their adapters.
- All UI text has a deterministic fallback when task name, tool display name, or message is missing.
- Existing always-on-top, desktop level, opacity, compact mode, task selection, and removal behavior remain supported.

## Test Strategy

Unit and GUI tests must cover:

- Codex discovery interval defaults to exactly five seconds.
- Safe activity classification for patch, test, build, search, inspection, unknown, malformed, and completion events.
- No prompt/response/command/file content is copied into the card message.
- Run start survives active heartbeats and waiting transitions.
- Terminal state freezes total duration.
- Terminal-to-active transition resets the run start time.
- Refined card hierarchy, tool badge, large task name, and terminal `总用时` label.
- Window grows and shrinks when cards are added or removed.
- Window height never exceeds 80% of available screen height and scrolling activates beyond the cap.
- Adaptive resizing preserves the docking edge and ignores stale saved height.

The full unit suite, formatting/lint checks, type checks, packaging smoke tests, and a manual macOS multi-task check are required before the rc.2 release.

## Out of Scope for rc.2

- Automatic discovery implementations for Claude Code, Cursor, or other tools.
- AI-generated natural-language summaries.
- Reading or displaying private prompt/response content.
- Moving or force-updating the existing `v1.3.0-rc.1` tag.
