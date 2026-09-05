# Hermes → StackOwl Capability Map

**Purpose.** Hermes Agent (NousResearch, commit `689b51bef`) is the reference architecture.
This document maps *every* load-bearing Hermes capability against what StackOwl has today,
so we can walk it one item at a time and decide: adopt, adapt, keep-ours, or drop.

**Status.** Mapping complete. No implementation started. No code changed.

**How to use this.** Each item has a stable ID (`D03.2`). Work top-to-bottom or jump by ID.
For each item I will ask you the decision questions listed under **Ask**. Your answer becomes
the design record. Do not answer them here — we walk them together.

---

## Verdict legend

| Verdict | Meaning |
|---|---|
| `CONFLICT` | StackOwl actively does the **opposite** of a Hermes law. Must be resolved before anything downstream. |
| `MISSING` | StackOwl has nothing in this space. |
| `PARTIAL` | Exists but materially narrower, weaker, or unreachable. |
| `DIVERGENT` | Both have it, designed differently. Needs an explicit choose-one decision. |
| `PARITY` | Comparable. Low priority. |
| `AHEAD` | StackOwl has something Hermes does not. Candidate to **keep**, not replace. |

**Counts:** 2 `CONFLICT` · 42 `MISSING` · 28 `PARTIAL` · 6 `DIVERGENT` · 16 `PARITY` · 16 `AHEAD` — **110 items across 18 domains**.

Read that distribution honestly: 42 missing + 28 partial is a large surface, but 16 `PARITY` +
16 `AHEAD` means **29% of the map is already at or above the reference**. This is not a rewrite from
zero. The two `CONFLICT` items are small in code and enormous in consequence — they are the reason
StackOwl costs more per turn than it should, and they gate the value of most of the `MISSING` work.

---

## The two laws (the frame for everything below)

Hermes states two laws at the top of its contributor guide and reviews every change against them.
They are not style preferences — they are the reason its architecture looks the way it does.

**Law 1 — Per-conversation prompt caching is sacred.** A long conversation reuses a cached
prefix every turn. Anything that mutates past context, swaps toolsets, or rebuilds the system
prompt mid-conversation invalidates the cache and multiplies cost. The only sanctioned
exception is context compression.

**Law 2 — The core is a narrow waist; capability lives at the edges.** Every model tool ships
on every API call, so a new *core* tool is the most expensive addition possible. The product
grows aggressively — at the edges, never at the waist.

**The Footprint Ladder** (their new-capability decision procedure — take the highest rung that works):

1. Extend existing code
2. CLI command + skill
3. Service-gated tool (`check_fn`)
4. Plugin
5. MCP server in the catalog
6. New core tool — last resort

> **StackOwl has neither law and no ladder.** That is the single biggest structural difference,
> and it is upstream of at least six of the conflicts below. Domain 01 exists to settle it first.

---

# D01 · Prompt economics & caching

> The highest-leverage domain. Every `CONFLICT` in the document lives here or is caused by here.

### D01.1 · System-prompt stability — `CONFLICT`
**Hermes.** The system prompt is built **once per session** (`agent/system_prompt.py`) and reused
for every turn. Three tiers — `stable` (identity/SOUL.md, tool guidance, skills prompt, environment
hints), `context` (caller message, AGENTS.md-style context files, workspace snapshot), `volatile`
(memory snapshot, USER.md, provider/model line). Even the "volatile" tier is **frozen at session
start**. Only context compression triggers a rebuild.
**StackOwl.** `pipeline/steps/assemble.py` rebuilds the entire system prompt **every single turn**:
persona + DNA injection + owls block + relevance-scored skills block + memory context + capability
banner, joined fresh each time. The prompt is a `PipelineState` field, not a session artifact.
**Gap.** Zero prefix reuse. Every turn pays full input price on a prompt that is mostly identical.
**Ask.** Can persona/DNA/skills/memory be resolved once at session start? What legitimately must
change mid-session, and can it move into the message stream instead of the prompt?

### D01.2 · Prompt-cache breakpoints — `MISSING`
**Hermes.** `agent/prompt_caching.py` — a deliberate Anthropic cache-breakpoint layout: 4
`cache_control` markers (static system prefix, end of system prompt, last 2 non-system messages),
falling back to 1 system + last 3 messages when no static prefix exists. Uniform TTL. Designed so
*new* sessions still reuse the stable system prefix across sessions.
**StackOwl.** No occurrence of `cache_control`, `cached_tokens`, or any caching concept anywhere in
`src/`. Not implemented, not configured, not measured.
**Gap.** Even after D01.1, no markers means no cache.
**Ask.** Which providers must this support? Do we adopt their 4-breakpoint layout verbatim?

### D01.3 · Per-turn tool-schema selection — `CONFLICT`
**Hermes.** The toolset is fixed for the life of a conversation. Changing toolsets mid-conversation
is explicitly listed as forbidden. Slash commands that mutate tool state default to **deferred**
invalidation (`--now` is opt-in).
**StackOwl.** `pipeline/context_budget.py` runs a **greedy relevance-ranked budgeter every turn**,
selecting which tools to present from ~60–78 registered, capped at `HARD_TOOL_COUNT_CAP = 150`.
The tools array therefore differs turn to turn.
**Gap.** A varying tools array invalidates the cached prefix on every turn, independently of D01.1.
The budgeter was built to solve a real problem (too many tools) — but it solves it the expensive way.
**Ask.** Does the tool-count problem survive if we adopt toolsets (D05.2) + progressive disclosure
(D05.4)? If so, the per-turn budgeter can be deleted rather than fixed.

### D01.4 · Deferred invalidation as a UX pattern — `MISSING`
**Hermes.** Any command that would mutate system-prompt state (install a skill, enable a tool,
change memory) takes effect **next session** by default, with `--now` for immediate. This is the
canonical pattern, documented as such.
**StackOwl.** Changes apply immediately everywhere; there is no notion of a deferred change.
**Gap.** No vocabulary for "this would cost you money right now".
**Ask.** Is deferred-by-default acceptable UX for your users, or does StackOwl's proactive/autonomous
posture need immediate application?

### D01.5 · Message-role alternation invariant — `MISSING`
**Hermes.** Hard invariant: never two same-role messages in a row, never a synthetic user message
injected mid-loop. Guarded in `agent/message_sanitization.py` with repair functions; violations
break strict providers outright. Even their self-nudge text has an explicit alternation guard.
**StackOwl.** No alternation invariant or repair pass found.
**Gap.** Latent provider-compat bug class, and a blocker for anything that injects mid-loop.
**Ask.** Have you seen provider rejections that could be this?

### D01.6 · Cost measurement per conversation — `PARTIAL`
**Hermes.** `agent/usage_pricing.py`, `credits_tracker.py`, `account_usage.py`, `/usage`,
`/insights [--days N]` — cost estimates, token consumption, model/platform breakdown from the
session DB.
**StackOwl.** `providers/cost_tracker.py` + `/cost` exist. No cache-hit-rate metric, no
per-conversation trend, no insights report.
**Gap.** Cannot measure whether D01.1–D01.3 actually worked.
**Ask.** Do we build the measurement *before* the fix so we have a baseline?

---

# D02 · Core agent shape

### D02.1 · One agent class, all surfaces — `DIVERGENT`
**Hermes.** A single `AIAgent` class (`run_agent.py`, ~60 ctor params) instantiated by CLI, TUI,
desktop, dashboard, ACP, gateway, cron, and subagents. There is no second agent implementation.
**StackOwl.** The turn engine is a **pipeline of 8 ordered steps** (`pipeline/registry.py`) driven
by `interaction/`, with services injected via `StepServices`. Different shape, same intent.
**Gap.** Not a gap — a genuine fork in the road. StackOwl's pipeline is more inspectable; Hermes'
class is more portable and far easier to fork for subagents/background review.
**Ask.** Is the 8-step pipeline load-bearing for you, or is it scaffolding around what is ultimately
a ReAct loop? Could steps become *phases inside* one agent object rather than a registry?

### D02.2 · Iteration budget — `MISSING`
**Hermes.** `agent/iteration_budget.py` — a thread-safe consume/refund counter per agent instance.
Parent cap `max_iterations` (default 90), subagent cap from `delegation.max_iterations` (default 50).
Plus a one-turn **grace call** so a budget-exhausted turn still produces an answer instead of
dying mid-loop.
**StackOwl.** No iteration cap found in the pipeline or the ReAct runner.
**Gap.** No bound on a runaway loop other than the tool-call breaker.
**Ask.** What is the right ceiling for an autonomous platform that is *supposed* to grind on long
objectives? Does the budget belong per-turn or per-objective?

### D02.3 · Interrupt / steer / redirect — `PARTIAL`
**Hermes.** Three distinct verbs on the agent: `interrupt()` (stop), `steer()` (inject guidance into
the *current* tool batch), `redirect()` (replace the goal). `/steer` drains **before** the next API
call so it lands on the current iteration rather than the next.
**StackOwl.** *(our side re-measured 2026-09-04 — the previous description named a gate
that no longer exists.)* Concurrent handling is a NON-BLOCKING intake in
`startup/orchestrator.py`: dispatch if idle, else ROUTE — STEER folds the message into the
running turn's mailbox, STOP halts it cooperatively, NEW becomes a queued turn — all under a
per-session intake lock plus `gateway/inflight_router`. The blocking `serialize_prior` gate is
GONE. So the steer/redirect distinction this entry calls missing DOES exist now; what remains
open is the pre-API drain.
**Gap.** Mid-turn course-correction is coarse.
**Ask.** Do you want to steer a running turn from Telegram, or is stop-and-resend enough?

### D02.4 · Turn finalization seam — `PARITY`
**Hermes.** `agent/turn_finalizer.py` — everything after the tool loop: budget-exhaustion summary,
trajectory save, session persist, diagnostics, response shaping.
**StackOwl.** `pipeline/steps/consolidate.py` + `turn_persist.py` + `deliver`.
**Ask.** None. Comparable.

### D02.5 · Acceptance authority / measured success — `AHEAD`
**Hermes.** Passive only: `agent/verification_evidence.py` records what was proved; `verification_stop.py`
turns that into a bounded follow-up when the model tries to finish right after editing code without
fresh evidence. Deliberately never blocks.
**StackOwl.** `pipeline/acceptance_authority.py`, `acceptance_llm.py`, `tools/verification.py`,
`ToolResult.verified` tri-state, `AcceptanceChecker` on GOAL. Success is **measured**, and it can veto.
**Ask.** Keep. Confirm it survives a rewrite — this is a StackOwl asset Hermes lacks.

### D02.6 · Recovery ladder — `AHEAD`
**Hermes.** `error_classifier.py` classifies API errors into retry / rotate-credential /
fallback-provider / compress-context / abort. That is *provider* recovery only.
**StackOwl.** `recovery_actuator.py` + `retry_actuator.py` + `capability_substitution.py`:
retry-once → substitute a capability → surrender honestly. That is *task* recovery.
**Gap.** Hermes is ahead on provider-error taxonomy; StackOwl is ahead on task recovery.
**Ask.** Adopt their error classifier under our recovery ladder? They compose rather than compete.

---

# D03 · Context management

### D03.1 · Pluggable context engine — `MISSING`
**Hermes.** `agent/context_engine.py` is an ABC; `ContextCompressor` is the default; third parties
drop in via `plugins/context_engine/<name>/`. Selected by `context.engine` in config. The engine owns
*when* to compact, *how* to compact, and may expose its own tools.
**StackOwl.** No context engine, no compaction. `pipeline/context_budget.py` is a tool-selection
budgeter, not a context manager.
**Gap.** Long conversations have no strategy other than truncation.
**Ask.** Is one engine enough, or do you want the ABC from day one?

### D03.2 · Conversation compression — `MISSING`
**Hermes.** `agent/context_compressor.py` — auxiliary (cheap) model summarizes middle turns while
protecting head and tail by **token budget** (not message count). Structured summary template with
Resolved/Pending tracking, tool-output pre-pruning before summarization, scaled summary budget,
iterative updates so information survives repeated compactions, and a filter-safe preamble so the
summarizer treats prior turns as source material rather than instructions.
**StackOwl.** Nothing. History is assembled fresh in `classify` each turn.
**Gap.** The single largest functional hole for long-running sessions.
**Ask.** This is a big build. Port their compressor design directly, or write ours?

### D03.3 · Session splitting on compression — `MISSING`
**Hermes.** Compression **splits the session** and chains the new one via `parent_session_id`
(`hermes_state.py`), so lineage survives compaction and session search can dedupe across it.
**StackOwl.** No session lineage concept.
> **CORRECTED 2026-09-02.** `sessions.parent_session_key` exists and is populated on 115 of
> 122 rows (94%), and `session.resolve` carries `previous_conversation_id` on 295 of 329
> records (89%) — those are SPAWN links, not compaction links, but the concept is present.
> **And the gap does not bite.** Their compressor SPLITS the session, so lineage is what stops
> compaction cutting recall. Ours (D03.2) compresses IN PLACE, and `recent_conversation_turns`
> filters on `source_ref IN (session_key, ...)` with no `conversation_id` predicate — so recall
> already spans every boundary. Bakir's own lane holds 311 conversations; none of them cut it.
**Ask.** Do we need lineage before compression, or can it come after?

### D03.4 · Tool-result overflow defense — `PARTIAL`
**Hermes.** Three levels: (1) each tool self-truncates, (2) `tools/tool_result_storage.py` persists
oversized results **into the sandbox temp dir** so the agent can re-read them by path instead of
losing them, (3) registry-level `max_result_size_chars` per tool.
**StackOwl.** Individual tools truncate (`search_files`, `git_tool`, `process_tool`). No registry
cap, no persistence — oversized output is simply lost.
**Gap.** Level 2 and 3 missing. Truncation currently destroys information.
**Ask.** Where do persisted results live given the sandbox boundary?

### D03.5 · Context-window discovery — `AHEAD`
**Hermes.** `agent/model_metadata.py` + `models_dev.py` — a catalog with context lengths, with a
change-detector-test ban to keep it from rotting.
**StackOwl.** `providers/model_window.py` actively **probes** the live endpoint for the real window
(built after finding a model running at 8192 vs its real 262144).
**Ask.** ANSWERED 2026-09-04 — keep the probe, do NOT add the catalog. The failure modes
are asymmetric: a stale catalog OVER-states a window and costs the whole call, while our
probe-failure floor UNDER-states one and costs only some context. Their own design concedes
it, pairing the catalog with "a change-detector-test ban to keep it from rotting". Measured
on the day's real outage: the floor cost a handful of calls at 100k instead of 262k and
self-corrected on the next probe. See `designs/D03.5.md`.

---

# D04 · Model providers & routing

### D04.1 · Providers as plugins — `MISSING`
**Hermes.** 32 provider profiles under `plugins/model-providers/<name>/`, each calling
`register_provider(ProviderProfile(...))` at import. **Lazy** discovery separate from the general
plugin manager. User plugins override bundled ones last-writer-wins, so a third party can replace
any built-in profile without patching the repo.
**StackOwl.** 4 concrete providers hardcoded in `providers/` (anthropic, gemini, openai, mock) plus
a registry. Adding a backend means editing core.
**Gap.** Provider breadth is a core-edit operation. This is Law 2 applied to inference backends.
**Ask.** Is provider breadth a goal, or is your local-weak-model + one cloud model enough?

