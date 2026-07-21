# AACC Kimi Desktop Monitoring Design

**Status:** Approved design candidate

**Date:** 2026-07-21

**Author:** zhangboqian <zhangboqian@hotmail.com>

## Goal

Extend AACC to monitor tasks running in the Kimi Desktop application
(Kimi.app, bundle id `com.moonshot.kimichat`) the same way it already
monitors Codex and Kimi Code CLI sessions: discovered task cards, status
detection, window focus, and the existing manual/retained/muted/auto-active
semantics. Both Agent tasks (daimon / OK Computer mode) and ordinary chat
conversations are monitored.

## Confirmed Product Decisions

- Monitor **both** Kimi Desktop Agent tasks and ordinary chat conversations.
- Chat conversations use **simplified status semantics**: RUNNING while a
  reply is being generated, otherwise idle/completed. No waiting-input or
  waiting-approval detection for chats.
- Agent conversations reuse the proven Kimi Code status detection
  (mtime freshness + wire.jsonl turn-boundary analysis + bounded active-turn
  window), reaching full RUNNING / WAITING_INPUT / WAITING_APPROVAL /
  COMPLETED parity with Kimi Code CLI cards.
- Card focus activates Kimi.app through the existing `mac_app` terminal
  mechanism (`open -b com.moonshot.kimichat`), the same mechanism Codex
  cards use to focus Codex.app. No automation-layer changes.
- GUI gains a third brand by following the existing codex/kimi pairing
  pattern. A brand-registry generalization is explicitly deferred (recorded
  as technical debt) until a fourth brand appears.

## Data Sources

All Kimi Desktop data lives under the daimon runtime root:

```
~/Library/Application Support/kimi-desktop/daimon-share/daimon/
```

The root path is injectable for tests, matching the existing discovery
modules' dependency-injection style.

### Conversation catalog: `conversations.sqlite`

`agents/main/sessions/hosted-logical/conversations.sqlite` indexes every
conversation. It is opened read-only and immutable
(`file:...?mode=ro&immutable=1`) so discovery never interferes with the
app's own writes.

Relevant `conversations` columns:

- `conversation_id` — stable id, becomes the task id suffix.
- `title` — card display name.
- `updated_at_ms` — last activity timestamp.
- `origin` — conversation origin; used to classify chat vs agent.
- `workspace_path` — working directory, shown as secondary metadata.
- `kernel_type`, `kernel_session_dir`, `kernel_records_path` — locate the
  embedded kimi-code session backing an Agent conversation.

Classification rule: a conversation **with** a `kernel_session_dir` is an
Agent task; everything else is a chat. The `origin` value mapping will be
calibrated against real data (see Verification); the kernel-dir rule is the
robust fallback and remains the primary signal.

### Agent session kernel: embedded kimi-code home

Agent conversations execute on an embedded kimi-code runtime whose home
(`runtime/kimi-code/home/`) mirrors the `~/.kimi-code` layout: per-session
directories containing `state.json` and `agents/main/wire.jsonl`. The
`kernel_session_dir` column points discovery at the right session
directory.

## Status Detection

### Agent conversations

Delegate to the existing Kimi Code turn analysis. `kimi_discovery.py`
gains an extracted, reusable single-session status function (pure refactor,
zero behavior change for the CLI path) that takes a session directory plus
injected clock/process callbacks and returns state, confidence, activity
message, and turn-start timestamp. `KimiDesktopDiscovery` calls it with
the kernel session dir. This inherits:

- 90-second activity freshness window,
- wire.jsonl turn-boundary scan (`usage.record` with turn scope vs.
  `turn.prompt` / `llm.request` / `context.append_loop_event`),
- the bounded active-turn window (default 1800 s) that keeps long-silence
  turns RUNNING before falling back to idle.

If the kernel session dir is missing or unreadable, the conversation falls
back to `updated_at_ms` freshness (chat rules below).

### Chat conversations (simplified)

- `updated_at_ms` within the 90-second activity window → RUNNING
  (reply generating).
