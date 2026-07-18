# Contributing to AACC

Thank you for helping make AI Agent Control Center more useful and trustworthy.

## Before opening an issue

- Search existing issues and releases first.
- Include your macOS version, AACC version, agent surface, and concise steps to reproduce.
- Remove local tokens, prompts, source code, terminal history, and personal paths from logs and screenshots.
- Use [SECURITY.md](SECURITY.md) instead of a public issue for security-sensitive reports.

## Development setup

```bash
git clone https://github.com/zhangboqian2022/AI-Agent-Control-Center.git
cd AI-Agent-Control-Center
uv sync
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Run the application in development mode with `./scripts/start.sh`.

## Pull requests

1. Create a focused branch from `main`.
2. Keep each pull request limited to one clear behavior or documentation improvement.
3. Add or update tests when changing runtime behavior.
4. Run the checks above and describe the result in the pull request.
5. Explain user-visible changes, security implications, and macOS permission implications when relevant.

Avoid uploading generated builds, local configuration files, SQLite databases, session data, or credentials. Preserve AACC’s local-first boundary: integrations must not send task content to external services without an explicit, separately reviewed design.

## Adapter contributions

Adapters translate external agent signals into `TaskStatus`; they must not directly manipulate the GUI. Prefer structured local events over text matching, make regexes conservative, and add false-positive tests. See [adapter development](docs/adapter-development.en.md).

## Community

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). For project questions, contact zhangboqian at <zhangboqian@hotmail.com>.
