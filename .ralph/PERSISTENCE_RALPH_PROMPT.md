# Ralph loop driver — Arc A: Persistence Loop / Never-Give-Up

Spec + story status: `.ralph/PERSISTENCE_IMPLEMENTATION_PLAN.md`.

## Each iteration
1. Read `.ralph/PERSISTENCE_IMPLEMENTATION_PLAN.md`; pick the FIRST story (or sub-story) not checked done.
2. Delegate implementation to a fresh subagent with a precise spec grounded in the plan.
   REUSE-FIRST (code-simplifier mandate): route existing pieces through the one Persistence/Delivery Authority —
   consequential snapshot, RecoveryActuator, the 4-gate delivery band, build_persistence_check, llm_gateway tier
   escalation, delegate_task/A2ADelegator/resolver. NO new parallel subsystem. NO 1000-line rewrites. Minimal diff.
   Typed, 4-point logging, NO hardcoded English keyword lists. Arc invariant: uncertainty fails CLOSED + emits a
   durable NACK (store row / honest message), never a bare log line.
3. Run the `code-simplifier` agent on the diff to cut it to the minimum before commit.
4. Verify yourself: targeted tests + `uv run ruff check` + `uv run mypy` on CHANGED files only. NEVER full pytest.
   QA + dev subagent review; fix every finding before commit.
5. Commit that story + push to main; mark it done in the plan with the commit hash.
6. Honor every rule in MEMORY.md (subagent-driven, per-story QA+party+smoke, commit small successes, all-state-in-home,
   no vendor-specific logic, never disable features, gateway durable=no → full restart for gateway-side changes).

## Guardrails
- Do NOT touch Arc B (Capability Resolver) beyond PA4b synth-ownership. Do NOT build full Secretary orchestration (MR2).
- Do NOT "fix" verify()=None default or breaker-containment — those are intentional (see plan). Add escalation, keep containment.
- If a story turns out larger than one clean diff, split it in the plan, never balloon a single commit.

## Completion
Stop ONLY when the completion promise in the plan holds: PA0–PA5 done, green (targeted tests+ruff+mypy), 2 ratchet
gates passing, pushed, server restarted boot-green + census passing, and the live never-give-up re-test passed.
