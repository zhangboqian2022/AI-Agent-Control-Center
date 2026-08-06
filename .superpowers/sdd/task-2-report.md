# Task 2 Report: 配置项 `opencode_workspace_url`

## What I implemented

Added `opencode_workspace_url: str` config field to `AppConfig` in `src/aacc/models.py`:

- `from urllib.parse import urlparse` import (top of file).
- Field: `opencode_workspace_url: str = Field(default="", max_length=2048)`.
- `@field_validator("opencode_workspace_url")`: strips whitespace; empty → `""`; otherwise
  requires scheme `https`, netloc `opencode.ai`, and path starting with `/workspace/`,
  raising plain-English `ValueError`s otherwise (surfaced by pydantic as `ValidationError`).
- `config.py` untouched — its `load_config` already wraps pydantic `ValidationError` into `ValueError`.

Appended 6 tests to `tests/test_config.py` (verbatim from the brief): accepts valid workspace
page, defaults empty, rejects foreign host, rejects http scheme, rejects non-workspace path,
round-trips through config file. Imports reused: `pytest`, `default_config`, `save_config`,
`load_config` were already present; added only `from pydantic import ValidationError` and
`from aacc.models import AppConfig`.

## TDD Evidence

### RED

Command: `.venv/bin/python -m pytest tests/test_config.py -q`

Output (tail):

```
>               raise ValueError(f'"{cls.__name__}" object has no field "{name}"')
E               ValueError: "AppConfig" object has no field "opencode_workspace_url"

FAILED tests/test_config.py::test_opencode_workspace_url_accepts_valid_workspace_page
FAILED tests/test_config.py::test_opencode_workspace_url_defaults_empty
FAILED tests/test_config.py::test_opencode_workspace_url_rejects_foreign_host
FAILED tests/test_config.py::test_opencode_workspace_url_rejects_http_scheme
FAILED tests/test_config.py::test_opencode_workspace_url_rejects_non_workspace_path
FAILED tests/test_config.py::test_opencode_workspace_url_round_trips_through_config_file
6 failed, 33 passed
```

Why expected: field does not exist yet on `AppConfig`, so all 6 new tests fail exactly as
the brief predicts (pydantic `ValueError` for field-set/invalid-input paths, `AttributeError`
for the defaults-empty accessor). All 33 pre-existing tests still pass — no collateral damage.

### GREEN

Command: `.venv/bin/python -m pytest tests/test_config.py -q`

Output: `39 passed in 0.60s` (33 existing + 6 new).

## Full-suite + ruff/format/mypy results

- `.venv/bin/python -m pytest -q`: **976 passed, 7 skipped** (skips are pre-existing
  platform-conditional skips).
- `.venv/bin/ruff check src tests`: **All checks passed!**
- `.venv/bin/ruff format --check src tests`: **All passed** (after one fix, see below).
- `.venv/bin/mypy src/aacc`: **Success: no issues found in 54 source files**.

## Files changed

- `src/aacc/models.py` (+18): urlparse import, new field + validator on `AppConfig`.
- `tests/test_config.py` (+38): 2 new imports, 6 new test functions.

Commit: `c8d710b feat: add opencode workspace url config` (exact message from brief, on
branch `feat/opencode-quota`).

## Self-review findings

- Implementation matches the brief verbatim except one whitespace-level deviation:
  the brief's multi-line `raise ValueError("opencode_workspace_url must point to an
  opencode.ai workspace page")` was collapsed to one line (92 chars) because the repo's
  ruff config sets `line-length = 100` (pyproject.toml:59) and `ruff format --check` is
  required to pass. Semantics and message text identical.
- Only `src/aacc/models.py` and `tests/test_config.py` were modified/committed; pre-existing
  uncommitted changes to `AGENTS.md` and `docs/superpowers/plans/2026-07-31-opencode-quota.md`
  were left untouched and are not in the commit.
- Test file: `tmp_path` untyped in the round-trip test — matches brief verbatim; mypy only
  covers `src/aacc` so no issue.
- `save_config`/`load_config` round-trip works because the field is a plain serializable str.

## Issues / concerns

None blocking. Minor note: the one-line reformat deviation from the brief's literal snippet
(required by repo ruff config, verified `git show c8d710b` contains the final form).
