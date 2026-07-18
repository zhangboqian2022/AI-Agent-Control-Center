# AACC Product Design

[中文版本](product-design.zh-CN.md) · [Repository overview](../README.md)

## Product promise

AACC gives a developer a persistent, quiet, local view of the AI coding tasks they have deliberately selected. It makes an actionable state easy to notice without pretending to know more than a local integration can reliably establish.

## Primary users

- Developers running several local coding-agent conversations who need to notice active, waiting, completed, or failed work at a glance.
- Power users who want a local API or CLI to connect custom agent hooks without handing task data to a hosted dashboard.
- Adapter contributors who need a conservative state model rather than a GUI-specific integration.

## Experience principles

1. **Selection before observation.** AACC should display and poll only tasks the user has chosen.
2. **Glanceability over noise.** A large colored light, elapsed time, task title, and short reason make the next decision visible.
3. **Explicit control.** Selecting a card must not unexpectedly hide the panel or move focus; focus and input actions are explicit.
4. **Honest uncertainty.** Missing or ambiguous signals remain `UNKNOWN` or `WARNING`, never silently become “completed.”
5. **Local by default.** The app stores configuration and history locally, binds its API to loopback, and does not upload conversations.

## Status model

The state manager handles configured, active, waiting, terminal, warning, error, paused, cancelled, stopped, and unknown states. Updates carry a source and confidence value. Fresh high-confidence data resists accidental overwrite by weak signals, while a new task start can move a previously terminal task back to an active state.

For Codex, session events have explicit semantics: `task_started` means the current turn is active and `task_complete` means it finished. These events outrank a merely recent file modification time.

## Data flow

```text
Local Codex index / selected session events / agent hooks / CLI wrapper
                                ↓
                       discovery and adapters
                                ↓
            normalized TaskState with source and confidence
                                ↓
               SQLite history and observer notifications
                                ↓
          PySide6 floating panel, menu bar, localhost API, CLI
```

The Codex discovery adapter reads only selected-session metadata needed for status: identifiers, titles, timestamps, event labels, and process information. It does not parse prompt bodies, code, or command text.

## Interaction model

- The floating panel remembers its position and supports optional always-on-top behavior.
- Settings expose task selection, opacity, and placement controls.
- A card click selects the task; a context menu contains the intentional focus action.
- Compact mode limits visual density; the full panel includes state reason and task details.
- Menu-bar access keeps the panel reachable when hidden.

## Security and privacy boundary

The API is loopback-only and token protected. It accepts no arbitrary shell commands. Keyboard injection is restricted to an explicit allowlist and only occurs after focusing the configured target. Logs redact common secret patterns. Third-party agents with no reliable public status interface degrade to configuration, wrapper, or manual status rather than fabricated precision.

## Non-goals for 1.0

AACC is not a cloud sync service, remote-control plane, mobile app, screen-reading tool, token-cost estimator, or multi-agent workflow scheduler. It does not bypass agent approvals or macOS permissions.

## Success criteria

A user can choose a small subset of local tasks, keep AACC at a stable desktop location, immediately notice a status transition, and understand when the application is uncertain. Contributors can add an adapter without changing the GUI or weakening the local security boundary.
