# 7-ADR Jarvis Arc — Live E2E Test Plan

All 7 ADRs are code-complete with flags **ON**. None confirmed against a running server
yet — this plan does that. Run on the real box (model host reachable, e.g. the remote
122b at `172.30.60.31`). Sandbox can't reach the model host.

## 0. Setup (once)

```bash
cd <repo root>
uv sync
uv run python -m stackowl db migrate          # applies 0071_turn_decisions (ADR-7 /explain)
uv run python -m stackowl health              # sanity: subsystems probe OK
uv run python -m stackowl serve               # start server (Telegram/TUI as configured)
```

Confirm flags are ON (committed defaults — verify, don't trust):
```bash
uv run python -c "from stackowl.config.settings import Settings; s=Settings(); \
print('acceptance_authority', s.acceptance_authority); \
print('reachability_enforcement', s.reachability_enforcement); \
print('reversibility_resolver', getattr(s,'reversibility_resolver',None)); \
print('trustworthy_learning', getattr(s,'trustworthy_learning',None)); \
print('health_loop', s.health_loop); \
print('decision_ledger', s.decision_ledger)"
```
Expect: `True / block / True / True / True / True`.

Logs are JSONL at `logs/stackowl-$(date +%F).log`. Tail decisions live:
```bash
tail -f logs/stackowl-$(date +%F).log | \
  jq -c 'select(.msg|test("decision_ledger|acceptance_authority|recovery_actuator|reversibility|health_sweep|repeated_approach")) | {ts,msg,fields}'
```

---

## ADR-7 — DecisionLedger + /explain  ★ easiest, do first
**What:** every authority's verdict recorded per turn; `/explain` reads it back.
**Trigger:** send any normal request to an owl (e.g. "summarize this file" / "what's 2+2").
**Then:** in the same chat send `/explain`.
**Expect:** a "why" readout — one line per decision, e.g.
`router — answer — clear_verdict`, `acceptance — accepted — …`, etc.
**DB check:**
```bash
sqlite3 ~/.stackowl/*.db "SELECT session_id, trace_id, substr(decisions_json,1,200) FROM turn_decisions;"
```
Expect one row per session, latest turn's decisions as JSON.
**Negative:** fresh session with no prior turn → `/explain` returns the friendly "no decisions recorded" message.

---

## ADR-1 — AcceptanceAuthority (success MEASURED, not asserted)
**What:** a tool claiming success is verified against its post-condition; a false "I did it" is caught.
**Trigger A (happy):** ask the owl to write a file or send a message. It succeeds.
**Trigger B (false win):** force a tool that returns success but produces no artifact (e.g. a write to a non-writable path, or disconnect the send transport mid-turn).
**Expect:** B does NOT report "done" — the floor/honesty path fires; logs show
`acceptance_authority … refuted` and a `acceptance — refuted` Decision in `/explain`.
```bash
jq -c 'select(.msg|test("acceptance_authority"))|{ts,msg,fields}' logs/stackowl-$(date +%F).log
```

## ADR-2 — RecoveryActuator (one retry/reroute/surrender ladder)
**Trigger:** induce a TRANSIENT provider failure — point the active provider at a bad
baseURL for ~10s (or kill the model host briefly) during a turn, then restore.
**Expect:** the turn retries/reroutes and recovers (not an instant give-up); on permanent
failure it SURRENDERS honestly (no fake success). `/explain` shows
`recovery — recovered …` (alternatives = rungs tried) or `recovery — surrendered …`.
```bash
jq -c 'select(.msg|test("recovery_actuator|recovery] turn summary"))|{ts,msg,fields}' logs/stackowl-$(date +%F).log
```
Consequential actions must NEVER auto-retry (no double-commit) — verify a write tool that
fails is not silently retried.

## ADR-3 — ReversibilityResolver (act-on-assumption vs ask)
**Trigger A (reversible/ambiguous):** a request with one obvious default — owl should ACT
on the assumption (state it) instead of asking a clarifying question.
**Trigger B (irreversible/high-stakes):** a destructive/irreversible request — owl should ASK.
**Expect:** A acts + states assumption; B parks for confirmation. `/explain` shows
`reversibility — act — <assumption>` or `reversibility — ask — parked …`.
Non-interactive contexts (cron/parliament) must only downgrade park→act, never the reverse.

## ADR-4 — Reachability enforcement = block
**What:** boot census REFUSES READY on a dangling wiring half-edge (was warn-only).
**Trigger (happy):** `serve` reaches READY normally → census passed, no dangling edges.
**Trigger (guard):** temporarily break a registration (unregister a referenced tool/command)
→ boot must FAIL LOUD (refuse READY) instead of warning and limping. Restore after.
```bash
jq -c 'select(.msg|test("reachability|census"))|{ts,msg,level,fields}' logs/stackowl-$(date +%F).log
```

## ADR-5 — Trustworthy learning
**What:** only MEASURED successes are mined; a blind re-issue of an exact (tool,args)
that already failed THIS turn is steered away; never stores negatives.
**Trigger:** prompt a task where a tool call fails, and the model tends to re-issue the
identical call. The repeat is refused (containment), the recovery ladder's own retries are
NOT blocked.
```bash
jq -c 'select(.msg|test("repeated_approach|failed_approaches"))|{ts,msg,fields}' logs/stackowl-$(date +%F).log
```
Also confirm a false-win turn (ADR-1 refuted) is NOT later promoted to a durable fact.

## ADR-6 — Closed-loop health (detect → heal → verify → escalate)
**Trigger:** while serving, kill a healable subsystem — e.g. close the browser runtime, or
drop the DB connection — and wait for the 5-min health sweep (or trigger `health`).
**Expect:** sweep DETECTS down → RECYCLES (heal) → RE-COLLECTS (verify) → recovered logs
"recovered" with NO operator alert; still-down → escalates with an alert. A user-visible
core crash respawn shows the F-39 notice.
```bash
jq -c 'select(.msg|test("health_sweep|heal|recovered|escalat"))|{ts,msg,level,fields}' logs/stackowl-$(date +%F).log
uv run python -m stackowl health
```

---

## Pass criteria
- `/explain` returns a real per-turn why (ADR-7) — the keystone observability proof.
- No turn ever reports success for an unverified effect (ADR-1).
- Transient failures recover; permanent ones surrender honestly; consequential never auto-retried (ADR-2).
- Reversible→act, irreversible→ask (ADR-3).
- Boot refuses READY on a broken wiring (ADR-4).
- Blind repeat refused; no negative learning; no false-win promotion (ADR-5).
- Down subsystem self-heals + re-verifies; alert only when still down (ADR-6).

## If something fails
Capture the `traceId` from the bad turn and pull its full trace:
```bash
jq -c 'select(.traceId=="<TRACE_ID>")' logs/stackowl-$(date +%F).log
```
Report the trace — that's the fastest root-cause path.
```
```
