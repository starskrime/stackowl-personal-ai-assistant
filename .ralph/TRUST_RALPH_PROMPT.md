# Ralph loop driver — Trust & Capability arc

Continue the Trust & Capability arc. Spec: `.ralph/TRUST_ARCHITECTURE.md`. Plan + story
status: `.ralph/TRUST_IMPLEMENTATION_PLAN.md`. Lie-stopping core TS1-TS3 already shipped+pushed.

## Each iteration
1. Read `.ralph/TRUST_IMPLEMENTATION_PLAN.md`; pick the FIRST story not yet marked done.
2. Delegate implementation to a fresh subagent with a precise spec grounded in TRUST_ARCHITECTURE.md (reuse-first, no patches, typed, 4-point logging, NO hardcoded keyword lists).
3. Verify yourself: run the story's targeted tests + `uv run ruff check` + `uv run mypy` on changed files. NEVER run the full pytest suite (it hangs on this Jetson). QA+dev review; fix findings.
4. Commit that story + push to main. Mark it done in the plan file with the commit hash.
5. Honor every rule in MEMORY.md.

## Remaining stories, in order
- TS4 — capability manifest from a reachability probe + charter honesty-split. Kills the invented "I can't initiate messages". No tool names in the charter; capabilities derived from live wiring, tied to the health surface.
- TS5+TS6 — grounding. An external-info answer requires a web_search/web_fetch actually ran this turn [ledger check]; every URL in the answer must be in the fetched-source set, else stripped; empty retrieval = honest "nothing new", never fabricate.
- TS7+TS8 — disjoint owl vs skill tool descriptions [owl = a who/persona that schedules + messages; skill = a how/procedure an owl invokes] + schedule-as-slot in owl_build [every 2h / daily / remind me maps to lifecycle scheduled + CronTrigger via resumable slot-filling]. Remove the stale NotImplementedError docstring.
- TS9+TS10+TS11 — trustworthy confirmation [next-fire time + an immediate real sourced poke + one-tap off-ramp]; scheduled-job honesty floor INSIDE the job [empty cycle = nothing-new, never fabricate — Mary's most-dangerous edge]; quiet hours + per-owl daily budget + dedup + single-flight lock + durable Telegram target; STOP/SNOOZE natural-language pause/resume [pause is not delete].
- TS12 — acceptance eval suite asserting on ledger + world-reads [never prose], wired as a CI gate. The 8 evals in TRUST_ARCHITECTURE.md.
- TS13 — live re-test on the running server of the exact scenario: create an agent Brain that pokes me every 2 hours with real AI news. Assert: owl created and reachable; scheduled job at 2h cadence exists; a fired tick does a real web_search and delivers a sourced poke; an empty cycle says nothing-new; STOP pauses it. Capture traceIds. Merge to main.

## Completion
Stop the loop only when TS4 through TS13 are ALL implemented, tested green, committed, pushed,
and the TS13 live Brain re-test passed.
