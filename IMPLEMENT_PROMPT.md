# StackOwl — Build the "Jarvis" Architecture (ADR implementation, Ralph loop)

## Mission
Implement the 7 architectural solutions designed in `.ralph/adr/ADR-1..7` (root causes in
`.ralph/ROOT_CAUSES.md`, theme map + build order in `.ralph/RESEARCH_PLAN.md`). These collapse the 88
audit findings in `.ralph/FINDINGS.md` into one unifying authority per theme. You write real code, with
tests, and ship each ADR end-to-end. The goal: take StackOwl from "honest about failure" to
"architecturally incapable of the whole class of failure," with ADR-1 (AcceptanceAuthority) as the
keystone.

Governing principle: **verification > representation**, **fix the core not the symptom**, and the ADR
is the spec — its **Invariant**, **Shape**, **Migration plan**, and **Verification** sections are your
contract.

## Build order (one ADR per iteration — do NOT reorder; later ADRs consume earlier ones)
**ADR-1 → ADR-4 → ADR-2 → ADR-3 → ADR-5 → ADR-6 → ADR-7.**
(ADR-1 is upstream of 2/5/6/7; ADR-4 prevents the half-edges the others would otherwise add.)

## Absolute rules (do not violate)
1. **NOTHING IS REMOVED.** Every change is additive/unifying. The existing proxies (`giveup_floor`,
   `overclaim_gate`, `judge_delivery`, per-tool `verify()`, `AcceptanceChecker`, the
   `side_effect_committed`/progress ledger, every learned-data column) are made to **delegate** to the
   new authority — never deleted. If a signal is unused, **consume it**, don't drop it.
2. **Flag-gated, off = byte-identical.** Each ADR lands behind a config flag whose default reproduces
   today's behavior exactly (prove it: the existing suite stays green with the flag off). On this
   **powerful production machine**, the flag is intended to **default ON once verified** — cost/latency
   is NOT a reason to cut a more-correct design.
3. **TDD.** For every behavior, write the failing test first, then the minimal code to pass it. The
   invariant in each ADR must have a test that would fail in today's code and passes after.
4. **Honor the hard product directives** (read `CLAUDE.md` + the memory it references):
   - **Positive-only learning** — never store "this failed / I can't" memories. (ADR-5 makes the
     *positives* trustworthy and adds *ephemeral within-turn* failure awareness; it never persists
     negatives.)
   - **No vendor-specific logic** outside thin adapters; **no hardcoded keyword/language lists** (derive
     from data: transient-failure markers, embeddings, Unicode tokenization).
5. **Git discipline.** Branch first (`feat/adr-<n>-<slug>` off `main`); never commit straight to `main`.
   End commit messages with the Co-Authored-By + Claude-Session trailers. Merge `--no-ff`; push only
   when the gate is green and you've confirmed there are no *new* failures.
6. **Run everything from the repo root with `UV_NO_SYNC=1 uv run …`** (mandatory — avoids re-sync).

## State files (your memory; re-read from disk every iteration — trust disk, not memory)
- `.ralph/IMPLEMENTATION_PLAN.md` — the ADR checklist. `[ ]`/`[x]`. Bootstrap it on first run from the
  build order above (one line per ADR with its slug + the findings it closes, copied from
  `RESEARCH_PLAN.md`).
- `.ralph/impl_progress.txt` — 5-line handoff (ADR just shipped, gate result, anything next iteration
  needs).

## Iteration procedure
1. **Bootstrap (idempotent).** Ensure the two state files exist; if `IMPLEMENTATION_PLAN.md` has no
   checklist, write one from the build order. Commit. End turn.
2. Re-read `IMPLEMENTATION_PLAN.md` + `impl_progress.txt` + the **next unchecked ADR** in build order
   and its `ROOT_CAUSES.md` entry.
3. **Explore before building.** Read the exact seams the ADR's *Shape* names; confirm line numbers
   (they drift). Reuse existing machinery (the codebase is full of half-built seams — wire/unify, don't
   rebuild). Where scope is wide, dispatch read-only Explore subagents in parallel to map the call
   sites the authority must subsume.
