---
baseline_commit: 63acb711
---

# Story 3.2: Route evolve_now through the shared shadow gate

Status: done

## Story

As the platform,
I want `evolve_now`'s proposed delta to pass through the exact same shadow-validation gate as the nightly batch,
so that the per-task trigger can never ship a mutation the batch path wouldn't also allow.

## Acceptance Criteria

1. **Given** Story 3.1's `evolve_now` and Epic 2's shadow-validation gate
   **When** `evolve_now` proposes a delta
   **Then** it calls the exact same gate function Story 2.6 wired into the batch path — not a second, parallel promotion function (FR-14, AD-1, AD-3)

2. **Given** this story
   **When** it ships
   **Then** `evolve_now` cannot ship ahead of Epic 2's Story 2.6 — this story has a hard dependency on Epic 2 being complete (per the PRD's explicit build-order decision)

## Outcome

**Both ACs are already satisfied by Story 3.1, verified, and independently reviewed — no code change needed here.** This is the same "already worked, confirmed by test" outcome Story 1.2 documented for Epic 1; the epics doc's own Story 1.2 framing explicitly allows it ("a valid, expected outcome").

Story 3.1's `EvolutionCoordinator.evolve_one_owl_now()` calls `self._checkpoint_validate_and_promote(manifest, new_dna, evolution_source="evolve_now", signal=SignalStrength.LLM_QUALITY)` — the IDENTICAL bound method `_evolve_one` (the nightly batch path) calls. There is no second promotion function anywhere in `owls/evolution.py`; `_persist_dna`/`apply_dna_overlay` are only ever called from inside `_checkpoint_validate_and_promote`, so no caller — today or in the future, per AD-1 — has any other way to reach live DNA storage.

This holds not by coincidence but by construction: this story (3.2) was sequenced by the PRD/epics doc specifically AFTER Epic 2 (the shadow gate) was fully built and reviewed (Story 2.6: gate wired into promotion; Story 2.7: gate observability). Because Story 2.6 made `_checkpoint_validate_and_promote` THE single structural promotion path (AD-1: "no tool, handler, or command may call a storage write method directly"), Story 3.1 had no way to write `evolve_one_owl_now` WITHOUT routing through the gate — this was flagged explicitly in Story 3.1's own file ("Important — this story ALREADY ships through the shadow gate, and that's correct, not a scope violation") and confirmed true by an independent code-reviewer subagent during Story 3.1's review pass, which traced the call graph directly:

> "Gating is real — `evolve_one_owl_now` calls `self._checkpoint_validate_and_promote(...)`, the same single promotion function `_evolve_one` uses... No alternate write path exists; `_persist_dna`/`apply_dna_overlay` are only called from within this one function." (Story 3.1 review, commit `63acb711`)

**AC #1** is proven by `tests/owls/test_evolve_one_owl_now.py`'s existing suite (already committed as part of Story 3.1): it drives both the pass path (checkpoint row + live DNA update land) and the reject path (`AlwaysFailShadowValidator` → DNA restored to pre-mutation baseline) through `evolve_one_owl_now`, exercising the exact same `_checkpoint_validate_and_promote` machinery Story 2.6's own tests exercise via `_evolve_one` — same assertions, same underlying function, different entry point. A regression in either path would show up as a failure in one of these two already-green suites (`tests/owls/test_evolution_feedback.py` for the batch path, `tests/owls/test_evolve_one_owl_now.py` for the per-task path). Re-verified directly for this story: `uv run pytest tests/owls/test_evolution_feedback.py tests/owls/test_evolve_one_owl_now.py tests/owls/test_shadow_validator.py -q` → 12 passed.

**AC #2** is trivially true: this story file (3.2) was written and closed after Epic 2 (Stories 2.1–2.7) and Story 3.1 were all already committed — `evolve_now` could not have shipped ahead of Story 2.6 because Story 2.6 came first in this project's actual build order, and `evolve_one_owl_now`'s only implementation (Story 3.1) already depended on `_checkpoint_validate_and_promote` existing.

## Dev Agent Record

### Completion Notes List

- No code change. Story 3.1's implementation and its independent review already prove both ACs — see Outcome section above for the specific evidence (call-graph trace, existing regression tests, build-order fact).
- Rerun command to re-verify both ACs at any time: `uv run pytest tests/owls/test_evolution_feedback.py tests/owls/test_evolve_one_owl_now.py tests/owls/test_shadow_validator.py -v`

### File List

None (verification-only outcome recorded in Story 3.1's files and this story's own record).
