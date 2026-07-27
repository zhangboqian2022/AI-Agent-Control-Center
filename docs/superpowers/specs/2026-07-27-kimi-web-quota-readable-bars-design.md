# Kimi Web Quota and Readable Quota Bars Design

**Status: Superseded.** The session-storage, logout, scheduling, timeout, and
MONTH reset assumptions in this document are replaced by
`2026-07-27-kimi-web-session-correction-design.md`. This file retains only the
accepted visual layout and a corrected summary of the data boundary.

## Accepted quota-strip layout

- Codex remains one `WEEK` row and accepts only its 10080-minute window.
- Kimi remains three rows ordered `5H`, `WEEK`, `MONTH`.
- Summary text is 12 px, period labels are 10 px, percentages are bold
  monospaced 11 px with enough width for `100%`, reset text is 10 px, and
  progress bars are 7 px high.
- Percentage, progress, and reset date/time have separate columns at the
  default panel width.
- A known percentage without a trustworthy reset remains visible and the reset
  label displays `--`.

## Corrected authorization and storage boundary

- AACC uses Qt's native system WebView. macOS uses WKWebView and Windows uses
  WebView2; AACC does not bundle Qt WebEngine or Chromium.
- The operating system's native per-application WebView store retains Kimi's
  first-party site session.
- For native website-session reuse, AACC persists only a protected reuse
  decision. That gate does not contain a website bearer token, password,
  cookie, account name, or quota value. Kimi Code OAuth credentials remain
  separately stored under the existing credential protection.
- Explicit logout synchronously disables reuse before attempting bounded
  native site-data cleanup. The disabled gate, rather than completion of the
  asynchronous cleanup, is the correctness guarantee.
- Native-session persistence and logout across restart require manual sign-off
  on both macOS and Windows.

## Corrected refresh and quota semantics

- One GUI-thread coordinator starts the web membership source and Kimi Code
  fallback from the same five-minute cycle; manual refresh uses the same path.
- Kimi Code may fill only sufficiently fresh missing `5H` or `WEEK` values.
  It never supplies `MONTH`.
- MONTH percentage is authoritative only from the web membership response.
  MONTH reset accepts only `subscriptionBalance.expireTime` or
  `subscriptionBalance.resetTime`; unrelated billing or root-level dates are
  not quota reset evidence.
- A quota lookup is metadata-only. It sends no prompt, performs no inference,
  and uses no generation tokens.
- Web requests have JavaScript and Python deadlines, carry a generation
  identifier, ignore late generations, redact errors, and retain the last
  trustworthy snapshot as stale.

## Verification boundary

Automated tests cover parser boundaries, the protected reuse gate, logout
ordering, request deadlines, one-cycle coordination, fallback freshness,
three-row order, enlarged typography, and non-overlap. Automated tests do not
exercise the real operating-system WebView store. Formal `v1.4.2` therefore
remains blocked until macOS and Windows manual checks confirm session
persistence, logout across restart, and the shared five-minute refresh.
