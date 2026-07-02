---
title: StackOwl De-complication — Unblock Jarvis (Learning + Proactive)
status: final
created: 2026-07-01
updated: 2026-07-01
---

# PRD: StackOwl De-complication — Unblock Jarvis

## 0. Document Purpose

This PRD directs a fresh implementation session (subagent-driven, per house rules) through the simplification of StackOwl so it behaves like Jarvis: an assistant that visibly learns from interactions and reaches out proactively. It was produced from three in-session codebase audits (hot path, learning/proactivity, structural), a verified design pass, and owner decisions locked on 2026-07-01 (see `.decision-log.md`). The full file-level technical plan — exact files, line references, constants, commit sequencing — lives in `addendum.md`; this document holds the requirements and acceptance criteria. (Note: `.decision-log.md` is a dotfile — use `ls -a` to see it.) Implementation follows the owner's standing rules: minimal diffs, small verified commits merged to main, targeted test suites only (never full pytest on this box), idempotent migrations, features ship ON.

## 1. Vision

StackOwl today is fully wired but over-hedged. Every learning loop runs, every proactive job fires — yet the owner cannot see the assistant learn or feel it act, because months of defensive arcs (honesty, verification, trust, persistence) stacked clamps, gates, and redundant judges on top of every behavior. A trivial message crosses ~19 component hops and 3–4 serialized LLM calls; personality evolution is mathematically inert; preference learning is capped to three formatting fields; proactivity silently requires Telegram.

This effort removes the damping, not the safety. One canonical honesty gate replaces nine overlapping ones. One LLM call serves a clean turn instead of four. Learning signals get room to move and a single surfacing channel. Proactive content is always generated and delivered wherever the owner actually is. The result is the same trustworthy core — floors still catch failed turns, vetoes still block false claims — wrapped around an assistant that visibly adapts within a session and reaches out on its own.

## 2. Target User

### 2.1 Jobs To Be Done
- As the builder-operator (single user), see the assistant adapt to feedback within the same conversation, not "someday".
- Receive proactive check-ins and morning briefs reliably, on whatever channel is alive.
- Get fast responses — a simple message should not pay a 4-LLM-call tax.
- Keep the honesty guarantees already built (no dressed-up give-ups, no overclaims, no fabricated citations).
- Keep the codebase maintainable enough that future arcs don't require touching two parallel backends and nine gates.

### 2.2 Non-Users (v1)
- Multi-tenant / multi-user deployments. `tenancy/` and `authz/` are out of scope here (parked list, F5).

### 2.3 Key User Journeys
*(Internal tool, single operator — light form.)*

- **UJ-1.** Boss tells the owl "be more concise" on Telegram; the very next answer is measurably shorter, and the preference persists across channels.
- **UJ-2.** Boss doesn't open Telegram for a day; the morning brief is still generated, lands in the undelivered outbox, and greets him as a banner on whichever channel he uses next.
- **UJ-3.** Boss sends "thanks, looks good" — the reply arrives after a single model call, with no judge/router/classifier round-trips.

## 3. Glossary

