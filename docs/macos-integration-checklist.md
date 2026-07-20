# macOS Integration Checklist — AACC v1.3.0-rc.1

Record only observed results; an empty row is not a compatibility claim.

| macOS | Machine / architecture | Accessibility | Codex discovery | Local API | DMG install | Result |
|---|---|---|---|---|---|---|
| 13 | Not tested | — | — | — | — | Pending |
| 14 | Not tested | — | — | — | — | Pending |
| 15 | Not tested | — | — | — | — | Pending |
| 26.5.2 | This Mac / Apple Silicon | Automated detection/guidance passed; existing grant not revoked | Pass: running and completed tasks detected | Pass: health `1.3.0rc1`, doctor passed | Pass: replace install, signature and image verified | Pass under tested conditions |

Manual checks: single-instance activation; missing Accessibility guidance; active/complete Codex transitions; retained green result until `×`; three-failure warning and two-success recovery; token rotation invalidates old token; config/database modes `0600`; app position and always-on-top preference; ad-hoc signature and DMG checksum.
