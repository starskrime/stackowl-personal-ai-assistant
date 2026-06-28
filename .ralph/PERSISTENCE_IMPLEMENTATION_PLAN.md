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

- [ ] **PA2 — Tighten the residual fail-open hole.** execute.py persistence check: the final `not seen_giveup` branch
  accepts on judge error. Close the residual case — judge erred on its ONLY pass AND no give-up ever vetted AND budget
  remains → nudge once more / deliver honest floor, never silent-accept an unvetted draft. Must NOT regress the
  conversational happy-path (a plain chat turn with no tools still ships). (MR5)

- [ ] **PA3 — Breaker → ladder.** Circuit-breaker-open (`_circuit_open_refusal`, execute.py:723) currently dead-ends
  ("stop and tell the user"). Route the open event into the Persistence Authority ladder: escalate model tier (reuse
  llm_gateway escalation), and if still stuck, surface to PA4. Keep the containment (don't re-offer the dead tool);
  add the escalation the containment was missing. This is the core loop-engineering move. (MR5)

- [ ] **PA4 — Hand-to-better-owl rung.** Inside the ladder, a stuck owl delegates to a better-fit owl. Reuse
  `delegate_task` / `A2ADelegator` / resolver. Bounded (one hand-off per turn, budget-gated). Honest if no better owl.
  - [ ] **PA4b — Synth-skill ownership (pulled fwd from Arc B).** synthesizer.py builds a manifest with
    `owls_registered=0` and never edits any owl — born unreachable. On synth, attach the skill to its owning owl so
    the hand-off rung (and injection) can actually reach it. Minimal capability read only; full resolver = Arc B. (MR1/MR4)

- [ ] **PA5 — Murat's ratchet gates (assert on the STORE, never a log).**
  - [ ] (a) **Lying-success gate** — fake tool returning `verified=False` + persistence judge stubbed to raise →
    assert turn outcome ≠ delivered_success (escalates or honest-confesses). PARAMETRIZE over the tool registry:
    no tool that declares an `effect_class` may ship with `verified` absent. Coverage ratchet on NEW tools only.
  - [ ] (b) **Silent-delivery gate** — locate the real quiet-hours / unreachable-handler delivery path (NOT
    scheduler/goal_execution.py — that path was wrong; find the actual one). Job-row whose handler doesn't resolve +
    a quiet-hours delivery → assert a durable NACK / dead-letter row exists IN THE STORE (read it back). Never a log.
    Cover BOTH delivery substrates in one fixture. (MR6/MR1)

## Completion promise (STOP only when ALL true)
PA0–PA5 implemented, committed at sub-story granularity, pushed to main with hashes recorded here;
targeted tests + `uv run ruff check` + `uv run mypy` GREEN on changed files (NEVER full pytest — hangs);
the 2 ratchet gates passing; server restarted onto the new code with boot green + census passing;
live never-give-up re-test on the running server: a turn that previously would surrender now escalates /
hands off / honestly confesses where it's blocked — it NEVER ships a silent shrug and NEVER fakes success.
Capture traceIds. Update project memory.