- **Hot path** — the per-message pipeline: ingress → gateway → 8 steps (`triage → dispatch → classify → assemble → feedback → execute → parliament_step → consolidate`) → gate cascade → deliver.
- **Backend** — the executor of the 8-step pipeline. Two exist: `AsyncioBackend` (current default) and `LangGraphBackend` (becomes canonical per this PRD).
- **Gate cascade** — the post-execute sequence of honesty surfacers (`giveup_floor`, `overclaim_gate`, `grounding_gate`, `critical_failure`, `persistence_handoff`, etc.) run by each backend before deliver.
- **Delivery gate** — the NEW single module consolidating the gate cascade: structural facts computed once, one precedence ladder.
- **Shared seam** — the NEW backend-agnostic post-run module (delivery gate call, `persist_turn` ordering, acceptance verification, outcome capture, skill success updates) used by any backend.
- **Give-up judge** — LLM judge deciding whether the model may stop (`build_persistence_check`); distinct from the **structural veto** (pure tool-outcome tally override, no LLM).
- **DNA** — per-owl personality trait vector; nightly **evolution** mutates it; the **directive latch** converts trait bands into prompt directives with hysteresis.
- **lessons_index** — unified LanceDB corpus aggregating reflections, tool heuristics, and skills; surfaced each turn by `classify`.
- **Preference entry** — durable, identity-scoped record of a user preference, surfaced in every prompt's prefs_block.
- **Owner identity** — NEW channel-agnostic setting identifying the single user for proactive delivery, replacing Telegram allowlist inference.
- **UndeliveredOutbox** — existing store for proactive content that could not be delivered; flushed as a banner on next user contact.
- **Soak** — running the new default backend live for 7 calendar days with clean logs (per the FR-14 queries) before deleting the old one.
- **LS7 seam** — the existing per-turn hook that feeds a turn's MEASURED outcome into applied skills' `success_rate` (`_update_skill_success_rates`; application seam = `skill_view` tool calls). FR-4 adds `n_executions` there.
- **F088 ordering invariant** — `persist_turn` runs AFTER the honesty floors and inside the tool-outcome-ledger ContextVar binding, so a floored turn persists the user utterance only, never the dressed-up draft. The shared seam (FR-12) must preserve this.

## 4. Features

### 4.1 F1 — Visible Learning
**Description:** The learning loops exist and run; they are damped into invisibility. This feature retunes and consolidates them so adaptation shows up in actual prompts and answers. Realizes UJ-1.

#### FR-1: Un-damp DNA evolution
Nightly evolution can produce observable prompt changes. Retune constants only — keep the governor bound and latch mechanism (safety/anti-flap).
**Consequences (testable):**
- Per-run mutation clamp widened from ±0.1 to ±0.25 (`owls/evolution.py`).
- Latch bands narrowed from 0.70/0.60 to 0.62/0.55 (`owls/directive_latch.py`).
- After a simulated multi-night run with consistent signal, the DNA injector emits at least one directive (log: `[dna] injector.inject: exit — directives appended`).
- Existing evolution suites stay green (`tests/owls/test_evolution_feedback.py`, evolution journeys).

#### FR-2: Preference learning beyond output_style
Confident non-format feedback (tone, length, content; polarity set; referent = last answer) is persisted as a natural-language preference entry and surfaced in every subsequent prompt. Realizes UJ-1.
**Consequences (testable):**
- "be more concise" (classified confident, aspect=length) produces a preference entry via the existing `PreferenceStore` under the identity-scoped key.
- Entries are capped (~20, newest-wins merge); `/preferences` commands list and remove them.
- Next turn's prefs_block contains the new entry (surfacing already exists in `classify._gather_preferences`).
- Low-confidence or non-referent signals write nothing.
**Out of Scope:** learning from anything other than the FeedbackClassifier verdict (no keyword matching — multilingual rule).

#### FR-3: Single lesson-surfacing channel
Reflections are surfaced once per turn, via `lessons_index` only.
**Consequences (testable):**
- The separate recent-reflections block is removed from `classify`; lessons_index remains the sole reflection surface.
- The actions block (live action recall — different data) is unchanged.
- Classify and learning-acceptance suites stay green.

#### FR-4: Consolidate learning loops (6 → 4) and close the skill loop
**Consequences (testable):**
- `critic_scorer` handler merges into `reflection_writer` handler; one scheduler job replaces two; scoring output is unchanged.
- `increment_n_executions` is called in the existing LS7 seam (where skill `success_rate` is already nudged per applied skill), making the synthesizer's refine/deprecate phases reachable.
- Scheduler-assembly and memory suites stay green.

#### FR-5: Reflection loop never starves
**Consequences (testable):**
- `reflection_writer` no longer defers under load (`defer_under_load = False`); heavy jobs (dream_worker, kuzu_sync) keep deferring.
- Under simulated load, reflection_writer executes within its 15-minute cadence.

