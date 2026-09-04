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

### D04.3 · Credential pool & rotation — `MISSING`
**Hermes.** `agent/credential_pool.py` — persistent multi-credential pool for same-provider failover;
the error classifier can decide "rotate credential" as a distinct action from "fall back to another
provider". Plus `credential_sources.py` / `secret_sources/` (1Password, Bitwarden, command).
**StackOwl.** One key per provider from config.
**Ask.** Do you run multiple keys per provider today?

### D04.4 · Auxiliary-model router — `MISSING`
**Hermes.** `agent/auxiliary_client.py` — **one** resolution chain for every side-LLM task
(compression, session search, vision, titles, curator review, web extraction). Config `auxiliary:`
lets each task pin its own provider/model/base_url/max_tokens/reasoning_effort; `auto` walks an
ordered fallback list.
**StackOwl.** Side-LLM calls are scattered — `acceptance_llm`, classifiers in `interaction/`,
`critic_scorer`, `reflection`, judges — each resolving its own model.
**Gap.** No single seam for "cheap model work", so cost/latency of side tasks is unmanaged.
**Ask.** This looks like a high-value, low-risk early adopt. Agree?

### D04.5 · Tier escalation — `AHEAD`
**Hermes.** `smart_model_routing` config + `/fast`; no in-loop escalation.
**StackOwl.** `providers/tier_selector.py` + `escalation_signal.py` — a same-turn ESCALATE sentinel
that re-runs the turn on a stronger tier when a breaker opens.
**Ask.** Keep. Confirm it survives.

### D04.6 · Rate limiting & circuit breaking — `PARITY`
**Hermes.** `rate_limit_tracker.py`, `nous_rate_guard.py`, jittered decorrelated backoff
(`retry_utils.py`) to avoid thundering herds.
**StackOwl.** `rate_limiter.py`, `circuit_breaker.py`, plus a shared Telegram flood guard.
**Ask.** Adopt jittered backoff specifically? Ours may be fixed exponential.

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

---

# D11 · Session state & recall

### D11.1 · Session store — `PARITY`
**Hermes.** `hermes_state.py` — `SessionDB`, SQLite **WAL**, source tagging per session
(`cli`/`telegram`/…), model config recorded per session.
**StackOwl.** SQLite with pool + 90 migrations, `conversations`/`messages`.
**Ask.** None.

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

### D11.4 · Session export & recap — `MISSING`
**Hermes.** `session_export_md.py`, `session_export_html.py`, `session_recap.py`, `session_filters.py`,
`session_listing.py`, `/sessions`, `/resume`.
**StackOwl.** `export/` covers full-archive backup, not per-session export/recap.
**Ask.** Product feature or nice-to-have?

### D11.5 · Trajectory capture for training — `MISSING`
**Hermes.** `agent/trajectory.py` + `batch_runner.py` + `trajectory_compressor.py` (70KB) —
batch trajectory generation and compression **for training tool-calling models**. This is Nous being
a research lab.
**StackOwl.** Nothing.
**Ask.** Out of scope, or does the "agentic OS" vision want its own training data?

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
**Ask.** Do our plugins get lifecycle hooks and CLI commands, or only tools?

### D16.2 · "Plugins must not touch core" rule — `MISSING`
**Hermes.** A named policy: plugins may not modify `run_agent.py`, `cli.py`, `gateway/run.py`,
`main.py`. If a plugin needs something the framework does not expose, **widen the generic plugin
surface** — never special-case the plugin in core. One PR removed 95 lines of hardcoded plugin argparse
from `main.py` for exactly this reason.
**StackOwl.** No stated rule.
**Ask.** Adopt verbatim. Costs nothing, prevents a known decay mode.

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
**Ask.** Adopt — it is the discipline that keeps a fast-moving core fast-moving.

### D16.5 · MCP — `PARITY`
**Hermes.** Client with OAuth manager, stdio watchdog, catalog, security review, dashboard OAuth flow;
plus `mcp_serve.py` exposing Hermes as an MCP server. MCP is **rung 5** — preferred over a new core tool.
**StackOwl.** `mcp/` covers both directions with allowlist, probe, cache, tool exposure.
**Ask.** Adopt the *doctrine* (MCP before core tool), not the code.

### D16.6 · Integrations — `DIVERGENT`
**Hermes.** Integrations arrive as **skills + CLI** (Google Workspace, Notion, Airtable, Apple apps,
GitHub — 180 skills total) or as gated tools (Home Assistant, Spotify, Feishu). Almost nothing is a
core tool.
**StackOwl.** `integrations/` with OAuth manager + Gmail + Google Calendar as first-class code.
**Gap.** Ours is rung 6 for something they solve at rung 2.
**Ask.** Would Gmail/Calendar be better as a skill + CLI command pair?

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

