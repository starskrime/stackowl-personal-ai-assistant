# Design: Same-tool repeated-failure circuit breaker (incident P2)

**Date:** 2026-06-18
**Branch:** `feat/p2-same-tool-circuit-breaker`
**Incident:** pictures-overclaim (`_bmad-output/incident-pictures-overclaim-fix-plan.md`, §P2)
**Status:** Design grounded in code (see map below). Implementation deferred to a fresh session (cost).

---

## 1. Problem

In the incident, after `execute_code` failed the weak model fired **9 consecutive failing `shell`
calls** (different commands each) and burned budget to the 120 s wall. The 120 s cap is a backstop,
not a spiral-termination strategy. After N consecutive failures of the **same tool**, we should stop
offering it for the rest of the turn so the model either switches tactics or stops cleanly.

This is complementary to the P1 `clarify` verdict (which reduces how often we *enter* the loop) and
to the P0 honest-floor (which prevents lying *at* the wall). The breaker contains the spiral *inside*
the loop.

## 2. Why the existing guards don't cover it (grounded)

- **`LoopGuard`** (`src/stackowl/providers/_react.py:49-92`, `warn_at=3`/`break_at=4`) trips on repeated
  **identical `(name, args)` signatures regardless of outcome**. The incident's 9 shells had
  **different commands** each → LoopGuard never tripped. The new breaker keys on **tool name +
  failure**, not arg-identity, so it catches different-arg failure spirals.
- **Capability substitution** (`execute.py:_try_substitute`, `substituted_tags` set) routes around a
  failed *capability* **once per turn** — but `shell`/`execute_code` have **no `capability_tag`**, so
  substitution doesn't fire for them, and it's a one-shot reroute, not a repeated-failure cutoff.
- The **tool outcome ledger** (`src/stackowl/infra/tool_outcome_ledger.py`) is a flat append-only
  tuple with **no consecutive-failure-by-name counter**.

## 3. Key architectural constraint (grounded)

`_run_with_tools` (`execute.py:552`) builds `tool_schemas` **ONCE per turn** (`execute.py:600-609`) and
hands the list to `provider.complete_with_tools`, which runs **every ReAct iteration internally behind
a single `await`** (`anthropic_provider.py:279` loop; `execute.py:1017-1019` documents this). The
`on_iteration_complete` callback does **NOT** expose `tool_schemas` (`react_callback.py:16-55`).
Therefore we **cannot** prune the offered schema set between iterations from a callback.

**Enforcement point = `_dispatch` (`execute.py:655`)** — the per-call dispatch seam the provider calls
for every tool invocation. Precedents to mirror, both already in `_dispatch`'s closure:
- `denied_this_run: set[str]` (`execute.py:647`, bounce at `:660-668`) — "stop running this tool this turn."
- `substituted_tags: set[str]` (`execute.py:653`) — per-turn set keyed by capability.

## 4. Design

### 4.1 Per-turn consecutive-failure tracking
Add a per-turn dict closed over in `_run_with_tools` alongside `denied_this_run`/`substituted_tags`:
```python
fail_streak: dict[str, int] = {}        # key -> consecutive failures (reset on success)
circuit_open: set[str] = set()          # keys whose breaker has tripped this turn
```
**Key = tool name** (primary). Rationale: the incident tool (`shell`) has no `capability_tag`, and
keying by name is the only thing that catches it. (Optional future: also group by `capability_tag`
when present, so two sibling tools sharing a capability share a streak — NOT in v1; YAGNI.)

Update at the **existing** outcome-record site (`execute.py:884-890`, where `record_tool_outcome` is
already called after a real tool run), in `_dispatch`:
- On `tr.success is True`  → `fail_streak[name] = 0` (a success breaks the streak).
- On `tr.success is False` AND `is_effectful_failure(...)`-style real failure (NOT a pre-exec refusal)
  → `fail_streak[name] += 1`; if `fail_streak[name] >= THRESHOLD` → `circuit_open.add(name)`.
  - Only count **genuine execution failures** toward the streak (a missing-param refusal or a
    consent-deny is not the tool "failing" — those already set `side_effect_committed=False`). Reuse
    the same predicate the ledger uses so the semantics match.

