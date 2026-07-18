# Troubleshooting AACC

[中文版本](troubleshooting.md) · [Back to README](../README.md)

## A card does not focus its target

Run `aacc doctor` to confirm configuration and API availability. Check `terminal.type`, `app_bundle_id`, and `window_title`. The first attempt to control Terminal or iTerm2 may require approval under **System Settings → Privacy & Security → Automation**. If the title does not match, AACC stops rather than sending input blindly.

## Shortcuts or dictation do not work

Grant AACC Accessibility access under **System Settings → Privacy & Security → Accessibility**, then quit and relaunch it. Confirm F13–F20 are not claimed by keyboard software. Dictation uses macOS double-Fn dictation; enable it first in **Keyboard → Dictation**.

## The CLI cannot connect

Confirm AACC is running, port 17650 is available, and the CLI reads the same configuration file. Run `aacc doctor`. Restart AACC after changing its port or token. The API intentionally does not accept non-loopback connections.

## A state does not change automatically

An agent without structured hooks may not expose reliable internal phases. Use `aacc-run --task task-1 -- codex` for process lifecycle or `aacc status` for manual updates. Add conservative Generic CLI patterns when suitable. `UNKNOWN` or `WARNING` is expected when the available evidence is insufficient.

## macOS blocks the local app

Locally built packages use ad-hoc signing and are not Apple-notarized. Use **System Settings → Privacy & Security → Open Anyway** only for an app built by you or downloaded from this project’s official release page.

## Log and data locations

Logs, configuration, and the database live under `~/Library/Application Support/AACC/`. Review diagnostics before sharing them: AACC redacts common tokens, passwords, Bearer values, and `sk-` keys, but manual review remains necessary.
