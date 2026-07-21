# AACC Adapter Development

[中文版本](adapter-development.md) · [Back to README](../README.md)

An adapter translates a third-party agent’s process or output into the shared `TaskStatus` model; it must not manipulate the GUI directly. Configuration-driven adapters use `AgentConfig` fields such as `process_patterns`, `running_patterns`, `waiting_input_patterns`, `waiting_approval_patterns`, `completed_patterns`, and `error_patterns`.

To add a built-in agent, add a conservative display name, process match, and line-based status patterns to `PRESETS` in `src/aacc/adapters.py`. Patterns should contain an explicit line start or context; avoid isolated generic words such as `allow` or `done`. `GenericCLIAdapter` removes ANSI escapes, rejects lines longer than 4096 characters, and applies a 20 ms timeout to each regex search.

Structured hooks should send `status`, a `message` of at most 2000 characters, a unique `source`, and a `confidence` value between 0 and 1 to `POST /api/v1/tasks/{task_id}/status`. Hook failure must never block the agent. Do not send full prompts, private code, passwords, or API keys to AACC.

Every new adapter needs tests for process detection, each explicit status pattern, ambiguous text that must not match, ANSI input, and oversized lines. Do not add agent-specific branching to the core GUI or API.

## Kimi Desktop (local discovery)

AACC also monitors tasks in the Kimi desktop app (Kimi.app, `com.moonshot.kimichat`), using `~/Library/Application Support/kimi-desktop/daimon-share/daimon/` as its data source:

- `agents/main/sessions/hosted-logical/conversations.sqlite` provides the
  session catalog (opened read-only; only metadata columns are read). Sessions
  with a `kernel_session_dir` are treated as agent tasks, and their status is
  determined by reusing Kimi Code's mtime + wire.jsonl turn-boundary analysis;
  the rest are treated as chat sessions and only distinguished as "generating
  a reply / idle".
- Task ids are prefixed with `kimi_desktop:`, and card focusing uses the
  `mac_app` mechanism (`open -b`), the same way Codex cards focus Codex.app.
