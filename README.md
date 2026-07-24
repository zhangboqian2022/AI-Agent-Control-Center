# AI Agent Control Center (AACC)

> A local-first macOS desktop control center for the AI coding agents you choose to monitor.

[中文文档](README.zh-CN.md) · [Download AACC 1.4.0](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/download/v1.4.0/AACC-1.4.0.dmg) · [Release notes](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/tag/v1.4.0) · [Product design](docs/product-design.md)

AACC is a floating macOS panel for monitoring local AI coding-agent tasks. It discovers Codex tasks from local metadata, lets you choose exactly which tasks to monitor, and presents each selected task with a large, glanceable state light. It also supports configurable CLI agents, a localhost API, a command-line client, and conservative focus/input automation.

![AACC panel with tasks in different states](docs/images/panel-overview.png)

![Platform](https://img.shields.io/badge/platform-macOS%2013%2B-black) ![License](https://img.shields.io/badge/license-MIT-blue) ![Local first](https://img.shields.io/badge/privacy-local--first-18a999)

## Highlights

- **Automatic active-task discovery.** Recent, verified running Codex tasks appear automatically; mute any task you do not want AACC to observe.
- **Results stay visible.** Completed, failed, stopped, and cancelled Codex tasks stay on the panel until you remove them, so a green result light is never lost automatically.
- **Fast visual scanning.** Large status lights distinguish running, waiting, completed, warning, error, and unknown states.
- **Compact multi-agent cards.** A small agent badge identifies Codex or a configured adapter, while the larger task name, whole-run timer, and short activity label remain easy to scan.
- **Adaptive desktop footprint.** The panel grows or shrinks with monitored tasks and switches to internal scrolling at 80% of the current screen's available height.
- **Timely private summaries.** Codex metadata is checked every five seconds and reduced to fixed labels such as “editing code” or “running tests,” without displaying raw payload content.
- **Local-first by design.** AACC reads only the local task metadata needed for status detection and never uploads task content.
- **Reliable status boundaries.** Codex session `task_started` and `task_complete` events take priority over file activity to avoid stale “running” indicators.
- **Visible discovery health.** Repeated Codex metadata errors show a recoverable warning banner with sanitized diagnostics instead of silently freezing task state.
- **Responsive, serialized control.** Complete focus-and-input transactions run in a bounded worker so concurrent calls cannot inject into the wrong window and the panel stays responsive.
- **Desktop control without blind input.** Cards select a task; the explicit context action focuses the target app. Keyboard injection is restricted to a small allowlist.
- **Extensible integration.** Use the local API, `aacc` CLI, `aacc-run` wrapper, or configurable adapters for Codex CLI/App, Claude Code, Kimi Code, and generic CLIs.

## Install

### Recommended: download the DMG

Download [AACC-1.4.0.dmg](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/download/v1.4.0/AACC-1.4.0.dmg), open it, and drag `AACC.app` to Applications.

This build is signed with a local self-signed certificate and is not notarized by Apple. If macOS blocks the first launch, use **System Settings → Privacy & Security → Open Anyway** only after verifying the release checksum. Developer ID signing and Apple notarization are planned once a paid developer account is available.

### Build from source

Requirements: macOS 13+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/zhangboqian2022/AI-Agent-Control-Center.git
cd AI-Agent-Control-Center
./scripts/install.sh
```

The installer skips tests by default (set `AACC_RUN_TESTS=1` to run them first), builds `AACC.app`, installs it under `~/Applications/AACC.app`, creates a production-only CLI runtime under `~/Library/Application Support/AACC/runtime`, and adds `aacc` and `aacc-run` to `~/.local/bin`.

To create a distributable image:

```bash
./scripts/build_dmg.sh
```

## Use AACC with Codex

1. Launch AACC. Open its settings with the gear icon.
2. Recent, verified running Codex tasks are automatically checked and added to the panel (up to four at a time).
3. Open **Choose Codex tasks to monitor** to keep inactive tasks manually, or uncheck an automatic task to mute it. Use **Restore automatic detection** to undo mutes.
4. A completed task stays in the retained section with its terminal status light. Use its `×`, the **Remove from panel** context action, or confirmed **Clear retained tasks** to remove it.
5. If a removed task has verified new activity later, AACC automatically shows it again.
6. Drag the panel to a fixed location; use settings to toggle always-on-top and return it to the desktop’s top-right corner.

A single click selects a card and keeps AACC visible. Use the card’s context menu and **Switch to task** when you intentionally want to focus Codex.

For selected Codex sessions, AACC reads task IDs, titles, timestamps, session-file modification times, event names, matching process identifiers, and a bounded recent tool-event category. It may inspect command category markers to distinguish tests and builds, but never copies raw prompts, responses, commands, credentials, code, or file contents into the panel, task history, or logs. A historical `task_started` event without recent activity is deliberately treated as unknown rather than running. See the [English user guide](docs/user-guide.en.md) or [中文用户指南](docs/user-guide.md).

## CLI and local API

Use the wrapper for process lifecycle reporting or update a task directly:

```bash
aacc-run --task task-1 -- codex
aacc status task-1 running --message "Analyzing the repository"
aacc status task-1 waiting-approval --message "Waiting for approval"
aacc status task-1 completed --message "Changes complete"
aacc list
aacc doctor
```

The API is bound only to `http://127.0.0.1:17650` and requires a random token generated in the local config file. It is intentionally not a remote-control API.

Use **Settings → Reset API credentials** to rotate the token locally. The previous token becomes invalid immediately and the new token is copied once. Keyboard injection and global hotkeys require macOS Accessibility permission; AACC detects a missing permission and opens the correct System Settings pane on request.

## Architecture and privacy

```text
Selected local agent tasks
          ↓
Task discovery / adapters / CLI wrapper
          ↓
State manager + SQLite history + confidence rules
          ↓
Floating PySide6 panel · menu bar · localhost API
```

Task discovery, adapters, state management, UI, API, and macOS automation are isolated modules. AACC prefers structured local events; when confidence is insufficient it reports `UNKNOWN` or `WARNING` rather than inventing a result.

Security boundaries:

- Loopback-only API with a random Bearer token.
- No arbitrary shell command endpoint and no `shell=True` subprocess calls.
- Allowed injected keys are limited to Enter, Esc, arrows, Ctrl+C, `1`, and `2`.
- Target app/window activation must succeed before input is sent.
- Logs redact common tokens, passwords, and Authorization headers.

Read the full [product design](docs/product-design.md), [security policy](SECURITY.md), [known limitations](KNOWN_LIMITATIONS.md), and [troubleshooting guide](docs/troubleshooting.en.md).

## Development

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
./scripts/start.sh
```

See [adapter development](docs/adapter-development.en.md) to add a supported agent without coupling it to the UI.

## Contributing and community

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) before participating.

Author and maintainer: **zhangboqian** · <zhangboqian@hotmail.com> · [Changelog](CHANGELOG.md)

## Attribution

The product design of the Kimi quota monitoring and session metrics features was informed by the following open-source projects, with some logic adapted:
[MoonshotAI/kimi-code](https://github.com/MoonshotAI/kimi-code) (official OAuth flow and quota API conventions),
[KimiCodeBar](https://github.com/xifandev/KimiCodeBar) (booster-wallet parsing and credential isolation design),
[kimi-code-monitor](https://github.com/bfjnbvf/kimi-code-monitor) (per-session token metric algorithms).
All three are released under the MIT License; this project complies with that license
and retains each author's copyright notice. See [NOTICE](NOTICE) for details.

## License

Copyright © 2026 zhangboqian. Released under the [MIT License](LICENSE).
