# Windows Smoke Verification Checklist — AACC

Record only what is actually observed; unchecked items are not compatibility claims.

Environment: Windows 10+, Python 3.12+, uv.

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
