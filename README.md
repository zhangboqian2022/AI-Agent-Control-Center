# AI Agent Control Center (AACC)

> A local-first macOS desktop control center for the AI coding agents you choose to monitor.

[中文文档](README.zh-CN.md) · [Download AACC 1.2.0](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/download/v1.2.0/AACC-1.2.0.dmg) · [Release notes](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/tag/v1.2.0) · [Product design](docs/product-design.md)

AACC is a floating macOS panel for monitoring local AI coding-agent tasks. It discovers Codex tasks from local metadata, lets you choose exactly which tasks to monitor, and presents each selected task with a large, glanceable state light. It also supports configurable CLI agents, a localhost API, a command-line client, and conservative focus/input automation.

![Platform](https://img.shields.io/badge/platform-macOS%2013%2B-black) ![License](https://img.shields.io/badge/license-MIT-blue) ![Local first](https://img.shields.io/badge/privacy-local--first-18a999)

## Highlights

- **Automatic active-task discovery.** Recent, verified running Codex tasks appear automatically; mute any task you do not want AACC to observe.
- **Results stay visible.** Completed, failed, stopped, and cancelled Codex tasks stay on the panel until you remove them, so a green result light is never lost automatically.
- **Fast visual scanning.** Large status lights distinguish running, waiting, completed, warning, error, and unknown states.
- **Local-first by design.** AACC reads only the local task metadata needed for status detection and never uploads task content.
- **Reliable status boundaries.** Codex session `task_started` and `task_complete` events take priority over file activity to avoid stale “running” indicators.
- **Desktop control without blind input.** Cards select a task; the explicit context action focuses the target app. Keyboard injection is restricted to a small allowlist.
- **Extensible integration.** Use the local API, `aacc` CLI, `aacc-run` wrapper, or configurable adapters for Codex CLI/App, Claude Code, Kimi Code, and generic CLIs.

## Install

### Recommended: download the DMG

Download [AACC-1.2.0.dmg](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/download/v1.2.0/AACC-1.2.0.dmg), open it, and drag `AACC.app` to Applications.

The public build is ad-hoc signed and is not notarized by Apple. If macOS blocks the first launch, use **System Settings → Privacy & Security → Open Anyway** only after confirming that the DMG came from this release page.

### Build from source

Requirements: macOS 13+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/zhangboqian2022/AI-Agent-Control-Center.git
cd AI-Agent-Control-Center
./scripts/install.sh
```

The installer resolves dependencies, runs tests, builds `AACC.app`, installs it under `~/Applications/AACC.app`, and adds `aacc` and `aacc-run` to `~/.local/bin`.

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

For selected Codex sessions, AACC reads task IDs, titles, timestamps, session-file modification times, event names, and matching process identifiers. It does **not** read prompts, code, commands, or conversation bodies. A historical `task_started` event without recent activity is deliberately treated as unknown rather than running. See the [English user guide](docs/user-guide.en.md) or [中文用户指南](docs/user-guide.md).

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

Read the full [product design](docs/product-design.md), [security policy](SECURITY.md), and [troubleshooting guide](docs/troubleshooting.en.md).

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

## License

Copyright © 2026 zhangboqian. Released under the [MIT License](LICENSE).
