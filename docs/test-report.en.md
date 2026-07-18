# AACC Test Report

[中文版本](test-report.md) · [Back to README](../README.md)

The 1.0 release validation covers the state machine, persistence, configuration, API authentication and validation, CLI behavior, agent adapters, automation, hotkeys, packaging, Codex discovery, and Qt GUI behavior.

Run the current validation suite with:

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

The release build is additionally checked with `codesign --verify --deep --strict` and `hdiutil verify` for the DMG.