### 4.2 Enforcement (bounce at dispatch)
At the **top** of `_dispatch` (next to the `denied_this_run` check at `execute.py:660`):
```python
if name in circuit_open:
    # stable, model-readable refusal — NOT a tool failure
    return _circuit_open_refusal(name)   # a string, like the denied_this_run bounce
```
The refusal string tells the model this tool is unavailable for the rest of the turn and to try a
different approach or stop. It must be language-neutral in spirit (the glue string may be English like
the other dispatch markers, but carry no case-specifics).

### 4.3 Honesty invariants (critical — must not regress P0)
- The circuit-open bounce is a **pre-execution refusal**, exactly like `denied_this_run`. It MUST
  record its ledger outcome (if any) with `side_effect_committed=False` (mirror the missing-param
  refusal at `execute.py:846-849`) so it **cannot trip the consequential give-up floor**. A tripped
  breaker is not a consequential failure — it's a containment.
- The breaker must NOT itself manufacture an overclaim: it only *stops offering* a tool; the turn's
  honest-floor / judge machinery (P0) still decides what ships. Verify a turn that trips the breaker
  and produces no real deliverable still floors honestly (does NOT overclaim).

### 4.4 Threshold
`THRESHOLD = 3` consecutive same-tool failures (one below LoopGuard's `break_at=4` for identical args,
since the breaker's scope is broader). Make it a named module constant, not a magic number. Capability-
host-agnostic (per "never pin to Jetson"): a fixed small N, not tuned to any model.

## 5. Files (anticipated)
| File | Change |
|---|---|
| `src/stackowl/pipeline/steps/execute.py` | `_run_with_tools`: add `fail_streak`/`circuit_open`; update at the existing record site; bounce at the top of `_dispatch`; a `_circuit_open_refusal(name)` helper + `THRESHOLD` constant. 4-point logging on trip. |
| (maybe) `src/stackowl/infra/tool_outcome_ledger.py` | Only if a shared consecutive predicate is cleaner here than inline. Prefer NOT widening the ledger signature (it's the single source of truth for the floor — see incident memory). |
| `tests/pipeline/test_circuit_breaker_*.py` | Unit: streak increments on failure, resets on success, trips at N, bounce returns refusal + records side_effect_committed=False. |
| `tests/journeys/test_circuit_breaker_journey.py` | Gateway journey: a tool scripted to fail repeatedly is dropped after N; the turn does NOT run it a 4th time; budget is NOT burned to the wall; the turn floors HONESTLY (no overclaim) — drive the REAL `_dispatch` path, mock only the provider's tool-call sequence. |

## 6. Tests / falsification guards
- **Trips**: N consecutive failures of tool X → (N+1)th call to X is bounced (refusal, not executed).
- **Resets**: failure, failure, **success**, failure → streak is 1 after the last, breaker NOT open
  (a success between failures resets — proves it's *consecutive*, not cumulative).
- **Scoped to the tool**: failures of X do not open the breaker for Y.
- **Honesty (load-bearing)**: a turn that trips the breaker and delivers nothing real still ships the
  honest floor, NOT an overclaim, and `failure_class=stop`. The bounce records
  `side_effect_committed=False` → the give-up floor's consequential tally is unaffected.
- **Falsification**: a tool that fails **twice then succeeds** is never bounced and the success is
  delivered — proves the breaker doesn't fire on transient failure.

## 7. Open decisions for the implementer
1. **Key by name only (v1) vs name+capability_tag** — recommend name-only for v1 (catches the incident; simplest). Group-by-capability is a clean follow-up.
2. **Refusal wording** — must steer the model to switch approach or stop, without case-specifics.
3. Confirm the genuine-execution-failure predicate to use for incrementing the streak (reuse the
   ledger's `is_effectful_failure` semantics so a refusal/deny doesn't count).
