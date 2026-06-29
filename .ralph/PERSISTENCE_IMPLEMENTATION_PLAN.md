# Arc A — Persistence Loop / Never-Give-Up (loop engineering) — IMPLEMENTATION PLAN

Origin: deep platform research + BMAD party (Winston/Amelia/Murat/John), 2026-06-28.
Disease named by Murat: **Silent Fail-Open** — every uncertain path resolves toward "looks fine"
with no negative-acknowledgement channel. Arc-wide invariant: **uncertainty fails CLOSED and emits a
durable NACK (a store row / honest user message), NEVER a bare log line.**

Owner decision: give-up policy = **loop engineering**. The never-give-up behavior is ONE engineered,
BOUNDED persistence ladder (completion-promise + max-iter), not a static escalate/handoff/confess pick.

Scope = Arc A only. Arc B (Capability Resolver seam, Winston) is the NEXT epic — out of scope here,
EXCEPT PA4b (synth-skill ownership) pulled forward because the hand-to-owl rung needs it.
Full Secretary-orchestration (MR2) DEFERRED (John: vanity until delivery works).

Verified against code first (2 party findings were intentional design, not bugs):
- `verify()=None` on ~46/56 tools = documented ADR-1 opt-in (base.py:183), NOT a defect → ratchet on NEW tools only.
- circuit-breaker not escalating (execute.py:723) = deliberate containment, NOT a bug → but it DEAD-ENDS; that is the hook point.
- fail-open judge (execute.py:153) already fails CLOSED when `seen_giveup`; only a residual hole remains (PA2).
- dead-verb (classify.py:405 `/skill show`) and orphaned synth skills (synthesizer.py, owls_registered=0) = REAL.

## The seam
One **Persistence/Delivery Authority** object that owns the turn's give-up verdict and the escalation ladder.
Reuse-first: route the EXISTING pieces through it — do NOT build a parallel subsystem.
Reuse: `_snapshot_consequential` / consequential snapshot (execute.py), `RecoveryActuator`, the 4-gate delivery
band (`surface_*_floor/gate` in asyncio_backend.py / langgraph_backend.py), the in-loop persistence judge
(`build_persistence_check`), existing tier escalation (llm_gateway fast→ceiling), `delegate_task`/`A2ADelegator`/resolver.

Ladder (bounded; budget + max-iter cap = real ralph discipline):
1. retry-once (existing recovery ladder)
2. escalate model tier — feed the circuit-breaker-open event INTO the ladder instead of dead-ending
3. hand to better-fit owl — the one approved slice of orchestration (needs PA4b ownership read)
4. honest escalate-to-user — never silent, never fake success (floor already exists)

## Stories (in order)

- [x] **PA0 — Consolidate the give-up verdict into ONE turn-outcome.** DONE. `decide_delivery(state)` +
  `DeliveryDecision` (delivery_decision.py): single function resolves the consequential give-up verdict,
  computed at READ time (no stamped field → no staleness). giveup_floor reads it; overclaim_gate shares the
  same underlying predicate. Zero behavior change. NOTE: an early stamp-at-snapshot impl reintroduced the P0
  budget-cap overclaim incident (verdict frozen before budget_capped=True); dev review caught it, fixed by the
  read-time design. Verified: budget-cap journey green + 15 consolidation + 231 floor/overclaim/giveup regression
  + ruff + mypy clean. Today "did we give up?" is recomputed at
  6+ sites (giveup_floor, anthropic/openai providers, overclaim_gate, turn_persist, registry) PLUS an independent
  LLM judge. Introduce one `DeliveryDecision` owned off the existing consequential snapshot; the 4-gate band and the
  re-derivations READ it instead of recomputing. No behavior change yet — pure consolidation + characterization test
  proving every old site now agrees with the one object. Kills the two-brains scatter (MR5).

- [x] **PA1 — Dead-verb fix.** DONE. classify.py `_gather_relevant_skills` now emits ``skill_view <name>``
  (the reachable load tool) instead of ``/skill show <name>`` (a CLI command the model can't call mid-turn);
  stale docstring updated too. Test asserts the block names a loadable tool, never the dead CLI verb. The
  `/skill show` references in commands/skill_command.py are the real human CLI command — left intact.
  Verified: classify suite (7) + skill injection/discovery journeys (6) + ruff + mypy green. (MR1)

- [x] **PA2 — Tighten the residual fail-open hole.** DONE. The persistence judge's final fail-open path (judge
  never vetted + no give-up flagged) used to always accept an unvetted draft. Now a three-way split on the existing
  ledger: effectful work → accept (consequential floor backstops); clean turn (no tools) → accept (never nudge
  ordinary chat); substantive non-effectful work the judge never vetted → fail CLOSED, nudge ONCE (closure latch
  `pa2_nudged`, bounded). Review caught a re-fire bug (fired every pass) → fixed with the latch. Also fixed a
  pre-existing harness failure: `test_gateway_agent_does_not_give_up` (`_FakeResponse.usage` missing + judge fake
  registered fast-only while the judge now resolves standard/local tiers). Verified: 49 persistence/judge/journey
  tests green incl. the previously-red gateway test, ruff + mypy clean. execute.py persistence check: the final `not seen_giveup` branch
  accepts on judge error. Close the residual case — judge erred on its ONLY pass AND no give-up ever vetted AND budget
  remains → nudge once more / deliver honest floor, never silent-accept an unvetted draft. Must NOT regress the
  conversational happy-path (a plain chat turn with no tools still ships). (MR5)

