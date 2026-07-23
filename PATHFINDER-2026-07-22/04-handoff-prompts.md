# Handoff Prompts

Copy any of these directly into `/make-plan`. Ordered by the priority in `03-unified-proposal.md` — Proposal 0 is a standalone bugfix, independent of the rest; Proposals 1-2 share a root cause with today's live incident; 3-5 are lower-urgency consolidation.

---

## Prompt: Proposal 0 — fix the persistence-handoff bug

```
Fix a verified, reproduced bug in StackOwl's delivery-gate cascade: surface_persistence_handoff
(src/stackowl/pipeline/delivery_gate.py:1475) successfully hands a would-be give-up off to a
better-fit owl and delivers its real answer, but the VERY NEXT gate in the cascade,
surface_consequential_giveup_floor (delivery_gate.py:329, called in fixed order from
backends/shared.py:96-141), re-derives a stale give-up verdict from state.consequential_failures/
recovered_consequential — fields stamped by execute.py's snapshot BEFORE any delivery gate runs —
and silently overwrites the handoff's real answer with a canned honest floor. Verified by direct
reproduction against the real (non-test-double) functions with a production-shaped PipelineState
(consequential_snapshot_taken=True):
  AFTER HANDOFF:           "It is 24C and sunny." (is_floor: [False])
  AFTER GIVEUP-FLOOR GATE:  "I couldn't fully complete this: ... capability that failed: send_email..." (is_floor: [True])

Fix: when surface_persistence_handoff's delegation succeeds, stamp the evolved state so the
giveup-floor gate immediately after it doesn't re-fire on stale ledger data — e.g.
state.evolve(responses=(chunk,), consequential_failures=(), recovered_consequential=True) instead
of the current state.evolve(responses=(chunk,)) alone. Verify recovered_consequential (or
whichever field decide_delivery() in delivery_gate.py actually reads) is the right signal by
reading decide_delivery()'s full implementation first — the fix must use the SAME mechanism the
codebase already relies on elsewhere for "this failure was resolved," not a new flag.

Call sites to check: delivery_gate.py's surface_persistence_handoff (success path, ~line 1582)
and surface_consequential_giveup_floor / decide_delivery (~line 329 and wherever decide_delivery
is defined). Add a test chaining BOTH gates in the same call (tests/pipeline/test_persistence_handoff.py
currently only tests the handoff in isolation — this is why the bug had zero coverage). Also check
whether langgraph_backend.py's gate cascade (same run_delivery_gate call) needs the identical fix
verified against its own state-threading.

Anti-pattern guards: do not add a new "handoff succeeded" flag if an existing ledger field already
means the same thing — read decide_delivery() fully before choosing the fix. Do not weaken
surface_consequential_giveup_floor's detection to make this pass — it must still catch a REAL
unrecovered failure, only a successfully-handed-off one should be exempted.

Reference flowchart: PATHFINDER-2026-07-22/01-flowcharts/delivery-gates-honesty-floors.md
```

---

## Prompt: Proposals 1+2 — one output-token ceiling, one wall-clock timeout authority

```
Consolidate StackOwl's output-token and wall-clock-timeout limits, each currently implemented
independently at multiple layers with a documented history of being patched in isolation (most
recently: a same-day incident where pipeline/steps/execute.py's _CONVERSATIONAL_MAX_TOKENS went
1024→broke every conversational reply→4096, because nothing accounted for the model's reasoning
token usage, and separately owls/manifest.py's OwlAgentManifest.max_tokens=4096 has ALWAYS been a
fully independent client-side-only guard that never bounds what the provider is told to generate).

PART A — output-token ceiling. Today: config/provider.py:84 max_output_tokens=250000 (backstop,
correct) feeds providers/openai_provider.py's _output_cap() (window-aware, correct) feeds
pipeline/steps/execute.py:145 _CONVERSATIONAL_MAX_TOKENS=4096 — but ONLY on the plain-stream path
(_open_stream, execute.py ~line 2687). The tool-loop path (_run_with_tools → _call_default,
execute.py ~line 1872) passes no max_tokens override at all and silently inherits the FULL
_output_cap() value even for a trivial tool-using turn — the exact "hi took 205s" failure shape is
still reachable today via any tool-using turn with a verbose model. Fix: introduce one function
(e.g. resolve_turn_output_ceiling(intent_class, uses_tools) -> int | None) that BOTH call sites use.
Raise owls/manifest.py's max_tokens to a value that's a pure backstop above whatever this function
returns (or have OwlResourceGuard read the resolved ceiling directly instead of a separately
configured manifest field) so it stops being a second, uncoordinated shaping decision.

PART B — wall-clock timeout. authz/bounds.py:50 DEFAULT_TURN_MAX_TIME_S=120.0 (whole turn) wraps
owls/manifest.py:79 timeout_seconds=400.0 (single stream item, INSIDE that same turn) — the outer
ceiling is TIGHTER than what it contains, because the item timeout was live-incident-widened twice
(30→60→400) and the turn-level ceiling around it was never revisited. providers/openai_provider.py's
and anthropic_provider.py's _ROUND_DEADLINE_FALLBACK_S=120.0 shares the same unverified-tightness
pattern. config/provider.py:71 timeout_seconds=30.0 is DEAD CODE (grep confirms it's never read
anywhere) sitting alongside these live values. Fix: derive DEFAULT_TURN_MAX_TIME_S so it can never
be tighter than OwlAgentManifest.timeout_seconds (mirror how config/provider.py:92
tool_max_iterations is ALREADY correctly derived from authz/bounds.py:51 DEFAULT_TURN_MAX_STEPS "so
the two bounds agree by construction" — use that exact pattern). Delete config/provider.py:71's
dead timeout_seconds field entirely rather than leave it as a landmine.

Call sites: pipeline/steps/execute.py (_call_default's _extra dict construction, _open_stream and
its caller), owls/manifest.py, authz/bounds.py, config/provider.py, providers/openai_provider.py,
providers/anthropic_provider.py.

Anti-pattern guards: do not add a config flag to choose between the old and new cap behavior — pick
the one correct value per turn shape and delete the others. Do not raise any cap so high it
effectively becomes "no limit" again (that's the OTHER failure mode from the same incident, in the
same file, on the same day). Test both the "hi" conversational case AND a tool-using conversational-
adjacent case to confirm the gap closes.

Reference: PATHFINDER-2026-07-22/05-numeric-limits-census.md (flags A and B),
PATHFINDER-2026-07-22/01-flowcharts/execute-step-tool-loop.md
```

