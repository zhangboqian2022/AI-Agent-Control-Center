# Windows 10/11 Manual Verification Checklist — AACC 1.4.2

Record only what is actually observed. Unchecked items are not compatibility
claims. Hosted GitHub Actions on Windows Server 2022/2025 verifies builds and
automated product smoke, but it does **not** replace this consumer Windows
10/11 checklist or the separate-account denial test.
Completing this checklist provides only the Windows half of the required
macOS and Windows manual sign-off for native WebView persistence and logout.

Candidate: `AACC-1.4.2-Setup.exe`

Verifier:

Date and time:

Machine model:

Windows edition (`Windows 10` or `Windows 11`), version, and build:

Account type (must be non-administrator for the primary pass):

Commit and candidate SHA-256:

- [ ] **Checksum and SmartScreen**: the Setup SHA-256 matches
  `AACC-1.4.2-Setup.exe.sha256`; launching the unsigned candidate shows the
  expected Unknown publisher/SmartScreen path, and **More info → Run anyway**
  opens Setup.
- [ ] **Per-user install**: Setup does not request administrator elevation and
  installs under `%LocalAppData%\Programs\AACC`.
- [ ] **WebView2 provisioning — Runtime absent**: on a real Windows 10 or 11
  standard-user machine without the WebView2 Runtime, run Setup while network
  is available. It installs Microsoft's Evergreen WebView2 Runtime for the
  current user before AACC, and the first Kimi login creates a usable native
  view rather than a blank dialog.
- [ ] **WebView2 provisioning — Runtime already present**: on a separate real
  Windows 10 or 11 standard-user machine with an already-installed WebView2
  Runtime, run Setup with network monitoring or disconnected after download.
  Setup recognizes the Runtime and does not require a network installation;
  the first Kimi login still creates a usable native view. Confirm the writable
  WebView2 user data folder is created at
  `%LOCALAPPDATA%\AACC\kimi-web-session`, not beside `AACC.exe`.
- [ ] **WebView2 diagnostic**: if a native Kimi login view cannot produce a
  loading event, it replaces the blank surface with the fixed 15-second
  WebView2/network repair diagnostic and Microsoft repair-page action. Record
  the observed category without account data or page URLs.
- [ ] **Shortcuts and startup**: the Start Menu shortcut exists; the desktop
  shortcut follows the selected option; no startup/login item was added.
- [ ] **First launch and tray**: the installed AACC panel opens, remains
  responsive, stays in the tray, and restores after hide/minimize.
- [ ] **Live language switching**: repeatedly switch language with real tasks,
  quota data, and an open Kimi login dialog. The complete visible UI updates
  each time; the chosen language persists after restart; quota values, task
  selection/state, Kimi login state, and compact mode do not change.
- [ ] **Discovery and focus**: real running Kimi/Codex sessions are discovered
  with status lights, and **Switch to task** focuses the intended terminal
  window by title.
- [ ] **Input controls**: allowlisted keys and text reach only the focused
  target; Win+H voice input and configured F13–F20 global hotkeys work.
- [ ] **Quota rows**: without starting a Codex task, refresh and verify one real
  Codex `WEEK` row. Kimi shows `5H`, `WEEK`, `MONTH` in that order after real
  membership login; each available row shows a complete local reset date/time,
  a known percentage without a trustworthy reset shows `--` for the reset,
  and unavailable percentages are `--`, never `0%`.
- [ ] **Settings and native session**: always-on-top and API credential reset
  persist. Confirm that the operating system's native per-application WebView
  store retains the first-party Kimi session across an AACC restart. Inspect
  `%APPDATA%\AACC\kimi-web-session-state.json` and confirm AACC stores only a
  protected reuse decision for native website-session reuse, not a cookie,
  password, website bearer token, account name, or quota value. Kimi Code OAuth
  credentials remain separately stored under AACC's credential protection.
- [ ] **Shared refresh and logout**: observe that the web source and Kimi Code
  fallback start from the same five-minute cycle and that metadata lookups use
  no generation tokens. Explicit logout must synchronously disable reuse,
  attempt bounded native site-data cleanup, and remain logged out after an
  immediate AACC restart.
- [ ] **Native DACL**: after AACC creates its files, inspect `config.yaml`,
  `aacc.db`, `aacc.db-wal`, `aacc.db-shm` when present, and
  `kimi-credentials.json`. Each file has inheritance disabled and exactly one
  full-control allow entry for the current user, Local System, and built-in
  Administrators, with no other allow, deny, or inherited ACE.
- [ ] **Separate-user denial**: sign in as a separate unprivileged local
  account and confirm the operating system denies reading every sensitive file
  above.
- [ ] **Upgrade and graceful shutdown**: while AACC is running, rerun the same
  Setup. It closes AACC gracefully, upgrades in place, restarts normally, and
  preserves AACC-owned settings, history, database, credentials, and reuse
  decision under `%APPDATA%\AACC`. Record native WebView persistence as a
  separate observed platform result, not as an AppData guarantee.
- [ ] **Uninstall and preserved AppData**: uninstall removes the program,
  shortcuts, and uninstall entry but preserves `%APPDATA%\AACC`. Reinstall
  starts normally with the preserved AACC data. The operating system owns the
  native WebView store separately, so this check does not claim that uninstall
  preserves or removes the website session.
- [ ] **Long-running stability**: leave AACC running for at least 30 minutes
  while tasks and quotas refresh; no crash, raw traceback, orphaned Codex
  broker tree, or sustained UI hang appears.

Evidence / notes:

```text
Record commands, observed results, screenshots/log locations, and deviations.
Attach this completed evidence to the release PR or release notes.
```