### D17.2 · Prompt-injection pattern library — `PARTIAL`
**Hermes.** `tools/threat_patterns.py` — one library organized **by attack class**, each pattern a
`(regex, id, scope)` tuple where scope selects which scanners use it (`"all"` everywhere,
`"context"` for context files + memory + tool results). Consumed by the prompt builder, the memory
tool, and the tool-result delimiter system.
**StackOwl.** `infra/prompt_safety.py` — a fence neutralizer, structural (no keyword lists, per your
standing rule). Narrower scope.
**Ask.** Extend ours to tool results and context files with the same scoping idea?

### D17.3 · External security scanner — `MISSING`
**Hermes.** `tools/tirith_security.py` — runs an external binary to scan commands for content-level
threats (homograph URLs, pipe-to-interpreter, terminal injection). **Exit code is the verdict**;
JSON stdout enriches but never overrides. Configurable fail-open; auto-installs from GitHub releases
with **SHA-256 verification** and cosign when available.
**StackOwl.** None.
**Ask.** Worth it, or does our sandbox make it redundant?

### D17.4 · URL / path / egress guards — `PARITY`
**Hermes.** `url_safety.py`, `path_security.py` (shared resolve+relative_to helpers), `website_policy.py`,
`docs/security/network-egress-isolation.md`.
**StackOwl.** `infra/net/ssrf_guard.py`, `tools/io/path_guard.py`, `web_search/providers/_egress.py`.
**Ask.** None.

### D17.5 · Dependency pinning policy — `MISSING`
**Hermes.** Every dependency needs an upper bound (`>=floor,<next_major`); git URLs pinned to a
40-char SHA; GitHub Actions pinned to SHA; CI-only pip pinned exact. Established after a real
supply-chain compromise and reinforced after a worm campaign.
**StackOwl.** Not stated.
**Ask.** Cheap. Adopt?

### D17.6 · Authz & tenancy — `AHEAD`
**Hermes.** Per-session approval + profile isolation + token locks. No principal model.
**StackOwl.** `authz/` `BoundsSpec` (5 axes, with an honest statement of which are actually enforced),
`tenancy/` principals + owned repositories, `audit/` hash-chained append-only log.
**Ask.** Keep. This is enterprise-grade and Hermes has nothing comparable.

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

### D18.2 · Config surface & loaders — `DIVERGENT`
**Hermes.** ~19 top-level sections, a 1,616-line annotated example file, and **three** loaders
(CLI / subcommand / gateway-raw) with a documented "know which one you are in" warning — an
acknowledged wart.
**StackOwl.** One `Settings` class, pydantic-validated, one loader. **Ours is better.**
**Ask.** Keep ours. Adopt their annotated example file as a docs artifact?

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

### D18.4 · State-path discipline — `PARITY`
Both mandate a single accessor (`get_hermes_home()` / `StackowlHome`) and ban hardcoded home paths.
Hermes additionally separates the **display** form. Their note that this was the source of 5 bugs in
one PR matches our own experience.
**Ask.** Add a display-form helper?

### D18.5 · Test isolation — `PARTIAL`
**Hermes.** `scripts/run_tests.sh` is **mandatory** — enforces CI parity (unset credential vars,
`TZ=UTC`, `LANG=C.UTF-8`, xdist workers, subprocess-per-test-file isolation so module-level state
cannot leak). An autouse fixture redirects `HERMES_HOME` to a temp dir. Auto-retries a failing file
once and reports `⚠ FLAKY` — treated as a bug, not noise.
**StackOwl.** `pytest`; full runs hang on the Jetson, so we run targeted paths with timeouts.
**Gap.** Our full suite is not runnable — Hermes' subprocess isolation is plausibly *why* theirs is.
**Ask.** Would subprocess-per-file isolation fix our hang?

### D18.6 · Change-detector test ban — `MISSING`
**Hermes.** A named anti-pattern: never assert on data expected to change (model catalogs, config
version literals, enumeration counts). Test **invariants** — "every model in the catalog has a
context-length entry" — not snapshots.
**StackOwl.** Not stated; we have numeric-limit and count assertions.
**Ask.** Adopt as a review rule?

### D18.7 · Installer & platform reach — `PARTIAL`
**Hermes.** One-line install on Linux/macOS/WSL2/Termux and **native Windows** (PowerShell), bundling
uv, Python, Node, ripgrep, ffmpeg, and a portable MinGit so shell commands work without a system Git.
Docker compose, Nix flake, systemd units.
**StackOwl.** `uv sync` + `setup --minimal` + `install-service`.
**Ask.** Distribution is adoption. How far do you want to go?

### D18.8 · Observability — `AHEAD`
**Hermes.** `hermes_logging.py` → `agent.log` / `errors.log` / `gateway.log`, browsable via
`hermes logs`. Optional Langfuse plugin. No trace propagation.
**StackOwl.** JSONL with `trace_id`/`span_id`/`parent_span_id` propagated through every async hop via
contextvars, mandated 4-point logging, `read_logs` tool, `trace` CLI.
**Ask.** Keep — this is a clear StackOwl win and it is what makes debugging the rewrite tractable.

### D18.9 · Migrations — `AHEAD`
**Hermes.** No migration framework visible; schema evolves in `hermes_state.py`.
**StackOwl.** 90 idempotent SQL migrations with a runner.
**Ask.** Keep.

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