---

## Prompt: Proposal 3 — merge the duplicated positive-only-learning filter

```
StackOwl has two independent, non-communicating learning pipelines that both mine
TaskOutcomeStore and both independently re-implement the same "never learn from a failed
outcome" filter policy: owls/dna_attribution.py's DnaAttributor (_filter_scored_outcomes,
~lines 99-114) and learning/tool_outcome_miner.py's ToolOutcomeMiner (its own equivalent filter,
~lines 109-113). Verified zero cross-package imports in either direction (owls/ never imports
learning/, learning/ never imports owls/) — this is genuine duplication of the filter LOGIC, not
of the pipelines' purpose (DNA trait mutation vs. tool-heuristic/lesson mining are legitimately
different outputs and should stay separate).

Fix: extract ONE shared helper — e.g. TaskOutcomeStore.iter_positive_signal(scope: "owl" |
"global", since_epoch) — used by both DnaAttributor.attribute() (owl-scoped call) and
ToolOutcomeMiner.mine() (global-scoped call), replacing their two independent filter
implementations with one. Do NOT merge the two pipelines' downstream logic (DNA mutation vs.
lesson/heuristic authoring) — those are genuinely different concerns delivered through two
different prompt-injection seams (assemble.py's DNA block vs. classify.py's lessons-search
block) and should remain exactly as separate as they are today.

Call sites: src/stackowl/owls/dna_attribution.py, src/stackowl/learning/tool_outcome_miner.py,
src/stackowl/memory/outcome_store.py (where the new shared method should live, alongside the
existing list_scored_for_owl/list_scored_for_owl_global/list_failed_global methods it should
probably compose or replace).

Anti-pattern guards: do not build a generic "learning pipeline registry" or plugin system — this
is a two-caller helper extraction, nothing more. Do not change either pipeline's scheduling
(evolution_batch@02:00, tool_outcome_miner@05:00 stay independent, uncoordinated by design).

Reference flowchart: PATHFINDER-2026-07-22/01-flowcharts/learning-dna-evolution.md
```

---

## Prompt: Proposal 4 — extract duplicated backend run()-wrapper boilerplate

```
StackOwl has two turn-orchestration backends (pipeline/backends/asyncio_backend.py's
AsyncioBackend, default; pipeline/backends/langgraph_backend.py's LangGraphBackend, config-gated
opt-in via OrchestratorSettings.backend, real and tested but off by default) that both drive the
EXACT SAME PIPELINE_STEPS functions from pipeline/registry.py (correctly single-sourced, no
action needed there). What IS duplicated near-verbatim between the two files' run() methods
(~90-110 lines total, NOT shared via backends/shared.py despite that file already owning the
gate-cascade logic both backends call):
  - contextvar bind/reset sequence (TraceContext, lesson_context, recovery_context,
    tool_outcome_ledger, decision_ledger, human_wait_ctx) — asyncio_backend.py:111-137 vs
    langgraph_backend.py:117-143
  - interactive-deadline computation — asyncio_backend.py:166-171 vs langgraph_backend.py:152-157
  - finally-block: recovery-summary log, decision-ledger persist to TurnDecisionStore,
    contextvar resets — asyncio_backend.py:252-292 vs langgraph_backend.py:202-241
Both files self-document this as "parity maintenance" and it has ALREADY caused one documented
drift bug (comment cites "FR-13 gap fix" as a past manual re-sync after divergence).

Fix: move the three duplicated blocks above into pipeline/backends/shared.py as functions both
backends call (e.g. bind_turn_contexts(state), compute_interactive_deadline(state),
persist_decision_ledger(state)). Leave AsyncioBackend's and LangGraphBackend's genuinely different
piece — how each drives PIPELINE_STEPS (a plain loop vs. a compiled StateGraph) — untouched. Also
leave LangGraphBackend's one FORCED difference in its deadline-floor handling (it must manually
re-invoke run_delivery_gate + deliver.run because its graph's own "deliver" node dies with the
cancelled task on timeout, whereas AsyncioBackend just falls through) as its own small callback —
that difference is real, not copy-paste.

Call sites: pipeline/backends/asyncio_backend.py (full run() method), pipeline/backends/
langgraph_backend.py (full run() method), pipeline/backends/shared.py (new home for the
extracted functions, alongside run_delivery_gate).

Anti-pattern guards: do not introduce a base-class/inheritance hierarchy between the two backends
"for flexibility" — extract plain shared functions, since that's the pattern shared.py already
uses for the gate cascade. Do not change PIPELINE_STEPS or either backend's step-driving
mechanism. Existing parity tests (tests/pipeline/backends/test_langgraph_backend_deadline.py,
test_asyncio_backend_deadline.py, and siblings) must still pass unchanged in behavior — this is a
pure extraction, not a behavior change.

Reference flowchart: PATHFINDER-2026-07-22/01-flowcharts/turn-pipeline-orchestration.md
```