- [x] **PA3 — Breaker → ladder.** DONE. commit `d95ed926` (+ harness fix `4aa75468`). New turn-scoped
  `providers/escalation_signal.py` ContextVar bridges the breaker (pipeline SETs) and the escalation ladder
  (provider READs — providers can't import pipeline). `_dispatch` circuit-open branch calls `request_escalation(name)`
  BEFORE returning the refusal (containment PRESERVED, escalation ADDED). Both provider ReAct loops, at iteration top:
  `if can_escalate and escalation_requested(): return ESCALATE_SENTINEL` — reuses the EXISTING sentinel path the
  LLMGateway already handles (discard attempt → on_escalate reset → re-run one tier up). `_on_tier_escalate` now
  `clear_escalation()` + `progress.reset()` (new `TurnProgressTracker.reset()`) so the stronger tier starts clean,
  not pre-bounced by the weak tier's open breaker. Pinned owls (no can_escalate → False) byte-identical; at ceiling
  the flag is ignored → existing honest floor takes over (no new give-up path). Also fixed a PRE-EXISTING harness
  failure (`test_weak_model_react_tool_dispatch_through_gateway`, fails on PA2 HEAD too): the give-up judge resolves
  `judge_tier`="standard"/"local" and cascaded onto the ReAct fake's powerful provider, consuming a sequenced response
  → IndexError. Gave the judge a dedicated `_JudgeProvider` (rules DELIVERED) — same drift class PA2 fixed. Verified:
  21 PA3 tests + the previously-red smoke green; 837 pipeline/provider tests pass; ruff + mypy clean on changed src. (MR5)

- [x] **PA4 — Hand-to-better-owl rung.** DONE. commit `c934f123`. New `pipeline/persistence_handoff.py::
  surface_persistence_handoff`, wired into BOTH backends right BEFORE `surface_consequential_giveup_floor`. On a
  would-give-up turn (decide_delivery give-up OR no-progress), resolve a CAPABILITY-matched better-fit owl
  (highest-cosine `store.semantic_recall(query_embedding)` skill whose owner — via PA4b `read_all_skill_ownership`
  rows + built-in `manifest.skills` scan — is a DIFFERENT registered owl), hand off via existing `A2ADelegator.delegate`,
  deliver child.content + provenance_footer on status==ok; else responses untouched → honest floor fires (fail CLOSED).
  Bounded: depth-0 only (recursion guard), not budget_capped, once/turn, B5-wrapped. Healthy turns return the SAME
  state object (byte-identical). ★Self-review caught a real leak: parent_state evolved from the give-up parent leaked
  the give-up snapshot into the child (which _run_specialist does NOT reset) → child would floor on the PARENT's failure
  → fixed by clearing consequential_failures/successes/recovered/delivered + no_progress_tools + turn_made_progress
  in parent_state (matches fresh-PipelineState semantics of delegate_task). 8 tests + band regression green. (MR5/MR2-slice)
  - [x] **PA4b — Synth-skill ownership (pulled fwd from Arc B).** DONE. commit `61bb0f65`. New
    `owls/skill_ownership.py` mirrors the DNA subsystem (attach_skill_to_owl live overlay + persist/read +
    hydrate_skill_ownership boot overlay), migration `0072_skill_ownership.sql` (PK incl owner_id for tenant
    isolation). `_synthesize_one` attaches the learned skill to its OWNING owl (most-frequent owl_name across
    `cluster.outcomes`) live + durable; boot re-hydrates. Best-effort (failed attach never aborts synth).
    ★Verified `owl.skills` IS read by injection (assemble.py:118 owned-skill playbook + classify.py:617 dedup) →
    attach is necessary AND sufficient (closes born-unreachable, not just the PA4 prereq). Opus QA caught a MEDIUM
    phantom-ownership bug (deprecate deleted the skill but not its ownership row → boot re-attached a dead skill
    forever) → fixed with `purge_skill_ownership` (live detach + durable DELETE) on both deprecate delete sites.
    36 tests green; ruff + mypy clean. (MR1/MR4)

- [ ] **PA5 — Murat's ratchet gates (assert on the STORE, never a log).**
  - [x] (a) **Lying-success gate + coverage ratchet.** DONE. commit `14365661`. Two judge-INDEPENDENT, store-asserting
    gates (test-only, zero prod change): (1) `tests/tools/test_effect_class_verification_ratchet.py` enumerates the real
    registry (`ToolRegistry.with_defaults`) and fails if any `effect_class` tool overrides NEITHER `Tool.verify` NOR
    `Tool.post_condition` — non-vacuous (send_message/skill_manage/owl_build covered) + self-policing allowlist;
    (2) `tests/pipeline/test_lying_success_gate.py` — `verified=False` + persistence judge STUBBED TO RAISE still floors
    (structural veto is judge-independent). ★Ratchet caught a REAL hole: `CronjobTool` declares `effect_class="schedules"`
    but verifies nothing → over-claims "scheduled!" on silent install failure. Documented in `_KNOWN_UNVERIFIED` debt +
    follow-up below. 30 tests green; ruff clean. (MR5)
  - [ ] **(a-followup) cronjob verification surface.** CronjobTool needs a `post_condition` (DeliveryAck/Custom)
    that reads the JobScheduler back to confirm the job row exists, for the schedule-CREATING actions (create/watch)
    only — then DELETE `cronjob` from `_KNOWN_UNVERIFIED` (the ratchet self-policing test enforces removal). Multi-action
    tool → own sub-story with QA, not a one-liner.
  - [ ] (b) **Silent-delivery gate** — ARCHITECTED: see [`.ralph/PA5B_DESIGN.md`](PA5B_DESIGN.md) (dedicated
    `undelivered_outbox` store: silent-drop paths → durable row → next-contact banner → clear; policy = defer +
    surface-on-next-contact, next-session banner; scope = prod NACK seam + ratchet). Implement per that doc. The real
    substrate is
    `scheduler/scheduler.py` + `notifications/proactive_job.py::ProactiveJobDeliverer` + `scheduler/scheduler_helpers.py`
    quiet-hours (NOT goal_execution.py). ★LANDMINE: scheduler F-62 (scheduler.py:209) INTENTIONALLY leaves a
    handler-not-registered job PENDING (registration-ordering, recoverable) — that is NOT a dead-letter. Distinct
    stored states: pending (F-62) vs failure-ledger (handler RAISED past retries, scheduler.py:280/401) vs
    quiet-hours-deferred vs dead-letter. The ratchet must assert the RIGHT invariant per state, not conflate them —
    a wrong ratchet is worse than none. Define which state SHOULD produce a durable NACK before writing the gate.
    (MR6/MR1)

## RESUME NOTE (paused 2026-06-28 — cost stop at iteration 4/12)
DONE + pushed to main: PA0 `7273edb6`, PA1 `2dcfcfdb`, PA2 (this commit). The `decide_delivery` seam
(giveup_floor.py) is the hook point for PA3/PA4 — the escalation ladder reads/extends it.
TO RESUME: re-launch `ralph-loop:ralph-loop` with this plan + PERSISTENCE_RALPH_PROMPT.md (set active:true,
max_iterations:12 in .claude/ralph-loop.local.md), start at PA3.
- PA3 next = the centerpiece: route the circuit-breaker-open event (`_circuit_open_refusal`, execute.py ~723)
  into an escalation ladder (escalate model tier via llm_gateway fast→ceiling) instead of dead-ending. Keep the
  containment (don't re-offer the dead tool); add the escalation it lacks. This is where "never give up" actually lives.
- PA4/PA4b = stuck-owl hands to better-fit owl (reuse delegate_task/A2ADelegator/resolver) + synth-skill ownership
  (synthesizer.py attaches the learned skill to its owning owl — born-unreachable fix, MR1/MR4).
- PA5 = Murat's 2 ratchet gates (lying-success parametrized over registry; silent-delivery reads the STORE not a log)
  + verify()-coverage ratchet on NEW tools. NOTE: locate the REAL quiet-hours/dead-letter path (scheduler/goal_execution.py
  did NOT exist — that anchor was wrong; find the actual substrate before writing the gate).

## Completion promise (STOP only when ALL true)
PA0–PA5 implemented, committed at sub-story granularity, pushed to main with hashes recorded here;
targeted tests + `uv run ruff check` + `uv run mypy` GREEN on changed files (NEVER full pytest — hangs);
the 2 ratchet gates passing; server restarted onto the new code with boot green + census passing;
live never-give-up re-test on the running server: a turn that previously would surrender now escalates /
hands off / honestly confesses where it's blocked — it NEVER ships a silent shrug and NEVER fakes success.
Capture traceIds. Update project memory.
