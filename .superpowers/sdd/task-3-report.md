# Task 3 Report: 错误归一化 `opencode_web_error.py` + i18n 键

**Status: DONE** — commit `3836e04` `feat: add opencode usage error categories and i18n keys`

## What I implemented

Created `src/aacc/opencode_web_error.py` exactly per the brief:
- `OpenCodeQuotaErrorCategory(StrEnum)` with `UNAUTHORIZED`, `REFRESH_TIMEOUT`, `REFRESH_FAILED`, `PARSE_FAILED` (values `unauthorized` / `refresh_timeout` / `refresh_failed` / `parse_failed`).
- `_ERROR_KEYS` mapping category → i18n key (`opencode.web_unauthorized` etc.).
- `normalize_opencode_quota_error_category(value: object) -> OpenCodeQuotaErrorCategory`: passes enum through, accepts valid strings, falls back to `REFRESH_FAILED` for unknown strings / non-str values (mirrors `kimi_web_error.py` house pattern).
- `opencode_quota_error_text(category: object, language_manager: LanguageManager) -> str`: normalizes then translates via `LanguageManager.text`.

Modified `src/aacc/i18n.py`:
- Added `"opencode_web_session"` to `LanguageSubscriberComponent` Literal and `LANGUAGE_SUBSCRIBER_COMPONENTS` frozenset.
- ZH_CN catalog: `opencode.quota`, `opencode.web_title`, `opencode.web_starting`, `opencode.web_need_config`, `opencode.web_unauthorized`, `opencode.web_refresh_timeout`, `opencode.web_refresh_failed`, `opencode.web_parse_failed` inserted after `"quota.last_update"`; `settings.opencode_web_login`, `settings.opencode_logout` after `"settings.kimi_logout"`.
- EN_US catalog: same 10 keys at the same anchors, per the brief's English strings.

Tests: created `tests/test_opencode_web_error.py` (3 tests from the brief) and appended `test_opencode_web_keys_exist_in_both_catalogs` to `tests/test_i18n.py`.

## TDD Evidence

**RED** — `.venv/bin/python -m pytest tests/test_opencode_web_error.py tests/test_i18n.py -q`
Output:
```
ERROR collecting tests/test_opencode_web_error.py
E   ModuleNotFoundError: No module named 'aacc.opencode_web_error'
```
Expected: module didn't exist yet, so collection failed. (The i18n catalog-keys test would also have failed on missing keys, but collection aborted first — the same RED condition.)

**GREEN** — same command after implementation:
```
18 passed in 0.50s
```
(15 existing test_i18n tests + 3 new opencode tests all pass, including the appended catalog-keys test.)

## Full-suite + lint results

- `.venv/bin/python -m pytest -q` → `980 passed, 7 skipped in 12.13s`
- `.venv/bin/ruff check src tests` → `All checks passed!`
- `.venv/bin/ruff format --check src tests` → `124 files already formatted`
- `.venv/bin/mypy src/aacc` → `Success: no issues found in 55 source files`

## Files changed

- `src/aacc/opencode_web_error.py` (new, 38 lines)
- `src/aacc/i18n.py` (+22 lines)
- `tests/test_opencode_web_error.py` (new, 39 lines)
- `tests/test_i18n.py` (+20 lines)

Commit: `3836e04` `feat: add opencode usage error categories and i18n keys` (4 files, 119 insertions). Pre-existing unstaged changes (`.superpowers/sdd/task-2-report.md`, `AGENTS.md`, plan doc) were left uncommitted, as they're outside this task's scope.

## Self-review findings

1. **One deviation from the brief (mechanical)**: `ruff format` collapsed one assert in `tests/test_opencode_web_error.py` to a single line (`normalize_opencode_quota_error_category(None) is OpenCodeQuotaErrorCategory.REFRESH_FAILED`). The brief's verbatim text exceeded 88 columns; CI runs `ruff format --check` (per AGENTS.md), which would have reddened. Semantics unchanged.
2. Catalog placement matches the brief's anchors exactly (`quota.last_update` → `task.switch` boundary preserved; `settings.kimi_logout` → `settings.visible_agents`). Both catalogs get identical key sets — verified by the existing `test_catalogs_have_identical_keys_and_placeholders` (no placeholders in the new keys, so placeholder parity is trivially satisfied).
3. Implementation matches the brief line-for-line except adding a docstring (the brief's file has one) — module docstring `"""Normalize opencode.ai workspace usage errors for display."""` included per the brief.
4. No logger or untrusted-input retention issues: normalize folds unknown values before any translation, consistent with `kimi_web_error.py`.

## Issues / concerns

- None blocking. Minor note: the brief's test snippet was not ruff-format-clean as written (see finding 1); future briefs with multi-line asserts over 88 chars will need the same mechanical collapse.
- Nothing outside the 4 allowed files was modified.
