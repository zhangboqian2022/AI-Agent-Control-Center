# Kimi Web Session Correction Design

## Status and decision

This design corrects an invalid assumption in the earlier
`2026-07-27-kimi-web-quota-readable-bars-design.md`: Qt's native `QWebView`
does not expose a profile or persistent-storage path API. Creating
`kimi-web-session/` therefore did not bind WKWebView/WebView2 authentication
data to that directory.

The selected design keeps the native system WebView and adds an AACC-owned,
protected authorization gate. It does not bundle Qt WebEngine/Chromium and
does not copy Kimi bearer tokens into an AACC-defined credential protocol.

## Alternatives considered

1. **Native WebView plus an AACC authorization gate — selected.**
   WKWebView/WebView2 retains the first-party site session in its native
   per-application store. AACC persists only whether it is permitted to reuse
   that session. Explicit logout disables reuse synchronously before any
   asynchronous site-data cleanup begins.
2. **Qt WebEngine with an explicit profile path — rejected.**
   This would make the profile directory directly configurable, but it bundles
   Chromium and adds a large runtime, helper processes, signing surface, and
   Windows packaging risk solely for the quota panel.
3. **Copy the website bearer token into an AACC file — rejected.**
   Kimi does not publish the website token refresh contract. Duplicating a
   short-lived or rotated token would create a second sensitive credential
   protocol without a reliable renewal path.

## Persistent authorization gate

- Add a small atomic state file below the protected AACC configuration
  directory. It contains a schema version and one boolean:
  `reuse_native_session`.
- A successful membership response sets the gate to `true`.
- A server `401`/`403` sets it to `false`, because the cached session is no
  longer usable.
- Explicit logout first writes `false` synchronously. Only then may it start
  asynchronous WebView cleanup. A restart or immediate application exit can
  therefore never re-enable the cached login.
- Automatic refresh must not navigate to Kimi or execute membership requests
  while the gate is false. Opening the login dialog is the explicit operation
  that permits a new login attempt.
- The state file contains no password, cookie, bearer token, account name, or
  quota value. It uses the same exact file protection as other AACC
  credentials.

The native website store remains owned by the operating-system WebView
backend, not by `kimi-web-session/`. Documentation must say this honestly.
The AACC directory stores the reuse decision, while the OS app-specific web
store holds Kimi's first-party session.

## Deterministic logout

Logout has two layers:

1. The protected gate is synchronously set to `false`, cached quota is cleared,
   and future automatic refresh is blocked.
2. The WebView is brought to the exact `https://www.kimi.com` origin if
   necessary. A generation-checked script clears LocalStorage and
   SessionStorage, and cookies are deleted. A bounded watchdog ends the cleanup
   attempt if the native callback never arrives.

The first layer is the correctness guarantee. The second layer removes native
site data when the platform completes the request. Manual macOS and Windows
checklists must verify that logout followed by restart remains logged out.

## One five-minute cycle

- One GUI-thread timer owns the 300-second Kimi quota cycle.
- Every cycle triggers the native web membership refresh and the Kimi Code
  fallback refresh from the same callback.
- `QuotaService` retains its worker thread and single-flight protections, but
  when the web coordinator is present it does not run an independent periodic
  clock.
- Manual refresh uses the same cycle callback.
- Kimi Code 5H/WEEK values may fill missing web windows only when their
  `fetched_at` is within one cycle plus a small scheduling tolerance. A new web
  MONTH value must not make an arbitrarily old fallback look freshly fetched.
- The merged snapshot timestamp describes the newest value, while each source
  remains subject to its own freshness gate.

## Bounded web requests

- Membership JavaScript uses `AbortController` with a 15-second deadline for
  both metadata POST requests.
- Each request carries a generation identifier through the title bridge.
- A Python-side watchdog clears `_refreshing` if no valid bridge result arrives
  within 25 seconds. Late results from an older generation are ignored.
- A timed-out cycle reports a redacted error, retains the last successful
  values as stale, and permits the next five-minute or manual refresh.
- Quota lookup remains metadata-only: it sends no prompt and consumes no model
  generation tokens.

## Monthly reset semantics

- MONTH percentage remains authoritative only from the web membership response.
- MONTH reset time is accepted only from the quota balance object's
  `expireTime` or `resetTime`.
- Subscription `nextBillingTime` and root-level billing dates are not quota
  reset evidence and must never populate the MONTH reset label.
- A known MONTH percentage with no trustworthy reset continues to show its
  percentage and displays `--` for reset time.

## Testing and release gates

Automated tests must prove:

- the protected gate survives construction of a new service instance;
- explicit logout writes `false` before WebView cleanup and blocks refresh
  after restart even if cleanup never calls back;
- successful login and unauthorized responses update the gate correctly;
- one timer callback triggers both web and Kimi Code sources;
- the fallback age boundary rejects an old Kimi Code snapshot;
- JavaScript and Python watchdog paths recover and permit a later refresh;
- an annual `nextBillingTime` never appears as a MONTH reset;
- Codex remains WEEK-only and the accepted Kimi `5H / WEEK / MONTH` A-format
  remains unchanged.

Formal `v1.4.2` remains blocked until both macOS and Windows manual checks
confirm native-session persistence, explicit logout across restart, and the
five-minute refresh behavior. Automated tests may validate the AACC gate and
orchestration, but cannot claim the platform WebView store itself was exercised.
