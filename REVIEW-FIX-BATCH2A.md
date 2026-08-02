# Review Fix Batch 2a — Process Lifecycle & State Arbitration

Branch: `fix/review-1.4.4` (base main @ 1fb0edd, batch-1 commit 408bed7 applied)
TDD per fix: failing test first (RED), then implementation (GREEN).

## Fix 1 (P1-C / P1-2): process-group cleanup

### (a) `src/aacc/run_wrapper.py`
- `Popen(command, shell=False)` now adds `start_new_session=True` when `os.name == "posix"`
  (Windows keeps direct-child behavior; `start_new_session` is never passed on Windows).
- New `_signal_process_group(process, sig)`: on POSIX signals the process group via
  `os.killpg(os.getpgid(process.pid), sig)` with fallback to direct-child
  terminate/kill when `getpgid`/`killpg` fails (AttributeError/OSError/ValueError).
- `terminate_process` rewritten: group SIGTERM → wait 3s → group SIGKILL → wait.

RED:
```
$ uv run pytest tests/test_run_wrapper.py -q
FAILED test_terminate_process_waits_for_cooperative_exit
FAILED test_terminate_process_kills_and_reaps_after_timeout
FAILED test_terminate_process_reaps_the_spawned_process_group
  (grandchild survived: old code killed only the direct child)
```
GREEN:
```
$ uv run pytest tests/test_run_wrapper.py -q
8 passed in 3.50s
```
Tests changed/added in `tests/test_run_wrapper.py`:
- `test_terminate_process_*` unit tests now monkeypatch `_signal_process_group` and assert the
  SIGTERM → (wait) → SIGKILL sequence.
- `test_signal_process_group_falls_back_to_direct_child` — killpg OSError falls back to child.
- `test_terminate_process_reaps_the_spawned_process_group` (POSIX-only, skip on win32) — spawns
  a Python parent that spawns a SIGTERM-ignoring grandchild; after `terminate_process` BOTH
  processes are gone (polled with a 10s deadline). Old code failed this because the grandchild
  survived.

### (b) `src/aacc/codex_app_server.py`
- `popen_options` gains `start_new_session=True` when `os.name == "posix"` (Windows broker path
  untouched — guard is runtime-OS based so the darwin-path tests also run safely on Windows CI).
- New module-level `_signal_process_group` helper (same fallback semantics as run_wrapper).
- `_reap()` now signals the process group on POSIX (`SIGTERM`, then `SIGKILL` after the 0.5s
  wait timeout) while preserving the Windows broker direct-child path; existing FakeProcess-based
  reap tests keep passing because the fallback path is exercised when `pid`/group is unavailable.

RED:
```
$ uv run pytest tests/test_codex_app_server.py -q
FAILED test_reader_reap_kills_the_spawned_process_group
FAILED test_posix_reader_spawns_app_server_in_its_own_session
  (KeyError 'start_new_session'; grandchild survived)
```
GREEN:
```
$ uv run pytest tests/test_codex_app_server.py -q
44 passed in 1.76s
```
Tests added in `tests/test_codex_app_server.py`:
- `test_reader_reap_kills_the_spawned_process_group` (POSIX-only) — spawner + SIGTERM-ignoring
  grandchild; `_reap()` must terminate→wait→kill the whole group; both gone within deadline.
- `test_posix_reader_spawns_app_server_in_its_own_session` — asserts `start_new_session=True` is
  passed to Popen on POSIX.

### (c) `KNOWN_LIMITATIONS.md` / `KNOWN_LIMITATIONS.zh-CN.md`
Line 8 reworded to "cleans up the spawned process tree (process group) after
SIGINT/SIGTERM" / "清理派生的进程树（进程组）". Bullet counts unchanged (21 = 21).

Verify:
```
$ uv run pytest tests/test_packaging.py -q -k "known_limitations or documentation"
4 passed, 43 deselected
```

## Fix 2 (P2-1): bound the same-source lower-confidence override

`src/aacc/state_machine.py` — the `same_source_newer_state` carve-out was removed. The confidence
guard now allows a same-source fresh candidate only when
`candidate.confidence >= current.confidence - 0.2`; stale states (age > STALE_SECONDS) remain
freely replaceable.

RED:
```
$ uv run pytest tests/test_state_machine.py -q
FAILED test_fresh_same_source_very_low_confidence_candidate_is_rejected
1 failed, 19 passed
```
GREEN:
```
$ uv run pytest tests/test_state_machine.py -q
20 passed in 0.17s
```
Tests added in `tests/test_state_machine.py`:
- (a) `test_fresh_same_source_very_low_confidence_candidate_is_rejected` — 0.10 vs 0.95 → rejected.
- (b) `test_fresh_same_source_close_confidence_candidate_is_accepted` — 0.90 vs 0.95 → accepted.
- (c) `test_stale_same_source_low_confidence_candidate_is_accepted` — stale 0.10 vs 0.95 → accepted
  (existing stale-replacement behavior preserved; `test_stale_state_can_be_replaced_by_lower_confidence_warning`
  still passes).

## Fix 3 (P1-1): adapter polling — shared snapshot + per-adapter isolation

`src/aacc/adapter_discovery_service.py` + `src/aacc/adapters.py`:
- (a) Each `adapter.get_status()` call in `poll_once` is wrapped in its own try/except; failures
  log `Adapter poll failed for task <id> (<display_name>)` with `exc_info` and the round continues.
- (b) `psutil.process_iter(["name", "cmdline"])` is captured ONCE per round (list) and passed to
  every adapter; on snapshot failure (`psutil.Error`/`OSError`) adapters fall back to on-demand
  snapshotting. `BaseAgentAdapter.detect(processes=None)` and `get_status(processes=None)` accept
  a pre-fetched process list, defaulting to snapshot-on-demand for backward compat.
  `asyncio.run` structure kept.

RED:
```
$ uv run pytest tests/test_adapter_discovery_service.py -q
FAILED test_adapter_exception_does_not_block_other_adapters
FAILED test_process_snapshot_is_captured_once_per_round
5 passed, 2 failed
```
GREEN:
```
$ uv run pytest tests/test_adapter_discovery_service.py tests/test_adapters.py -q
18 passed in 0.79s
```
Tests changed/added in `tests/test_adapter_discovery_service.py` (FakeAdapter signature extended
with `processes` parameter):
- (a) `test_adapter_exception_does_not_block_other_adapters` — raising adapter does not stop the
  other adapter from updating that round.
- (b) `test_process_snapshot_is_captured_once_per_round` — patched `psutil.process_iter` called
  exactly once with 2 adapters.

## Full gate

```
$ uv run pytest -q
1242 passed, 7 skipped, 1 warning
$ uv run ruff check src tests
All checks passed!
$ uv run ruff format --check src tests
139 files already formatted
$ uv run mypy src/aacc
Success: no issues found in 61 source files
```

Note: one full-suite run hit a `Fatal Python error: Bus error` inside pytestqt
`_process_events` (Qt event processing in a GUI test); it did not reproduce in the following
three full runs and no GUI code was touched by this batch — pre-existing flake.

## Files changed

- `src/aacc/run_wrapper.py`
- `src/aacc/codex_app_server.py`
- `src/aacc/state_machine.py`
- `src/aacc/adapters.py`
- `src/aacc/adapter_discovery_service.py`
- `KNOWN_LIMITATIONS.md`
- `KNOWN_LIMITATIONS.zh-CN.md`
- `tests/test_run_wrapper.py`
- `tests/test_codex_app_server.py`
- `tests/test_state_machine.py`
- `tests/test_adapter_discovery_service.py`
