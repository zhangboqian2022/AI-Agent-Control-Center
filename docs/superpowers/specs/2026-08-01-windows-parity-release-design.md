# AACC 1.4.3 Windows Parity and Formal Release Design

**Date:** 2026-08-01
**Status:** Approved under the maintainer's standing automatic-approval instruction.
**Target:** AACC `1.4.3` formal release for macOS and Windows.

## Goal

Ship a formal `1.4.3` release whose Windows package exposes the same supported
product surfaces as the current macOS build: Codex/Kimi/OpenCode discovery,
green completion after normal or forced OpenCode termination, work-directory
display, unified quota terminology, and Codex/Kimi/OpenCode quota panels where
the platform has a supported browser session.

## Findings that block a release today

- The Windows build and installer pipeline already exists and is exercised on
  hosted Windows Server runners.
- OpenCode CLI discovery is platform-neutral, but
  `_default_opencode_web_quota_service_factory()` currently returns `None` on
  Windows. The Windows binary therefore cannot show OpenCode web quota.
- The Windows PyInstaller spec intentionally excludes `PySide6.QtWebView`, so
  copying the macOS WebView session into the Windows package would be an
  invalid fix. Windows needs the existing managed Edge/CDP pattern used by
  Kimi, with a separate profile and an OpenCode-specific payload boundary.
- GitHub-hosted Windows Server 2022/2025 runners are not consumer Windows 10
  or Windows 11 machines. A CI matrix may run the same compatibility contract
  under labels for Windows 10 and 11, but release notes must not call that
  real-machine verification.

## Chosen architecture

### 1. Windows OpenCode quota session

Add `opencode_edge_cdp.py` and `opencode_edge_session.py` beside the existing
Kimi Edge modules.

- The session owns only
  `%LOCALAPPDATA%/AACC/opencode-edge-profile`; it never opens or deletes a
  user's normal Edge profile.
- Visible login starts Edge with the configured, validated
  `https://opencode.ai/workspace/...` URL. A successful DOM extraction grants
  reuse permission in a site-specific state file.
- Headless refresh is allowed only after that permission is present.
- CDP accepts only a loopback DevTools endpoint, a page on `opencode.ai`, and
  normalized rolling/weekly/monthly numeric values. No page text, cookie,
  token, prompt, response, or reasoning content crosses into Python logs or
  persistent AACC state.
- Logout revokes reuse before clearing only the AACC-owned OpenCode profile.
- The public session protocol remains the same as the macOS session, so
  `OpenCodeWebQuotaService` and the GUI do not need platform-specific branches.

### 2. Existing task/state behavior

Keep the current cross-platform OpenCode discovery implementation as the source
of truth. It already uses SQLite part snapshots, per-session process working
directory checks, bounded history, and immediate completion when a valid final
snapshot remains after the process exits. Add only contract tests proving the
Windows factory and packaged module inclusion; do not duplicate the state
machine.

### 3. CI and evidence

- Keep the full quality matrix on `macos-latest`, `windows-2022`, and
  `windows-2025-vs2026`.
- Keep Windows frozen/package smoke checks and Setup artifact upload on the
  Windows Server jobs.
- Add a small `windows-consumer-compatibility-contract` matrix with labels
  `windows-10` and `windows-11`, executed on the available hosted Windows
  runner. It validates the same native/platform contracts and explicitly emits
  that the runner is Windows Server compatibility evidence, not consumer
  Windows 10/11 hardware evidence.
- Formal release assets are the locally built and verified
  `AACC-1.4.3.dmg` plus the CI-built, checksum-verified
  `AACC-1.4.3-Setup.exe`. The release verifier must require the four assets and
  a non-draft, non-prerelease `v1.4.3` release.

### 4. Version and documentation

Advance the package version in the required order:

1. `src/aacc/__init__.py` and `pyproject.toml` to `1.4.3`.
2. Regenerate `uv.lock`.
3. Add the latest bilingual `1.4.3` changelog sections and
   `docs/release-notes-1.4.3.md`.
4. Update download links, OpenCode terminology, and platform limitations to
   say macOS and Windows where supported.

Historical rc notes remain unchanged. The formal notes distinguish hosted
Windows Server CI, compatibility-contract labels, and any unavailable real
consumer Windows verification.

## Error and lifecycle behavior

- Missing Edge, unsafe profile, invalid CDP endpoint, timeout, page change, or
  malformed payload becomes an allowlisted OpenCode quota error category.
- Unauthorized refresh clears the site reuse decision and returns the GUI to
  the authorization state; transient failures preserve the last known quota.
- App shutdown cancels the worker, closes CDP, terminates only the Edge process
  tree AACC started, and never blocks the Qt event loop indefinitely.

## Test plan

- TDD tests for the OpenCode Edge profile path, launch arguments, target
  allowlist, payload normalization, authorization/reuse state, cancellation,
  logout cleanup, and service factory selection on `win32`.
- Packaging tests for Windows hidden imports and the absence of QtWebView from
  the Windows spec.
- Existing regression tests for completion lights, forced termination, work
  directories, and quota wording remain mandatory.
- Run the full local suite, ruff check/format, mypy, and the macOS DMG build.
- Push the release branch, wait for every required GitHub Actions job, download
  and checksum the Windows Setup artifact, then create `v1.4.3` only from that
  verified commit.

## Out of scope

- Claiming or fabricating real consumer Windows 10/11 hardware/VM results.
- Reading browser credentials or agent conversation content.
- Replacing the existing task state machine with platform-specific code.
- Deleting or rewriting prior prerelease tags/assets.
