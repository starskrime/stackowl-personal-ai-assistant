# Census: Every Hardcoded Numeric Limit / Backstop

Triggered by the same-day incident where a hardcoded conversational output cap (1024, then 4096) broke every reply for ~30 minutes, on the same day the *absence* of a real cap (250,000 default) had caused a 205s/$0.14 "hi." The question this census answers: which of the platform's numbers are genuine safety backstops (generous, only catch pathological behavior) vs. tuning levers in a backstop's clothing (tight enough to shape normal behavior, brittle, the exact pattern that broke twice in one day)?

## Sources consulted

`pipeline/steps/execute.py`, `pipeline/budget/governor.py`, `pipeline/budget/callback.py`, `pipeline/context_budget.py`, `pipeline/persistence.py`, `pipeline/delivery_gate.py`, `pipeline/supervisor.py`, `pipeline/progress_tracker.py`, `pipeline/acceptance_llm.py`, `pipeline/durable/delegation_link.py`, `authz/bounds.py`, `config/provider.py`, `providers/openai_provider.py`, `providers/anthropic_provider.py`, `providers/_truncate.py`, `providers/model_window.py`, `providers/registry.py`, `providers/tier_selector.py`, `owls/manifest.py`, `owls/guards.py`, `owls/delegation_limits.py`, `owls/sticky_route_cache.py`, `owls/router.py`, `owls/base_prompt.py`, `owls/owl_schedule_guards.py`, `memory/retry_queue_store.py`, plus a repo-wide grep for `_MAX_`/`_MIN_`/`_CAP`/`_LIMIT`/`_THRESHOLD`/`_TIMEOUT`/`_DEADLINE`/`TTL` across `pipeline/`, `providers/`, `owls/`, `authz/`.

## Census table (48 items)

