# AACC Finalization Cleanup Design

**Date:** 2026-08-01

**Goal:** 完成 OpenCode/Codex 任务状态与工作目录展示的善后修复，统一 OpenCode 额度文案，并让双语 GitHub 界面截图反映当前产品能力。

## Context and confirmed root causes

1. OpenCode discovery currently evaluates only the newest `part` row. A final `text` or `reasoning` row can be newer than the preceding `step-finish`, so the evaluator reports `RUNNING` and the task card remains blue after the turn has finished.
2. Codex rollout JSONL already carries privacy-safe working-directory metadata in `payload.cwd` (and, in some records, `payload.directory`), but `CodexLocalDiscovery` drops it. `TaskCard` also excludes `codex_cli` from its work-directory presentation condition.
3. The OpenCode quota bar and related catalogs/docs still call the feature “usage/用量”, while the rest of the product consistently calls account limits “quota/额度”.
4. `scripts/capture_panel_screenshot.py` injects only Codex and Kimi quota services, so the tracked bilingual screenshots cannot show the newly supported OpenCode quota bar.
5. OpenCode usage rows can be structurally present with a valid reset time but no valid percentage; the row renders `--`, while its tooltip interpolates `None%`.

## Chosen approach

### OpenCode completion state: bounded part-history inference

Keep the existing read-only SQLite boundary and never inspect or retain prompt, response, command, or reasoning text. For each selected session, inspect a small bounded set of recent part records and normalize only:

- part type;
- tool state status;
- part update timestamp.

Track the newest unfinished step start and the newest terminal step finish/tool completion. Completion wins when the terminal marker belongs to the current step, including when a final text/reasoning part was written after `step-finish`. A newer running/pending tool or newer step start still wins and remains blue/yellow. If no terminal marker is available, retain the existing activity-window fallback.

This is preferable to querying OpenCode's event payloads because it preserves the current privacy contract and does not depend on undocumented event body schemas. It is also safer than changing only the color mapping, because the incorrect status originates in discovery.

### Codex working directory: metadata-only extraction

Read the first bounded metadata records of each Codex rollout file and accept only string values from `payload.cwd` or `payload.directory`. Store the value as `state.metadata["work_dir"]`; do not copy prompt or response content. Add a GUI condition for `codex_cli` so Codex uses the same basename-plus-full-path-tooltip presentation already used by Kimi Code and OpenCode. Missing or malformed values continue to hide the label.

### Naming and screenshots

Keep stable translation keys such as `opencode.quota` and update their values to `OpenCode 额度` / `OpenCode quota`. Update OpenCode-specific errors, tooltips, README/release-note wording, and tests. Extend the synthetic screenshot harness with a fake OpenCode quota service and a synthetic OpenCode quota payload; give the Codex demo card a synthetic working directory; regenerate both tracked PNGs and inspect them visually. The screenshot remains synthetic and privacy-safe.

### Out of scope

- No release/version bump, GitHub push, or release asset replacement is performed in this cleanup unless separately requested.
- No changes to OpenCode network extraction, cookies, authentication, Windows WebView support, or unrelated agent adapters.
- Existing unrelated uncommitted changes in `AGENTS.md` and `.superpowers/sdd/*.md` are preserved.

## Error handling

- Malformed or missing OpenCode part metadata falls back to the existing conservative status decision tree.
- Malformed/missing Codex directory metadata is ignored; the task remains visible without a directory label.
- Unknown OpenCode quota percentages render as `--` in both the row and tooltip.
- Existing stale/error/authentication states remain unchanged except for the terminology change from usage to quota.

## Testing and acceptance criteria

- A regression test proves an OpenCode final text/reasoning part after a completed step is green, while a new step after the prior completion remains blue.
- Codex discovery tests prove `payload.cwd`/`payload.directory` becomes `metadata["work_dir"]` without retaining unrelated content; GUI tests prove basename and full-path tooltip display.
- OpenCode quota-bar tests prove missing percentage never renders `None%`, and both language catalogs/tests use quota terminology.
- Screenshot contract tests require the synthetic OpenCode quota fixture and updated dimensions/assets; both generated PNGs remain valid, fixed-size, and privacy-safe.
- Final verification runs the project's full pytest suite, ruff check, ruff format check, mypy, and `git diff --check`.