### 4.2 F2 — Robust Proactivity
**Description:** Proactive jobs generate content unconditionally; deliverability is decided at delivery time, not schedule time. Realizes UJ-2. [Owner-approved design change: schedule-time gate → delivery-time gate.]

#### FR-6: Channel-agnostic owner identity
**Consequences (testable):**
- A single owner identity setting exists in config; recipient resolution is deterministic (no allowlist inference).
- Resolution works for every configured channel adapter, not only Telegram.

#### FR-7: Delivery-time proactive gate
**Consequences (testable):**
- `check_in` and `morning_brief` are always seeded, targeting the owner identity.
- When no channel resolves at delivery time, the generated content lands in `UndeliveredOutbox` and is surfaced as a banner on the owner's next contact over any channel.
- Exactly-once delivery ledger semantics are preserved (no duplicate sends after outbox flush).
- Scheduler idempotency and notifications suites stay green.

### 4.3 F3 — Hot-Path Diet
**Description:** A clean turn costs one LLM call; honesty enforcement consolidates into one delivery gate computed once. Byte-identical outputs are the bar for the refactor commits. Realizes UJ-3.

#### FR-8: Feedback classifier pre-filter
**Consequences (testable):**
- Messages ≥ 200 chars skip the FeedbackClassifier LLM call (reactions are short; the referent check would reject long messages anyway after paying for the call). 200 is the constant; tuning note in addendum.
- Short reaction messages still classify exactly as before.

#### FR-9: Sticky routing
The bypass is a purely mechanical rule — no LLM-free "new-topic detection" is attempted; the length ceiling IS the heuristic, and its misroute risk is the accepted ceiling guarded by CM-2.

[Adversarial review, 2026-07-01, applied before merge] The original design also reused cached `"standard"` (work-turn) resolutions. A dedicated adversarial-risk pass found this genuinely unacceptable: a `"standard"` resolution is the one most likely to be stale by the time a short follow-up arrives, and reusing it silently defeats the F120 tool-capability gate and the answer-floor tier (both key off `intent_class`) — a real new task disguised as a short message could land on a non-tool-capable provider with no fallback. Fixed by restricting reuse to `intent_class == "conversational"` only (never written OR read back for `"standard"`/`"clarify"`), and shrinking the TTL from 30 to 5 minutes (30 min was found to comfortably span common real interruptions — checking a phone, a call — making topic drift the common case, not the exception, at that window).

**Consequences (testable):**
- Sticky bypass fires iff ALL of: same session has a previous turn that resolved to an owl with intent_class `"conversational"`, within a 5-minute recency window; the gateway scanner found no direct owl address; message < 200 chars (same constant as FR-8); the cached owl still resolves in the registry. Any condition false → LLM router, exactly as today.
- A `"standard"` or `"clarify"` router result is never written to the cache, and a defense-in-depth read-side check also refuses to reuse a `"standard"`/`"clarify"` entry even if one were present.
- On bypass, the previous turn's owl AND `intent_class` (always `"conversational"`) are reused; the carried-forward `intent_class` still gates classify's heavy context blocks correctly.
- Direct-address turns never read or write the cache.

#### FR-10: Conditional give-up judge [owner-approved narrowing]
**Consequences (testable):**
- The LLM give-up judge (and its fallback tier) runs only when the turn had ≥1 failed tool call, a refusal-shaped draft, or an empty draft.
- The structural veto and never-empty floor remain always-on.
- A clean turn (all tools succeeded, substantive draft) completes with exactly 1 LLM call.
- Honesty journeys stay green (no dressed-up give-up, overclaim, budget-cap floor).

#### FR-11: One delivery gate
**Consequences (testable):**
- `giveup_floor`, `overclaim_gate`, `grounding_gate`, `critical_failure`, and `persistence_handoff` are replaced by a single `delivery_gate` module: structural facts computed once, one precedence ladder.
- Refactor commits produce byte-identical outputs on the existing gate test corpus; the five old modules are deleted after their tests move to the merged module.
- Cross-imports from `turn_persist`, providers, and `objectives/driver` are re-homed.

