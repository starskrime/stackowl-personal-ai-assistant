# Duplication Report

Synthesized from 11 Phase-1 subagent reports. Several Phase-0 "candidate duplications" turned out, on deep tracing, to be legitimate layering — flagged explicitly below as **RESOLVED: not duplication**, because knowing what ISN'T a problem is as important as knowing what is.

---

## 1. CONFIRMED DUPLICATION — Output-token ceiling, 4 uncoordinated layers

**Locations:**
- `config/provider.py:84` `max_output_tokens=250000` (provider-level backstop)
- `providers/openai_provider.py:1038-1099` `_output_cap()` (window-headroom math)
- `pipeline/steps/execute.py:145` `_CONVERSATIONAL_MAX_TOKENS=4096` (tool-free-turn override, added 2026-07-22)
- `owls/manifest.py:73` `OwlAgentManifest.max_tokens=4096` (client-side word-count cutoff in `OwlResourceGuard.stream()`)

**Why they diverged:** the first three form one coordinated chain (provider default → window-aware cap → today's tool-free override). The fourth is a completely separate, older mechanism that only stops *consuming* an already-generated stream after ~4096 words — it never tells the provider to generate less. Today's incident fix patched only the tool-free path (items 1-3's newest link). **A tool-using turn still requests up to `_output_cap()` — tens of thousands of tokens — from the provider.** The exact "hi → 205s/$0.14" failure shape is still reachable today via any tool-using turn with a verbose/reasoning model.

**Verdict:** real duplication, not specialization. Consolidate into one owned ceiling concept with the client-side guard as a true backstop, not a second independent decision-maker.

## 2. CONFIRMED DUPLICATION — Wall-clock timeout, 5 layers, 1 inversion

**Locations:**
- `authz/bounds.py:50` `DEFAULT_TURN_MAX_TIME_S=120.0` (whole-turn ceiling)
- `owls/manifest.py:79` `OwlAgentManifest.timeout_seconds=400.0` (single stream-item stall timeout, lives INSIDE a turn)
- `providers/openai_provider.py:182` / `anthropic_provider.py:75` `_ROUND_DEADLINE_FALLBACK_S=120.0`
- `config/provider.py:71` `timeout_seconds=30.0` — **dead code, never read anywhere**

**Why this matters:** the 120s whole-turn ceiling is *tighter* than the 400s single-item timeout nested inside it. The item timeout was live-incident-driven and widened twice (30→60→400) as real reasoning turns proved it too tight; the turn-level ceiling wrapping it was never revisited to match. This is architecturally the same failure that just happened with the output cap — a number patched in isolation without checking what it interacts with. The dead 30s config value is a landmine for an operator who'd reasonably expect setting it in `stackowl.yaml` to do something.

**Verdict:** real duplication/inconsistency. Same remediation pattern as #1.

## 3. CONFIRMED DUPLICATION — Learning: DNA/evolution vs failure/tool-outcome mining

**Locations:** `owls/evolution.py` (`EvolutionCoordinator`) vs `learning/failure_outcome_miner.py` + `learning/tool_outcome_miner.py`

**Verified: zero code-level data flow in either direction** (confirmed by a targeted grep across both packages for cross-imports — zero hits). Both independently mine `TaskOutcomeStore`, both independently re-implement the exact same "positive-only learning" filter policy (never learn from failures) as two separate functions on two separate query methods, and both deliver into the live turn through **two disjoint prompt-injection seams** (DNA→`assemble.py`'s persona block; lessons→`classify.py`'s memory-context block) that never reference each other.

**Why this happened:** not obviously legitimate specialization — the *concern* ("make the owl better from outcomes") and the *data source* (`TaskOutcomeStore`) are the same; only the *target* differs (personality traits vs. tool-usage knowledge). Scheduling is coordinated only by clock-slot spacing to avoid collision, not by any data dependency.

**Verdict:** real duplication of concern and machinery, legitimate divergence only in output target. Worth a shared "learning event" abstraction that both consume from, even if what they DO with it differs.

## 4. CONFIRMED BUG (not duplication, but load-bearing) — `surface_persistence_handoff`'s fix is discarded by the next gate

**Location:** `pipeline/delivery_gate.py:1475` (handoff) → `pipeline/delivery_gate.py:329` (giveup floor), both called in order from `backends/shared.py:96-141`.

The module's own docstring claims a successful hand-off prevents the giveup-floor gate from re-firing. **Verified false by direct reproduction against the real functions.** The handoff's success path evolves the *original* state rather than a cleared copy, so `consequential_failures`/`recovered_consequential` (stamped before any gate runs) survive unchanged into the very next gate, which reads only those ledger fields — never `state.responses` — and re-detects the same give-up, silently overwriting the handoff's real answer with a canned floor. Zero test coverage for the chained interaction (only the handoff in isolation is tested).

**This is the platform's ONLY non-mechanical "try to fix it before giving up" rung** (every other gate's fix attempt is a bounded retry/tool-call), and it is dead in the normal production path whenever `execute` stamped a consequential snapshot. **Recommend fixing this ahead of / independent from the broader consolidation work below — it's a small, well-understood, high-value fix, not an architecture change.**

## 5. RESOLVED: NOT duplication — "which owl handles this" routing (3 layers)

**Phase 0 flagged:** `gateway/scanner.py` (`GatewayScanner`) vs `gateway/turn_router.py` (`TurnRouter`) vs `owls/router.py` (`SecretaryRouter`).

**Deep trace verdict:** clean precedence chain, not competing decision-makers.
- `GatewayScanner` owns explicit/structural addressing (`@mention`, DM vocative) — runs once, before any `PipelineState` exists.
- `pipeline/steps/triage.py` (the pipeline's own first step) is a pure downstream *validator* of the scanner's decision when one was made, and only invokes `SecretaryRouter` (LLM semantic routing) or the sticky-route cache when the scanner deferred to the "secretary" default.
- `TurnRouter` is not an owl-routing decision at all — it only fires when a turn is already RUNNING for the session, deciding STOP/STEER/NEW; a NEW verdict re-invokes the SAME scanner, not a third decision engine.

**No consolidation needed.** This is legitimate layering: syntactic addressing → semantic addressing → in-flight interruption handling, each owning a genuinely different question.

## 6. RESOLVED: NOT duplication — loop/repetition guards in execute.py

**Phase 0 flagged:** `LoopGuard` (`providers/_react.py`) vs the degenerate-repetition regex (`execute.py`) vs `OwlResourceGuard`'s word-count cutoff.

**Deep trace verdict:** three guards, three genuinely different failure modes, at two different layers. `LoopGuard` catches a model re-calling the identical tool with identical args (tool-loop branch only). The degenerate-repeat counter catches a model stuck emitting the same short raw text unit — a stream-level pathology the tool-loop's guard cannot see since no tool call is involved (plain-stream branch only). The word-count cutoff is an unrelated total-length ceiling. **No consolidation needed** — legitimate specialization by branch and failure shape.

## 7. RESOLVED: NOT dead code — `LangGraphBackend`

**Phase 0 flagged as a possible dead/vestigial duplicate of `AsyncioBackend`.**

**Deep trace verdict: live, reachable via ordinary config (`orchestrator.backend: langgraph`), off by default, with real parity test coverage.** Both drive the exact same `PIPELINE_STEPS` functions from `registry.py` (single-sourced, no drift risk in step *logic*). What genuinely duplicates: ~90-110 lines of `run()`-wrapper boilerplate (contextvar binding, deadline handling, decision-ledger persistence) hand-copied between the two backend files rather than shared via `backends/shared.py`. Both files self-document this as "parity maintenance," and it has already caused one documented drift bug (FR-13, acceptance-verification gap).

**Verdict:** legitimate alternate-execution-path duplication (two real engines, by design), but the WRAPPER boilerplate around them should be extracted into `shared.py` alongside the gate cascade it already owns — same remediation shape as items 1/2, applied to code instead of numbers.

## 8. Isolation, not duplication — provider-layer resilience vs. app-level retry

**Not competing implementations of the same thing — genuinely disconnected layers with no shared context.** The provider layer (8 distinct retry/circuit-breaking mechanisms: SDK auto-retry, `resilient_round`'s single-attempt classify+record, circuit-breaker adaptive backoff, circuit-breaker rate-limit cooldown, same-tier retry-once, tier cascade, cross-provider-in-tier cascade, rate-limiter penalty) makes every decision from purely local state. The app-level `RetryQueueStore` (goal-level, DB-backed, capped @3) never threads its attempt count or banned-capability history down into the provider layer, and the provider layer's `trace_id` — the only thing that crosses the boundary — is used exclusively for cost/log correlation, **never read by any retry decision**.

**This isn't wasted duplicate code — it's a missing coordination signal.** A request can, in principle, be independently retried by the SDK, `resilient_round`, the same-tier retry, the tier cascade, AND the app-level retry queue, with no single layer aware of how many times the others have already tried. Worth deciding whether that's acceptable (defense in depth) or whether attempt-count context should thread through — a design question, not a straightforward "delete the duplicate."

## 9. Complexity, not duplication — classify.py + assemble.py's four independent lean gates

**Not competing implementations, but the same signal (`is this turn tool-free/conversational`) re-derived independently four times** instead of computed once and threaded through: `classify.py`'s `_lean` (`TOOL_FREE_CLASSES` check), `assemble.py`'s own window-based `lean`, and three more separate `TOOL_FREE_CLASSES` re-checks scattered through `assemble.py` (`skills_block`, `describe_tool_protocol`, `capabilities.tools_enabled`). Combined with 9 top-level fetch operations in `classify.py` (one dead) and 7 more in `assemble.py`, all `await`ed sequentially with no `asyncio.gather` anywhere in either file.

**Verdict:** not "duplicate subsystems" in the delete-one sense — genuinely different data each gather fetches — but a strong candidate for (a) computing the lean/tool-free decision ONCE and threading it as a state field instead of re-deriving, and (b) parallelizing the independent I/O-bound gathers.

## 10. Missing capability, not duplication — `read_logs`

**Confirmed twice, independently** (Phase 0 + the self-observability subagent): CLAUDE.md documents a `read_logs` tool for the AI to query its own logs directly, with example queries. It does not exist anywhere in the codebase (zero grep hits in any `.py` file). **Actively false documentation** a future session will act on and fail.

**What actually exists instead:** `classify.py`'s `_gather_recent_actions` — a much narrower, session-scoped, last-3-outcomes window, gated to only `standard`-intent turns. Retry exhaustion and scheduled-job circuit-breaker trips bypass this entirely (raw `send_text`, never touches `task_outcomes`) — genuinely invisible to any future turn, not just under-surfaced.

---

## Summary table

| # | Concern | Verdict | Priority |
|---|---|---|---|
| 1 | Output-token ceiling, 4 layers | Real duplication | High — same failure class as today's incident, still reachable via tool-using turns |
| 2 | Wall-clock timeout, 5 layers + 1 inversion | Real duplication | High — same pattern, dead config value is a landmine |
| 3 | DNA/evolution vs learning/mining | Real duplication | Medium — works today, but doubles maintenance surface for the same concern |
| 4 | Persistence-handoff fix discarded | **Confirmed live bug** | **Highest — fix independently of consolidation work** |
| 5 | 3-layer owl routing | Not duplication | — |
| 6 | 3 loop/repetition guards | Not duplication | — |
| 7 | Dual orchestration backends | Legitimate (wrapper boilerplate should still consolidate) | Medium |
| 8 | Provider vs app-level retry isolation | Design gap, not duplicate code | Medium — decide intentionally, don't just delete |
| 9 | classify/assemble's 4 lean gates + no parallelization | Complexity, not duplication | Medium |
| 10 | `read_logs` doesn't exist | Missing capability + false docs | High — directly answers "provider is blind" |