4. **Implement (TDD), behind the flag.** Land the new abstraction with a default that's byte-identical
   when off; route the named proxies to delegate; close the ADR's findings in its migration order.
5. **Gate.** `UV_NO_SYNC=1 uv run ruff check <changed>` + run the touched suites together
   (the cross-cluster gate). Then prove flag-off is byte-identical against the existing suite.
6. **Distinguish pre-existing failures from regressions.** Known pre-existing reds on this box (do NOT
   chase them; verify any *new* red against clean `main` via `git stash` before treating it as yours):
   - `tests/pipeline/test_execute_floor_invariant.py::test_execute_threads_residual_deadline_into_provider`
   - `tests/pipeline/test_phaseA_react_gateway_smoke.py::test_weak_model_react_tool_dispatch_through_gateway`
   - `tests/pipeline/test_phaseD_persistence.py::test_gateway_agent_does_not_give_up`
   - `tests/tools/knowledge/test_phaseB_self_improvement.py::{test_reflect_now_runs_real_handler_and_writes_reflection, test_agent_triggers_reflect_now_through_gateway}`
   - `tests/parliament/test_concurrent_sessions.py::test_two_sessions_no_interjection_clobber`
   (These are a `_FakeResponse.usage` test-double drift, a `reflect_now` data quirk, and an aiosqlite
   teardown race — all fail identically on clean `main`. If you have spare scope, fixing the
   `_FakeResponse.usage` drift is a legitimate bonus, but it is NOT part of any ADR.)
7. **Ship.** Commit on the branch; merge `--no-ff` to `main`; if the ADR adds a DB migration, apply it
   (`UV_NO_SYNC=1 uv run python -m stackowl db migrate`); push `main`. Mark the ADR `[x]`; write
   `impl_progress.txt`; update `.ralph/BACKLOG.md`/`FINDINGS.md` to note which findings this ADR closed.
8. **Live-verify.** Restart the platform on the new code and confirm health
   (`UV_NO_SYNC=1 uv run python -m stackowl health` — db/fs/graph + all 3 provider tiers green; the
   remote provider box must be reachable, else note it and skip the restart, don't fake it). Set the
   ADR's flag ON in production config once the gate is green, per Rule 2.
9. **If every ADR is `[x]` and its invariant test is green on `main`:** emit
   `<promise>ALL_ADRS_SHIPPED</promise>`. Otherwise end the turn without the sigil.

## Per-ADR Definition of Done (the bar for marking `[x]`)
- The ADR's **Invariant** has a test that fails on pre-ADR code and passes now.
- Flag OFF ⇒ existing suite byte-identical; flag ON ⇒ the ADR's findings are closed (cite F-numbers).
- Every proxy the ADR names now **delegates** to the new authority (grep-confirm none were deleted).
- Touched-suite gate green (only the known pre-existing reds remain); ruff no net-new lint; mypy clean
  on changed files.
- Merged to `main`, pushed; migration applied if any; platform restarts healthy (or the box-down
  reason is recorded).
- No directive violated (no stored negatives; no vendor/keyword lists).

## Where to look
- `.ralph/adr/ADR-<n>-*.md` (the spec) + `.ralph/ROOT_CAUSES.md` (the why) + `.ralph/FINDINGS.md`
  (the file:line evidence for each finding the ADR closes).
- `CLAUDE.md` (commands, logging/observability conventions, sensitive-data rules) + the memory it
  references (positive-only learning; deepest-root "no verification primitive"; fix-core-not-patch;
  registered≠reachable).
- The seams each ADR's *Shape* section names (e.g. `tools/base.py` verify seam, `tools/verification.py`
  `is_trustworthy_success`, `pipeline/acceptance.py`, `pipeline/persistence.py` `judge_delivery`,
  `pipeline/steps/execute.py` recovery ladder, `providers/llm_gateway.py`, `scheduler/assembly.py`,
  `health/reachability/census.py`, `health/aggregator.py`, the `notifications/` deliverer).

## Completion
Emit `<promise>ALL_ADRS_SHIPPED</promise>` **only** when all 7 ADRs are `[x]`, each invariant test is
green on `main`, and `main` is pushed. Until then, end each turn without the sigil so the loop continues.