#### FR-12: One shared post-run seam
**Consequences (testable):**
- A backend-agnostic shared module owns: delivery gate invocation, `persist_turn` ordering, acceptance verification, outcome capture, and skill success updates.
- Both backends call it; neither backend imports private helpers from the other.
- Ongoing (no completion date): tools migrate to declaring `post_condition()` so `AcceptanceAuthority` becomes the real single authority (2 of ~111 tools done today).

### 4.4 F4 — LangGraph Canonical Backend [owner decision: asyncio dies]
**Description:** LangGraph becomes the one pipeline backend; asyncio is deleted after parity and soak. Bonus: AsyncSqliteSaver checkpointing per `session::task_id` becomes the durable-turn resume substrate.

#### FR-13: Backend parity
**Consequences (testable):**
- LangGraph backend calls the FR-12 shared seam, closing its known gaps: acceptance verification (currently always None) and outcome-capture import.
- Per-node timing populates `step_durations` (currently empty).
- Backend-parity tests pass identically for both backends.

#### FR-14: Default flip + soak
**Consequences (testable):**
- Default `orchestrator.backend` becomes `langgraph`; gateway fully restarted (gateway is not durable — known landmine).
- Live smoke over Telegram passes.
- Soak exit = 7 calendar days where BOTH log queries return zero rows across all daily logs:
  `jq 'select(.msg | startswith("[langgraph_backend] run: graph invocation failed"))'` and
  `jq 'select(.msg | contains("checkpointer: sqlite init failed"))'`.
  Any hit resets the soak clock; FR-15 is blocked until soak exit.

#### FR-15: Delete asyncio backend
**Consequences (testable):**
- After soak, `asyncio_backend.py` is deleted; its logic already lives in the shared seam; the backend factory is simplified.
- Zero remaining imports of the deleted module; pipeline and journey suites green.

### 4.5 F5 — Structural Consolidation (background backlog, independent items)
**Description:** Sprawl reduction that never blocks F1–F4; each item is an independent small commit. Items marked [consent] require an explicit owner ask before execution (never disable features unilaterally).

#### FR-16: Config diet
**Consequences (testable):**
- `cloud_enabled` is defined exactly once (currently 3×).
- Dead flags (no caller gates behavior) are removed one commit each; flags gating real features go to a batched [consent] ask. When in doubt whether a flag gates a real feature, treat it as [consent].

#### FR-17: Drop stale shadow tables
**Consequences (testable):**
- One idempotent migration drops `skills_new`, `staged_facts_new`, `tool_heuristics_new`, `user_preferences_new` (`DROP TABLE IF EXISTS`); migration suites green.

#### FR-18: Ledger unification
**Consequences (testable):**
- `pipeline/decision_store` folds into `infra/decision_ledger` (ADR-7 canonical); `tool_outcome_ledger` and DB `side_effect_ledger` remain (distinct concerns).

#### FR-19: Package merges [consent]
**Consequences (testable):**
- Proposed and executed only after owner approval: `events/` + `messaging/` + `notifications/event_bridge` → one messaging package; `supervisor/` + `process/` → `runtime/`; `ipc/` custom framing evaluated against stdlib asyncio streams. Park-or-keep list presented for `tenancy/`, `authz/`, `export/`, `webhooks/`, `parliament/`.

#### FR-20: Registry thinning
**Consequences (testable):**
- Registries wrapping ≤4 static items are inlined; TurnRegistry, scheduler HandlerRegistry, ProviderRegistry (genuinely dynamic) remain.

### 4.6 F6 — Dead Code & Docs Hygiene
#### FR-21: Dead code removal
**Consequences (testable):**
- Duplicate telemetry-only `select_tool_provider` call in `assemble` removed; dead `is_final` comment in `deliver` removed. `parliament_step` stub STAYS (future LangGraph Send fan-out home).
- [Owner-confirmed 2026-07-01, reverses PRD default] `acceptance_llm.py` (`LlmAcceptanceDeriver`) deleted, including its call site in the acceptance-derivation path and the `acceptance_tier` setting if unused elsewhere after removal.