---

## Prompt: Proposal 5 — close the self-observability gap (read_logs + wider outcome capture)

```
StackOwl's CLAUDE.md documents a read_logs tool ("The AI assistant can also query logs directly
via the read_logs tool") with example queries (errors in the last hour, what a specific tool
received/returned, slowest tool calls). This tool does NOT exist anywhere in the codebase —
confirmed by `grep -rn "read_logs" src/` returning zero hits, independently verified twice during
this audit. A future session reading CLAUDE.md will believe it has this capability, attempt to use
it, and fail — or worse, assume logs are unqueryable at all.

The ONE mechanism that actually loops a failure back into the model's own context is
classify.py's _gather_recent_actions (lines 278-343): reads TaskOutcomeStore.recent_for_session
(session-scoped, last 3, gated to intent_class=="standard" turns only) and injects a "## What You
Did Recently" block. Two failure modes never reach it at all: (1) retry exhaustion — when a
retry_queue row hits its 3rd failed attempt, retry_actuator.py's _notify_gave_up sends the "still
couldn't" notice via a RAW adapter.send_text() call that completely bypasses the pipeline (no
PipelineState, no _capture_outcome, no task_outcomes row); (2) a provider circuit breaker opening
during a scheduled/proactive job that never constructs a user-facing PipelineState.

Decide and implement ONE of, or both:
1. Build read_logs as a real tool matching CLAUDE.md's existing documented shape (query by
   trace_id / time range / level / tool name against ~/.stackowl/logs/stackowl.jsonl) — OR remove
   the false claim from CLAUDE.md if it's deliberately out of scope. Leaving it documented-but-
   absent is worse than either choice.
2. Route retry-exhaustion and scheduled-job circuit-breaker-open events through _capture_outcome
   (or an equivalent that writes to task_outcomes) so they become visible to classify.py's
   existing recent-actions surfacing instead of vanishing after a one-time user-facing apology.

Call sites for #1: src/stackowl/tools/ (new tool, following the existing Tool/ToolManifest pattern
seen throughout that package), src/stackowl/infra/observability.py (the JSONL writer/reader
target). Call sites for #2: src/stackowl/pipeline/retry_actuator.py (_notify_gave_up,
~lines 345-362), src/stackowl/providers/circuit_breaker.py / _resilient_round.py (the
CircuitOpenError raise site for a non-turn context), src/stackowl/pipeline/backends/shared.py
(_capture_outcome, the existing write path to mirror).

Anti-pattern guards: if building read_logs, do not give it unbounded/unscoped log access — mirror
existing tool patterns for owner-scoping and truncation (see how other tools in src/stackowl/tools/
bound their output). If widening _capture_outcome's call sites, do not change what
_should_surface_failure_history gates on (classify.py:160-171) without checking whether that
gate's intent-class restriction should also loosen — these events may need their own surfacing
condition, not necessarily the same one ordinary in-turn floors use.

Reference flowchart: PATHFINDER-2026-07-22/01-flowcharts/self-observability.md
```

---

## Not handed off — flagged for your product decision, not a code-change prompt

The census (`05-numeric-limits-census.md`) flags several values as tuning levers rather than
backstops: `HARD_TOOL_COUNT_CAP` (pipeline/context_budget.py, 40 tool schemas/turn),
`LEAN_WINDOW_THRESHOLD` (owls/base_prompt.py, 8192), `MAX_DELEGATION_DEPTH`/
`MAX_CONCURRENT_DELEGATIONS` (owls/delegation_limits.py, 2/4 and 4/8), sticky-route cache
`TTL_SECONDS` (owls/sticky_route_cache.py, 300s), and `MAX_SCHEDULED_OWLS` (owls/
owl_schedule_guards.py, 5). These genuinely shape normal behavior by design — how wide should
delegation fan-out go, how long should routing stay sticky — and are product/policy calls, not
bugs or duplication. No prompt is offered for these; they're listed in the census for you to
decide case by case.