- Otherwise → COMPLETED when the conversation has any assistant reply, else
  IDLE. (Calibration against real data may collapse this to IDLE-only if
  reply presence is not cheaply detectable; the card-facing behavior is
  identical since both are non-active states.)

### Process liveness fallback

When the sqlite catalog is missing or unreadable, discovery degrades to
process-liveness mode: if Kimi.app is running, known conversations report
UNKNOWN instead of disappearing, and `DiscoveryHealth` records the
degradation reason. A missing daimon root (app not installed or Agent
features never used) is reported through health without producing cards or
error dialogs.

## Identity and Configuration

- Task id: `kimi_desktop:{conversation_id}`.
- `AgentConfig(type="kimi_desktop", display_name="Kimi Desktop")`.
- `TerminalConfig(type="mac_app", app_bundle_id="com.moonshot.kimichat")`.
- `visible_agent_types` default gains `"kimi_desktop"` (`models.py`, plus
  the GUI display-name mapping).
- Key/voice injection follows the existing `mac_app` focus path; actual
  injection behavior against Kimi.app is validated manually and not
  guaranteed beyond focus.

## Service Assembly

- New `KimiDesktopDiscoveryService(LocalDiscoveryService)` thin subclass in
  `discovery_service.py` (~15 lines, brand `"Kimi Desktop"`), inheriting
  the manual/retained/muted/auto-active set semantics including automatic
  un-muting of running tasks.
- `app.py`: `Runtime` gains the service field; `build_runtime()`
  constructs it; start/stop wired alongside the existing two services; a
  third callback group passed to `MainWindow`.

## GUI Changes (`gui.py`)

Follow the existing codex/kimi pairing pattern for a third brand:

- Third callback group in `MainWindow.__init__` and its storage.
- New QSettings keys `kimi_desktop_manual_tasks`,
  `kimi_desktop_retained_tasks`, `kimi_desktop_muted_tasks` (no legacy
  migration needed).
- `KimiDesktopTaskSelectionDialog` thin subclass plus a third
  task-selection button in `SettingsDialog`.
- All `task.id.startswith(...)` dispatch sites gain the `kimi_desktop:`
  prefix.
- The discovery-health merge logic (currently assuming exactly two sources)
  becomes a list-based merge over all registered brands — the only place
  the existing code hardcodes brand count, so it must change.

## Error Handling and Degradation

| Condition | Behavior |
| --- | --- |
| daimon root missing | Health reports "not found"; no cards, no dialogs |
| sqlite missing/corrupt/schema mismatch | Degrade to process-liveness mode; health records reason |
| Kernel session dir missing for one conversation | That conversation falls back to timestamp-freshness status |
| Embedded kimi-code layout changes | Extraction layer isolates the breakage to agent-status precision; catalog and chat detection keep working |

## Testing

- `tests/test_kimi_desktop_discovery.py` (new): tmp_path-built daimon
  trees, real sqlite tables created with the observed schema, injected
  `now` / process-liveness callbacks — mirroring `test_kimi_discovery.py`
  style. Covers: chat active/idle, agent status delegation, kernel-dir
  fallback, missing/corrupt sqlite degradation, mixed-catalog enumeration.
- `tests/test_discovery_service.py`: third-brand thin-subclass case.
- `tests/test_gui.py`: third QSettings key group persistence, selection
  dialog, visibility filtering.
- Regression guard: existing kimi/codex discovery tests must pass
  unchanged, proving the status-function extraction is behavior-preserving.
- Gate: `pytest -q`, `ruff check src tests`, `mypy src/aacc` all green.

## Documentation

- `docs/adapter-development.md` (both languages) gains a short section on
  the Kimi Desktop discovery source.
- `AGENTS.md` architecture notes updated for the third discovery service.

## Verification With Real Data

The sqlite value formats (`origin` values, `kernel_session_dir` path
shape) are calibrated against a real Kimi Desktop session before
implementation is declared complete: run one Agent task and one chat in
Kimi.app, then confirm discovered cards, titles, and status transitions
match observation. Degradation fallbacks above guarantee the code stays
correct (if less precise) even where real formats diverge from the
assumptions recorded here.
