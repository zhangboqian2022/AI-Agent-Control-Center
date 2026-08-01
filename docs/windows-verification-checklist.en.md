# Windows 10/11 Manual Verification Checklist — AACC 1.4.4-rc.1

Record only what is actually observed. Unchecked items are not compatibility
claims. Hosted GitHub Actions on Windows Server 2022/2025 verifies builds and
automated product smoke, but it does **not** replace this consumer Windows
10/11 checklist or the separate-account denial test.
Completing this checklist provides only the Windows half of the required
macOS and Windows manual sign-off for Kimi session persistence and logout.

Candidate: `AACC-1.4.4rc1-Setup.exe`

Verifier:

Date and time:

Machine model:

Windows edition (`Windows 10` or `Windows 11`), version, and build:

Account type (must be non-administrator for the primary pass):

Commit and candidate SHA-256:

- [ ] **Checksum and SmartScreen**: the Setup SHA-256 matches
  `AACC-1.4.4rc1-Setup.exe.sha256`; launching the unsigned candidate shows the
  expected Unknown publisher/SmartScreen path, and **More info → Run anyway**
  opens Setup.
- [ ] **Per-user install**: Setup does not request administrator elevation and
  installs under `%LocalAppData%\Programs\AACC`.
- [ ] **Microsoft Edge availability**: Microsoft Edge is installed. Setup does
  not download a separate browser runtime and AACC starts normally.
- [ ] **Dedicated Kimi login**: click **Sign in to Kimi with dedicated Edge**.
  A visible Edge window opens at Kimi, login succeeds, all three quota windows
  arrive, and AACC closes only the Edge process it started.
- [ ] **Profile isolation and background reuse**: confirm the AACC-owned Edge
  profile exists at `%LOCALAPPDATA%\AACC\kimi-edge-profile`, never under the
  normal Edge profile. Restart AACC and Windows; quota refresh still works
  without login, and a five-minute background refresh leaves no managed Edge
  window open.
- [ ] **Shortcuts and startup**: the Start Menu shortcut exists; the desktop
  shortcut follows the selected option; no startup/login item was added.
- [ ] **First launch and tray**: the installed AACC panel opens, remains
  responsive, stays in the tray, and restores after hide/minimize. Left-click
  toggles the panel, right-click leaves the menu open, and both its **Quit
  AACC** action and the header power button exit the complete process.
- [ ] **Live language switching**: repeatedly switch language with real tasks
  and quota data before and after Kimi login. The complete visible UI updates
  each time; the chosen language persists after restart; quota values, task
  selection/state, Kimi login state, and compact mode do not change.
- [ ] **Discovery and focus**: real running Kimi/Codex sessions are discovered
  with status lights, and **Switch to task** focuses the intended terminal
  window by title.
- [ ] **Input controls**: allowlisted keys and text reach only the focused
  target; Win+H voice input and configured F13–F20 global hotkeys work.
- [ ] **Quota rows**: without starting a Codex task, refresh and verify one real
  Codex `WEEK` row. Start AACC before ChatGPT/Codex, then confirm the desktop
  app opened or restarted after AACC recovers synchronization within the
  automatic every 60 seconds cycle; clicking the Codex strip refreshes
  immediately. Kimi shows `5H`, `WEEK`, `MONTH` in that order after real
  membership login; each available row shows a complete local reset date/time,
  a known percentage without a trustworthy reset shows `--` for the reset,
  and unavailable percentages are `--`, never `0%`.
- [ ] **OpenCode parity**: configure an OpenCode workspace URL, sign in through
  the managed Edge profile, and confirm rolling/weekly/monthly quota rows. Verify
  `%LOCALAPPDATA%\AACC\opencode-edge-profile` is separate from Kimi's profile,
  forced-closing an OpenCode terminal changes its task light from blue to green,
  and a discovered session shows its work-directory name and focuses the matching
  Windows Terminal window.
- [ ] **Settings and dedicated Edge session**: always-on-top and API credential
  reset persist. Confirm the AACC-owned Edge profile retains the first-party
  Kimi session across AACC and Windows restarts. Inspect
  `%APPDATA%\AACC\kimi-web-session-state.json` and confirm AACC stores only a
  protected reuse decision for native website-session reuse, not a cookie,
  password, website bearer token, account name, or quota value. Kimi Code OAuth
  credentials remain separately stored under AACC's credential protection.
- [ ] **Shared refresh and logout**: observe that the web source and Kimi Code
  fallback start from the same five-minute cycle and that metadata lookups use
  no generation tokens. Explicit logout must synchronously disable reuse,
  remove only the dedicated Edge profile, and remain logged out after an
  immediate AACC restart.
- [ ] **Native DACL**: after AACC creates its files, inspect `config.yaml`,
  `aacc.db`, `aacc.db-wal`, `aacc.db-shm` when present, and
  `kimi-credentials.json`, plus the
  `%LOCALAPPDATA%\AACC\kimi-edge-profile` directory. Each target has inheritance
  disabled and exactly one full-control allow entry for the current user,
  Local System, and built-in Administrators, with no other allow, deny, or
  inherited ACE.
- [ ] **Separate-user denial**: sign in as a separate unprivileged local
  account and confirm the operating system denies reading every sensitive file
  and the AACC-owned Edge profile above.
- [ ] **Upgrade and graceful shutdown**: while AACC is running, rerun the same
  Setup. It closes AACC gracefully, upgrades in place, restarts normally, and
  preserves AACC-owned settings, history, database, credentials, and reuse
  decision under `%APPDATA%\AACC`. Confirm the dedicated Edge session also
  remains usable after the in-place upgrade.
- [ ] **Uninstall and preserved AppData**: uninstall removes the program,
  shortcuts, and uninstall entry but preserves `%APPDATA%\AACC`. Reinstall
  starts normally with the preserved AACC data. Record whether the separate
  AACC-owned Edge profile remains after uninstall; use explicit AACC logout
  before uninstall when the website session must be removed.
- [ ] **Long-running stability**: leave AACC running for at least 30 minutes
  while tasks and quotas refresh; no crash, raw traceback, orphaned Codex
  broker tree, or sustained UI hang appears.

Evidence / notes:

```text
Record commands, observed results, screenshots/log locations, and deviations.
Attach this completed evidence to the release PR or release notes.
```
