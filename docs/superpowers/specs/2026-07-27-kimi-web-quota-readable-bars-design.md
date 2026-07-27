# Kimi Web Quota and Readable Quota Bars Design

## Goal

Make the Codex and Kimi quota strips readable at normal panel size, and let
AACC show Kimi's 5-hour, weekly, and monthly membership usage from one
five-minute refresh cycle.

## Confirmed facts

- The current quota typography is too small: period, percentage, and reset
  labels are all 9 px.
- AACC already owns and persists a Kimi Code OAuth grant with `scope=kimi-code`.
- That grant can read `GET https://api.kimi.com/coding/v1/usages`, but it is
  rejected by Kimi's web membership service.
- The live Kimi Code response supplies 5-hour and weekly usage but currently
  returns an empty `totalQuota`.
- Kimi's signed-in membership page reads all three displayed ratios from
  `MembershipService/GetSubscriptionStats`; the billing reset comes from the
  active subscription's `nextBillingTime`.

## User experience

### Quota strip

- Increase summary text from 11 px to 12 px.
- Increase period labels from 9 px to 10 px.
- Increase percentage text from 9 px to 11 px, keep it bold and monospaced,
  and reserve enough width for `100%`.
- Increase reset text from 9 px to 10 px.
- Increase progress height from 5 px to 7 px.
- Increase row spacing and minimum column widths without changing the
  established A-format: Codex remains one WEEK row; Kimi remains three rows
  ordered 5H, WEEK, MONTH.

### Kimi web authorization

- Clicking the Kimi strip while the monthly source is unauthorized opens an
  AACC-owned Kimi login window backed by Qt's native system WebView.
- The user signs in directly on `https://www.kimi.com`; AACC never receives or
  stores the account password.
- Web cookies and site storage remain in the operating system's per-application
  web store and survive app restarts.
- The settings page distinguishes “Kimi Code 授权” and “Kimi 会员网页登录”.
- “退出 Kimi 登录” clears both the existing Kimi Code credential and the
  AACC-owned web profile. It is the only normal local action that clears the
  cached sessions.
- Server revocation, password changes, or cookie expiry can still require a
  new login.

## Data flow

1. When AACC is open, `KimiWebQuotaService` schedules one refresh every 300
   seconds.
2. The service uses the AACC-owned native WebView session to call, from the
   `www.kimi.com` origin:
   - `MembershipService/GetSubscriptionStats`
   - `MembershipService/GetSubscription`
3. One successful cycle yields 5H, WEEK, and MONTH ratios plus reset times with
   one shared `fetched_at`.
4. Web membership data is authoritative for all three displayed rows.
5. The existing Kimi Code `/coding/v1/usages` reader remains a fallback for 5H
   and WEEK when web login is unavailable or the internal web endpoint changes.
6. A failed refresh retains the last successful values and marks them stale;
   it never replaces known values with blanks.
7. Clicking the strip triggers an immediate refresh, still protected by
   single-flight logic and a short cache TTL.

## Token and request semantics

- Both sources use an authentication token or session cookie to identify the
  account.
- A quota lookup is metadata-only. It sends no prompt and performs no model
  inference, so it does not consume Kimi model tokens or membership quota.
- Five-minute polling is at most 288 cycles per continuously open day. Each
  cycle performs two small membership metadata requests.
- The existing Kimi Code OAuth access token is refreshed only near expiry; the
  rotated refresh token is committed atomically to AACC's protected credential
  file.

## Security and platform behavior

- macOS uses the system WKWebView and Windows uses the system WebView2 backend;
  AACC does not bundle a Chromium engine.
- AACC's session marker directory uses the project's existing
  protected-directory helper; the native web store remains isolated by the
  operating system's per-application container.
- No browser cookies, bearer tokens, or account identifiers are logged.
- Error text is redacted before it reaches logs or tooltips.
- The web endpoint is an internal first-party interface rather than a published
  public API, so parsing must be defensive and Kimi Code fallback must remain.

## Testing

- Parser fixtures cover decimal ratios, missing fields, epoch/ISO reset values,
  and malformed Connect responses.
- Merge tests prove web values win, Kimi Code fills only missing 5H/WEEK values,
  and stale failures preserve the last good snapshot.
- Service tests use a fake web page bridge; automated tests never log in to a
  real account.
- GUI tests assert the three-row order, enlarged font metrics, non-overlap at
  the default panel width, cached-login status, and unified logout.
- Packaging tests assert Qt WebEngine modules and resources are included on
  macOS and Windows.
