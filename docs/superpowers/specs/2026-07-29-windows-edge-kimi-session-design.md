# Windows Edge Kimi Persistent Session Design

**Date:** 2026-07-29  
**Target:** AACC 1.4.2 Windows candidate  
**Status:** Approved direction; implementation pending

## 1. Problem

The embedded Qt WebView2 Kimi login window can remain blank on Windows 11 even
when Microsoft Edge WebView2 Runtime is installed. The current timeout message
incorrectly suggests that the Runtime is missing whenever the embedded control
does not emit a page-load event.

AACC needs a Windows login path that:

- works with the installed Microsoft Edge browser instead of the embedded
  WebView2 control;
- preserves the Kimi membership login across AACC and Windows restarts;
- refreshes Kimi 5H, WEEK, and MONTH quota every five minutes without consuming
  model tokens;
- does not read or modify the user's normal Edge profile;
- does not expose Kimi cookies or access tokens to AACC logs or persistence.

macOS keeps the existing native web-session implementation.

## 2. Decision

On Windows, AACC will open Microsoft Edge with an AACC-owned browser profile and
communicate with that managed browser through a short-lived, loopback-only
Chrome DevTools Protocol (CDP) connection.

The profile is stored under:

```text
%LOCALAPPDATA%\AACC\kimi-edge-profile
```

This profile remains available until the user explicitly logs out of Kimi in
AACC, Kimi invalidates the session, or AACC detects that the profile is unsafe
or corrupted. AACC will not impose a seven-day re-login interval.

## 3. User Experience

### First login

1. The user clicks the Kimi quota login action.
2. AACC opens a visible, dedicated Edge window at the Kimi membership page.
3. The user signs in normally.
4. AACC detects the authenticated membership response, updates 5H, WEEK, and
   MONTH quota, then closes only the Edge process it started.

### Later launches

- Restarting AACC or Windows does not require another login while the Kimi
  session is still valid.
- Every five minutes, AACC briefly opens Edge in background/headless mode with
  the same dedicated profile, reads the three quota windows, and closes it.
- A manual refresh uses the same mechanism.
- If Kimi reports that authentication has expired, AACC changes to the signed
  out state and asks the user to sign in again.

### Logout

When the user selects Kimi logout in AACC:

1. AACC stops its managed Edge process and disables session reuse immediately.
2. It clears Kimi site data from the dedicated profile.
3. If targeted clearing cannot be verified, it safely removes only the
   AACC-owned Kimi Edge profile after validating the path.
4. The user's normal Edge browser profile remains untouched.

## 4. Architecture

### Session abstraction

`KimiWebQuotaService` keeps its existing session protocol. Session construction
becomes platform-specific:

- macOS and other supported platforms: existing `KimiWebSession`;
- Windows: new `KimiEdgeSession`.

Both implementations expose the same signals and operations:

- `login_state_changed`
- `quota_received`
- `error_occurred`
- `refresh()`
- `open_login()`
- `logout()`
- `close()`
- `retranslate_ui()`

This confines the Windows change to the web-session boundary and preserves the
existing quota fallback and five-minute scheduling logic.

### Managed Edge lifecycle

The Windows session locates `msedge.exe` from trusted installation locations
and registry entries. It starts Edge directly without a shell, using:

- the AACC-owned `--user-data-dir`;
- a unique remote-debugging endpoint;
- the Kimi membership URL;
- visible mode for interactive login;
- headless mode for authenticated background refresh.

Only one managed Edge operation may run at a time. Login, refresh, logout, and
application shutdown use a generation/cancellation guard so a stale result
cannot overwrite newer state.

The CDP endpoint binds only to loopback and exists only while the managed Edge
process is running. AACC discovers the random endpoint from Edge's
`DevToolsActivePort` file instead of reserving a predictable fixed port.

On normal completion AACC requests `Browser.close`. If Edge does not exit within
a bounded interval, AACC terminates only the process tree it created.

### Quota query

After the Kimi page is ready, AACC evaluates the existing same-origin membership
query inside the managed Edge page. The page context reads its own
`localStorage.access_token` and calls the official Kimi membership endpoints.

Only normalized quota values and reset timestamps cross back to Python:

- 5H usage and reset time;
- WEEK usage and reset time;
- MONTH usage and reset time.

Cookies and access tokens are never returned to Python, written to AACC
configuration, or logged. Quota metadata requests do not invoke a model and do
not consume Kimi model tokens.

### Threading

Edge launch, CDP connection, navigation, and quota evaluation run outside the
Qt UI thread. Results are delivered through Qt signals. The existing
single-flight behavior is retained so timer and manual refreshes cannot launch
competing browser instances against the same profile.

## 5. Persistence and Security

- The dedicated profile is protected using AACC's native Windows exact-DACL
  security layer for the current user, SYSTEM, and Administrators.
- AACC never imports from or points at the user's normal Edge profile.
- The profile path must be an exact expected child of AACC's local data
  directory and must not be a symlink or reparse-point escape before any
  cleanup.
- CDP is loopback-only, random, and short-lived.
- Commands use explicit executable and argument arrays; no `cmd.exe`,
  PowerShell, or shell interpolation is involved.
- Logs contain lifecycle stages and sanitized error categories only. They never
  contain cookies, authorization headers, access tokens, page storage, or raw
  membership responses.
- A corrupt, inaccessible, or insecure profile fails closed and requires a new
  login rather than silently falling back to the user's normal browser data.

## 6. Failure Handling

The UI distinguishes these cases:

- Edge is not installed or cannot be located;
- the managed Edge process cannot start;
- the local CDP connection cannot be established;
- Kimi login is incomplete;
- the Kimi session has expired;
- quota endpoints are temporarily unavailable;
- the dedicated profile is insecure or corrupted.

A failed background refresh keeps the last known quota as stale data when
appropriate. An explicit authentication failure signs the session out.

The old generic “repair WebView2 Runtime” message is not used for the Windows
Edge path.

## 7. Alternatives Considered

### Read the user's normal Edge cache

Rejected. Edge profile data is security-sensitive, can be locked or encrypted,
and is not a stable application API. Reading it would couple AACC to the user's
daily browser and create unnecessary privacy and corruption risks.

### Browser extension in the user's normal Edge profile

Rejected. It would require installation and broad browser permissions, add a
second deployment surface, and weaken the isolation between AACC and personal
browsing.

### Bundle Qt WebEngine

Rejected for this release. It would substantially increase installer size and
would still create a separate browser login. It would not reuse the already
installed Edge browser.

### Managed AACC Edge profile through CDP

Selected. It uses the browser already present on Windows, provides durable
session storage, preserves isolation, and allows the existing quota query to
remain within Kimi's page origin.

## 8. Validation Scope

Implementation follows TDD with focused tests for:

- platform session selection;
- trusted Edge discovery;
- launch arguments and unique profile path;
- CDP endpoint discovery and timeout behavior;
- successful login and three-window quota parsing;
- five-minute refresh reuse;
- expired-session handling;
- manual logout and safe profile cleanup;
- cancellation, single-flight, and managed-process shutdown;
- secret redaction and security failures.

Before delivering the new Setup candidate, run the focused test set, Ruff, mypy,
and the Windows packaging/build checks needed to produce and validate the
installer. Do not repeat the full long-form Windows product smoke suite for this
iteration; Windows 10/11 real-machine release gates remain documented and are
not claimed as complete.

## 9. Release Boundary

This change produces a new AACC 1.4.2 Windows Setup candidate for direct user
installation and verification. It does not authorize creation of the formal
`v1.4.2` tag or Latest GitHub Release while the existing real-machine release
gates remain open.
