# AACC Test Report

[中文版本](test-report.md) · [Back to README](../README.md)

The 1.2 release validation covers the state machine, persistence, configuration, API authentication and validation, CLI behavior, agent adapters, automation, hotkeys, packaging, Codex discovery, retained terminal cards, removal/reappearance behavior, and Qt GUI behavior.

Run the current validation suite with:

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
uv run --extra dev mypy src
```

The release build is additionally checked with `codesign --verify --deep --strict` and `hdiutil verify` for the DMG.
