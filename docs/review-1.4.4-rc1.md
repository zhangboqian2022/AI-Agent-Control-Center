# AACC 1.4.4-rc1 Review Decision Record

This record separates verified defects from scope disagreements in the four
review reports. Evidence was checked against the current `main` merge and the
RC worktree before implementation.

| Finding | Decision | Evidence / treatment |
| --- | --- | --- |
| OpenCode stays blue after finish or forced stop | Accept | Part history and process liveness now require explicit completion evidence; cancellation/failure are distinct and process disappearance becomes STOPPED. |
| Kimi cancellation becomes completed | Accept | `turn.cancel` maps to CANCELLED. |
| Terminal state can be overwritten by stale active evidence | Accept | Timestamp and run-boundary checks reject stale terminal restarts. |
| Discovery repeatedly initializes SQLite | Accept | Runtime registration performs `INSERT OR IGNORE` only for a new task. |
| Adapter registry exists but is not runtime-wired | Partial/accept boundary | Runtime wiring now provides process-only evidence for configured non-native tasks; agent-specific completion remains out of scope and is documented. |
| Wrong-window keyboard injection | Accept | Unique target matching and a final foreground identity check fail closed on macOS/Windows. |
| Config parent-directory TOCTOU | Partial | POSIX save path uses an anchored directory descriptor and relative replace. Windows retains protected DACL boundaries; full cross-platform handle-based rewrite is deferred. |
| Absolute Kimi Daimon path in INFO log | Accept | INFO contains only candidate count. |
| `uv.lock` absent from current repository | Reject | Current Git history and CI require and contain tracked `uv.lock`; the report used an old review copy. |
| Hosted Windows 10/11 equals real-device verification | Reject | CI labels and checklists explicitly state the evidence limitation. |

Historical v1.2 findings were not reintroduced as RC blockers where current
tests and later release notes already cover them. The remaining manual evidence
is intentionally listed rather than implied by automation.
