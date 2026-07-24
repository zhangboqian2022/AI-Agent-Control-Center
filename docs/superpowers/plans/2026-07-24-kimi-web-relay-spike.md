# Kimi Web Relay Spike (Subsystem C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine — with captured real data — whether AACC should subscribe to the local `kimi web` server for real-time session status/metrics, and produce the concrete protocol facts an implementation plan needs.

**Architecture:** Spike only. Start a real `kimi web` instance, capture its OpenAPI/AsyncAPI documents and live WebSocket traffic into `tests/fixtures/kimi_web/`, then write a findings doc with a go/no-go decision. The full implementation plan is written AFTER this spike, based on the findings.

**Tech Stack:** local `kimi` CLI (already installed on this machine), curl, Python stdlib (+ `websockets` only if WS capture needs it).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-three-in-one-integration-design.md` (Subsystem C section).
- Branch: `feat/kimi-quota-integration`.
- Known official facts (verified against docs, do not re-derive): default port 58627 with +1 retry on conflict; instances register under `~/.kimi-code/server/instances/`; persistent bearer token at `~/.kimi-code/server.token`; `GET /openapi.json` + `GET /asyncapi.json` served by the running instance; WS auth via `kimi-code.bearer.<token>` subprotocol.
- Do NOT modify `~/.kimi-code/` contents; read-only except where `kimi web` itself writes.
- Stop every `kimi web` process the spike starts (`Ctrl-C` / kill the pid it reports).
- Do not commit captured files that contain the bearer token or session content — redact tokens before committing fixtures.

---

### Task 1: Environment probe + REST/AsyncAPI capture

**Files:**
- Create: `tests/fixtures/kimi_web/openapi.json`, `tests/fixtures/kimi_web/asyncapi.json`
- Create: `docs/superpowers/specs/2026-07-24-kimi-web-relay-findings.md` (started here, finished in Task 3)

- [ ] **Step 1: Record environment facts**

```bash
which kimi && kimi --version
ls ~/.kimi-code/server/ 2>/dev/null
ls ~/.kimi-code/server/instances/ 2>/dev/null
```

Note in the findings doc: CLI version, whether `server.token` exists (do NOT print its value), instance registry file format (redact tokens/pids are fine to keep).

- [ ] **Step 2: Start a throwaway server**

```bash
kimi web --no-open --port 58699 > /tmp/aacc-kimi-web-spike.log 2>&1 &
echo $!  # record as SPIKE_PID
sleep 3
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:58699/openapi.json
```

Expected: `200`. If the port differs (busy retry), read the actual port from the startup banner in `/tmp/aacc-kimi-web-spike.log`.

- [ ] **Step 3: Capture the protocol documents**

```bash
mkdir -p tests/fixtures/kimi_web
curl -s http://127.0.0.1:58699/openapi.json | python3 -m json.tool > tests/fixtures/kimi_web/openapi.json
curl -s http://127.0.0.1:58699/asyncapi.json | python3 -m json.tool > tests/fixtures/kimi_web/asyncapi.json
wc -c tests/fixtures/kimi_web/*.json
```

- [ ] **Step 4: Authenticated REST smoke**

Using the token from `~/.kimi-code/server.token` (never write it into any file that gets committed):

```bash
TOKEN=$(cat ~/.kimi-code/server.token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:58699/api/v1/sessions | head -c 2000
```

(The exact route comes from the captured openapi.json — find the session-list and session-events routes there and record them in the findings doc.)

- [ ] **Step 5: Summarize in the findings doc**

Write `docs/superpowers/specs/2026-07-24-kimi-web-relay-findings.md` with: environment facts, the REST routes that matter (session list, session detail, `last_seq` snapshot), the AsyncAPI channel names and message schemas for turn/usage events, and open questions for Task 2.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/kimi_web docs/superpowers/specs/2026-07-24-kimi-web-relay-findings.md
git commit -m "docs: capture kimi web protocol documents for relay spike"
```

Verify no token values are in the committed files: `grep -ri "bearer\|eyJ" tests/fixtures/kimi_web/` must return only schema descriptions, no live tokens.

---

### Task 2: Live WebSocket capture

**Files:**
- Create: `tests/fixtures/kimi_web/ws-events-sample.jsonl` (redacted)

- [ ] **Step 1: Choose the WS client**

Check if `websockets` is already importable in `.venv`. If not, install it into the project venv as a real dependency (it will be needed by the implementation anyway):

```bash
.venv/bin/python -c "import websockets" 2>/dev/null || uv add websockets
```

- [ ] **Step 2: Capture events during one real turn**

Write and run a throwaway script (not committed) that: connects to the session events channel from the AsyncAPI doc with subprotocol `kimi-code.bearer.$TOKEN`, fetches the `last_seq` snapshot first (per the REST route found in Task 1), then triggers one tiny turn (e.g. create/resume a session via the REST API and send a one-word prompt), and logs every WS frame to a local file for ~60 seconds.

- [ ] **Step 3: Redact and save the sample**

Strip any prompt/response text bodies, tokens, and absolute home paths from the captured frames, keeping event types, seq numbers, usage objects, and status fields. Save as `tests/fixtures/kimi_web/ws-events-sample.jsonl`.

- [ ] **Step 4: Stop the server**

```bash
kill $SPIKE_PID  # from Task 1 Step 2
ps aux | grep 'kimi web' | grep -v grep  # confirm nothing the spike started is left
```

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/kimi_web/ws-events-sample.jsonl uv.lock pyproject.toml
git commit -m "docs: capture kimi web websocket event sample for relay spike"
```

---

### Task 3: Findings, decision, follow-up plan

**Files:**
- Modify: `docs/superpowers/specs/2026-07-24-kimi-web-relay-findings.md`

- [ ] **Step 1: Complete the findings doc**

Answer these questions with evidence (fixture paths + line refs):

1. Which WS channel(s) carry turn lifecycle and usage events? Exact message shapes?
2. Does the `last_seq` snapshot + incremental subscription work as kimi-code-monitor assumes?
3. Can AACC map WS events onto its existing `TaskStatus` semantics (RUNNING/COMPLETED/WAITING_*) without ambiguity?
4. What happens on reconnect — is seq-based resume sufficient, or is a full snapshot re-fetch needed?
5. Effort estimate for the real implementation (module list, test surface).

- [ ] **Step 2: Go / no-go decision**

- **Go (WS relay):** write a new implementation plan `docs/superpowers/plans/2026-07-24-kimi-web-relay.md` using the writing-plans skill conventions, implementing `src/aacc/kimi_web_discovery.py` behind config flag `kimi_web_relay_enabled: bool = False` (add to `AppSettings`), feeding `SessionUsage`/metrics from subsystem B where possible.
- **Degrade (REST poll):** same plan, but a 5s REST polling adapter instead of WS.
- **No-go:** document why; close subsystem C with the findings doc as the deliverable.

- [ ] **Step 3: Update the spec's Subsystem C section** with the decision outcome (one paragraph), then commit:

```bash
git add docs/
git commit -m "docs: kimi web relay spike findings and decision"
```

---

## Self-Review Notes

- This plan intentionally ends at a decision gate; production code for subsystem C is planned only after real protocol data exists (per the spec's 实施门禁).
- Token hygiene steps (redaction, grep verification) are mandatory before every commit.