#### FR-22: Strike instincts from docs
**Consequences (testable):**
- CLAUDE.md / docs no longer promise an instincts engine (never built in the Python app; lessons_index + FR-2 cover the ground). Scope carefully: the root CLAUDE.md "Instincts" bullet and `src/instincts/engine.ts` table row describe the ARCHIVED v1 TypeScript app — clarify/re-scope that section rather than blindly deleting it.

## 5. Non-Functional Requirements

- **NFR-1 No honesty regression.** Every consolidation commit that touches gates is byte-identical on the existing honesty test corpus; floors still fire on failed turns, overclaims still blocked, fabricated citations still stripped.
- **NFR-2 Test discipline.** Targeted pytest paths with `--timeout` only — never full pytest on this box (hangs). `ruff check src/` and `mypy src/` green per commit.
- **NFR-3 Commit discipline.** Small verified commits at sub-story granularity; QA + dev subagent review before each commit; merge to main + push when green.
- **NFR-4 DB changes via idempotent migrations only.**
- **NFR-5 Cross-platform, no vendor names in src/, no hardcoded keyword lists, all state under ~/.stackowl/.**
- **NFR-6 4-point logging preserved** (entry/decision/step/exit) in every touched execute path; no silent catches.
- **NFR-7 Features ship ON** — completed behavior changes default enabled, not behind dormant flags.

## 6. Success Metrics

- **SM-1:** Clean turn = exactly 1 LLM call (from 3–4). Measure via per-turn provider-call count in logs.
- **SM-2:** Tone/length feedback appears in the next turn's prefs_block (UJ-1) — manual acceptance session after F1.
- **SM-3:** Nightly evolution emits ≥1 prompt directive within a week of consistent interaction signal (from ~never).
- **SM-4:** Morning brief generated and delivered (or outboxed + flushed) on a day with no Telegram contact (UJ-2).
- **SM-5:** Gate modules on the hot path: 9 → 4 (delivery_gate, structural veto, never-empty floor, AcceptanceAuthority/Checker).
- **Counter-metric CM-1:** honesty journey suite failures = 0 across all commits.
- **Counter-metric CM-2:** routing quality — sticky routing must not misroute topic shifts; verify with router/triage suites + journeys before merging FR-9.

## 7. Explicitly Out of Scope

- `_drain_next` lock-across-await — by-design §4.3 race fix; no action.
- New subsystems of any kind; no instincts engine.
- Multi-user/tenant work.
- Standalone splits of `orchestrator.py` (3375 lines) / `execute.py` (2344) — only opportunistic, when another FR already forces edits there.
- Test-suite hygiene (sleeps/skips) as a standalone project — only in suites touched by the above.

## 8. Implementation Order (for the fresh session)

1. **Week 1 — F1 + FR-5:** FR-1 → FR-3 → FR-5 → FR-2 → FR-4 (independent; targeted suite each).
2. **Week 2 — F3:** FR-8, FR-9, FR-10 (independent) → FR-11 + FR-12 (phased, byte-identical) → FR-21.
3. **Week 3 — F4 + F2:** FR-13 → FR-14 (flip + soak) → FR-15 (delete after soak) → FR-6 → FR-7 → FR-22.
4. **Filler, anytime:** FR-16, FR-17, FR-18. **After consent:** FR-19, FR-20.
5. **Ongoing:** FR-12's post_condition migration, one tool per commit.

Full file/line-level plan, constants, and per-commit verification commands: see `addendum.md`.

## Open Items

- [ASSUMPTION] Latch band retune values (0.62/0.55) and clamp (±0.25) are starting points; tune against SM-3 if evolution over- or under-fires. Owner sign-off implicit in FR-1 approval.
- [consent] FR-19 package merges and any FR-16 flag that gates a real feature require an explicit owner ask before execution.
- RESOLVED at kickoff 2026-07-01 (see `.decision-log.md` items 5–7): keep parliament_step stub (confirmed); acceptance_llm.py → DELETE (reverses PRD default, folded into FR-21); instincts docs → clarify/re-scope (confirmed).
