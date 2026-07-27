# AACC 1.4.2 Windows Stable Setup Handoff

Recorded on 2026-07-27 at the user's pause request.

## Repository state

- Branch: `codex/v1.4.2-quota-windows-hardening`
- Current implementation head: `7fd1e78`
- Remote branch: pushed to `origin/codex/v1.4.2-quota-windows-hardening`
- `main`: not merged
- Tag/release: not created
- Implementation plan:
  `docs/superpowers/plans/2026-07-27-windows-stable-setup.md`
- Design:
  `docs/superpowers/specs/2026-07-27-windows-stable-setup-design.md`

## Completed work

1. Design and implementation plan
   - `7f4b0d9 docs: design stable Windows setup`
   - `0e4a2bd docs: plan stable Windows setup`
   - `a1964c2 docs: correct Windows ACL implementation plan`
2. Task 1 — native exact Windows ACLs
   - `4026c11 fix: protect Windows credentials with native ACLs`
   - Removed runtime `whoami.exe` and `icacls.exe`.
   - Added pywin32 exact protected DACL replacement and read-back verification.
   - Task review: approved with no Critical, Important, or Minor findings.
   - Verification: 563 passed, 4 skipped; ruff and mypy passed.
3. Task 2 — friendly fail-closed startup errors
   - `c3e9350 fix: show safe Windows startup errors`
   - Known credential-protection failure now shows a sanitized bilingual dialog
     and exits nonzero instead of exposing a raw frozen traceback.
   - Task review: approved. One non-blocking test-strengthening item remains.
4. Task 3 implementation — static fixed-purpose Codex broker
   - `7fd1e78 feat: isolate Windows Codex processes`
   - Added the `/MT` C++ broker, exact `KERNEL32.dll` dependency allowlist,
     sanitized environment, fixed Codex argv, suspended Job assignment, narrow
     handle inheritance, and Windows integration fixtures.
   - Implementer security audit: no remaining Critical, Important, or Minor
     findings.
   - Verification on macOS: focused 33 passed/1 Windows skip; full 571 passed/5
     skips; ruff, format, mypy, and diff checks passed.

## Pending evidence and work

- The controller's formal Task 3 review was interrupted when the user requested
  a pause. Re-dispatch it before marking Task 3 complete:
  - brief: `.superpowers/sdd/task-3-brief.md`
  - report: `.superpowers/sdd/task-3-report.md`
  - diff: `.superpowers/sdd/review-c3e9350..7fd1e78.diff`
- Inspect the GitHub Actions run triggered by the branch push. The Windows leg
  must actually compile with MSVC, run `dumpbin`, and execute the Job Object
  integration. A macOS skip is not evidence of Windows success.
- Continue Tasks 4–9 from the implementation plan:
  1. route packaged Codex app-server calls through the broker;
  2. add graceful Setup shutdown;
  3. build the per-user Inno Setup;
  4. add frozen and installed Windows product smoke tests;
  5. regenerate `docs/images/panel-overview.png` from the real Qt UI with
     synthetic Codex/Kimi quota data and update bilingual documentation;
  6. run final verification and whole-branch review;
  7. merge `main`, synchronize GitHub, and build desktop DMG/Setup artifacts
     only after every automated gate is green.

## Release guard

Do not merge `main`, tag, publish a Release, or claim Windows runtime
validation until the hosted Windows gate passes. Formal release also remains
blocked on the documented Windows 10/11 standard-user, separate-account ACL,
real Kimi login, real Codex quota, tray/hotkey, SmartScreen/Defender, and
extended-operation checks.