| # | Constant | File:line | Value | Bounds what | Class | Why |
|---|---|---|---|---|---|---|
| 1 | `ProviderConfig.max_output_tokens` | `config/provider.py:84` | 250000 | Ceiling `_output_cap()` requests against | BACKSTOP | Explicitly "generous... NOT a small cap" |
| 2 | `_output_cap()` window-headroom logic | `providers/openai_provider.py:1038-1099` | derived | Per-call `max_tokens` = min(#1, window−input−margin) | BACKSTOP | Mechanism, exists to keep #1 within the window |
| 3 | `_CONVERSATIONAL_MAX_TOKENS` | `pipeline/steps/execute.py:145` | 4096 | Hard override for tool-free turns only | **TUNING LEVER** | Same-day 1024→4096; a number tuned to "fit" one model, applied unconditionally to a whole turn class |
| 4 | `OwlAgentManifest.max_tokens` | `owls/manifest.py:73` | 4096 | Client-side whitespace word cutoff in `OwlResourceGuard.stream()`, every owl/turn | **TUNING LEVER** | Applies unconditionally; own comment admits it doesn't bound what the provider generates |
| 5 | `OwlAgentManifest.timeout_seconds` | `owls/manifest.py:79` | 400.0 | Per-stream-item stall timeout | BACKSTOP | Was a lever (30→60) until live traffic proved too tight; now explicitly widened |
| 6 | `DEFAULT_TURN_MAX_STEPS` | `authz/bounds.py:51` | 20 | Whole-turn ReAct round/tool-call cap | BACKSTOP | Documented as "generous for happy-path, bounds the pathology" |
| 7 | `DEFAULT_TURN_MAX_TIME_S` | `authz/bounds.py:50` | 120.0 | Whole-turn wall-clock cap | **UNCLEAR** | A single stream item inside this SAME turn (#5) is separately allowed 400s — tighter than what it wraps, never revisited when #5 widened |
| 8 | `DEFAULT_SCHEDULED_TURN_MAX_STEPS` | `authz/bounds.py:63` | 45 | Step cap for scheduled turns | BACKSTOP | Re-derived from an observed real job maxing 20/23 |
| 9 | `_DEGENERATE_REPEAT_THRESHOLD` | `execute.py:120` | 20 | Identical-chunk repeat count before cutoff | BACKSTOP | No legitimate answer repeats this much |
| 10 | `_WINDOW_PROBE_DEADLINE_S` | `execute.py:695` | 5.0 | Fallback window-probe timeout | BACKSTOP | Off steady path |
| 11 | `_TOOL_DEADLINE_S` | `execute.py:705` | 180.0 | Per-tool-call execution deadline | BACKSTOP | "Generous, without truncating a legitimately long tool" |
| 12 | `SAME_TOOL_FAILURE_THRESHOLD` | `execute.py:714` | 3 | Consecutive same-tool failures before bounce | BACKSTOP | Fires on repeated failure, not volume |
| 13 | `_ROUND_DEADLINE_FALLBACK_S` | `openai_provider.py:182`, `anthropic_provider.py:75` | 120.0 | Fallback per-round deadline | **UNCLEAR** | Same number/pattern as #7 — unverified tightness vs. a reasoning-heavy round |
| 14 | `_INPUT_TOKEN_SAFETY_MARGIN` | `openai_provider.py:188` | 2000 | Buffer subtracted from window headroom | BACKSTOP | Estimate-error cushion |
| 15 | `_MIN_OUTPUT_TOKENS` | `openai_provider.py:192` | 256 | Floor on requested output under scarce headroom | BACKSTOP | Prevents zero/negative request |
| 16 | `ProviderConfig.timeout_seconds` | `config/provider.py:71` | 30.0 | *(intended)* HTTP timeout | **DEAD** | Never read anywhere — phantom limit |
| 17 | `tool_max_iterations` | `config/provider.py:92` | = #6 (20) | Provider's own tool-loop ceiling | BACKSTOP (model example) | Deliberately derived from #6 "so the two bounds AGREE by construction" — fixes a past redundancy bug |
| 18 | `PROMPT_SAFETY_FRACTION` | `context_budget.py:13` | 0.9 | Fraction of window usable for tool-schema budgeting | BACKSTOP | Mechanical |
| 19 | `RESPONSE_RESERVE_TOKENS` | `context_budget.py:14` | 2048 | Reserved for response when budgeting schemas | BACKSTOP | Mechanical |
| 20 | `HARD_TOOL_COUNT_CAP` | `context_budget.py:19` | 40 | Max tool schemas presented per turn | **TUNING LEVER** | "Lower it for weak/quantized models that derail" — directly shapes what the model may do |
| 21 | `MAX_OBSERVATION_CHARS` | `providers/_truncate.py:22` | 12000 | Cap on one tool result before context entry | BACKSTOP | Generous, prevents explosion |
| 22 | `CONTEXT_CHAR_BUDGET` | `_truncate.py:23` | 400,000 | Total message-list char budget | BACKSTOP | "Well under typical context windows" |
| 23 | `DEFAULT_WINDOW_FALLBACK` | `model_window.py:30` | 262,144 | Context-window floor when model reports nothing | BACKSTOP | Explicitly "NOT a cap" — probe-failure floor |
| 24 | `_CLOUD_DEFAULT` | `model_window.py:31` | 200,000 | Known-cloud-model window default | BACKSTOP | Reasonable estimate |
| 25 | `_PROBE_TIMEOUT` | `model_window.py:54` | 4.0 | Window-probe HTTP timeout | BACKSTOP | Minor infra probe |
| 26 | `LEAN_WINDOW_THRESHOLD` | `owls/base_prompt.py:31` | 8192 | Window size below which model gets "lean" charter/DNA | **TUNING LEVER** | Changes what instructions the model receives — shapes behavior by design |
| 27 | `MAX_DELEGATION_DEPTH` (interactive/autonomous) | `owls/delegation_limits.py:20,42` | 2 / 4 | Max sub-agent delegation recursion | **TUNING LEVER** | "Deeper recursion is almost never legitimate" — a judgment call, not pathology |
| 28 | `MAX_INFLIGHT_PIPELINES` | `delegation_limits.py:28` | 4 | Host-wide concurrent delegated+parliament pipelines | BACKSTOP | Explicitly "PHYSICAL host ceiling" |
| 29 | `MAX_CONCURRENT_DELEGATIONS` (interactive/autonomous) | `delegation_limits.py:32,43` | 4 / 8 | Per-turn delegation fan-out width | **TUNING LEVER** | Constrains legitimate wide fan-out, not just abuse |
| 30 | `GOVERNOR_ACQUIRE_TIMEOUT_SECONDS` | `delegation_limits.py:67` | 45.0 | Wait for concurrency slot before fail-fast | BACKSTOP | Deadlock avoidance |
| 31 | `MAX_DELEGATION_ATTEMPTS_PER_TURN` | `delegation_limits.py:72` | 12 | Cumulative delegate() attempts/turn | BACKSTOP | "Above the structural depth×width×ladder bound" |
| 32 | `MAX_LIVE_SESSIONS` | `delegation_limits.py:78` | 8 | Concurrently-live named owl sessions | **UNCLEAR** | Soft cap on assumed usage, not measured pathology |
| 33 | `SESSION_IDLE_TTL_SECONDS` | `delegation_limits.py:84` | 1800.0 | Idle session reap time | BACKSTOP | Leak prevention |
| 34 | `TTL_SECONDS` (sticky route cache) | `sticky_route_cache.py:32` | 300 | How long a routed owl is reused w/o re-asking the router | **TUNING LEVER** | Self-admitted: "shrunk... Tune against CM-2" — explicit behavior-shaping dial |
| 35 | `_TIMEOUT_HEALTH_WINDOW_S`/`_TIMEOUT_DEGRADED_THRESHOLD` | `owls/guards.py:30,33` | 300.0 / 3 | Rolling "owl degraded" health window | BACKSTOP | Reports health, doesn't block a turn |
| 36 | `_ROUTING_MAX_TOKENS` | `owls/router.py:29` | 64 | Max tokens for the router's owl-pick call | BACKSTOP | Already sets `disable_thinking=True` |
| 37 | `_DERIVE_MAX_TOKENS` | `pipeline/acceptance_llm.py:35` | 64 | Max tokens for acceptance-expectation extraction | **TUNING LEVER / LIVE LANDMINE** | Does NOT set `disable_thinking` — identical bug shape to the 2026-07-22 incident, unpatched here |
| 38 | `_APOLOGY_MAX_TOKENS` | `delivery_gate.py:1066` | 60 | Max tokens for fallback-apology generation | BACKSTOP | Already sets `disable_thinking=True` |
| 39 | `MAX_TURN_NUDGES` | `pipeline/supervisor.py:16` | 6 | Ceiling on give-up-judge corrective nudges/turn | BACKSTOP | "A tool-spamming weak model would otherwise nudge forever" |
| 40 | `NO_PROGRESS_THRESHOLD` | `pipeline/progress_tracker.py:16` | 3 (2 lean-window) | Consecutive zero-progress dispatches before bounce | BACKSTOP | Containment-only, adaptive |
| 41 | `_WAIT_TIMEOUT_S` (clarify) | `pipeline/budget/callback.py:23` | 120.0 | Max wait for human Raise/Stop answer | **UNCLEAR** | Human-response timeout, different category from model-behavior caps |
| 42 | `_REQUEST_CAP`/`_DRAFT_CAP`/`_TOOLS_CAP` | `pipeline/persistence.py:82-84` | 2000/2000/40 | Truncation of the give-up-judge's own prompt | BACKSTOP | Bounds the judge's prompt, not the user-facing answer |
| 43 | `_MIN_SUBSTANCE_WORDCHARS` | `delivery_gate.py:424` | 20 | Floor below which a URL-heavy answer is "gutted"/floored | **UNCLEAR** | Quality gate, not length/time/cost per se, but affects delivery |
| 44 | `MIN_SCHEDULED_INTERVAL_SECONDS` | `owls/owl_schedule_guards.py:35` | 300.0 | Floor on scheduled-owl firing frequency | BACKSTOP | "Jetson-safe" hardware floor, refuses rather than clamps |
| 45 | `MAX_SCHEDULED_OWLS` | `owl_schedule_guards.py:37` | 5 | Per-user quota of standing scheduled owls | **TUNING LEVER** | Arbitrary policy quota, not hardware-bound |
| 46 | `MAX_CONSECUTIVE_FAILURES` | `owl_schedule_guards.py:39` | 3 | Scheduled-owl circuit breaker | BACKSTOP | Genuine-failure-only trigger |
| 47 | `_MAX_ANCESTOR_WALK` | `pipeline/durable/delegation_link.py:44` | 64 | Cap on walking a delegation parent-chain | BACKSTOP | "Far above MAX_DELEGATION_DEPTH=2" — pure cycle protection |
| 48 | `_MAX_ATTEMPTS`/`_RETRY_INTERVAL_MINUTES`/`_RETRY_INTERVAL_CAP_MINUTES` | `memory/retry_queue_store.py:35,36,41` | 3 / 1 / 10 | Retry cadence for a previously-floored turn | BACKSTOP | Bounded exponential backoff on a real prior failure |

## Redundant/overlapping-limit flags

**A. Output-token ceiling — four uncoordinated layers (the most important finding).** `config/provider.py:84` (250000) → `_output_cap()` → `_CONVERSATIONAL_MAX_TOKENS` (4096, tool-free ONLY) is one chain; `OwlAgentManifest.max_tokens` (4096, client-side word-count, every turn) is a SEPARATE, uncoordinated chain that only stops *consuming* the stream, never tells the provider to generate less. **Consequence: the 2026-07-22 fix only patches the tool-free path. Any TOOL-USING turn still requests up to `_output_cap()` (tens of thousands of tokens) — the exact "hi → 205s/$0.14" shape is still reachable today via a tool-using turn.**

**B. Wall-clock timeout — five layers, one inversion.** `DEFAULT_TURN_MAX_TIME_S=120.0` (whole turn) wraps `OwlAgentManifest.timeout_seconds=400.0` (single stream item) — the outer ceiling is TIGHTER than the inner one it wraps. The item timeout was live-incident-driven and widened (30→60→400); the turn-level ceiling around it was never revisited to match. `_ROUND_DEADLINE_FALLBACK_S=120.0` shares the same unverified-tightness pattern. `config/provider.py:71` `timeout_seconds=30.0` is dead code sitting alongside these live ones.

**C. Small-fixed-token judge/classifier calls — repeated shape, one still-live landmine.** `_ROUTING_MAX_TOKENS` and `_APOLOGY_MAX_TOKENS` both already learned the 2026-07-22 lesson (`disable_thinking=True`). `_DERIVE_MAX_TOKENS` (`acceptance_llm.py`) does NOT — same root cause, unpatched, lower severity (fails closed rather than hangs, but "acceptance verification silently stops working").

**D. Good counter-example.** `tool_max_iterations` is deliberately derived FROM `DEFAULT_TURN_MAX_STEPS` specifically to close a past redundancy bug where the two used to disagree by +10. This single-source-of-truth-with-dependents pattern is what the other overlapping pairs should follow.

## Overall recommendation

Of 48 items: **~10-12 look like tuning levers** worth raising/removing/replacing with prompt-driven shaping — `_CONVERSATIONAL_MAX_TOKENS` (#3), `OwlAgentManifest.max_tokens` (#4), `HARD_TOOL_COUNT_CAP` (#20), `LEAN_WINDOW_THRESHOLD` (#26), `MAX_DELEGATION_DEPTH` (#27), `MAX_CONCURRENT_DELEGATIONS` (#29), sticky-route `TTL_SECONDS` (#34), `_DERIVE_MAX_TOKENS` (#37, needs `disable_thinking` at minimum), `MAX_SCHEDULED_OWLS` (#45), plus two UNCLEAR-but-suspect timeouts (#7, #13).

**~34 are legitimate backstops** that should stay: cycle guards, hardware ceilings, prompt-truncation mechanics, probe timeouts, circuit breakers keyed on genuine repeated *failure* rather than volume of legitimate use. `DEFAULT_TURN_MAX_STEPS`, `MAX_INFLIGHT_PIPELINES`, `SAME_TOOL_FAILURE_THRESHOLD` already carry exactly the "generous, pathology-only" documentation this platform should standardize on.

Flags A and B are the highest-priority structural issue — not 48 independent numbers, but ~2 conceptual ceilings (max output, max turn duration) each implemented 3-5 times at different layers with different values and different isolated-patch histories. Consolidating each into one owned source of truth (mirroring `tool_max_iterations`/`DEFAULT_TURN_MAX_STEPS`) prevents the next incident being "a different layer's copy of the same number was wrong."

## Confidence note + known gaps

High confidence on all current values (verified against source, not assumed — several listed as "candidates" in the task brief had already changed by the time this ran). Medium confidence on the BACKSTOP/LEVER split for delegation-width/session-count items (#29, #32) — code-comment intent only, no real usage telemetry. Did not read `scheduler/`, `notifications/`, `channels/*` adapters, or `parliament/` for turn-shaping constants — `scheduler/handlers/` likely has its own per-job timeouts worth a follow-up pass if consolidation extends to scheduled/proactive turns. `providers/registry.py`/`tier_selector.py` confirmed to hold NO per-tier numeric ceilings — tier selection is purely round-robin/health-based.
