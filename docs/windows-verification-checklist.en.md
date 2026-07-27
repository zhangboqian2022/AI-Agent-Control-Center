# Windows Smoke Verification Checklist — AACC

Record only what is actually observed; unchecked items are not compatibility claims.

Environment: Windows 10+, Python 3.12+, uv.

Verifier:

Date and time:

Machine model:

Windows edition, version, and build:

Commit:

- [ ] **Build**: `scripts\build_windows.ps1` completes and produces `dist\AACC\AACC.exe`
- [ ] **Launch**: double-click `AACC.exe`, choose **More info → Run anyway** at the SmartScreen prompt; the panel appears and stays in the tray
- [ ] **Discovery**: running kimi / codex sessions are discovered automatically with status lights
- [ ] **Card focus**: the "Switch to task" context action focuses the target terminal window (window-title matching)
- [ ] **Key injection**: allowlisted keys (Enter / Esc / arrows / Ctrl+C / 1 / 2) reach the target window
- [ ] **Text injection**: injected text arrives correctly in the target window
- [ ] **Voice**: Win+H voice input works in the target window
- [ ] **Hotkeys**: global hotkeys summon the panel (on keyboards where F13–F20 need an Fn-layer mapping, confirm after mapping)
- [ ] **Tray**: the panel restores from the tray after minimize/hide
- [ ] **Quota bar**: the Kimi quota bar renders correctly (after completing one device authorization)
- [ ] **Settings page**: settings (always-on-top, API credential reset, etc.) work and persist
- [ ] **Codex live quota without a task**: with no active or recently started Codex task, click the Codex strip and verify that exactly one `WEEK` row shows a real percentage
- [ ] **Visible absolute resets and Kimi order**: Codex shows its local reset date/time in the row; Kimi shows `5H`, `WEEK`, `MONTH` in that order, every available reset is visible in its row, and an unavailable month is `--`, never `0%`
- [ ] **Sensitive-file ACLs**: after saving settings and completing Kimi authorization, run `icacls` on both `config.yaml` and `kimi-credentials.json`; inheritance is removed and explicit full-control grants are limited to the current user SID, Local System, and local Administrators
- [ ] **Separate-user denial**: sign in as another unprivileged local account and confirm the operating system denies reading both `config.yaml` and `kimi-credentials.json`

Evidence / notes:

```text
Record commands, observed results, screenshots/log locations, and any deviations here.
```
