# AACC V1.0 Design

## Product boundary

AACC is a local-only macOS floating control center for four AI coding-agent tasks. V1.0 ships a usable GUI, menu-bar control, persistent task state, authenticated localhost API, CLI, safe app/window focus, whitelisted key injection, configurable voice hotkey, process/log adapters, and repeatable `.app` packaging. It does not invent unsupported third-party hooks or automatically approve dangerous commands.

## Chosen approach

Use Python 3.12+ with PySide6, Pydantic, PyYAML, FastAPI/Uvicorn, SQLite, and standard macOS automation commands. This follows the supplied product specification, keeps adapters easy to add, and produces a native desktop window without Electron's runtime footprint.

Alternatives considered:

- SwiftUI would provide the strongest native integration and smallest bundle, but conflicts with the specified Python adapter ecosystem and would slow delivery.
- Tauri would provide a compact web-based UI, but adds Rust/frontend build complexity without improving the local control core.
- PySide6 is selected because it balances desktop capability, rapid iteration, testability, and the requested extension model.

## Architecture

The application is divided into focused units:

1. `models` defines task configuration, runtime state, events, and the status enum.
2. `core` applies transition precedence, stores state/history in SQLite, publishes changes, loads YAML configuration, and redacts logs.
3. `api` exposes a token-protected service bound strictly to `127.0.0.1`.
4. `cli` calls the same API and includes diagnostics; `aacc-run` wraps CLI agents and reports lifecycle changes.
5. `adapters` normalize configured process/log signals for Codex CLI, Claude Code, Kimi Code, Codex App, and generic/Z Code.
6. `terminals` and `automation` focus Terminal.app, iTerm2, Codex or an arbitrary bundle ID, and safely send only approved keys/text.
7. `gui` renders four cards in compact or expanded mode and reacts to persisted/API state without blocking the UI thread.

The GUI never parses an agent's output. Adapters emit standard status updates into `TaskManager`, which validates precedence, persists history, and notifies UI subscribers.

## State and persistence

States are `UNCONFIGURED`, `IDLE`, `STARTING`, `THINKING`, `RUNNING`, `WAITING_INPUT`, `WAITING_APPROVAL`, `COMPLETED`, `WARNING`, `ERROR`, `PAUSED`, `CANCELLED`, `STOPPED`, and `UNKNOWN`. Each update includes source, confidence, message, and timestamps. Manual updates have the highest priority. Lower-confidence events cannot replace higher-confidence fresh events. Terminal states can restart only through an explicit active-state update.

Configuration lives at `~/Library/Application Support/AACC/config.yaml`; runtime data and history live in SQLite beside it. First launch creates a four-task configuration and a random API token. Window geometry and UI preferences are persisted through Qt settings.

## User experience

The floating, translucent, resizable panel starts with four task cards and stays above normal windows. Each card shows slot, agent, task name, status, message, elapsed time, and update time. Blue means active, yellow means user attention, green means complete, red means error, and grey means idle/unconfigured. Yellow cards use a gentle pulse that can be disabled.

Clicking a card focuses its configured target. The card menu offers focus, Enter, 1, 2, arrow keys, voice, manual state changes, copy details, and configuration/log locations. The menu-bar icon can show/hide the panel, toggle compact mode and topmost behavior, and quit.

## Control and safety

The API validates bearer tokens, input sizes, task IDs, statuses and key names. It never accepts shell commands. Subprocess calls use argument arrays, timeouts, and `shell=False`. Key/text injection occurs only after the configured application and optional window title are successfully focused. API defaults to localhost and refuses any non-loopback host in V1.0.

Global function-key hotkeys use a macOS event tap when Accessibility permission is available; the app continues without global hotkeys when permission is absent. Keyboard injection, text sending, voice triggering, and automatic focus can each be disabled.

## Adapter behavior

Generic CLI is the reliable base adapter. Specialized Codex, Claude, and Kimi adapters supply executable and conservative status-pattern defaults. Codex App uses bundle activation plus manual/API state because no private event interface is assumed. Z Code uses Generic CLI configuration and makes no product-specific assumption. Ambiguous output maps to `WARNING` or `UNKNOWN`, never a fabricated approval state.

## Error handling

Adapter, API, automation, database, and GUI errors are isolated and logged. User-facing control failures return actionable messages and never terminate the GUI. Corrupt configuration is backed up before a safe default is created. Database initialization and migrations are transactional.

## Testing and acceptance

Unit tests cover state precedence, configuration, redaction, regex parsing, SQLite recovery, API auth/validation, key whitelist, and AppleScript escaping. Integration tests cover CLI/API-to-state persistence and restart recovery. GUI smoke tests use Qt's offscreen platform. Final verification runs the complete test suite, builds the package, starts the API, exercises authenticated state changes, builds the `.app`, and launches it on macOS.

## Explicit V1.0 limitations

Reliable per-tab focus depends on stable Terminal/iTerm2 window or tab titles. Accessibility and Automation permissions are user-controlled macOS grants. Third-party agents without structured hooks use wrapper/API/manual updates or conservative log patterns. Unsigned local `.app` builds may require the user to approve first launch in macOS privacy settings.
