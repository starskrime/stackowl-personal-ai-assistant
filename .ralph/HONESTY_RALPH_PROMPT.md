# Ralph loop driver — Arc B: Agentic Honesty & Delivery Reliability

Spec + story status: `.ralph/HONESTY_IMPLEMENTATION_PLAN.md`.
Approved plan with full root-cause evidence + BMAD party verdict: `/home/boss/.claude/plans/i-want-you-analayze-quizzical-stonebraker.md`.

## Each iteration
1. Read `.ralph/HONESTY_IMPLEMENTATION_PLAN.md`; pick the FIRST story not checked done, respecting its stated
   ordering dependencies (PB1 before PB0c; PB6a before PB5 and PB7b; PB3 is an explicitly-flagged interim fix,
   not a substitute for PB6a/6b).
2. Delegate implementation to a fresh subagent with a precise spec grounded in the plan and the story's exact
   file:line citations. REUSE-FIRST: route through existing primitives — RecoveryActuator, the PA3
   breaker→escalation-ladder, the existing `ToolResult.verified` tri-state + `effect_class` machinery, the
   already-fixed `send_file` pattern for PB2, `goal_execution.py`'s outcome mapping for PB3, the already-designed
   `PA5B_DESIGN.md` for PB7a. NO new parallel subsystem. NO 1000-line rewrites. Minimal diff. Typed, 4-point
   logging, NO hardcoded English keyword lists (hard stop for PBC specifically).
3. Run the `code-simplifier` agent on the diff to cut it to the minimum before commit.
4. Verify yourself: targeted tests + `uv run ruff check` + `uv run mypy` on CHANGED files only. NEVER full pytest.
   QA + dev subagent review; fix every finding before commit. Apply the story's fault-injection tests from the
   plan's Test Strategy section — a witness must be asserted in a test, logging alone is not proof.
5. Commit that story + push to main; mark it done in the plan with the commit hash.
6. Honor every rule in MEMORY.md (subagent-driven, per-story QA+party+smoke, commit small successes, all-state-in-home,
   no vendor-specific logic, never disable features, gateway durable=no → full restart for gateway-side changes —
   PB1/PB0b/PB0c/PB2 all touch gateway-side code and need a full restart to verify live, not hot-reload).

## Guardrails
- Do NOT rebuild the scheduler (`scheduler.py`/the `jobs` table) — it is already the single unified register for
  all platform scheduling, confirmed by research. This arc only adds the honesty/verification layer on top.
- PB3 is interim. Do not let its completion close gap (a)/(b) in tracking — those close at PB6a+PB6b.
- PB7b does not start before PB6a lands — there is no `verified` signal to gate an outbox on until then.
- PBC's research-intent detection MUST route through the existing model-driven intent classifier. A hardcoded
  keyword list ("research", "look up", etc.) is a hard stop in review — reject and redo.
- If a story turns out larger than one clean diff, split it in the plan, never balloon a single commit (PB6b in
  particular — one JobHandler subclass per commit).

## Completion
Stop ONLY when the completion promise in the plan holds: every story PB1 through PB-CANARY done, green (targeted
tests+ruff+mypy), pushed, gateway+core restarted boot-green, the PB-CANARY synthetic round-trip live and alerting
on absence, and a live re-test against the real Telegram bot confirms a message sent now gets a response.