### D04.2 · Transport adapters — `PARTIAL`
**Hermes.** `agent/transports/` — chat-completions, Anthropic native, Bedrock, Codex responses
(+ app-server + event projector), plus native Gemini/Vertex/Azure-identity adapters. `api_mode` is a
first-class constructor param.
**StackOwl.** One shape per provider file; no transport/profile split.
**Ask.** Do we need more than chat-completions + Anthropic native?
**MEASURED 2026-09-05 — NO, AND THE PREMISE IS WRONG.** "No transport/profile split" is
false: `setup/provider_catalog.py:28` defines `PROTOCOLS = ("anthropic","openai","gemini",
"grok")`, validated per entry, and `providers/registry.py::_build_provider` dispatches on
`config.protocol` and never on a name — **protocol IS the transport dimension**, with the 49
bundled providers mapping onto it (45 openai-shaped, 2 anthropic, 1 gemini, 1 grok), and zero
imports of a concrete provider class anywhere outside `providers/`. **DEMAND IS ZERO:**
`cost_records` holds 130,523 rows, and every call for 57 days (96,937 of them) went through
the one LiteLLM gateway, while the anthropic, gemini and grok protocols have NEVER carried a
single call. **AND NONE OF THE REFERENCE'S FOUR TRANSPORTS IS UNREACHABLE THROUGH A GATEWAY**
— read against their code, their Vertex and Azure "adapters" are credential providers feeding
the OpenAI SDK (Vertex's own docstring says "OpenAI-compatible endpoint"); only Bedrock
differs (boto3 SigV4) and Codex-responses is a second wire format, both reachable via LiteLLM,
which does the SigV4 itself. **WHAT THE ITEM WAS WORTH:** the standing constraint bans
branching on a provider's name, and it was being broken TWICE — `model_window.py:268` and
`openai_provider.py:1283` independently tested `":11434" in url or "ollama" in url.lower()`.
De Morgan-identical, never diverged, and both wrong the same two ways: a gateway path like
`gw.example.com/ollama/v1` matches, and so does a vLLM server on port 11434, which was then
sent an `options.num_ctx` body it may reject. The CAPABILITY is real (a native metadata
endpoint no other OpenAI-compatible server exposes); the INSTRUMENT was a vendor sniff. Fixed
by probing instead of guessing — the probe already returned int-or-None, so guessing first
bought nothing — and the result is recorded as a measured fact that `openai_provider` now
asks. Fifteen lines away, `model_window.py:271` describes the incident where a URL-shaped
assumption cost a 32x window error, and `config/provider.py:152` already named the pattern:
"a hardcoded guess wearing discovery's clothes." See `designs/D04.2.md`.

### D04.3 · Credential pool & rotation — `MISSING`
**Hermes.** `agent/credential_pool.py` — persistent multi-credential pool for same-provider failover;
the error classifier can decide "rotate credential" as a distinct action from "fall back to another
provider". Plus `credential_sources.py` / `secret_sources/` (1Password, Bitwarden, command).
**StackOwl.** One key per provider from config.
**Ask.** Do you run multiple keys per provider today?
**MEASURED 2026-09-05 — NO, AND `MISSING` IS TOO FLAT.** Four providers are configured,
**one is enabled** (NeraAiRaw) with **one** api_key, and `ProviderConfig.api_key` is
`str | None` — the model permits nothing else. **Demand is zero:** across **502,215 log
records in 9 files** there are exactly TWO distinct auth-or-rate-limit messages — a 400
payload rejection (twice, our own request) and `credential_rotation handler registered` (569
times). Not one rate-limit event, not one auth failure, ever. (A first `grep -c` reported
271/250/262 hits for "429"/"401"/"403"; they were OUTPUT TOKEN COUNTS — `(640in/429out
tokens)`.) **AND THE HALF THE MAP CREDITS THE REFERENCE WITH IS ALREADY HERE:** the error
classifier that "can decide rotate-credential as a distinct action" exists in
`providers/_resilient_round.py` and its docstring says it was deliberately ported — `AUTH`
(401/403), `RATE_LIMIT` (429, "back-pressure, not an outage"), `BILLING` (402, "credit
exhausted; rotate, do not retry"), PAYLOAD_TOO_LARGE, TIMEOUT, BAD_REQUEST, SERVER_5XX, each
mapped to a RecoveryAction and pinned by `tests/providers/test_failure_taxonomy.py`. Ours
dropped their English-substring matching and their runtime-specific member names. **Only the
POOL is absent, and there is nothing to put in it.** **TWO NEAR-MISSES, both checked before
concluding:** `credential_rotation` is a NAME COLLISION — a browser-session liveness check,
registered 569 times with ZERO `jobs` rows, but reachable on demand via `cronjob.py:520`'s
generic `handler_name=handler`, so not dead ("the zero-row table is the QUESTION"); and
`FailureCause` appears zero times outside its module but the retry loop IS its consumer.
**WHAT SHIPPED INSTEAD:** `SecretResolver._from_file` read secret files without ever looking
at their mode. The operator's own are correct (`700` dir, `0600` on all six keys) so the
check is SILENT on his box; it exists for the invisible case — a restore or `cp` leaving a
key world-readable, which the platform would have read forever without a word. It WARNS and
still returns the secret (D18.9: fail closed only when refusing prevents the harm), stays
quiet when the mode is right (D18.7: a guard that fires on correct code is never wired), and
is POSIX-guarded. See `designs/D04.3.md`.

### D04.4 · Auxiliary-model router — `MISSING`
**Hermes.** `agent/auxiliary_client.py` — **one** resolution chain for every side-LLM task
(compression, session search, vision, titles, curator review, web extraction). Config `auxiliary:`
lets each task pin its own provider/model/base_url/max_tokens/reasoning_effort; `auto` walks an
ordered fallback list.
**StackOwl.** Side-LLM calls are scattered — `acceptance_llm`, classifiers in `interaction/`,
`critic_scorer`, `reflection`, judges — each resolving its own model.
**Gap.** No single seam for "cheap model work", so cost/latency of side tasks is unmanaged.
**Ask.** This looks like a high-value, low-risk early adopt. Agree?
**MEASURED 2026-09-03 and 2026-09-05 — NO, ON BOTH HALVES, and the framing is worth
rejecting explicitly because the map wrote its conclusion into the question.** The GAP LINE IS
HALF RIGHT: the seam already exists — `interaction/classifier_base.py` gives one resolution
chain (`resolve_cascade_tier`, circuit-aware, walks fast->standard->powerful->local, never
raises; plus `safe_complete`) and THIRTEEN modules use it. What does not exist is anything
cheaper to route TO: four providers, three disabled and all naming the SAME model, and the one
enabled provider declares fast AND standard AND powerful, so **every rung resolves to
`NeraAiRaw/neraai-v1-raw`** — cost records agree, side tasks and conversational turns both at
100%. **"High-value" is arithmetically ZERO** (a router's product is choosing between models;
applied to side tasks at 5% of spend where every choice resolves to one string, the ceiling is
zero by construction), and **"low-risk" is INVERTED** — the risk arrives on the first day it
does something, changing thirteen modules at once. **The reference's own default is NOT to
route:** its `auxiliary_client.py` is 8,255 lines but ships exactly TWO pinnable tasks, both
`provider: "auto"`, `model: ""`, while ~14 task labels appear at its call sites; its `auto` is
an availability fallback of the same kind as our `get_with_cascade`. **WHAT SHIPPED (09-03):**
`describe_tier_ladder()`, `tier_ladder_is_degenerate()` and a boot INFO line, because the real
defect was that tier routing is a NO-OP and nothing said so — verified still firing.
ESC-111 answered "leave it". **WHAT SHIPPED (09-05):** the concrete cost of bypassing the seam
is CORRECTNESS, not cents. `safe_complete` sets `disable_thinking=True`; the provider defaults
it FALSE, so a bypassing call spends its whole output budget on invisible reasoning and
returns empty — and SEVEN sites coerced empty into a benign default, most sharply
`shadow_validator`, where it becomes `quality=None` and **silently rejects a good DNA
proposal**. `classifier_base`'s own docstring records this bug being "independently
rediscovered and patched" in FIVE places — "five incidents, one root cause". All seven fixed
and a tripwire now fails any awaited completion that neither disables thinking nor justifies
keeping it. See `designs/D04.4.md`.

### D04.5 · Tier escalation — `AHEAD`
**Hermes.** `smart_model_routing` config + `/fast`; no in-loop escalation.
**StackOwl.** `providers/tier_selector.py` + `escalation_signal.py` — a same-turn ESCALATE sentinel
that re-runs the turn on a stronger tier when a breaker opens.
**Ask.** Keep. Confirm it survives.
**CONFIRMED 2026-09-05 — IT SURVIVES; THE EXPLANATION DID NOT.** Verified three ways:
`LLMGateway._can_escalate_meaningfully` exists and asks the real question ("is there a HIGHER
tier that actually resolves somewhere else?"); on the live configuration it answers FALSE
because the ladder is degenerate (all three rungs `NeraAiRaw/neraai-v1-raw`); and BOTH provider
loops honour it, with six test files exercising the area. **The gate was earned** — its
docstring records the cost of not having it: 25 escalations all landing on the same
(provider, model), each making the provider DISCARD a finished attempt (14, 15, 16 and 19 tool
calls in four of those turns) and hand back a turn to re-run, with the tool-outcome ledger
reset so the re-run was blind to what the first had learned. **WHAT WAS MISSING:** the
pipeline logs "circuit open — requested tier escalation" at INFO when a tool breaker opens —
**12 such requests in the kept logs and ZERO lines recording the outcome.** Someone asking why
a turn did not escalate found a request and silence, when the honest answer (every rung is the
same model) is exactly the fact D04.4 had to make visible for the ladder itself. Fixed by ONE
shared decision, `escalation_signal.escalation_allowed()`: both loops previously open-coded
`if can_escalate and escalation_requested():`, so adding the message to each would have been
two copies of one rule — the shape that made the vendor sniff wrong in D04.2. It is silent
when nothing asked, says the reason ONCE per turn, and a test asserts neither provider
open-codes the decision again. See `designs/D04.5.md`.

### D04.6 · Rate limiting & circuit breaking — `PARITY`
**Hermes.** `rate_limit_tracker.py`, `nous_rate_guard.py`, jittered decorrelated backoff
(`retry_utils.py`) to avoid thundering herds.
**StackOwl.** `rate_limiter.py`, `circuit_breaker.py`, plus a shared Telegram flood guard.
**Ask.** Adopt jittered backoff specifically? Ours may be fixed exponential.
**Answered 2026-09-05 — `docs/reference-mapping/designs/D04.6.md`.** Confirmed: exactly ONE
file in `src/` imports `random` (`owls/dna_attribution.py`), so nothing jitters — and the
shapes are not uniformly exponential either (`startup/provider_probe.py` is LINEAR). Jitter
ADOPTED on the operator's call, additive-only, on self-computed delays only; never on the
breaker's half-open field, which `open_for()` overwrites with the server's `Retry-After`.
The larger finding was elsewhere: `penalize()` returned silently on an uncapped bucket — the
live configuration for 100% of traffic — while the caller recorded a `rate_limit_penalty`
ledger event claiming the platform had slowed down. It now records the EFFECT.

---

# D05 · Tool architecture

### D05.1 · Registry & auto-discovery — `PARITY`
**Hermes.** `tools/registry.py` — tool files self-register at import; discovery **AST-parses** each
module for a top-level `registry.register(...)` before importing, so helpers are never pulled in by
accident. `ToolEntry` carries `check_fn`, `requires_env`, `max_result_size_chars`, and
`dynamic_schema_overrides` (a callable re-evaluated at definition time so the schema can reflect
live config).
**StackOwl.** `tools/registry.py` with auto-discovery, consent gate, span wrapping, arg redaction.
**Gap.** Small: no `dynamic_schema_overrides`, no AST prefilter.
**Ask.** Adopt `dynamic_schema_overrides`? It is how they tell the model the *current* limits.

### D05.2 · Toolsets — `MISSING`
**Hermes.** `toolsets.py` — 33 named, composable bundles that can include other bundles. Each surface
picks a base set: CLI differs from messaging; webhooks get `_HERMES_WEBHOOK_SAFE_TOOLS`
(4 tools only) because payloads may carry untrusted third-party text. `_HERMES_CORE_TOOLS` (~40) is
the default bundle everything inherits.
**StackOwl.** No toolset concept. All registered tools are candidates every turn, then budgeted.
**Gap.** This is the mechanism that makes Law 2 enforceable. Without it there is no "core" to keep narrow.
**Ask.** What are StackOwl's natural bundles? Per-owl `tool_presets` already hints at this —
is that the seam to grow?

### D05.3 · Service gating (`check_fn`) — `PARTIAL`
**Hermes.** A tool with a `check_fn` is **absent from the schema entirely** until its prerequisite
exists (env var, installed driver, GUI present, kanban task active). Result cached with a TTL.
Zero footprint when unconfigured. This is rung 3 of the ladder and the main reason 75 tools cost like 40.
**StackOwl.** Tools exist unconditionally; unavailability surfaces as a runtime error or a degraded result.
**Gap.** Unconfigured capability still costs tokens on every call and invites hallucinated use.
**Ask.** Straight adopt? This is cheap and high-yield.

### D05.4 · Progressive tool disclosure — `MISSING`
**Hermes.** `tools/tool_search.py` — when MCP + non-core plugin tools would consume more than
`threshold_pct` (default 10%) of the window, they are replaced by three bridge tools
(`tool_search` / `tool_describe` / `tool_call`) and surfaced on demand. **Core tools never defer.**
**StackOwl.** `tool_search.py` / `tool_describe.py` files exist under `tools/meta/` but are not the
same mechanism — and the actual selection is the per-turn budgeter (D01.3).
**Gap.** This is the *cache-safe* answer to the problem the budgeter solves expensively.
**Ask.** Confirm: adopt disclosure, delete the budgeter?

### D05.5 · Programmatic tool calling (PTC) — `PARTIAL`
**Hermes.** `tools/code_execution_tool.py` — the model writes a Python script that calls Hermes tools
over RPC (Unix domain socket locally, file-based RPC remotely) against a generated `hermes_tools.py`
stub. A multi-step chain collapses into **one** inference turn; intermediate calls cost zero context.
**StackOwl.** `tools/code/execute_code.py` + `_ptc.py` and `sandbox/ptc/` exist — a privileged tool
channel. Need to confirm whether the model can actually author scripts that call arbitrary tools.
**Gap.** Likely narrower (a privileged escape hatch rather than a general RPC surface).
**Ask.** Walk the current `_ptc.py` together — is this already 80% there?

### D05.6 · Cross-tool references in schemas — `MISSING`
**Hermes.** Hard rule: a tool's schema description must never name a tool from another toolset
(that tool may be absent → hallucinated calls). Needed cross-references are injected **dynamically**
in `get_tool_definitions()`.
**StackOwl.** No such rule; descriptions are static strings.
**Ask.** Audit our descriptions for this once toolsets land?

### D05.7 · Tool-loop guardrails — `PARTIAL`
**Hermes.** `agent/tool_guardrails.py` — a side-effect-free controller tracking per-turn tool-call
observations (repeats, oscillation, no-progress) and returning decisions; runtime decides whether a
decision becomes guidance, a synthetic result, or a halt. Config section `tool_loop_guardrails`.
**StackOwl.** `pipeline/progress_tracker.py` + tool-outcome ledger + circuit breaker cover part of this.
**Ask.** Is our TurnProgressSupervisor the same thing under another name? (Dedup check.)

### D05.8 · Tool count — `PARITY`
Both register **75** tools. The difference is entirely in exposure discipline: Hermes ships ~40 by
default and gates the rest; StackOwl presents up to 150.

---

# D06 · Execution environments & sandboxing

### D06.1 · Pluggable execution backends — `PARTIAL`
**Hermes.** `tools/environments/` — six interchangeable backends behind one base class: local, Docker,
SSH, Singularity, Modal, Daytona. Unified **spawn-per-call** model: a session snapshot (env, functions,
aliases) captured once and re-sourced before each command; CWD carried via in-band stdout markers
remotely or a temp file locally.
**StackOwl.** Two confinement backends (`sandbox/bwrap.py`, `sandbox/docker.py`) behind `SandboxBackend`.
Local host only.
**Gap.** No remote/cloud execution. The ABC exists — the implementations do not.
**Ask.** Does "runs anywhere" matter to you, or is the Jetson + local the target?

### D06.2 · Serverless / hibernating environments — `MISSING`
**Hermes.** Modal and Daytona offer persistent sandboxes that hibernate when idle and wake on demand —
the marketing claim "costs nearly nothing between sessions" is this feature.
**StackOwl.** None.
**Ask.** Relevant to your deployment story, or out of scope for self-hosted?

### D06.3 · Confinement depth — `AHEAD`
**Hermes.** Docker/Singularity isolation; no seccomp/cgroup layer of their own.
**StackOwl.** `sandbox/` has seccomp filters, cgroup limits, mount planning, scratch dirs, a host-wide
concurrency governor, and a privileged tool channel.
**Ask.** Keep. Ours is genuinely stronger here.

### D06.4 · Filesystem checkpoints — `MISSING`
**Hermes.** `tools/checkpoint_manager.py` — transparent snapshots into **one shared shadow git store**
before any file-mutating operation, once per turn, with rollback via `/rollback`. Explicitly **not a
tool**: the model never sees it. Git objects dedupe across projects.
**StackOwl.** `tools/io/undo_store.py` gives per-tool undo. No turn-level snapshot, no cross-tool rollback.
**Gap.** No "undo the whole turn".
**Ask.** High value for an autonomous agent that edits files unattended — priority?

---

# D07 · Delegation & multi-agent

### D07.1 · Subagent delegation — `PARITY`
**Hermes.** `tools/delegate_tool.py` — child agents with fresh context, own `task_id`/terminal session,
parent's toolsets minus child-blocked tools, focused system prompt from the goal. Single or parallel
batch. Parent context sees only the call and the summary.
**StackOwl.** `tools/agents/delegate_task.py` + `owls/a2a_delegation.py` + `A2ADelegator` +
`ConcurrencyGovernor`. Comparable.
**Ask.** None structurally — see D07.2/D07.3 for the deltas. *(VERIFIED 2026-09-04, and
behaviourally rather than structurally: all four parts exist, and against 4,152 tool-bearing
turns, 76 — 1.8% — used a delegate tool. `[a2a-delegator] delegate: entry` fires in the
window with tool traffic and `[governor]` slot lines appear 300,364 times.)*

### D07.2 · Delegation roles & depth — `PARTIAL`
**Hermes.** Two explicit roles. `leaf` (default) **cannot** call `delegate_task`, `clarify`, `memory`,
`send_message`, `cronjob` — but keeps `execute_code`. `orchestrator` keeps `delegate_task`, gated by
`delegation.orchestrator_enabled` and bounded by `max_spawn_depth` (default 2). Knobs:
`max_concurrent_children` (3), `child_timeout_seconds`, `subagent_auto_approve`, `inherit_mcp_toolsets`,
`max_iterations`.
**StackOwl.** `owls/delegation_limits.py` + governor exist; role split and per-role tool blocking
need confirming.
**Ask.** ANSWERED 2026-09-04 — we have the distinction, as a depth PREDICATE rather than a
named role (depth 0 may delegate, depth>0 may not), enforced at BOTH presentation and
dispatch. Narrower than their `leaf`, which also blocks clarify/memory/send_message/cronjob.
The finding was underneath it: both layers gate on the SAME predicate, and deleting the one
`+ 1` that increments child depth neutered the whole cap with **481 tests still passing**.
Now pinned, and the exclusion raised from DEBUG to INFO so it is countable.

### D07.3 · Background delegation & durability — `PARTIAL`
**Hermes.** `background=true` returns a delegation id immediately; the result re-enters the conversation
later through an async completion queue. **Stated durability rule:** background delegation is
process-local — for work that must survive restart, use `cronjob` or `terminal(background=True,
notify_on_complete=True)` instead. They are explicit about the boundary.
**StackOwl.** `pipeline/durable/` gives durable tasks and recovery — arguably stronger — but the
delegation/durability boundary is not stated.
**Ask.** ANSWERED 2026-09-04 — see `designs/D07.3.md`. THE RULE: a delegation is durable IFF
its parent turn is a durable task; durability is INHERITED, never declared. Durable tasks,
their delegated children, pending messages, scheduler jobs, owl schedules and processes all
recover at boot with counts; a delegation from a non-durable turn does not. Ours is stronger
than the map implies — a durable child is RE-ATTACHED by a deterministic id rather than
restarted. The defect found was an asymmetry: "durable" logged at INFO while "process-local,
will be lost on restart" logged at DEBUG, so the only outcome worth warning about was the
invisible one. Now INFO.

### D07.4 · Mixture-of-agents — `PARITY`
**Hermes.** `/moa` marks one turn as MoA-enabled; `agent/moa_loop.py` gathers reference-model context
before each iteration. Deliberately **not** a model tool.
**StackOwl.** `tools/agents/mixture_of_agents.py` — is a tool.
**Ask.** ANSWERED 2026-09-04 — no, keep it on the schema, and the disagreement is recorded
rather than averaged away. Their Law 2 arithmetic holds here: `mixture_of_agents` is 1,371 of
79,392 schema chars (1.73% of every call) against ONE use in 4,154 tool-bearing turns (0.02%).
But this codebase has measured the other side and been burned — `skills_list` showed zero
invocations for eight days because on those turns it was NOT PRESENTED, so a tool the model
cannot reach is a capability that silently does not exist. ESC-120's recorded position governs:
"the token cost is a reason to reduce ROUND COUNT rather than to ration the capability
surface." They optimise the waist; we have decided to optimise round count.

### D07.5 · Named persistent sessions — `AHEAD`
**Hermes.** No equivalent.
**StackOwl.** `sessions_spawn` / `sessions_send` + `owls/session_registry.py` — named persistent owl
sessions with TTL, caps, mailbox drain.
**Ask.** Keep. *(VERIFIED 2026-09-04, with a caveat worth carrying: AHEAD here is a claim about
the CODE. All three parts are real and the registry is wired as an injected singleton, and the
design is careful — continuity is deliberately NOT on the handle, so turns run under
`session:{label}` and are read back from the history store. But measured against 4,154
tool-bearing turns, `sessions_spawn` has ONE invocation and `sessions_send` ZERO, and no row
anywhere is keyed `session:` — the continuity mechanism has never threaded a conversation.
"Keep" is a bet on future use, not a defence of observed value.)*

### D07.6 · Multi-agent work queue (Kanban) — `MISSING`
**Hermes.** A durable SQLite board where multiple **profiles** collaborate. A dispatcher loop (60s,
inside the gateway by default) reclaims stale claims, promotes ready tasks, atomically claims, and
spawns assigned profiles. Board = hard isolation boundary (`HERMES_KANBAN_BOARD` pinned in worker env);
tenant = soft namespace within a board. Auto-blocks a task after `failure_limit` consecutive failures.
Workers get a `kanban_*` toolset only when spawned as workers — zero schema footprint otherwise.
Swarm topology on top: planning root → parallel specialists → verifier → synthesizer.
**StackOwl.** *(re-measured 2026-09-04 — this entry predates the ONE loop.)* `tasks` IS the
durable board: `lease_owner` + `lease_expires_at` (atomic claim AND stale-claim reclaim),
`depends_on` (promotion), `attempt_count`/`max_attempts` with a terminal `dead_letter` state
(auto-block after a failure limit — 76 rows sit in it), `parent_task_id` (topology),
`next_attempt_at`, `position`, `idempotency_key`. 1,169 rows have been through it. `objectives/`
is not the closest analogue.
**Gap.** ~~No shared work queue, no claim/reclaim,~~ **only** cross-profile collaboration and the
board-as-isolation-boundary — both multi-tenancy shapes this deliberately single-principal
deployment does not have. **Verdict should read `PARTIAL`, not `MISSING`.** And the work is NOT
to build a board: "never add a second queue, a second retry path, or a second status column" —
cross-profile support is columns and a scope predicate on `tasks`, not a fifth engine.
**Ask.** This is the closest thing in either codebase to your "autonomous epic platform" vision.
Is Kanban the right shape, or is `objectives/` the right shape with Kanban's durability bolted on?

---

# D08 · Memory architecture

### D08.1 · Two-file curated memory — `DIVERGENT`
**Hermes.** Deliberately **low-tech**: `MEMORY.md` (agent's own notes — environment facts, project
conventions, tool quirks) and `USER.md` (who the user is — preferences, style, expectations). Entry
delimiter `§`. Both injected as a **frozen snapshot** at session start; mid-session writes hit disk
immediately but do **not** change the prompt until next session (Law 1).
**StackOwl.** A full pipeline: fact extractor → promoter → reinforcer → contradiction detector →
trust scoring → recall ranker → pruner, over SQLite + LanceDB + Kuzu.
**Gap.** Inverted. Ours is far more sophisticated and far more expensive; theirs is legible, editable
by the user in a text editor, and cache-safe.
**Ask.** The big one. Is StackOwl's memory pipeline earning its complexity? Would a two-file
curated layer *in front of* the pipeline give most of the value at a fraction of the cost?

### D08.2 · Memory providers as plugins — `MISSING`
**Hermes.** `agent/memory_provider.py` ABC + `agent/memory_manager.py` orchestrator + 8 in-tree
providers (honcho, mem0, supermemory, byterover, hindsight, holographic, openviking, retaindb).
Lifecycle: `sync_turn`, `prefetch`, `shutdown`, optional `post_setup`. **Only one external provider
active at a time** — enforced, to prevent schema bloat and conflicting backends. Provider CLI commands
are exposed only for the *active* provider. The set is now **closed**: new backends must ship as
standalone repos.
**StackOwl.** One memory implementation, no provider seam.
**Ask.** Do you want third-party memory backends, or is this breadth you do not need?

### D08.3 · Memory-write nudges — `MISSING`
**Hermes.** The agent tracks `_turns_since_memory` and nudges itself toward a memory write past
`memory.nudge_interval` (default 10). Cheap, deterministic, no extra model call to decide.
**StackOwl.** Memory extraction runs as a scheduled handler, not as an in-turn nudge.
**Ask.** Nudge vs. scheduled job — which fits an assistant that may go quiet for days?

### D08.4 · Memory as prompt-injection surface — `PARITY`
**Hermes.** `tools/threat_patterns.py` scoped `"context"` patterns applied to memory + context files
+ tool results.
**StackOwl.** `infra/prompt_safety.py` — a shared fence neutralizer for skill injection and memory recall.
**Ask.** None. Both solved this.
> **CORRECTED 2026-08-22 — this citation is STALE and the verdict does not survive it.**
> `neutralize` has exactly two callers (`memory/trust.py`, `skills/instruction_injector.py`)
> and **neither is the curated path**. It was true when written: "memory recall" then meant the
> archive renders. `D01.1` removed `memory_context` from the prompt and `D08.1` replaced it with
> the curated markdown files, so the mechanism cited here no longer touches the surface this item
> names. Curated memory reaches the system prompt as **plain prose with no fence at all**.
> Measured against the clone, the verdict is a **SPLIT**, not parity — behind on a load-time
> snapshot re-scan and on a write-time "declarative facts, not imperatives" instruction; ahead on
> the fence primitive, on having one unguarded writer to their three, and on trust tiering.
> See `ESC-37` and `designs/D08.4.md`.

### D08.5 · Graph memory — `AHEAD` *(contested — see the 2026-09-02 correction)*
**Hermes.** One optional plugin (`holographic`) does retrieval over a store; no first-class graph.
**StackOwl.** Kuzu adapter + entity extractor + graph reconciliation job, first-class.
**Ask.** Keep — but does it survive the D08.1 decision?
> **CORRECTED 2026-09-02 — the 2026-08-22 answer below is WRONG, and the subsystem is not inert.**
> Measured over every retained log (2026-08-28 to 2026-09-02, 1,950 `classify: exit` records
> carrying the field): `graph_context_len` is **NON-ZERO on 1,771 of them — 90%** — at 54 to
> 361 characters, and never below 75% on any single day. The graph on disk is **40.3 MB** and
> was last written **2026-09-02T03:36:50**. It is read on nine turns in ten and is actively
> maintained for owl/skill/trait nodes.
>
> WHY THE OLD ANSWER READ ZERO IS NOT ESTABLISHED. Its window predates every retained log, so
> it cannot be re-run; recorded as unverifiable rather than reconciled away. What is certain is
> that it is not true of any day that can still be observed.
>
> WHAT IS ACTUALLY TRUE TODAY is narrower: the FACT-ENTITY feeder is gone. `kuzu_sync_handler`
> joined on `committed_facts` (zero rows since migration 0112) and was deleted on 2026-09-02 as
> dead code — correctly, since it could sync nothing. The owl, skill and trait writers remain.
> So the graph keeps serving fact-derived entities that nothing can refresh, on 90% of turns,
> while `ESC-78` measures its novel-entity contribution at ~4%.
>
> A LIMIT ON THIS MEASUREMENT, stated rather than hidden: node counts by type could not be
> read. Kuzu holds a single-writer lock and the live platform owns it, so an out-of-process
> query fails. The 90% is from the pipeline's own log field, not from the graph.
>
> The disposition — purge, rebuild, or leave — is `ESC-78`, unchanged and still the operator's.
>
> *The superseded 2026-08-22 answer follows, kept because a record that quietly loses its own
> mistakes cannot be trusted about anything else:*
>
> **ANSWERED 2026-08-22 — it did NOT survive, and `AHEAD` now describes an inert subsystem.**
> `graph_context_len` on `[pipeline] classify: exit` is **non-zero on 0 of 2,321 turns** over
> eight days, while on the same records `prefs_len` and `stable_len` are non-zero on 2,321 of
> 2,321. The one reader (`classify.py:97`) traverses entities mirrored `FROM committed_facts`,
> and that table holds **zero rows** against `staged_facts`' 4,654. The tree already records why,
> at `commands/memory_helpers.py:160-168`: the stage-then-promote chokepoint went with
> `FactPromoter` in `D08.2`, `D08.1` retargeted both writers at curated memory, and
> `committed_facts` "has held 0 rows since migration 0112 and now has no writer at all".
> So the graph's feeder lost its writer as a **consequence** of `D08.1`. Whether it is re-fed,
> retired or left is `ESC-39`.

---

# D09 · The learning loop

> Hermes' headline differentiator, and it is real code, not a prompt instruction.

### D09.1 · Background review fork — `MISSING`
**Hermes.** `agent/background_review.py` — after a turn, a **daemon thread forks a second `AIAgent`**
on a snapshot of the conversation, restricted by a runtime whitelist to memory + skill tools, and asks
itself "should any skill/memory be saved or updated?". The fork inherits the parent's live runtime
(provider, model, credentials, **cached system prompt**) so it hits the same prefix cache. The main
conversation and its cache are never touched. Writes land directly in the stores.
**StackOwl.** `memory/reflection_writer_handler.py` and `learning/` miners run as **scheduled jobs**,
detached from the turn that produced the material.
**Gap.** Learning is not tied to the moment the lesson exists, and does not see the live conversation.
**Ask.** Fork-per-turn vs. scheduled miner — or both? The fork is what makes their loop feel instant.
> **CORRECTED 2026-08-22 — this entry overstates its own source on two points.**
> (1) **It is not "after a turn".** The reference gates the review on `_memory_nudge_interval = 10`
> and fires only when turns-since >= 10; the skill nudge is the same. And `memory_enabled`
> **defaults to `False`**. So it is roughly 1 turn in 10, opt-in.
> (2) **"hits the same prefix cache" buys nothing here.** `cached_input_tokens` is **0 on 104,899
> of 104,899 cost records, all-time** — this deployment has no prompt cache for a fork to inherit.
> Two further facts the entry's `MISSING` verdict does not survive: `tools/knowledge/reflect_now.py`
> already reaches the SAME `ReflectionWriterHandler` in-turn, and it — along with
> `synthesize_skills` and `evolve_now`, all three in the **guaranteed** base set and presented on
> every turn — was called **zero times in eight days**. The gap is that the model never
> volunteers, not that it cannot. See `ESC-41`.

### D09.2 · The review prompt itself — `MISSING`
**Hermes.** Worth reading verbatim. It tells the fork that **"a pass that does nothing is a missed
learning opportunity, not a neutral outcome"**; it names user *frustration* ("stop doing X", "this is
too verbose", "why are you explaining") as a **first-class skill signal, not merely a memory signal**;
it enforces a target library shape — **class-level** skills with a `references/` directory, not a flat
list of one-session entries; and it sets a preference order: **patch the skill that was actually loaded
this session** before writing a new one.
**StackOwl.** Feedback capture exists (`pipeline/steps/feedback.py`, `FeedbackClassifier`,
`output_style` preferences) but routes to *preferences*, not to *skills*.
**Gap.** We treat correction as a setting; they treat it as procedural knowledge.
**Ask.** This is the most portable single artifact in the whole repo. Adopt the prompt design directly?

### D09.3 · Skill curator — `MISSING`
**Hermes.** `agent/curator.py` — **idle-triggered**, no cron daemon: when the agent is idle and the
last run is older than `interval_hours`, it forks an agent to review agent-created skills. Auto-transitions
active → stale → archived from derived activity timestamps; can pin, consolidate, patch.
Invariants: only touches `created_by: agent` skills; **never deletes** (archive is the max destructive
action, and archives are restorable); pinned skills bypass every auto-transition and the LLM review.
Telemetry sidecar `.usage.json` tracks use/view/patch counts and last activity. CLI:
`hermes curator status|run|pause|resume|pin|unpin|archive|restore|prune|backup|rollback`.
**StackOwl.** `tools/knowledge/synthesize_skills.py` + `skills/synthesizer_handler.py` create skills;
nothing manages their lifecycle afterward.
**Gap.** Skills accumulate and rot with no pressure to consolidate.
**Ask.** Adopt the lifecycle model wholesale? The invariants (never delete, pinned exempt,
agent-created only) look non-negotiable.

### D09.4 · Skill-creation nudges — `MISSING`
**Hermes.** `_iters_since_skill` counter, `skills.creation_nudge_interval` (default 10), gated on
`skill_manage` actually being in the tool set.
**StackOwl.** None.
**Ask.** Same question as D08.3.

### D09.5 · `/learn` — user-initiated skill authoring — `MISSING`
**Hermes.** `agent/learn_prompt.py` — one prompt that points the **live agent** at whatever the user
described (a directory, a doc URL, "what we just did", pasted notes), tells it to gather sources with
the tools it already has, and author a single `SKILL.md` to the authoring standard. **No separate
distillation engine, no model-tool footprint** — a textbook rung-2 solution.
**StackOwl.** `synthesize_skills` is a tool (rung 6).
**Ask.** Move ours to a slash command? Same capability, zero schema cost.

### D09.6 · Learning made visible — `MISSING`
**Hermes.** `agent/learning_graph.py` — assembles a graph of learned skills + memory chunks as
first-class nodes, with skill→skill edges from declared `related_skills` and memory→skill edges from
lexical overlap. Rendered in the desktop app. Answers "what have I actually taught it?".
**StackOwl.** Owl DNA evolution is inspectable in the TUI; learning is not.
**Ask.** ANSWERED — and it did not need escalating. The shipping commit (2026-09-02) cites
Bakir's own words about `SystemSpendAssembler`, *"that will give visibility to user what is
happening in system"*: the same question, already settled for spend, so learning got the same
treatment on the same surface. `LearningAssembler` reports the last 24h of `lessons` BY KIND
plus `learning_artifacts` counts in the morning brief. Live since 2026-09-02 and verified
2026-09-04 — see `designs/D09.6.md`.

### D09.7 · Outcome mining from failures — `AHEAD`
**Hermes.** No equivalent.
**StackOwl.** `learning/failure_outcome_miner.py`, `tool_outcome_miner.py`, RCA verdict router,
heuristic store with ranking, `lessons_index`.
**Ask.** Keep — this is real and Hermes has nothing like it. But confirm the lessons actually reach a
turn (registered ≠ reachable).

---

# D10 · Skills system

### D10.1 · Skill format — `PARITY`
Both use `SKILL.md` with YAML frontmatter, compatible with the wider agent-skills convention.
Hermes adds `platforms:` (OS gating), `metadata.hermes.tags/category/related_skills`, and
`metadata.hermes.config` (config keys the skill needs, prompted during setup, injected at load).
**CLOSED 2026-09-04 — parity holds on the FILE, and did not hold on the PLACE.** We carry
`category` and `tags` already; `platforms`/`related_skills`/`config` remain unbuilt, and
`extra="forbid"` means a file carrying them fails to load ENTIRELY — loud and recoverable, but
it bounds the "compatible with the wider convention" claim to that convention's shape, not its
whole vocabulary. The defect this item found is elsewhere: **40 `SKILL.md` on disk, 39 loaded,
and the fortieth had been dark for 69 days with ZERO log lines.** `load_all` iterates
`_VALID_SOURCES` joined onto the root, which enumerates the dirs it EXPECTS — and an iteration
over the expected set can never notice an unexpected member, so the "never silent" warning could
not cover a file it never looked at. The loader now walks disk-first and REPORTS strays (WARNING,
verified live) without adopting them, since `source` is a trust input. `_VALID_SOURCES` was also
a second copy of the manifest's `SkillSource` Literal; it is now derived. See `designs/D10.1.md`.

### D10.2 · Authoring standard — `MISSING`
**Hermes.** A hard, reviewer-enforced rubric: description **≤ 60 characters**, one sentence, no
marketing words, does not repeat the skill name; tools referenced by their **real Hermes names** in
backticks (never `grep`/`cat`/`sed` — point at `search_files`/`read_file`/`patch`); `platforms:`
audited against actual script imports; author credits the **human** first; fixed section order
(`When to Use` → `Prerequisites` → `How to Run` → `Quick Reference` → `Procedure` → `Pitfalls` →
`Verification`); ~200 lines complex / ~100 simple; scripts in `scripts/`, references in `references/`,
templates in `templates/`; tests at `tests/skills/test_<skill>_skill.py`.
**StackOwl.** No authoring standard. ~20 skills in tree.
**Gap.** Without a standard, an agent that writes its own skills writes inconsistent ones.
**Ask.** Adopt the rubric verbatim (it is generic), or write ours?

### D10.3 · Active vs. optional skills — `MISSING`
**Hermes.** `skills/` (69, active by default) vs `optional-skills/` (111, shipped but installed
explicitly). Heavy-dep or niche skills belong in the second directory. This is Law 2 for skills.
**StackOwl.** One flat set.
**Ask.** Do we have enough skills for this split to matter yet?
**ANSWERED 2026-09-04 — NO, and measuring it showed the premise is wrong in a way that
matters more.** 39 registered skills (14 builtin, 25 learned) is nowhere near the reference's
69/111. But the 25 learned skills are roughly EIGHT concepts, each confirmed by reading its
description rather than inferred from its name: a SEVEN-strong VERIFIER family, three
GATHERERs, two OWLS, and `incident_owl_build` / `incident_owl_build_stop` with BYTE-IDENTICAL
descriptions. Splitting a catalogue whose problem is redundancy yields a redundant active set
and a redundant optional set — the catalogue does not need dividing, it needs to stop growing
sideways. ROOT CAUSE: all three rungs of the mint-time duplicate gate are LEXICAL
(`parent_traces`, `base_name`, `canonical_key`), and skills are the artifact where two
different sets of words describe one capability — `verify_rca_evidence` and
`evidence-brief-verifier` share no tokens, so no rearrangement makes them equal. Each past fix
extended the lexical family instead of changing the KIND of question. `store.semantic_recall`
was already built and 39 of 39 skills already carried an embedding — built but not wired. A
third rung now feeds the SAME reinforce action at a MEASURED threshold (0.90: catches 6 of 26
known duplicate pairs, flags zero non-duplicates, over all 741 live pairs). Replayed over the
live corpus it catches 10 of 25. See `designs/D10.3.md`. The split itself stays MISSING and
unbuilt — revisit when the corpus is concepts, not copies.

### D10.4 · Skills Hub — `MISSING`
**Hermes.** `tools/skills_hub.py` — a `SkillSource` ABC over GitHub repos and the bundled optional set,
with `HubLockFile` provenance tracking, a **quarantine** directory, an audit log, an index cache, taps,
and `skills_ast_audit.py` static analysis of skill scripts before install.
**StackOwl.** No install path from outside.
**Ask.** Distribution is how a platform gets adopted. In scope?
**CORRECTED 2026-09-04 — "no install path from outside" is WRONG.** `/skill add <local-path>`
and `/skill add --url <url>` both exist and are user-reachable, installing from a directory, a
`git clone --depth=1`, or a downloaded archive into `skills/installed/`, with real defences
against oversized downloads and zip-bomb expansion, plus an audit row and a restorable
snapshot. What is genuinely absent is the hub AROUND it: no source ABC/registry, no lockfile,
no quarantine, no taps, and **no static analysis of installed code**. The `installed/`
directory is empty — the path has never been used. THE DEFECT FOUND: the loader gated
`owls.yaml` (DECLARATIVE) on trusted source and `tools/*.py` (IMPORTED AND EXECUTED) on
nothing but the directory existing. D05.1's actuator looks like it covers this and does not —
it refuses to exec while the skills tree sits inside the model-writable workspace, which
answers "can the MODEL write what we run?", and for `installed/` is a documented no-op. No
scan covered it either: `skill_helpers.py` references the security scan gate ZERO times, and
that gate reads SKILL.md TEXT rather than Python. Both sidecars now read ONE trust set
`{builtin, user}` — zero live impact, measured: all 39 skills report `tools: 0` and no
`tools/` directory exists under any source. See `designs/D10.4.md`. The hub itself is NOT
built; whether installed skills may ever ship executable tools, and behind what scan, is
**ESC-129** — which is this entry's Ask.

### D10.5 · Skills as slash commands — `PARTIAL`
**Hermes.** `agent/skill_commands.py` scans the skills dir and exposes each as `/<skill-name>`,
injecting the skill as a **user message** — never into the system prompt — precisely to preserve
caching. Deliberate and documented.
**StackOwl.** `/skill` command exists; skills are injected into the **system prompt** by `assemble`.
**Gap.** ~~Direct Law 1 violation, and it also means the model cannot be pointed at a skill on demand.~~
**Ask.** ~~Move skill injection to the message stream?~~

> **CORRECTED 2026-08-29 (measured twice, independently).** Both halves of the struck gap
> line are stale, and the description of the reference platform is wrong too.
> 1. **Not a Law 1 violation.** `skills_len` on the INFO line `[pipeline] assemble: exit`
>    varies in **0 of 403 lanes** over four days — only two values exist, 4159 and 0 — while
>    **304 of those same lanes** do vary their `prompt_hash`. The instrument detects
>    variation; skills never cause it. Independently: **0 `prompt_hash` changes across 54
>    genuinely multi-turn conversations.** It *was* true — `assemble.py:263` records
>    "skills_len went 4169 -> 0 across two turns of ONE conversation" on 2026-07-27 — and was
>    fixed by D01.1 slices 4b + 5 and ESC-10.
> 2. **The model CAN be pointed at a skill on demand.** `tools/knowledge/skill_view.py`
>    returns any skill's body by name, is in the guaranteed tool base
>    (`tools/_infra/presentation.py:93-102`) with a reachability probe
>    (`health/reachability/probes.py:20`), and ran **391 times in 7 days**. D01.1's approved
>    approach named it explicitly.
> 3. **"Never into the system prompt" mis-states the reference.** It carries a name +
>    57-char-description *index* in its system prompt (`prompt_builder.py:1461`,
>    `curator.py:426`). Only skill **bodies** stay out — exactly our posture, since
>    `SkillTier.FULL` has no producer anywhere in `src/`.
>
> **The real gap, and what this item is now for:** the *operator* has no way to point at a
> skill. `/skill` is CRUD only. That matters because the 4,000-char catalogue cap means **163
> of 179 skills are dropped from the prompt on every turn**, and the model can only ever
> reach what it can see. A human naming a skill needs no catalogue. The seam already exists —
> `SlashCommand.build_turn_prompt` (`commands/base.py:44`), honoured at
> `orchestrator.py:2263-2320`, used today by `/learn`, and mechanically identical to the
> reference's `event.text = msg` + fall-through.

### D10.6 · Skill relevance scoring — `AHEAD`
**Hermes.** All enabled skills' *names + descriptions* go in the prompt; the model picks.
**StackOwl.** ~~`skills/skill_relevance.py` scores and tiers per turn.~~
**Gap.** ~~Ours is smarter but is a per-turn prompt mutation (feeds D01.1).~~
**Ask.** ~~Keep scoring but move it to session start?~~

> **CORRECTED 2026-08-29 (measured, during D10.5's Round 0).** The struck lines are stale.
> `skills/skill_relevance.py` **no longer exists** — it and `assign_tiers` were deleted on
> 2026-08-15 by ESC-10, recorded at `skills/instruction_injector.py:66-77`. There is no
> per-turn scoring left to move: `assemble.py:325` hardcodes every entry to
> `SkillTier.SUMMARY`, and the catalogue is `store.list_enabled()` in a deterministic total
> order (`store.catalogue_order_key`). The "Ask" is therefore already answered — scoring did
> not move to session start, it was removed, and the prompt is frozen per conversation by
> D01.1 slice 5.
> **What is actually open here** is a different and larger problem, measured the same day:
> the catalogue is capped at 4,000 chars (`instruction_injector.py:12`), so of **179 enabled
> skills only 16 are presented and 163 are dropped on every turn** (2,604
> `catalog truncated by budget` WARNINGs in the retained logs). Over 7 days the model loaded
> 5 distinct skills, all from the visible head; 174 were never loaded once. Selecting *which*
> 16 the operator's question actually needs is the real D10.6, and `DEBT-25`'s constraint
> still binds it: any reader must score **without** putting per-query work back into the turn
> path, or it undoes D01.1's byte-identical prompt.
> `DEBT-25` itself should be closed as **overtaken** — it says "KEEP, do not delete"
> the two artefacts ESC-10 deleted two weeks later.

### D10.7 · Skill ownership per persona — `AHEAD`
**Hermes.** No equivalent.
**StackOwl.** `owls/skill_ownership.py`, per-owl `tool_presets`, pinned skills.
**Ask.** Keep — and note this is the natural seed for toolsets (D05.2).
**CONFIRMED 2026-09-04 — two of the three are exercised, the third could not be turned on.**
OWNERSHIP is real: 29 `skill_ownership` rows across SIX owls (verifier 8, secretary 8,
rca_gatherer 4, scout 4, jobmarket 3, mailbutler 2), written automatically by
`failure_outcome_miner` — the ONLY writer — when an owl fails. `tool_presets` is imported by
six modules including `authz_compose` and `presentation`. PINNED SKILLS was not: three
readers (`set_lifecycle_state`'s `AND pinned = 0`, `consolidation.py` twice,
`lifecycle.py:218`), `store.set_pinned` with SEVEN callers every one of them a test, and 0 of
39 rows pinned — the same finding measured without a grep. `/skill dedupe`'s own help told the
operator "a pinned member wins outright". `/skill pin|unpin` now exist, declared AND
dispatched together. SECOND FINDING, which makes D10.3 concrete: the duplicate families are
ATTACHED TO OWLS and concentrate — the `verifier` owl owns 8 skills of which FOUR PAIRS are
twins at >=0.90, `rca_gatherer` owns 4 with one twin pair. Ownership also feeds
`owl_drive_thresholds` (retirement nudged by the owning owl's drive), so a split family splits
the very decay signal that would retire it. See `designs/D10.7.md`.

---

# D11 · Session state & recall

### D11.1 · Session store — `PARITY`
**Hermes.** `hermes_state.py` — `SessionDB`, SQLite **WAL**, source tagging per session
(`cli`/`telegram`/…), model config recorded per session.
**StackOwl.** SQLite with pool + 90 migrations, `conversations`/`messages`.
**Ask.** None.
**CONFIRMED 2026-09-04 — parity holds on all three named properties, and the count was
stale.** WAL: set by the pool (`PRAGMA journal_mode=WAL`) AND persistent in the live file
header — the pool also sets `foreign_keys=ON` and `busy_timeout=15000` (raised from 5000 after
a measured writer-contention burst). SOURCE TAGGING: `sessions.channel`, populated —
telegram 107, cli 13, rca 3. MODEL CONFIG: we are AHEAD, and I read this backwards first.
`messages.model` was empty in 0 of 3,841 rows, which looked like the gap — but the fact lives
ONE TABLE OVER in `cost_records`: **130,420 rows, `model` populated in 100% of them**, beside
provider, tokens, cost, TTFT and prompt hash, joinable by trace_id / conversation_id /
session_key / owl_name. That is per-CALL, finer than their per-session config. MIGRATIONS:
**135**, not 90 — the number here was stale. `messages.model` is now DROPPED (migration 0136):
0 rows, 0 readers, one INSERT handed None by its only caller, and 9 of the 10 test fixtures
already omitted it. See `designs/D11.1.md`.

### D11.2 · Full-text search over messages — `PARTIAL`
**Hermes.** **FTS5 virtual table over all session messages**, used by session search.
**StackOwl.** FTS5 exists only for `committed_facts` and `skills`. `tools/knowledge/session_search.py`
explicitly documents that there is **no FTS over messages** and falls back to SQL `LIKE` substring
search — a deliberate, recorded decision to avoid a second store.
**Gap.** Substring ≠ relevance. Cross-session recall is weak in exactly the way Hermes advertises as
a headline feature.
**Ask.** Add an FTS5 mirror over `messages`? The tool is already written to slot it in behind `_discover`.

### D11.3 · Session-search shapes — `PARITY`
Both expose discovery / scroll / browse from one tool with zero LLM cost. Hermes adds **lineage
dedupe** across compression-split sessions and "bookend" context (first/last 3 messages of the session).
**Ask.** Adopt bookends — cheap and clearly useful.
**ADOPTED 2026-09-04 — on `discover`, which is where the frame was missing.** MEASURED first
across all nine daily logs: `session_search` ran 18 times — **discover 16, browse 2, scroll 0**
(control: 1,765 `skill_view` mentions in the same files). The live log alone said ZERO, and so
did the control in it — it rotates daily and was 46 minutes old. A CORRECTION TO THIS ENTRY'S
IMPLIED SHAPE: `browse` does not list sessions. All three modes are scoped to ONE session
(`WHERE c.session_key = ?`); browse pages through a single session's messages. So bookends are
not a session picker — they are the frame for a `discover` hit returned from the middle of a
session with nothing to say what the session was. Two LIMIT-bounded queries, no model call
(the "zero LLM cost" property this row is measured on), and only when there IS a hit to frame.
They render through `_render`, which applies `redact_secrets` — deliberately, because a
session's opening and closing turns are where a credential is most likely to sit, and a second
formatting path would be a redaction hole of exactly the "same rule, one case short" kind.
Mutation-proven: bypassing `_render` leaks the key and the test catches it. LINEAGE DEDUPE
across compression-split sessions is NOT adopted and stays unbuilt. See `designs/D11.3.md`.

### D11.4 · Session export & recap — `MISSING`
**Hermes.** `session_export_md.py`, `session_export_html.py`, `session_recap.py`, `session_filters.py`,
`session_listing.py`, `/sessions`, `/resume`.
**StackOwl.** `export/` covers full-archive backup, not per-session export/recap.
**Ask.** Product feature or nice-to-have?
**VERIFIED 2026-09-04 — the gap is real and BIGGER than this row says; nothing built (ESC-130).**
No session-export surface exists, and two names that look like hits are not: `/sessions` is
BROWSER sessions and `/resume` resumes an OWL's cadence. But "full-archive backup" is generous:
`export/` is a curated FIVE-table export — committed_facts (0 rows), staged_facts (230),
owl_dna (11), parliament_sessions (0), audit_log (11,257) — and `conversations` (1,107),
`messages` (3,841) and `sessions` (123) are in NONE of it. **The conversation history is in no
export at any granularity**, so the gap is prior to formatting and recap. `committed_facts` is
NOT removed here: it is the unwired target of a promotion step that never runs (staged_facts
holds the live rows), which is different from a retired table, and a sanitization merge-gate
depends on it. DEMAND, measured across nine daily logs: `session_search` 18, `transcripts` tool
**0**, operator-run exports **0** (all 8 export lines are the migration runner's automatic
backup). The transcripts zero is about demand, not reach — live discovery finds it among 79
tool classes, while a static grep says it is unreferenced, and a raw grep for "transcript"
returns 3,353 that are the WRITER plus 168 lines about voice transcription. See
`designs/D11.4.md`.

### D11.5 · Trajectory capture for training — `MISSING`
**Hermes.** `agent/trajectory.py` + `batch_runner.py` + `trajectory_compressor.py` (70KB) —
batch trajectory generation and compression **for training tool-calling models**. This is Nous being
a research lab.
**StackOwl.** Nothing.
**Ask.** Out of scope, or does the "agentic OS" vision want its own training data?
**CORRECTED 2026-09-04 — "Nothing" is WRONG; nothing built (ESC-131).** There is no batch
generator and no compressor, but the TRAJECTORIES are captured and already labelled:
`task_outcomes`, **20,276 rows**, one per turn — `input_text` 100%, `tool_sequence` 100% (real
rows look like `["browser_navigate","browser_extract",…]`), `tool_call_count` 100%, `success`
100%, `dna_snapshot` 100%, `response_text` 99%, `failure_class` 52%, `quality_score` 47%. Six
more tables join on the same `trace_id`: cost_records 130,420, reflections 6,427, messages
3,841, approach_rating_pending 1,427, turn_decisions 766, message_ledger 498. THE 11% THAT WAS
NOT A DEFECT: `task_outcomes.model` reads 2,346/20,276, which looks like an actuator wired on
only some paths — but three quarters of that denominator PREDATE the feature (ESC-47/50; first
stamped outcome 2026-08-24 21:57). Since then it is **90%**, and the remainder is not a gap
either: turns with 1+ tools are 99%, zero-tool successes are 100%, and **246 of the 253
unstamped rows are `AllProvidersUnavailableError`** — no provider was reachable, so no model
ran and an empty stamp is the CORRECT value. What is missing is only the export in a training
shape — the same shape as D11.4. See `designs/D11.5.md`.

---

# D12 · Channels & gateway

### D12.1 · Platform adapter contract — `DIVERGENT`
**Hermes.** `BasePlatformAdapter` ABC: four abstract methods (`connect`/`disconnect`/`send`/
`get_chat_info`) plus a **declared capability surface** — `MAX_MESSAGE_LENGTH`, `message_len_fn`,
`supports_draft_streaming`, `prefers_fresh_final_streaming`, `streaming_overflow_limit`,
`enforces_own_access_policy`, `authorization_is_upstream`. Capability is *declared data*, so the
runner adapts instead of branching per platform.
**StackOwl.** `ChannelAdapter` ABC with `receive`/`send`/`send_text` + optional
`send_inline_keyboard`/`send_clarify`/`send_file`/`download_media`. Capabilities are implicit.
**Gap.** Ours branches on channel identity in places; theirs reads a descriptor.
*(Re-measured 2026-09-04: exactly FOUR sites outside `channels/` compare a channel name,
and `channels/base.py` exposes no capability descriptor for them to ask. Three are not the
gap — a setup wizard, a one-line CLI ownership decision, and an approach-rating gate that is
correct in effect because `approach_rating` exists only in the Telegram package. The one that
IS the gap is `notifications/recipient.py`, which resolves a proactive address for Telegram
alone and already carries a `TODO(channels)` for a per-channel resolver registry. It costs
nothing today: no undelivered row anywhere says "no address", and Telegram is the only
enabled channel. The work belongs in the change that enables a second channel.)*
**Ask.** Adopt a declared capability descriptor? This is what makes 20 adapters maintainable.

### D12.2 · Channel breadth — `PARTIAL`
**Hermes.** 20 adapters: Telegram, Discord, Slack, WhatsApp, Signal, Email, Teams, Matrix, Mattermost,
IRC, LINE, SMS, ntfy, DingTalk, WeCom, Weixin, Feishu, Google Chat, QQ, BlueBubbles, Home Assistant,
plus a generic webhook and an HTTP API server.
**StackOwl.** 4 (Telegram, Slack, Discord, WhatsApp) + CLI + socket.
**Ask.** Which channels actually matter to you? Breadth is cheap *after* D12.1/D12.3, expensive before.
> **MEASURED 2026-09-02 — breadth is not the gap; it already exceeds use 4:1.** Across 20,112
> all-time turns: telegram 9,575, rca 9,508 (a machine lane), cli 979, internal 50. Slack,
> Discord and WhatsApp have carried **zero turns, ever**, and have never been configured —
> every boot logs a skip, 44 times each. `message_ledger` holds telegram only (486 rows).
> Adding a fifth adapter would be adding to three that have never carried a message. `ESC-108`.

### D12.3 · Generic relay adapter — `MISSING`
**Hermes.** `gateway/relay/` — the endpoint of the pattern: **one** adapter that receives a
`CapabilityDescriptor` at handshake telling it which platform it is fronting and what to advertise.
Wire I/O is delegated to an injected transport. **There is no per-platform gateway code at all** —
the connector is the only side that knows.
**StackOwl.** Four hand-written adapters with visible parity duplication (each has its own
`callbacks.py`, `clarify.py`, `consent.py`, `helpers.py`, `settings.py` — re-measured
2026-09-04: `memory_callbacks.py` no longer exists in any channel).
**Gap.** ~24 near-duplicate modules that must each be fixed separately. This is the *dedup* target
you asked me to find.
**Ask.** Highest-ROI refactor in the channel layer. Do it before adding channel #5?
**RE-MEASURED 2026-09-04 as DEBT-114 required before scheduling; nothing refactored (ESC-132).**
The whole `channels/` tree is **12,985 lines**: the four channel dirs are **10,596** (slack
2,208, whatsapp 1,525, **telegram 5,421**, discord 1,442) and the six same-named modules across
them account for 7,244. It is NOT four copies, in three ways — whatsapp has no `callbacks.py`
or `clarify.py` at all; telegram is more than half the total, carrying twelve modules the others
lack; and **a shared layer already exists**, 2,389 lines across 12 top-level modules
(`_format`, `base`, `registry`, `splitter`, `callback_authz`, `socket_adapter`, …) doing exactly
the factoring D12.3 proposes. DEBT-114 also found `is_authorized` in four structurally distinct
variants (3/3/8/23 lines). **THE
FACT THAT CHANGES THE QUESTION: three of the four channels have never carried a message.**
Sessions are telegram 107 / cli 13 / rca 3; task_outcomes telegram 9,617 / rca 9,623 / cli 989 /
internal 50; and slack, discord and whatsapp have **ZERO log lines across all nine daily logs**.
So 5,175 lines — slack 2,208, whatsapp 1,525, discord 1,442 — have never run. TWO SUB-FINDINGS: only telegram has a
`notifications.py` (a channel-specific proactive dispatcher, built beside the channel-agnostic
NotificationRouter/ProactiveDeliverer), and the `format_morning_brief` /
`format_parliament_synthesis` helpers in slack, whatsapp and discord are reached by NOTHING —
the agnostic deliverer formats through the shared `channels/_format`, and only telegram's
dispatcher calls a per-channel formatter. Nothing deleted: three registered user-facing
channels are the operator's to keep or retire. See `designs/D12.3.md`.

### D12.4 · Streaming to chat platforms — `PARTIAL`
**Hermes.** `gateway/stream_consumer.py` — bridges sync agent callbacks to async delivery: queue the
deltas, then **progressively edit a single message** via the edit transport (universally supported
on Telegram/Discord/Slack), rate-limited and buffered. Plus draft streaming where supported.
**StackOwl.** `ResponseChunk` with `kind=answer|progress` and a progress renderer.
**Ask.** Do we edit-in-place, or post successive messages? (Affects flood-control, which has bitten us.)

### D12.5 · Turn lease / concurrency correctness — `PARITY`
**Hermes.** `gateway/turn_lease.py` — serializes the load→run→flush region **per session_id**, because
busy guards are keyed by *routing key* while the transcript is owned by *session_id*, and
`switch_session()` makes that mapping many-to-one. Two keys on one session would otherwise interleave
flushes and corrupt the transcript.
**StackOwl.** *(re-measured 2026-09-04.)* `turn_registry.session_intake_lock(session_key)` +
`gateway/inflight_router`; the `serialize_prior` gate is GONE, replaced by the non-blocking
intake described under D02.3.
**Ask.** ANSWERED 2026-09-04 — ours is keyed correctly and needs no change. The lock is keyed on
`session_key`; the transcript is owned by `messages.conversation_id`; and `conversations` carries
exactly ONE session_key per row, so the lock key OWNS the transcript key rather than crossing it.
Measured over 1,103 conversations / 508 session_keys: zero conversations reachable from more than
one session_key. Our `switch_session` analogue (`resolve_identity_key`) is a label on the session
record, not a shared store, and is unconfigured here.

### D12.6 · Delivery routing & mirroring — `PARITY`
**Hermes.** `gateway/delivery.py` routes by explicit target / platform home channel / origin / local.
`gateway/mirror.py` appends a delivery-mirror record to the target session so the receiving agent has
context — and works standalone from CLI/cron/gateway.
**StackOwl.** `NotificationRouter` + `ProactiveDeliverer` + delivery ledger + undelivered outbox.
**Ask.** ANSWERED 2026-09-04 — we DO mirror, so this is PARITY with no work owed. The
agent's history is not the `messages` transcript: `_gather_history` reads `staged_facts`
where `source_type='conversation'`, and 29 of those 178 rows (16%) are proactive sends —
the morning brief, job-failure alerts and goal answers — stored with an empty user half.
The reader handles that half deliberately (`_parse_turns_to_messages` skips it,
`merge_consecutive_roles` collapses the run) rather than synthesising a user turn.

### D12.7 · Pairing & authorization — `PARTIAL`
**Hermes.** `gateway/pairing.py` (DM pairing), `slash_access.py`, `authz_mixin.py`, per-adapter
`enforces_own_access_policy` / `authorization_is_upstream`, plus scoped **token locks** so two
profiles cannot use the same bot credential.
**StackOwl.** `tenancy/` + `authz/` are stronger conceptually, but channel-level pairing and token
locks are not present.
**Ask.** ANSWERED 2026-09-04. The footgun is real but sits one layer BELOW the bot
token: `IpcServer.start` unlinked its socket unconditionally, so a second instance stole a
running gateway's endpoint silently (proven: two servers on one path both bound). It now
probes for a live listener and refuses, while still reclaiming a socket file left by a
hard-killed process. A bot-token lock as described guards the MULTI-PROFILE case, and there
is no profile concept in settings — so that half is not built.

### D12.8 · Untrusted-input toolset — `MISSING`
**Hermes.** Webhook events may carry third-party content, so the webhook toolset is deliberately
constrained to 4 read-only tools to blunt prompt injection.
**StackOwl.** `webhooks/` accepts and enqueues; the resulting job gets the normal tool surface.
**Gap.** A public webhook can currently reach shell.
**Ask.** Security item. Raise priority?

---

# D13 · Client surfaces

### D13.1 · Surface count — `PARTIAL`
**Hermes.** Six: classic CLI (prompt_toolkit + Rich), Ink/React TUI over JSON-RPC, Electron desktop,
web dashboard embedding the **real TUI** via a PTY WebSocket, ACP server (VS Code / Zed / JetBrains),
and the messaging gateway.
**StackOwl.** Three: CLI (typer), TUI (Textual), messaging gateway.
**Ask.** Which surfaces matter? A dashboard is often what makes a platform feel real to non-terminal users.

### D13.2 · "Do not rebuild the chat experience" rule — `MISSING`
**Hermes.** An explicit architectural rule: the dashboard **embeds** `hermes --tui` through a PTY
rather than reimplementing it in React. Structured React around it is allowed; a second transcript
or composer is not. Anything added to the TUI appears in the dashboard automatically.
**StackOwl.** N/A today, but the rule is what prevents surface sprawl later.
**Ask.** Adopt as a written rule before building surface #4?

### D13.3 · TUI transport — `DIVERGENT`
**Hermes.** Newline-delimited JSON-RPC over stdio between Node (screen) and Python (sessions, tools,
model calls, slash logic). TypeScript owns the screen; Python owns everything else.
**StackOwl.** Textual in-process, plus an EventBus for live progress.
**Ask.** In-process is simpler and works. Any reason to split?

### D13.4 · Editor integration (ACP) — `MISSING`
**Hermes.** `acp_adapter/` — an ACP server so VS Code / Zed / JetBrains can drive the agent, with
its own auth, edit approval, permissions, provenance, and event model.
**StackOwl.** None.
**Ask.** Is coding-in-editor part of the vision, or is StackOwl a life/ops assistant?

---

# D14 · Commands & UX

### D14.1 · Single command registry, derived everywhere — `PARTIAL`
**Hermes.** One `COMMAND_REGISTRY` list of `CommandDef` objects in `hermes_cli/commands.py`. Every
consumer **derives**: CLI dispatch, gateway dispatch (`GATEWAY_KNOWN_COMMANDS`), `/help` text, the
Telegram BotCommand menu, Slack `/hermes` subcommand routing, autocomplete, help-by-category. Adding
an alias is a one-tuple change with **no other file edits**. Fields include `cli_only`, `gateway_only`,
and `gateway_config_gate` (a config dotpath that opens a CLI-only command to the gateway).
**StackOwl.** `commands/registry.py` + `metadata.py` with a recursive `SubCommand` tree; Telegram
menu is derived (`commands_registration.py`). Close, but `CommandDef`-style surface flags
(`cli_only`/`gateway_only`/config-gating) are not present.
**Ask.** Add surface flags to our `CommandMeta`? Small change, removes per-channel special-casing.

### D14.2 · Command count — `PARTIAL`
Hermes 84 slash + 39 CLI subcommand modules. StackOwl 33 slash + 10 CLI groups.
**Ask.** Which of their 84 name a capability we lack? (Walk the list — it doubles as a feature audit:
`/compress`, `/insights`, `/usage`, `/rollback`, `/steer`, `/queue`, `/handoff`, `/learn`, `/curator`,
`/yolo`, `/journey`, `/goal`, `/subgoal`, `/blueprint`, `/hatch`, `/pet`.)

### D14.3 · Contextual onboarding — `MISSING`
**Hermes.** `agent/onboarding.py` — no blocking questionnaire. A one-time hint the **first time the
user hits a behavior fork** (message-while-running, first long-running tool), tracked in config under
`onboarding.seen.<flag>`, shown once per install ever. Deliberately dependency-free so CLI and gateway
can both import it.
**StackOwl.** `setup/` is a 3-step wizard + `setup/disclosure.py` (progressive disclosure).
**Ask.** Add just-in-time hints alongside the wizard?

### D14.4 · Doctor — `PARTIAL`
**Hermes.** `hermes doctor` diagnoses setup issues; `hermes_cli/diagnostics_upload.py` ships a report.
**StackOwl.** `stackowl health` + `HealthAggregator` + **reachability census** (asks whether a
capability can be *reached*, not merely registered).
**Ask.** Ours is arguably better. Add the "explain how to fix it" half?

### D14.5 · Theming — `MISSING`
**Hermes.** `hermes_cli/skin_engine.py` — skins are **pure data** (`~/.hermes/skins/*.yaml`),
customizing banner colors, spinner faces/verbs, tool prefix, response box, branding. `/skin` switches
at runtime. Plus a "pet" (`agent/pet/`) and token-free affection detection (`agent/reactions.py` —
`ily`, `<3`, `good bot` — a curated lexicon, **no model call**).
**StackOwl.** TUI has `glyphs.py`/`color_caps.py`; no user-facing skin system.
**Ask.** Personality-as-product. Matters for "Jarvis for everyone"?

---

# D15 · Autonomy & scheduling

### D15.1 · Cron scheduler — `PARITY`
**Hermes.** `cron/jobs.py` + `cron/scheduler.py`; the gateway ticks it every 60s under a file lock
(`~/.hermes/cron/.tick.lock`) so overlapping processes cannot double-fire.
**StackOwl.** `JobScheduler` + 31 handlers + assembly. Comparable or ahead.
**Ask.** ANSWERED 2026-09-04 — we do NOT have a tick lock and do not need one; the guarantee
sits a layer lower. `UPDATE jobs SET status='running' WHERE job_id=? AND status='pending'`, with
the rowcount checked via SQLite `changes()` and the loser bailing at INFO. Stronger than a file
lock, which guards one host's filesystem and can be ORPHANED by a hard kill — the same shape as
the orphaned process that kept a port bound across a restart. Since today it is also defended
twice independently: the IPC socket guard stops a second instance binding, the CAS stops a
double-fire. Both are unexercised because only one scheduler runs, which is the correct state
for a safety mechanism.

### D15.2 · Job richness — `PARTIAL`
**Hermes.** Per job: `skills` to load, `model`/`provider` override, a **pre-run script** whose stdout
is injected into the prompt (`no_agent=True` makes the script the entire job), `context_from` to chain
job A's output into job B, `workdir` (runs there with that dir's `AGENTS.md` loaded), and
multi-platform delivery. Schedule formats: duration (`30m`), phrase (`every monday 9am`), 5-field cron,
ISO one-shot.
**StackOwl.** Handlers are typed Python classes; jobs carry less per-job configuration.
**Gap.** ~~Their jobs are user-authorable in natural language; ours are developer-authored
handlers.~~ *(HALF WRONG, re-measured 2026-09-04.)* `cronjob` — the user-facing tool — creates jobs
with `_HANDLER = "goal_execution"`, and GoalExecutionHandler "runs a natural-language goal through
the pipeline ... as if the user had typed the goal at the prompt", with `run_once` for
fire-and-forget. Five live jobs use it. The 34 typed handlers are the PLATFORM's own work, not a
ceiling on the user's. The real gap is per-job CONFIGURATION.
**Ask.** ANSWERED as posed — we already have "I can schedule anything". The narrowed question,
which per-job knobs are worth building, is **ESC-124**. Two of the five are already settled by
evidence: a model override is a no-op while the tier ladder is degenerate (D04.4), and
`context_from` would be a second expression of the `depends_on` edge the ONE loop already has.

### D15.3 · Cron hardening invariants — `PARTIAL`
**Hermes.** **3-minute hard interrupt** on cron sessions so a runaway loop cannot monopolize the
scheduler. Catchup window = half the job's period, clamped 120s–2h. Grace window 120s for missed
one-shots. Cron sessions pass `skip_memory=True` — memory providers intentionally do not run.
Cron deliveries are **not** mirrored into the target gateway session; they land in their own session
with a header/footer frame so role alternation stays intact.
**StackOwl.** Circuit breaker + cooldown auto-requeue + self-heal notify (shipped). Timeouts and
catchup semantics need confirming.
**Ask.** WALKED 2026-09-04. (1) Hard interrupt: PRESENT — `asyncio.wait_for(..., 1200s)` vs their
180s, defensible because dispatch is concurrent so a long handler never delays the next tick.
(2)+(3) Catchup/grace: PRESENT but DIFFERENT — a per-job `replay_missed` flag that coalesces to
<=1 catch-up, rather than a computed window clamped 120s-2h. (4) `skip_memory`: ABSENT, zero hits
— a scheduled job runs the full pipeline; left alone as an unmeasured optimisation. (5) Delivery
mirroring: DIFFERENT, and the one with a moving number — we mirror into the operator's lane, and
33 of 34 lanes have zero empty-user rows while his has 30 of 60 with a longest consecutive-assistant
run of **9**, up from 4 when `merge_consecutive_roles` measured it on 2026-08-26. Contained: the
merge is lossless and wired on classify's history path. The latent risk is a STRICT provider, which
rejects consecutive same-role turns outright.

### D15.4 · Objectives / standing goals — `AHEAD`
**Hermes.** `/goal`, `/subgoal`, `cron/blueprint_catalog.py`, `suggestions.py` — lighter.
**StackOwl.** `objectives/` — decomposer, driver on a cadence, epic runner, graph, store.
**Ask.** HALF CLOSED 2026-09-04. No Kanban is coming — D07.6 established that `tasks` IS the
board and a second one would be the fifth engine the no-second-engine rule was written against.
The other half is open and now measured: objectives and tasks are two engines in code, and only
one has work. `tasks` 1,170 rows, newest today; `objectives` 0 parent rows, all 28 subgoals
terminal (27 done, 1 failed) since 2026-08-28, and its driver ran 19 times this boot advancing
NOTHING each time, behind 2,530 lines. NOT DEAD — the objective tool has nine turns and 27 of 28
subgoals succeeded — but FINISHED, which is a different thing. Fold-or-keep is **ESC-125**.

### D15.5 · Background-process notifications — `PARTIAL`
**Hermes.** `terminal(background=true, notify_on_complete=true)` → the gateway watches for completion
and triggers a **new agent turn**. Verbosity configurable: `all` / `result` / `error` / `off`.
**StackOwl.** `process` + `wait` tools + `task_liveness_sweep`; completion does not itself trigger a turn.
**Ask.** ANSWERED 2026-09-04 by evidence, not escalated — NOTIFY THE USER, do not wake the agent.
The map's claim holds (the only completion callbacks are the durable per-iteration hook and the
queued-turn drain). Usage narrows it: `process` ran in 14 of 4,155 tool-bearing turns and `wait`
in 6, so the agent already handles completion explicitly in six of fourteen. Three recorded
reasons against a wake: the destination rule is satisfied by a notification; an unprompted turn
spends the PLATFORM's budget, and this tree already billed 3.9M tokens over 137 rounds on one
autonomous recovery; and `wait` already exists for when the agent needs the result. **What would
change it:** measuring that those eight fire-and-forget turns LOSE their result entirely.

### D15.6 · Proactive outreach — `AHEAD`
**Hermes.** Cron delivery only.
**StackOwl.** `NotificationRouter` (decide) + `ProactiveDeliverer` (transport) + delivery ledger +
undelivered outbox + quiet hours + digest.
**Ask.** Keep — this is core to the "reaches out to you" product. *(VERIFIED 2026-09-04: five of
the six parts are EXERCISED, not merely present — router 6,909 logged, deliverer/ledger 5,553
attempts, outbox 195 all surfaced, digest running today. The sixth is not: `/quiet` writes a
`notification_overrides` row that NOTHING reads — the only other reference in the tree is a
health-cadence declaration, and the telegram checker is config-only — and it used to report
success anyway. The message now says RECORDED but NOT YET ENFORCED; wire-or-retire is **ESC-126**.
A second landmine sits underneath: quiet hours exists in three places and the telegram copy's
timezone defaults to UTC while `system.timezone` is America/Chicago, so enabling as configured
would silence him from 17:00 local.)*

### D15.7 · Instincts / perches — `AHEAD`
**Hermes.** No equivalent.
**StackOwl.** Reactive behavioral triggers and passive context observers feeding constraints back
into the pipeline.
**Ask.** CONFIRMED 2026-09-04 — reachable in the WIRING sense, never exercised in the
BEHAVIOURAL one, and the halves are named differently from this entry. PERCHES: the `perch`
handler is registered (present by name in the live boot log's 38) and user-creatable — cronjob
routes a path to it — but ZERO perch jobs exist. INSTINCTS: no such name in `src/`; the capability
is the EventBus + `notifications/event_bridge.py`, whose allow-list decides which events may ping
the user. It has been SUBSCRIBED 664 times (once per boot) and `_on_event` has fired ZERO times —
a trustworthy zero, because that success path logs at INFO. The event half of perches is already
retired in the tree: `perch.file_landed` is "dead v1 vocabulary — no module/emitter exists".

---

# D16 · Extensibility

### D16.1 · Plugin surface — `PARTIAL`
**Hermes.** `PluginManager` discovers from `~/.hermes/plugins/`, `./.hermes/plugins/`, and **pip entry
points**. A plugin's `register(ctx)` can add: lifecycle hooks (`pre_tool_call`, `post_tool_call`,
`pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`), tools via
`ctx.register_tool(...)`, and **CLI subcommands** via `ctx.register_cli_command(...)` — the plugin's
argparse tree is wired into `hermes` at startup with no core edit.
**StackOwl.** `plugins/` with registry, manifest, capabilities, remote install, verify. Hook set and
CLI-command registration need confirming.
**CONFIRMED 2026-09-04 — both, and four more; this entry's "need confirming" is now answered and
the ITEM IS ALREADY CLOSED (all seven stages done, `designs/D16.1.md`).** Plugins load AT BOOT
from `~/.stackowl/plugins/` via `load_installed_plugins` + `LocalPluginLoader`, handed SEVEN
registries: tools, **CLI commands** (`CommandRegistry`), scheduler handlers, channels, owls,
**lifecycle hooks** (`HookRegistry`) and prompt contributors — against the reference's three.
ALL SIX reference hook points exist and are DISPATCHED, not merely declared: `PRE_TOOL_CALL` /
`POST_TOOL_CALL` from `tools/base.py`, `PRE_LLM_CALL` / `POST_LLM_CALL` from `providers/base.py`,
`ON_SESSION_START` / `ON_SESSION_END` from `sessions/store.py` (twice each). Observe-only is
ENFORCED rather than trusted — `dispatch` returns `None` whatever a hook returns, so no call site
can grow a veto by accident — and with no plugins installed the whole surface costs one dict
lookup. LIVE: `[plugins] boot: no plugins installed` on every boot, which is the loading path
running and honestly reporting zero. NOT pip entry points — declined in ESC-16.

### D16.2 · "Plugins must not touch core" rule — `MISSING`
**Hermes.** A named policy: plugins may not modify `run_agent.py`, `cli.py`, `gateway/run.py`,
`main.py`. If a plugin needs something the framework does not expose, **widen the generic plugin
surface** — never special-case the plugin in core. One PR removed 95 lines of hardcoded plugin argparse
from `main.py` for exactly this reason.
**StackOwl.** No stated rule.
**ADOPTED 2026-09-04.** Stated in `src/stackowl/plugins/__init__.py`, which was EMPTY — the
place someone adding a ninth extension point actually looks. THE DECAY IS NOT HYPOTHETICAL
HERE: D08.2 added `MemoryProvider` to the ABC table and not to the registry table, so
`_registries.get("MemoryProvider")` returned None and registration hit `continue` SILENTLY —
a plugin would have loaded and registered nowhere with every table looking correct. HALF WAS
ALREADY ENFORCED by two tests predating this item (`set(_ABC_NAMES) == set(loader._registries)`
both directions, plus one that reads the REAL construction site because that check passes even
when a slot's VALUE is None). ADDED: a tripwire over all **841 modules** in `src/stackowl` —
exactly SEVEN outside the plugin package may import it (three hook-dispatch seams, boot, and
four plugin-management modules that name no plugin). Set equality in BOTH directions, because
this repo has had an allowlist rot the other way (three dead entries in the owner-scope list),
and marked `@pytest.mark.tripwire` so it joined the gate by its marker — 50 tests to 55, no
path list edited. NOT ENFORCED and stated openly: "core must not special-case a plugin" cannot
be screened while ZERO plugins are installed — the decay looks like `if plugin_name == "foo"`
and there is no name to look for, so it would be a zero over a zero denominator. See
`designs/D16.2.md`.

### D16.3 · Category ABCs — `PARTIAL`
**Hermes.** Six categories each with ABC + orchestrator + implementation directory: memory providers,
model providers, context engines, image-gen, video-gen, web search. Plus the meta-rule: **when 3+
contributions integrate the same category, stop merging one at a time — design the ABC and convert
them into plugins.**
**StackOwl.** ABCs exist for embeddings, web search, media, sandbox, channels. Missing for memory,
model providers, context engines.
**Ask.** Which categories are worth an ABC for *your* roadmap?

### D16.4 · Third-party products stay out of tree — `MISSING`
**Hermes.** Explicit, twice-stated policy: observability backends, vendor SaaS connectors, analytics —
ship as **standalone plugin repos**, not in-tree. Reason given is maintenance load, not quality.
The in-tree memory-provider set is formally **closed**.
**StackOwl.** No policy.
**ADOPTED 2026-09-04 — and the tree already VIOLATES it, live (ESC-133).** The design agreed in
June: `IntegrationRegistry.register()` is documented "open for extension: plugins can call
register() at import time", and `/connect` tells the operator "No integrations registered.
**Install an integration plugin first.**" But FOUR vendor-specific modules live in
`src/stackowl/integrations/` — `gmail.py`, `gmail_settings.py`, `google_calendar.py`,
`google_oauth.py`. **CORRECTED 2026-09-04 by D16.6:** this entry first said they were
"registered from THREE core sites". They are NOT — that came from an unsound predicate of mine
(a file containing both the strings `IntegrationRegistry` and `.register(` anywhere), and those
three register health CONTRIBUTORS, COMMANDS and owls. **Nothing in `src/` constructs
`GmailAdapter` or `GoogleCalendarAdapter`**: the vendor code is in-tree but UNWIRED, which is
why `/connect` correctly reports "install an integration plugin first". A Gmail OAuth token on
disk was refreshed 2026-09-02, but no adapter reads it. The in-tree MEMORY-PROVIDER set is already effectively closed at one
(`BuiltinCuratedProvider`), matching the reference. The rule is stated in
`integrations/__init__.py` and enforced by a tripwire that pins BOTH the module set and the
registrar set with set equality — a fifth vendor module or a fourth registrar fails the gate.
Moving the existing four out is user-facing capability with real credentials, so it is the
operator's call. See `designs/D16.4.md`.

### D16.5 · MCP — `PARITY`
**Hermes.** Client with OAuth manager, stdio watchdog, catalog, security review, dashboard OAuth flow;
plus `mcp_serve.py` exposing Hermes as an MCP server. MCP is **rung 5** — preferred over a new core tool.
**StackOwl.** `mcp/` covers both directions with allowlist, probe, cache, tool exposure.
**Ask.** Adopt the *doctrine* (MCP before core tool), not the code.
**ALREADY ADOPTED, confirmed 2026-09-04 — and the evidence line was the thing missing.**
PROCESS.md's Footprint Ladder already reads "extend existing code -> CLI command + skill ->
service-gated tool -> plugin -> **MCP server** -> new core tool (last resort)", so MCP is rung 5
and a core tool is the last resort — the doctrine, in the file every architect stage must cite.
The code is there both directions (client + allowlist/probe/cache; server + tool_exposure +
sse_encoder). MEASURED: MCP has NEVER run — zero `mcp.*` messages across nine daily logs against
a control of 1,521 `transcript.record_turn: exit` lines in the same files. AND IF A SERVER WERE
CONFIGURED THE OPERATOR COULD NOT SEE IT: of 117 log calls, TWO were INFO and 82 DEBUG, and the
two questions that matter — did it connect and how many tools did it expose
(`discover_tools: exit`), which of them reached the registry (`register_server_tools: exit`) —
were both DEBUG, invisible at production level. Both now INFO, entry lines deliberately left at
DEBUG and pinned by a test. Separately measured: 24 of 47 design docs name a ladder rung, though
PROCESS.md requires it — a process-compliance gap recorded, not retrofitted. See
`designs/D16.5.md`.

### D16.6 · Integrations — `DIVERGENT`
**Hermes.** Integrations arrive as **skills + CLI** (Google Workspace, Notion, Airtable, Apple apps,
GitHub — 180 skills total) or as gated tools (Home Assistant, Spotify, Feishu). Almost nothing is a
core tool.
**StackOwl.** `integrations/` with OAuth manager + Gmail + Google Calendar as first-class code.
**Gap.** Ours is rung 6 for something they solve at rung 2.
**Ask.** Would Gmail/Calendar be better as a skill + CLI command pair?
**ANSWERED 2026-09-04 — no, and the premise needs correcting first: it is not rung 6.** A rung-6
"new core tool" would be model-callable, and there is NO gmail or calendar tool among the 77
registered — zero. The entire capability of both adapters is one method,
`get_morning_brief_section()`, contributing a section to the morning brief through the
`IntegrationAdapter` ABC. So skill+CLI (rung 2) is the wrong shape too: this is an ADAPTER
interface, not a command. The fitting rung is 4 — a plugin implementing the ABC that already
exists, which `IntegrationRegistry.register()` is documented "open for extension" to accept.
TWO DEFECTS FOUND. (1) `get_morning_brief_section` returned a PLACEHOLDER once
`is_connected()` passed — a section titled "Email" whose only item read "[Gmail brief section —
live fetch requires active connection]", with no fetch anywhere in the method. The same file
states the rule it broke, twenty lines below: "NEVER fabricate 'ok' for an unperformed action"
(F024). Same rule, one method short. Now returns None, and it was UNREACHABLE (nothing
constructs the adapter), so this is a correction rather than a behaviour change. (2) D16.4's
"three core registrars" was my own false positive; corrected above and the guard's predicate
replaced. See `designs/D16.6.md`.

---

# D17 · Security & safety

### D17.1 · Dangerous-command approval — `PARTIAL`
**Hermes.** `tools/approval.py` is the **single source of truth**: pattern detection, per-session
approval state (thread-safe, keyed by session), CLI interactive + gateway async prompting, a
**smart-approval** path where an auxiliary LLM auto-approves low-risk commands, and a permanent
allowlist persisted to config.
**StackOwl.** `tools/consent.py` — trust tiers, session batch, time-window grants, always-ask
exclusions, audited, fails **closed** with no prompter. Conceptually richer.
**Gap.** No LLM-assisted smart approval; no persisted user allowlist.
**Ask.** Add smart-approve? It is the difference between "approve everything" fatigue and usable autonomy.
**MEASURED 2026-09-05 — THE FATIGUE IS NOT THERE; nothing built (ESC-134).** Across nine daily
logs: **121 consent decisions, 83 allow / 38 deny** — about thirteen a day, mostly granted. And
**27 of the 38 denials are a single already-fixed defect**: all 27 `execute_code` denials match
one-for-one the 27 `autonomous grant REFUSED — this is always-ask` lines (same tool, category
null, channel rca, 2026-08-28 → 2026-09-02), and every one PREDATES ESC-98's fix `058e94ee`
("code execution gated by NAME is now gated by NOTHING", 2026-09-01 23:51 local; the last
refusal is 02:05 UTC = 21:05 local). Since that deployed: **319 consent assemblies, 10
decisions, ALL allow, zero denials** — though no `execute_code` request has come through, so
the fix is unrefuted rather than exercised. So smart-approve would add an auxiliary-LLM trust
surface for ~zero measured friction, and it would contradict recorded decisions
(`_DEFAULT_ALWAYS_ASK_TOOLS` is annotated "Bakir's decision"; ESC-1 explains why
`prompt_surface` is always-ask despite being reversible). THE OTHER GAP IS REAL and is not
about an LLM: `_session_batch` and `_windows` are in-memory dicts, so **every restart wipes the
operator's grants** — and CodeWatcher exec-replaces the core on every code change (eight boots
in the current window). Persisting them widens authority across a boundary the operator never
approved, so it is a posture decision too. NOT a defect, checked: the 27
`RoutingPrompter: no channel UX` lines are the fallback WORKING — an unknown channel routes
rather than denying, pinned by `tests/channels/test_unwired_channel_consent_fails_closed.py`.
See `designs/D17.1.md`.

### D17.2 · Prompt-injection pattern library — `PARTIAL`
**Hermes.** `tools/threat_patterns.py` — one library organized **by attack class**, each pattern a
`(regex, id, scope)` tuple where scope selects which scanners use it (`"all"` everywhere,
`"context"` for context files + memory + tool results). Consumed by the prompt builder, the memory
tool, and the tool-result delimiter system.
**StackOwl.** `infra/prompt_safety.py` — a fence neutralizer, structural (no keyword lists, per your
standing rule). Narrower scope.
**Ask.** Extend ours to tool results and context files with the same scoping idea?
**DONE 2026-09-05 — and this entry names only half our machinery.** Besides `prompt_safety.py`
(a header neutraliser) there is **`infra/untrusted.py`**, the fence D12.8 built —
`wrap(text, source=…)` — whose docstring carries the measurement that justified it: 974 turns
over 7 days, 66 fetching external content AND using a powerful tool in the same turn.
**FOUR tools are named in that table and only TWO fenced**: `web_fetch` ✓, `browser_extract` ✓,
`web_search` ✗, `browser_navigate` ✗. web_search serialised third-party titles and descriptions
straight into `output`; browser_navigate returned `await page.title()`, a string the visited
site chooses. Same rule, one tool short — twice, in the module written for that shape, with its
own docstring naming the tools it had not reached. BOTH NOW FENCED, and the set is tied to the
evidence: a tripwire asserts every tool in that table fences with a source naming it, and that
every guarded tool is still named in the docstring — both directions, so a fifth entry point
cannot be added without fencing it. NO PATTERN LIBRARY ADOPTED: theirs is regexes by attack
class, which is a keyword list under a standing ban and monolingual besides; a test pins that
`untrusted.py` never acquires one. The web_search fence is PER FIELD (title, description) so
the JSON stays parseable — the `_render` docstring states the shape exists "keeping the
canonical shape available to downstream consumers", and a first attempt wrapping the whole
document broke it. Still a MARKER, not a control (ESC-110). See `designs/D17.2.md`.

### D17.3 · External security scanner — `MISSING`
**Hermes.** `tools/tirith_security.py` — runs an external binary to scan commands for content-level
threats (homograph URLs, pipe-to-interpreter, terminal injection). **Exit code is the verdict**;
JSON stdout enriches but never overrides. Configurable fail-open; auto-installs from GitHub releases
with **SHA-256 verification** and cosign when available.
**StackOwl.** None.
**Ask.** Worth it, or does our sandbox make it redundant?
**ANSWERED 2026-09-05, threat by threat; nothing built (ESC-135).** (1) PIPE-TO-INTERPRETER is
structurally IMPOSSIBLE, not merely undetected: `ShellTool` uses `create_subprocess_exec` with
`shell=False` (ARCH-75) and its docstring states the consequence — "pipes/redirects/chaining are
inert". `curl | sh` cannot be expressed, so a scanner for it is redundant and strictly weaker.
(2) HOMOGRAPH URLs are NOT covered — `ssrf_guard.py` is resolve-then-validate on IPs with no
IDN/punycode handling — but the threat model differs: a homograph is a PHISHING control for a
human misreading a domain, and our reader is a model that sees bytes while the guard asks where
the host actually resolves. Open question, not asserted either way. (3) TERMINAL INJECTION is a
real, narrow exposure: `cli_adapter.py:70` composes `RichLog(..., markup=True, ...)`, so `[...]`
in content is interpreted — but that widget's own docstring says "Used in tests / fallback
only" and what reaches it is assistant response text, not raw tool output. Ours to fix
structurally (escape markup on untrusted content) if worth fixing, not an argument for a
third-party binary. AND D16.4 PRE-ANSWERS WHERE IT WOULD LIVE: third-party products ship as
plugins, so even a "yes" lands at rung 4, never in `src/`. See `designs/D17.3.md`.

### D17.4 · URL / path / egress guards — `PARITY`
**Hermes.** `url_safety.py`, `path_security.py` (shared resolve+relative_to helpers), `website_policy.py`,
`docs/security/network-egress-isolation.md`.
**StackOwl.** `infra/net/ssrf_guard.py`, `tools/io/path_guard.py`, `web_search/providers/_egress.py`.
**Ask.** None.
**PARITY CONFIRMED 2026-09-05, all three wired with named call sites.** ssrf_guard →
`web_fetch` (guard_playwright_navigation), `browser/sessions.py` (make_route_guard),
`vision/loader.py`; path_guard → `search_files`, `pdf`, `write_file`, `edit`; `_egress` →
`brave.py`, `searxng.py`. The SSRF guard is resolve-then-validate over IP CLASSES (link-local
covers 169.254.169.254; CGNAT covers 100.100.100.200), and `_egress.py` is egress LOGGING
hygiene — collapse a URL to scheme://netloc/path so logs never carry the query text or an API
key. `ddg.py` not using it is CORRECT, not a gap: it drives a library rather than building a
URL and logs a literal `ddgs:text` label plus `query_len`, never the query — checked, because
the shape looks exactly like the one-case-short defect found five times this session and is the
one that is not. WHAT WAS WRONG: the guard's docstring said "Known limitation **(tracked)**"
while progress.yml and every reference-mapping doc mentioned rebind / pinned resolver / proxy
egress **ZERO** times, and it still listed redirect hops as unvalidated when
`guard_playwright_navigation` re-validates every hop and a test pins it. Corrected; the
residual (TTL-0 rebind between check and connect) is now DEBT-118, which exists. See
`designs/D17.4.md`.

### D17.5 · Dependency pinning policy — `MISSING`
**Hermes.** Every dependency needs an upper bound (`>=floor,<next_major`); git URLs pinned to a
40-char SHA; GitHub Actions pinned to SHA; CI-only pip pinned exact. Established after a real
supply-chain compromise and reinforced after a worm campaign.
**StackOwl.** Not stated.
**ADOPTED 2026-09-05 — and it was cheap, but not where the entry implies.** MEASURED first: 44
dependencies with **2** upper bounds, and 8 `uses:` lines with **ZERO** SHA pins — all eight on
mutable tags (`@v4`, `@v2`, `@v5`). Two of the four clauses were VACUOUS (0 git-URL deps, 0
CI-only pip installs) and a test now asserts they stay so. THE ACTION HALF IS THE SHARPER ONE:
`uv.lock` is committed, so installs are already reproducible and the dependency clause protects
the RESOLUTION step — but a lockfile does not cover CI actions at all, and
`actions/checkout@v4` is whatever that tag points at when the job runs, with the repository's
credentials. All 8 now pin a 40-char commit SHA (annotated tags dereferenced to the commit —
`astral-sh/setup-uv@v5` is annotated, so pinning the tag object would have pinned nothing
useful) with a `# v4` comment a test requires. BOUNDS DERIVED FROM THE LOCKED VERSION, not the
floor, so the existing lock satisfies every new specifier by construction: `uv lock --check`
resolves 210 packages and the lock diff has ZERO `version =` changes — only the
`[package.metadata] requires-dist` block moved. See `designs/D17.5.md`.

### D17.6 · Authz & tenancy — `AHEAD`
**Hermes.** Per-session approval + profile isolation + token locks. No principal model.
**StackOwl.** `authz/` `BoundsSpec` (5 axes, with an honest statement of which are actually enforced),
`tenancy/` principals + owned repositories, `audit/` hash-chained append-only log.
**Ask.** Keep. This is enterprise-grade and Hermes has nothing comparable.
**KEEP CONFIRMED 2026-09-05, and the "honest statement" re-measured axis by axis — it is
honest.** `ENFORCED_AXES = {"tools"}`, and `enforcement.py` does more than document the gap: a
task-scoped divergence on an unenforced axis is REFUSED at construction, fail closed, whether
TIGHTER or looser, because "a task envelope that diverges on an axis no seam enforces would
manufacture false confidence (e.g. `network: none` that does not block the network)". That is
the defect this whole programme hunts, refused by construction. VERIFIED: nothing in `src/`
reads `BoundsSpec.network` (the `.network` hits are execute_code's sandbox flag on a different
object); `path_guard` anchors to a global `data_root()` rather than per-owl roots. ONE PLACE
TWO TRUE STATEMENTS READ AS CONTRADICTORY: bounds.py called `data_owner_id` "enforced by
tenancy OwnedRepository (already)" while enforcement.py excludes it — both correct, because
OwnedRepository constrains EVERY query to its owner while NOTHING plumbs a task-scoped value,
so a per-task divergence cannot be honoured. They now cross-reference with that distinction
stated. ADDED: a tripwire that the CLOSED enumeration stays closed in BOTH places — an axis in
`BoundsSpec` but absent from `_AXIS_UNSET` is never walked, so it would be a constraint no seam
enforces and no check refuses. See `designs/D17.6.md`.

---

# D18 · Configuration, state, operations

### D18.1 · Config vs. secrets discipline — `MISSING`
**Hermes.** A hard rule: `.env` is **secrets only** (API keys, tokens, passwords). **Every** behavioral
setting — timeouts, thresholds, feature flags, display prefs — lives in `config.yaml`. PRs that tell
users to "set X in your .env" are rejected unless X is a credential. Internal code may bridge a config
value to an env var, but user-facing docs always point at `config.yaml`.
**StackOwl.** `Settings` merges YAML + `STACKOWL_*` env with env taking priority — both channels are
equally blessed.
**Ask.** Adopt the split? It makes config discoverable and prevents env sprawl.
**MEASURED 2026-09-05 — THERE IS NO SPRAWL, and adopting the rule verbatim would have been
WRONG.** All 18 `STACKOWL_*` variables were read and every one has a reason to be
environmental: BOOTSTRAP (9 — you cannot read config.yaml to learn where config.yaml is),
HOST-SPECIFIC (2 — `CONTEXT_CEILING`'s own docstring says "ONLY to opt into a host-specific cap
(e.g. to bound KV-cache RAM on a constrained inference server)", and moving that to YAML would
ship one machine's RAM limit to another), TERMINAL CONVENTION (2 — the NO_COLOR /
prefers-reduced-motion family), LOGGING BOOTSTRAP (2 — logging configures before Settings
loads), DEPLOYMENT SECRET (1, read at import before SecretResolver exists, with its fallback's
weakness stated), UNWIRED (1). AND THE SECRETS HALF IS ALREADY ANSWERED MORE STRONGLY: there is
no `.env` at all — `config/secret_resolver.py` resolves a REFERENCE (`keychain:`, `file:`, or an
env name), so the config file holds a pointer and the strongest option is the OS keychain rather
than a dotfile. WHAT WAS MISSING is not a migration but the QUESTION: today's discipline is held
by careful authors and nothing asks when the next variable is added. A tripwire now classifies
every `STACKOWL_*` name against five reasons — and "behavioural" is deliberately NOT one of
them, because a behavioural setting has a home and it is not the environment. See
`designs/D18.1.md`.

### D18.2 · Config surface & loaders — `DIVERGENT`
**Hermes.** ~19 top-level sections, a 1,616-line annotated example file, and **three** loaders
(CLI / subcommand / gateway-raw) with a documented "know which one you are in" warning — an
acknowledged wart.
**StackOwl.** One `Settings` class, pydantic-validated, one loader. **Ours is better.**
**Ask.** Keep ours. Adopt their annotated example file as a docs artifact?
**MEASURED 2026-09-05 — THE ARTIFACT IS WORTH HAVING; THEIR WAY OF MAINTAINING IT IS NOT.**
The loader half is settled in our favour and was re-checked rather than assumed:
`settings_customise_sources` returns `(env_settings, _YamlSource(...))` — one loader, no
"know which one you are in" warning to inherit. The example half is a real gap: **210
settable fields across 33 top-level sections** (36 nested models at every depth, 52
top-level keys) and **zero** example files, so an operator's only route to the surface was
1,100 lines of `settings.py`. But theirs is 1,616 HAND-WRITTEN lines — a second copy of the
surface, correct only until someone forgets, which is `CLAUDE.md` shape 3. Ours is
**GENERATED**: `scripts/gen_config_example.py` walks `Settings` and emits every field with
its default and its own `Field(description=...)`; a tripwire regenerates and compares BYTES,
so adding a setting without regenerating fails the gate. It ROUND-TRIPS (`Settings(**it)`
accepts it) and it INVENTS NOTHING — **107 of 210 fields carry a description**, and the
other 103 emit no comment rather than a guessed one, so the file shows honestly where the
model's documentation stops. **AND THE GUARD IMMEDIATELY EARNED ITSELF:** it failed under
pytest only, which exposed four `*_dir` defaults resolving against `STACKOWL_HOME` — the
checked-in artifact was carrying absolute paths wrong on every other machine AND leaking the
operator's home directory name into a publishable doc. Rendering home-derived paths as
`~/.stackowl/...` fixed portability, the leak, and reproducibility together. See
`designs/D18.2.md`.

### D18.3 · Profiles / multi-instance — `MISSING`
**Hermes.** Fully isolated instances, each with its own `HERMES_HOME` (config, keys, memory, sessions,
skills, gateway). One function `_apply_profile_override()` sets the env var **before any import**;
every path helper scopes automatically. Rules: always `get_hermes_home()` for code paths and
`display_hermes_home()` for user-facing text; profile operations are HOME-anchored not
HERMES_HOME-anchored so `hermes -p coder profile list` sees all profiles. **Profiles are islands on
purpose** — a PR adding config inheritance between them was rejected as fighting the design.
**StackOwl.** One `StackowlHome`; all state under `~/.stackowl/`. No profiles.
**Gap.** No way to run a work assistant and a personal assistant side by side, and no clean test isolation.
**Ask.** Do you want multiple isolated StackOwl instances? It is also how Kanban (D07.6) gets its
worker isolation.
**MEASURED 2026-09-05 — `MISSING` IS WRONG IN BOTH DIRECTIONS, and the launcher was the
instance-hostile part.** The STORAGE half is already complete: **all 25 `StackowlHome`
accessors returning a Path are contained by `STACKOWL_HOME`, 0 escapes** — database, logs,
config, kuzu graph, skills, plugins, secrets, the runtime socket and the pid file — and the
root is read at CALL time. The second clause of the gap, "no clean test isolation", is
simply FALSE: `tests/conftest.py` already redirects the home per run, added after test data
reached the operator's live `USER.md`. **But "it works today" was also wrong, and a panel
lens falsified it:** `./start.sh`, which `CLAUDE.md` mandates as *the* restart path,
hardcoded `$HOME/.stackowl` twice and swept processes by NAME
(`pgrep -f "python3? -m stackowl"`), blind to which home it was acting on — so
`STACKOWL_HOME=… ./start.sh` would have KILLED the running instance, deleted its pid and
socket, and overwritten its stdout log. **Root cause: the single-accessor rule holds across
all of `src/` and broke in the two files written in BASH, where no guard this repo owns
could see it** — `start.sh` and `scripts/full_suite.sh:20`, the only two of 20 shell files.
Both now ASK `StackowlHome` and refuse to act if it cannot be resolved; the sweep decides
which home a PID belongs to and warns rather than killing when it cannot tell (proven both
directions against the live platform). Two further fixes: the ONE import-time frozen path in
`src/` (`plugins/index.py`, found by AST sweep, the only one of its kind) now resolves at
call time — it was the single exception that would have made a `--profile` flag silently
wrong; and `STACKOWL_HOME` is documented in the generated config example, guarded by a
containment tripwire so the claim cannot rot. **NOT CLAIMED: multi-instance is not
supported.** Five process-level names still collide — keychain service strings, the Telegram
bot token (no 409 handler), the service-unit filename, and the webhook/MCP ports (8766 is
bound right now). That is ESC-136. **And this entry's claim that profiles are "how Kanban
gets its worker isolation" is FALSE** — D07.6's own re-measurement says `tasks` IS the
durable board and isolation comes from `lease_owner`/`lease_expires_at`, not processes; the
`blocks: [D07.6]` edge is dropped. See `designs/D18.3.md`.

### D18.4 · State-path discipline — `PARITY`
Both mandate a single accessor (`get_hermes_home()` / `StackowlHome`) and ban hardcoded home paths.
Hermes additionally separates the **display** form. Their note that this was the source of 5 bugs in
one PR matches our own experience.
**Ask.** Add a display-form helper?
**MEASURED 2026-09-05 — NO to the helper, and asking the question found TWO defects,
ONE HIDING THE OTHER.** The helper is declined on evidence: the live DB holds 20 messages
containing `/home/boss/`, 14 from the assistant, and every one is **model-authored prose**
(runnable instructions like `cd /home/boss/.stackowl/workspace && …`) that the model emits
because it SEES absolute paths in tool results — so a helper wired at the five formatting
sites fixes **0 of 14**. One of those five, `tools/system/shell.py:784`, is the tool SCHEMA
the model reads, and `ShellTool` dispatches through `create_subprocess_exec` with `workdir`
passed straight to `cwd=` unexpanded, so a `~` there is a latent breakage. The reference's
own history agrees: their helper caused an outage and the fix reverted their tool
description to a static string — the same site — and their motivation was that hardcoded
homes broke the model under PROFILES, which D18.3 recorded as explicitly unsupported. **A
display helper is profile machinery bought before the profile decision it serves.**
**WHAT MEASURING IT FOUND:** `settings_customise_sources` accepted `init_settings` and never
RETURNED it, so pydantic discarded every keyword argument — `Settings(webhook={"port":9999})`
came back 8766 and `Settings(**{"no_such_key":1,"webhook":{"port":"not-a-number"}})` was
ACCEPTED. That made two tests vacuous (a supervisor contract for an ENABLED webhook receiver
asserted against a disabled one) and made **D18.2's own round-trip acceptance check validate
nothing**. Fixing it UNMASKED a second defect it had been concealing: the example config's
`~/.stackowl/...` paths had measured as absolute only because the YAML value was being
discarded; with kwargs honoured they come back RELATIVE — `Path("~/x")` is a directory
literally named `~`. `ConfigPath` (a BeforeValidator expanding `~` and `$VARS`) now covers all
five Path-typed settings fields. Third fix: a mistyped SECTION NAME silently discarded that
whole section (`extra="ignore"` at the root, `forbid` in every sub-model) — it now WARNS and
still boots, because locking the operator out to catch a typo is a bad trade. **And the half
worth keeping from the reference is the one THEY lost:** the accessor rule holds in our `src/`
but nothing enforced it (D18.3 guarded shell only, so Python was not one short — it was zero),
while their tree has the rule in its guide, no guard at all, and ~20 files that build the home
by hand. Guard added. See `designs/D18.4.md`.

### D18.5 · Test isolation — `PARTIAL`
**Hermes.** `scripts/run_tests.sh` is **mandatory** — enforces CI parity (unset credential vars,
`TZ=UTC`, `LANG=C.UTF-8`, xdist workers, subprocess-per-test-file isolation so module-level state
cannot leak). An autouse fixture redirects `HERMES_HOME` to a temp dir. Auto-retries a failing file
once and reports `⚠ FLAKY` — treated as a bug, not noise.
**StackOwl.** `pytest`; full runs hang on the Jetson, so we run targeted paths with timeouts.
**Gap.** Our full suite is not runnable — Hermes' subprocess isolation is plausibly *why* theirs is.
**Ask.** Would subprocess-per-file isolation fix our hang?
**MEASURED 2026-09-05 — THREE OF THIS ENTRY'S PREMISES ARE FALSE, AND ONE IS A LESSON FOR
THE WHOLE PROGRAMME.** (1) "Our full suite is not runnable" — it runs about every 1.7 hours:
20 suite logs over 29 hours, 17 completed, median ~30:08, and today `11994 passed, 18
skipped, 0 failed in 1856.98s, rc=0`. (2) "xdist workers" — the reference platform
**explicitly DROPPED xdist**, under a docstring heading "Why drop xdist entirely?": *"xdist's
persistent workers accumulate state across files, which is exactly the leakage we wanted to
fix."* It is not in their dependencies. (3) **The error came from their own contributor guide,
which still describes the runner as using xdist — a doc stale relative to its code, and this
map copied it. A claim sourced from the reference's DOCUMENTATION is not evidence about the
reference's BEHAVIOUR, and that applies to every remaining item here.**
**WHAT WAS ACTUALLY BROKEN:** the full run is the programme's only cross-pollution detector,
and **9 of 17 completed runs — 53% — printed `SUITE TREE CHANGED … this verdict is about NO
SINGLE TREE`.** The 2026-09-04 fix LABELLED the moving run instead of curing it, which is
fixing WHAT happened; a warning nobody acts on is not a fix, and nine times nobody did. A
moving run now RE-RUNS ITSELF once (bounded) and states `SUITE TREE STILL` positively. The
completion stamp is also now written from an EXIT trap: it used to sit below a piped
fingerprint call inside `set -euo pipefail`, and one kept log ends with results and NO `SUITE
DONE`, so the documented collector reports a run finished 29 hours ago as still going.
**THE ONE NON-VACUOUS PARITY CLAUSE EARNED ITSELF IMMEDIATELY.** Credential-unsetting has
nothing to bind to here (one harness-owned variable; every operator secret is a `file:`
reference under a home conftest already redirects). Pinning `TZ=UTC` exposed
`_next_local_hour_iso` — and its ROOT CAUSE was not the timezone but that it was a SECOND
implementation: `compute_next_run` had always resolved `system.timezone` for this exact
computation, so every job's FIRST run used one clock and every LATER run another. The
duplicate also silently dropped MINUTES (`daily@04:30` seeded at 04:00, three schedules). It
is DELETED and routed through the canonical function, along with `_daily_schedule_hour`, the
dead `next_hour` parameter at 11 call sites, and the landmine note whose mine is now cleared.
**REJECTED, with reasons, so they are not re-proposed:** subprocess-per-file isolation (it
does not detect leakage, it makes leakage invisible — and detection is what we need),
xdist, credential-unsetting, and a copied-snapshot run (stronger, but imports resolve
`stackowl` from an editable install, so it only takes effect by redirecting PYTHONPATH —
a wrong verdict from a mis-wired snapshot is worse than a late one). See `designs/D18.5.md`.

### D18.6 · Change-detector test ban — `MISSING`
**Hermes.** A named anti-pattern: never assert on data expected to change (model catalogs, config
version literals, enumeration counts). Test **invariants** — "every model in the catalog has a
context-length entry" — not snapshots.
**StackOwl.** Not stated; we have numeric-limit and count assertions.
**MEASURED 2026-09-05 — THE RULE IS RIGHT, AS STATED IT IS TOO BLUNT, AND PROSE DOES NOT
ENFORCE IT.** An AST sweep of 1,611 test files found **937** `len(X) == <int>` assertions and
nearly all are correct — `assert len(rows) == 1` after inserting one row is a contract about
the operation. Narrowing to counts of collections the codebase GROWS leaves **five**, and the
history says what they cost: `test_provider_catalog.py` bumped **15 -> 17 -> 49** (one commit
added 32 providers and had to edit the test), `test_discovery.py` pinned `== 77` on the tool
registry as a one-time parity check with the hand-written list discovery replaced, and
`test_graph_reconciliation.py` asserted `== 7  # one per TRAIT_NAMES entry` — the comment
already stating the relationship the assertion refused to. All five converted, and each
replacement is STRONGER: the catalog now compares the loaded NAME SET to the directory it
loads from (catching a name/filename disagreement and two files collapsing onto one key,
neither of which a count can see), and discovery compares two POPULATIONS (77 registered, 77
discovered, difference 0 — a count cannot tell "discovered but never registered" from
"deliberately removed"). **THE CARVE-OUT MATTERS MORE THAN THE RULE:** two count assertions
here are deliberate — the closed authz axis set (`== 6`) and the closed in-tree vendor set
(`== 4`) — and for those, breaking on growth IS the function. A blunt ban would force someone
to weaken a multi-tenancy guard, and the costs are asymmetric: the catalog error is a one-line
edit, while deleting a closed-set assertion silently un-guards an authz enumeration and
nothing would say so. The rule adopted is therefore narrower: **a count literal is legitimate
when the TEST produced the number, or when the number is the subject of a stated closure
decision; it is the anti-pattern when it passively echoes a fact owned elsewhere.** **AND IT
IS ENFORCED, because the reference measures what prose achieves:** they state the rule in
their contributor guide with banned examples, have NO lint rule, NO CI job and NO test for it,
and their own suite violates it verbatim — including a file that cites the rule by name about
150 lines from where it breaks it. See `designs/D18.6.md`.
**Ask.** Adopt as a review rule?

### D18.7 · Installer & platform reach — `PARTIAL`
**Hermes.** One-line install on Linux/macOS/WSL2/Termux and **native Windows** (PowerShell), bundling
uv, Python, Node, ripgrep, ffmpeg, and a portable MinGit so shell commands work without a system Git.
Docker compose, Nix flake, systemd units.
**StackOwl.** `uv sync` + `setup --minimal` + `install-service`.
**Ask.** Distribution is adoption. How far do you want to go?
**MEASURED 2026-09-05 — THE INSTALLER QUESTION IS YOURS; MEASURING IT FOUND THAT EIGHT
ARCHITECTURAL CHECKS RUN NOWHERE.** On distribution the evidence is one-sided: ONE human
in ~4,037 commits, NO README at the repo root, ONE tag (`v0.1.1`, from the pre-rewrite
TypeScript era), no published package and no pushed image — so an installer serves users
who do not exist, and the reference's equivalent is 3,127 + 3,830 lines plus a Rust GUI
bootstrapper, most of it managing Node, which a `uv` project does not have. Escalated as
ESC-139. **WHAT MEASURING FOUND:** `scripts/boundaries/` holds B1-B9; **five exit 1** —
b1 50 import cycles, b2 196 oversized files, b3 4 ASCII-only regexes, b5 145 silent
excepts, b6 mypy --strict — and **NOT ONE of the eight was in `scripts/tripwires.sh`**,
their only CI home being a pre-commit hook still running `cd v2 &&` after the v2->root
migration deleted `v2/` on 2026-06-17. **FOUR OF THEM ENFORCE STANDING OPERATOR RULES**
(cross-platform, "every except logs", "never hardcode English keyword lists", file size).
**b4 IS FIXED AND WIRED, AND WHY IT WAS NEVER WIRED IS THE ROOT CAUSE: it was
UNWIREABLE** — it fired on `os.path.expandvars` (portable; pathlib has no equivalent), a
`hasattr(signal,"SIGHUP")`-guarded call, a security REGEX that DETECTS /tmp staging, help
text, and a container-side mount. Made sound first (carve-outs for docstrings, hasattr
tests, posix-named functions, and an allowlist with a reason each), now 0 across 842
files, wired, and the gate proven to fail on it. Its `os.path` rule was DELETED: os.path
IS cross-platform, so that was a STYLE rule smuggled into a portability checker, and
ruff's PTH ruleset does it properly (23 findings measured; enabling it is a separate call
because several sit in shell.py's path-safety code and the ruff baseline may not rise).
**PLATFORM REACH, MEASURED:** Windows has exactly ONE hard blocker — `split_process`
defaults True and the gateway/core IPC uses asyncio's unix-socket helpers, which CPython
exports only under `hasattr(socket,"AF_UNIX")` (4 call sites, boot crash). macOS degrades
gracefully, but code execution is unavailable there and nothing says so at boot.
**DELETED (retired means deleted):** `release.yml` (npm-era, referencing a package.json
and a build script that do not exist — and D17.5 pinned commit SHAs onto it the day
before without noticing it could never run), `deploy/migrate-to-root.sh` and
`deploy/ci-post-migration.yml`. See `designs/D18.7.md`.

### D18.8 · Observability — `AHEAD`
**Hermes.** `hermes_logging.py` → `agent.log` / `errors.log` / `gateway.log`, browsable via
`hermes logs`. Optional Langfuse plugin. No trace propagation.
**StackOwl.** JSONL with `trace_id`/`span_id`/`parent_span_id` propagated through every async hop via
contextvars, mandated 4-point logging, `read_logs` tool, `trace` CLI.
**Ask.** Keep — this is a clear StackOwl win and it is what makes debugging the rewrite tractable.
**VERIFIED 2026-09-05 — THE CLAIM HOLDS, AND THE GAP WAS THAT IT HAD NO ALARM.** A real
turn produces a nested tree: one trace with **56 records across 11 spans**, `triage`,
`dispatch`, `memory.recall` and `classify` all parented to the backend `run` span, and all
eleven trace fields present on every one of 21,523 live log records. The attribution defect
that prompted the trace work STAYED FIXED: blank `trace_id` on cost records went **54.5%
(2026-08-29) -> 3% (08-30) -> 0-2% daily -> 0% today**; the 51% all-time figure is history.
**The untraced 80% of log lines is not a defect and checking the denominator mattered** —
it is `registry.register` (1,813), `loader._load_one` (1,638), `store.upsert` (1,638) and
`startup` (3,495): boot-time registration and recovery sweeps with no request to belong to.
Likewise `stackowl.tasks` at 2-of-396 is entirely boot recovery, not task execution.
**WHAT WAS MISSING:** `_bind_job_trace` is pinned by THREE test files — and they pin the
CODE, that a scheduled job binds a lane. NOTHING WATCHED THE EFFECT. The original 54.5% was
found because a person happened to query for it, so a new background caller reaching a
provider outside any TraceContext would reappear the same silent way. This repo's standing
question — if this degrades silently, what notices? — had the measured answer NOTHING, asked
of its own observability. `UnattributedSpendContributor` now rides the 5-minute health sweep
that already exists (never a second loop): degraded at >=10% of a 24h window (measured — the
failure was 54.5%, healthy is 0-3%), a floor of 20 records so a ratio is never computed over
a denominator that cannot carry it, DEGRADED never DOWN, an instrument failure reported as
"could not measure" rather than as a regression, and the OK case carrying its denominator.
Live: `ok — 1 of 175 unattributed (1%) in 24h`. **THE RESIDUAL IS CHARACTERISED, NOT
GUESSED:** 142 records since 08-30, every one with `system_prompt_chars = 0`, no owl,
180-244 input tokens, falling 107/day to 1/day. It is NOT attributed to a caller here
because `cost_records` has no column naming the code path — which is exactly why the first
54.5% needed a human to reason it out. ESC-140. See `designs/D18.8.md`.

### D18.9 · Migrations — `AHEAD`
**Hermes.** No migration framework visible; schema evolves in `hermes_state.py`.
**StackOwl.** 90 idempotent SQL migrations with a runner.
**Ask.** Keep.
**VERIFIED 2026-09-05 — KEPT, AND VERIFYING IT FOUND THE HALF THAT WAS MISSING.** Two
specifics are stale: there are **136** migrations, not 90 (all applied, highest 0136); and
"idempotent" describes the RUNNER, not the SQL — 68 of the 136 use `ALTER TABLE`, which
SQLite cannot re-run, so safety comes from `_apply` skipping any version already in the
ledger. **THE DEFECT: `_apply` has always written `sha256(sql)` into
`schema_migrations.checksum` and NOTHING in src/ ever read it back.** An applied migration is
skipped by version, so editing its file changes what a FRESH install builds while the
existing database keeps the old schema. **It had already happened: 130 checksums matched and
6 DRIFTED**, all six edited by one commit ("no vendor names in shipped code") with ZERO
SQL-statement lines changed — no schema diverged, and nothing would have told the difference.
**AND THERE IS A SECOND DATABASE:** `tests/_schema_template.py` builds schema from the
migration FILES while the live DB holds what was APPLIED, and `stackowl db restore` replaces
the live DB from a backup whose ledger already lists those versions. **WHY THE READER WAS
NEVER WIRED: it was UNWIREABLE** — a byte comparison would have opened with six false alarms,
every one a comment edit, which is the same finding D18.7 recorded for the cross-platform
checker one item earlier. So `semantic_checksum` hashes the STATEMENTS, taken from
`_split_sql` — the very function the runner uses to EXECUTE — with a new `keep_comments=False`
mode. That is deliberately not a normalisation RULE: the tokenizer deciding what is a comment
is the one deciding what gets run, so there is no second copy to keep correct. Measured: all
six drifted files hash IDENTICALLY under it. The column is added by the RUNNER at bootstrap,
not by a migration — it was first written as 0137 and the first behavioural test failed,
which is how that was caught: `schema_migrations` is the runner's own bookkeeping, so a
numbered migration would have to run before the column could be read and no test fixture with
its own migrations directory would ever get it. **WARN, NOT REFUSE, on a principle rather
than a precedent** (the repo's two precedents disagree): fail closed when refusing PREVENTS
the harm, warn when the harm already happened — refusing to boot cannot un-diverge a schema.
An applied migration whose FILE IS GONE now warns too; `_pending` only ever computed
disk-minus-applied. **AND A CLAIM RELAYED EARLIER IS REFUTED:** the "19% of suite time
recoverable by converting migration-replaying tests" figure is wrong — the populations are
disjoint (49 sites inside `tests/db`, 35 outside) and the template's own docstring forbids its
use in tests OF the migrations. See `designs/D18.9.md`.

---

# Cross-cutting: the dedup targets

Places where **StackOwl already has two of something**, or where adopting Hermes would create a
duplicate. Resolve these *before* building, per your standing rule.

| # | Duplication risk | Detail |
|---|---|---|
| X1 | Two work queues | `objectives/` vs. a Hermes-style Kanban board (D07.6, D15.4). Pick one kernel. |
| X2 | Two tool-selection mechanisms | Per-turn budgeter (D01.3) vs. toolsets + progressive disclosure (D05.2/D05.4). The budgeter should go. |
| X3 | Two learning paths | Scheduled miners (`learning/`) vs. per-turn background review (D09.1). Decide whether they compose or one replaces the other. |
| X4 | Two memory models | The extraction pipeline (D08.1) vs. a curated two-file layer. Layer, do not fork. |
| X5 | Two loop-guard systems | `TurnProgressSupervisor` vs. Hermes `tool_guardrails` (D05.7). Confirm they are not the same thing twice. |
| X6 | Four near-duplicate channel adapters | ~24 parity modules across telegram/slack/discord/whatsapp (D12.3). Collapse to one relay + descriptors. |
| X7 | Two skill-injection paths | System prompt (`assemble`) vs. slash-command user message (D10.5). |
| X8 | Two verification systems | `acceptance_authority` (blocking) vs. Hermes `verification_evidence` (passive). Ours is a superset — do not add theirs. |
| X9 | Two provider-error paths | `circuit_breaker` + `escalation_signal` vs. Hermes `error_classifier`. Compose: classifier feeds our ladder. |
| X10 | Two config channels | YAML and `STACKOWL_*` env both blessed (D18.1). Pick the split. |

---

# Suggested walk order

Not a plan — a reading order, so each conversation has what the previous one decided.

1. **D01** — prompt economics. Everything downstream depends on the answer.
2. **D05** — tool architecture (toolsets, gating, disclosure). Resolves X2.
3. **D02** — core agent shape. Decides whether the pipeline survives.
4. **D08 + D09** — memory and learning. The largest product question. Resolves X3, X4.
5. **D03** — context management. Depends on D01 and D02.
6. **D12** — channels. Resolves X6; largest mechanical win.
7. **D07 + D15** — delegation and autonomy. Resolves X1.
8. **D10 + D11** — skills and recall.
9. **D16 + D17 + D18** — extensibility, security, operations.
10. **D04 + D06 + D13 + D14** — breadth items, once the core is settled.

---

*Compiled 2026-07-25 from a full read of `do_not_push_to_git_research_only/hermes-agent` @ `689b51bef`
and `src/stackowl/` @ `eb079ea1`. Every Hermes claim is traceable to a file named inline or to that
repo's own `AGENTS.md`. Every StackOwl claim was verified against the working tree, not assumed.*
