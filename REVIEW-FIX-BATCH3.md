# REVIEW-FIX-BATCH3.md — Documentation review findings (batch 3)

Branch: `fix/review-1.4.4`. Single commit on top of batches 1/2a/2b + step-shadow fix.

## Fix 1 (P1-3): least-privilege deployment guidance

- `SECURITY.md`: added a "Least-privilege deployment" section recommending
  `keyboard_injection: false` in `config.yaml` to disable input actions entirely
  on both platforms for environments without desktop-control needs, and noting
  that `/send-text` plus `Enter` equals the current user's interactive typing
  ability, so the API token must be protected.
- No Chinese SECURITY pair exists (only `SECURITY.md`; confirmed via
  `git ls-files | grep -i security`) — left as-is per instructions.
- `docs/release-notes-1.4.4rc1.md`: added a "Least-privilege deployment" /
  "最小权限部署" Security bullet to both the English and Chinese sections.

## Fix 2 (P1-D): correct Windows identity-verification wording

The 1.4.4-rc.1 notes claimed foreground *identity* re-verification on Windows;
the implementation only re-checks the foreground window handle (unique
window-title match + HWND equality), not PID / process creation time /
executable image. Corrected:

- `docs/release-notes-1.4.4rc1.md` (both languages): Windows requires a unique
  window-title match and re-checks the foreground window handle immediately
  before input; process identity (PID or image path) is not re-verified.
  Fail-closed claim (ambiguous or changed targets fail closed) kept as-is.
- `CHANGELOG.md` / `CHANGELOG.zh-CN.md` 1.4.4-rc.1 entries: same overstatement
  ("verify foreground identity ... on both macOS and Windows" / "核验前台身份")
  corrected to distinguish Windows (handle re-check, no process identity
  re-verification) from macOS (frontmost application/window identity check).

## Files touched

`SECURITY.md`, `docs/release-notes-1.4.4rc1.md`, `CHANGELOG.md`,
`CHANGELOG.zh-CN.md`. No code touched.

## Verification

- `uv run pytest -q`: 1249 passed, 7 skipped.
- `uv run ruff check src tests`: All checks passed.
- `uv run ruff format --check src tests`: 139 files already formatted.
- `uv run mypy src/aacc`: Success, no issues in 61 source files.

Commit: `docs: add least-privilege guidance and correct windows identity wording`
