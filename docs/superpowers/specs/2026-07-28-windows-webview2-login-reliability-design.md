# Windows WebView2 Login Reliability Design

**Status:** Approved under the maintainer's standing authorization for v1.4.2
release-blocking fixes.

## Problem

The Windows 1.4.2 candidate includes Qt 6.11's native
`qtwebview_webview2.dll`, but Setup neither detects nor deploys the Microsoft
Edge WebView2 Runtime. On a machine with a missing or damaged Runtime, Qt's
native plug-in may never emit a useful `loadingChanged` failure, leaving the
Kimi login dialog permanently white. Existing hosted smoke tests assert that
the plug-in file exists but never create a real WebView2 controller.

Microsoft's WebView2 distribution contract requires desktop applications to
ensure that the Evergreen or Fixed Version Runtime is available. AACC's
per-user, non-elevated Setup currently violates that deployment precondition.

## Decision

AACC will keep Qt's native WebView2 backend and add two complementary controls:

1. The Windows Setup build downloads one immutable Microsoft Evergreen
   bootstrapper URL, verifies a pinned SHA-256 and valid Authenticode
   signature, and embeds the bootstrapper as a temporary-only Setup payload.
2. Before AACC files are mutated, Setup checks Microsoft's documented
   per-machine and per-user `pv` registry values. If no usable Runtime is
   present, Setup runs the bootstrapper as the current user with
   `/silent /install`, then repeats the registry check. Setup stops with a
   bilingual error if installation fails.
3. The Kimi login dialog displays a bounded startup/loading status. If no
   WebView loading event arrives within 15 seconds, or navigation reports
   failure, it replaces the white surface with a bilingual WebView2/network
   diagnostic and a button to open Microsoft's official WebView2 download
   page. Successful loading cancels the diagnostic timer.
4. Windows CI runs a real native-WebView smoke after Setup: create a visible
   `QWebView`, load deterministic inline HTML, wait for `Succeeded`, execute
   JavaScript, and require the expected result. This is hosted Windows Server
   evidence; Windows 10/11 manual verification remains required.
5. Before Qt initializes on Windows, AACC sets
   `WEBVIEW2_USER_DATA_FOLDER` to the protected, writable
   `%LOCALAPPDATA%\AACC\kimi-web-session` directory. The installed application
   and CI smoke must not depend on WebView2's default UDF beside `AACC.exe`.

## Security and Packaging Constraints

- Setup remains per-user and must not request elevation.
- The WebView2 bootstrapper is never committed to Git.
- Build input is accepted only after exact SHA-256 and Windows Authenticode
  validation.
- The bootstrapper must not be installed when an existing `pv` value is
  non-empty and greater than `0.0.0.0`.
- No password, cookie, token, URL query, fragment, or remote page error body is
  written to AACC logs.
- Qt WebEngine remains excluded; the candidate size should increase only by
  the small Evergreen bootstrapper.
- A runtime installation failure must be explicit; Setup must not silently
  deliver another permanently blank login window.

## Alternatives Rejected

- **Bundle Qt WebEngine:** deterministic but adds a Chromium stack and a much
  larger attack/update surface.
- **Bundle a Fixed Version WebView2 Runtime:** fully offline but adds roughly
  hundreds of megabytes and makes AACC responsible for shipping every security
  update.
- **Open only the system browser:** the browser's cookies are not the native
  per-application WebView session, so it cannot provide the cached membership
  quota contract.

## Verification

- Unit/contract tests cover registry paths, version rejection, bootstrapper
  hash/signature checks, Setup invocation, timeout/failure/success UI states,
  bilingual documentation, and CI wiring.
- Local macOS tests remain green without instantiating the Windows backend.
- Hosted Windows 2022/2025 builds, frozen smoke, Setup install/reinstall,
  native WebView smoke, strict artifact verification, and upload must pass.
- The resulting Setup is still a candidate until the Windows 10/11 and
  cross-account DACL manual gates are signed.
