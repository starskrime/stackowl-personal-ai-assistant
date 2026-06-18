# Delegation Self-Healing (Owl Capability Arc, Story 3)

> When owl-to-owl delegation goes wrong — target owl missing, child crashes, times out,
> returns empty, or forms a cycle — the system must **detect it honestly** (never swallow a
> failure into an empty string that masquerades as a real answer), **recover where it safely
> can** (retry-once → fallback-to-secretary), and **surface honestly** when it can't. Reshaped
> from a maximal draft by party-mode (Winston/Murat/Dr. Quinn/Amelia).

**Status:** Design approved (2026-06-06); pending spec re-review
**Builds on:** the E8 delegation stack (`delegate_task` tool → `A2ADelegator` → nested `AsyncioBackend.run` child), the Epic-2 S2 `child_floor` (narrowing-only anti-escalation), `TraceContext` lineage, `surface_critical_failure`, the boundary-router persona (owl-builder S1 [[project_owl_builder_arc]]).
**Followed by (arc):** memory/persona robustness.

---

## 1. Problem & value

Delegation is well-built (depth backstop ≤2, width cap ≤4, concurrency governor, `child_floor` bounds clamp, structured refusals) — but its **failure paths swallow or mislead**:

| Failure | Today | Risk |
|---|---|---|
| typo'd / unknown `to_owl` | `OwlNotFoundError` caught → silently falls through to a **different** owl | wrong owl answers; user never told |
| child run crashes (`StackOwlError`) | `_run_specialist` logs + returns `""` | indistinguishable from timeout/empty — error lost |
| child hits step/time/budget cap | parent gets **partial** text, no truncation signal | give-up masquerades as a complete answer |
| child returns empty | collapses to `""` → `timeout_or_empty` | conflated with crash |
| cycle (A→B→A within depth 2) | only the numeric depth backstop trips (after the fact) | no real cycle detection |
| any of the above | failure reaches only the **model** as a JSON record; if the model fabricates/swallows, the user gets a non-answer delivered as real | **"give-up masquerading as delivery"** ([[feedback_verify_outcomes_not_names]], [[feedback_no_hidden_errors]]) |

Self-healing makes delegation **honest + resilient**: detect structurally, recover safely, surface truthfully. Aligns with [[feedback_always_self_healing]] (detect → recover → continue, bounded retry-once).

**Scope decision (user):** structural give-up detection only (empty/error/timeout/truncation/refusal/cycle/target-missing); the fuzzy LLM relevance-judge ("is a non-empty answer actually good?") is deferred. Full recovery ladder. Both model-status + safety-net user surfacing.

---

## 2. Decomposition — 3a then 3b (Winston)

| | Sub-story | Deliverable |
|---|---|---|
| **3a** | **Honest delegation outcomes** | `delegate()` returns a structured status (no more `""`-swallow); child-error/truncation/empty are distinct, governor-decided; target-missing distinct from wrong-target; cycle detection via `delegation_chain`; depth backstop kept. The parent model receives an honest, prose-first failure. **After 3a the system stops lying — shippable, no recovery yet.** |
| **3b** | **Recovery ladder + surfacing** | Bounded ladder (retry-once → fallback-to-secretary, same slot/floor, fallback delegation-disabled + chain-appended); `recovered_via_secretary` + attributed lead-in; `surface_critical_failure` extended (terminal-exhaustion only); global per-turn attempt budget. |

3a consumes nothing from 3b; 3b consumes 3a's status enum. The seam is the enum.

---

## 3. Story 3a — Honest delegation outcomes

### 3.1 `A2AResult` + `DelegationStatus` (the seam)
`A2ADelegator.delegate(...)` stops returning a bare `str`. It returns a structured `A2AResult` (frozen):
- `status: DelegationStatus` — an enum/`Literal`: `ok | empty | timeout | child_error | truncated | refused | cycle | target_not_found`.
- `content: str` — the child's response text (may be partial when `truncated`).
- `child_detail: str` — a **sanitized, length-capped** detail (from `final_state.errors` / the `BudgetBreach` marker), for the model-facing note + logs. **Untrusted data** (see §5).
- `resolved_owl: str` — the owl actually run (audit; target-missing vs wrong-target).

The status is **governor-decided from observed facts** (an exception caught, a timeout elapsed, an empty/partial response, a `BudgetBreach` marker in `final_state.errors`) — **never parsed from child output** (Murat P1-1/P1-3: a child can't fake a status to steer the ladder).

### 3.2 `delegate()` / `_run_specialist` fidelity (kills the `""`-swallow)
- `_run_specialist` (the nested child run) constructs/returns the outcome from `final_state`: success → `content` + `ok`; non-empty errors → `child_error` + joined-sanitized detail; `BudgetBreach`/budget marker in `errors` → `truncated` + partial content; empty content (no errors) → `empty`.
- `delegate()` maps the mailbox/transport outcome: `A2ATimeoutError`/no message → `timeout`; otherwise the child-derived `A2AResult`.
- Transport: encode the child status/detail on the response. Because the call is **in-process synchronous** (parent awaits the child), prefer building `A2AResult` directly from `final_state` (Winston) and carry status/detail as additive frozen fields on `A2AMessage` (`status`/`error`, defaulted — back-compat) for the queue hop.

### 3.3 Target-missing distinct from wrong-target
In `resolver.resolve_target_owl`: when an **explicit** `to_owl` is given but not found (`OwlNotFoundError`), return a `target_not_found` signal — do **not** silently fall through to role/default. (Role/default resolution is unchanged when no explicit `to_owl`.) The tool emits `status="target_not_found"` **before** acquiring a width slot (no slot leak).

### 3.4 Cycle detection — `delegation_chain` (Murat P0-2)
- Add `delegation_chain: tuple[str, ...] = ()` to `PipelineState` (frozen, defaulted) and `TraceContext` (start/get). Threaded **via `sub_state.delegation_chain`** through `AsyncioBackend.run → TraceContext.start` (NOT contextvar inheritance — the child task's contextvar copy is fragile across the governor await). Stamped at `_run_specialist`: `chain + (resolved_target,)`.
- Entries are **resolved owl identities** (the canonical name the resolver returns), **governor-stamped, never model-controlled** (`delegate_task` does not accept a chain argument).
- In `delegate_task.execute`, **after resolve, before width-acquire**: if `resolved_target in chain` (or `== current owl`) → `status="cycle"` refusal (no spawn, no slot). The fallback path (3b) appends to the chain too, so `secretary→specialist→fallback-secretary` is caught.
- **Keep the depth backstop** (`MAX_DELEGATION_DEPTH`): orthogonal — chain catches *revisits*, depth catches *linear runaway* (A→B→C→D, all distinct). `len(chain) == delegation_depth` invariant (asserted in tests).

### 3.5 Prose-first model-facing result (Dr. Quinn)
The `ToolResult` the model reads is **imperative natural language first**, enum as secondary metadata: e.g. *"Delegation could not be completed (target unavailable). Do not delegate again for this request — answer the user directly with your own knowledge, or tell them plainly you cannot complete this part."* Explicitly forbids re-delegation; names the two allowed exits (do-it-yourself / honest hand-off). The `record.status` enum stays for telemetry + the safety-net. All user/model-surfaced phrasing is **multilingual** (i18n/message helper, no hardcoded English — [[feedback_no_hardcoded_english]]); status values stay ASCII machine keys. New `results.py` builders (`cycle_result`, `target_not_found_result`, `truncated_result`, `child_error_result`) are additive; existing builders + the `{note, record}` JSON shape unchanged.

---

## 4. Story 3b — Recovery ladder + surfacing

### 4.1 Bounded ladder (in `_run_delegation`, inside the held width slot)
At most **3** `delegate()` calls per delegation site, linear (no loop):
1. **Attempt** the resolved target.
2. **Retry-once** if `status ∈ {timeout, empty, child_error}` (transient/structural).
3. **Fallback-to-secretary** if still failing and the secretary is resolvable and `secretary ∉ delegation_chain` (cycle-guarded) and `secretary != resolved_target`.

- **Same width slot** (acquired once in `execute`, released once in `finally`) — retry/fallback do **NOT** re-acquire (Murat P0-3: re-acquire at width cap = deadlock; reuse avoids governor starvation).
- **Depth does not increment** on retry/fallback — each is a spawn at the same `parent.depth + 1` (a *replacement*, not a child of the failed attempt). Assert `depth(fallback) == depth(attempt)`.
- Resolve the secretary via the existing resolver/default-owl lookup (no hardcoded `"secretary"` string).
- **No fallback-retry** (cut — caps total at 3; Dr. Quinn's latency concern on the weak box).

### 4.2 Fallback bounds = the original attempt's `child_floor` (Murat P0-1 — THE security invariant)
The fallback secretary-delegation reuses the **exact same `child_floor`** the original attempt carried (the parent's effective bounds at the delegation site). So `effective(fallback) = secretary_own ∩ child_floor ⊆ child_floor = effective(original_attempt)` — **fallback changes *who*, never *what's allowed*; it can never widen.**
- Forward flow (you → secretary[broad] → specialist fails): floor is already broad → the secretary fallback is fully **useful**.
- Reverse flow (narrow specialist → secretary): floor is narrow → the secretary fallback is **narrow** → if the task genuinely needed a tool the parent never had, fallback correctly **fails → honest failure**. No escalation backdoor.
- **Invariant test (required):** fuzz the parent bounds; assert the fallback's tool-axis is always a subset of the original attempt's effective tool-axis. Fallback MUST reuse the exact clamp computation (`child_floor`), not a reimplementation.

### 4.3 Fallback runs with `delegate_task` DISABLED (Dr. Quinn #5 — the key loop guard)
The secretary-fallback invocation removes `delegate_task` from the child's presented/permitted toolset for that run, so a weak secretary cannot re-delegate the failed task (→ instant cycle). **Mechanism, not prose.** (Reuse the existing depth>0 spawn-tool exclusion seam in `execute.py`, or clamp the fallback child's bounds to exclude delegation.)

### 4.4 Attribution + surfacing (Dr. Quinn #3/#4, Murat P1-2)
- **Retry = invisible.** **Secretary handoff = attributed**: on `recovered_via_secretary`, the result carries a localized one-line lead-in (e.g. *"[specialist] was unavailable, so the generalist handled this:"*) so the user understands the identity change (no specialist "ventriloquizing" the secretary). `recovered_result` status.
- **Safety-net** (`surface_critical_failure` extension): a sibling predicate `_delegation_failed_with_no_answer(state)` trips when the **final assistant response is empty/missing AND** a delegate record with a failing status exists in the turn — surfacing a **localized, minimal** honest message: outcome + a correlation id (the traceId), **no owl names / bounds / child error text / stack** (those go to the structured log under the traceId). Fires **only on terminal exhaustion** — `recovered_via_secretary`/`ok` and any non-empty honest parent answer must NOT trip it (Winston: don't double-report a recovered delegation).

### 4.5 Global per-turn attempt budget (Murat P0-3)
A trusted per-turn counter caps total delegation *attempts* (across all sites, including retries/fallbacks) so a crafted prompt can't walk the full width×depth×ladder tree every turn (sustained cost amplification). Independent of the structural depth/width caps. On exhaustion → refusal (`status="budget"`-style), logged.

---

## 5. Security model (Murat — non-negotiable)
- **No escalation:** §4.2 shared-floor rule + the subset property test. `child_floor` survives the fallback path unchanged.
- **Cycle chain authoritative:** governor-stamped, resolved-identity, model-untouchable, reconstructed across the A2A hop (threaded via `sub_state`, not implicit contextvar inheritance). Fallback appends. `secretary→specialist→fallback-secretary` test.
- **Bounded amplification:** retry/fallback reuse the slot, don't increment depth, ≤3 attempts/site, + global per-turn budget.
- **Child-error detail = untrusted data:** the *status* is governor-decided (never from child text); the child free-text detail is **escaped + length-capped + fenced** as untrusted when surfaced to the parent model (never concatenated raw into the instruction region). The ladder keys off the trusted enum only.
- **No info leak:** user-facing surfacing = outcome + traceId; internals → logs.

---

## 6. Data flow

```
delegate_task.execute (parent):
  ctx = TraceContext.get()  → delegation_depth, delegation_chain, owl_name, child_floor
  resolve target            → target_not_found? → honest result (no slot)
  cycle: resolved_target in chain or == owl?  → cycle result (no slot)
  depth >= MAX?             → depth refusal
  acquire ONE width slot (try / finally release):
    _run_delegation (the ladder, ≤3 delegate() calls, same slot, same child_floor):
      attempt  = delegate(target,  floor)                # A2AResult, governor-decided status
      if transient: retry = delegate(target, floor)
      if still failing and secretary∉chain: fb = delegate(secretary, floor, delegate_disabled=True)
      → ok / recovered_via_secretary(+attributed lead-in) / honest-failure(status)
  return prose-first ToolResult{note, record.status}    # model: "do not delegate again; answer or tell user"

A2ADelegator.delegate(to_owl, floor, *, delegation_chain, delegate_disabled) -> A2AResult:
  spawn _run_specialist → AsyncioBackend.run(sub_state with chain+(to_owl,), child_floor, depth+1)
  status from final_state (errors→child_error, budget→truncated, empty→empty, else ok); timeout from mailbox

deliver / safety-net (parent turn):
  if final response empty AND a failing delegate record AND not recovered:
      surface_critical_failure → localized "couldn't complete (ref: <traceId>)"
```

---

## 7. Testing (TDD; mock only the AI provider)

**3a units (`tests/tools/agents/`, `tests/owls/`)** — `delegate()` returns structured status not `""`; child-error vs timeout vs empty are **distinct** (deterministic via a `_FakeDelegator` returning scripted `(text, status)`); target-missing → `target_not_found` with the delegator **never called** + **no slot acquired**; cycle: seed `delegation_chain=("specialist",)`, delegate to `specialist` → `cycle`, delegator never called, no slot; `len(chain)==delegation_depth` invariant; chain threaded through `AsyncioBackend.run`→`TraceContext`; prose-first note forbids re-delegation; result-builder JSON shape stable.

**3b units** — ladder call-count proves retry-once + single slot (script `[("","timeout"),("","timeout"),("ans","ok")]` → exactly 3 calls, final `recovered_via_secretary`); **slot accounting**: retry/fallback within one `execute` never block a width-1 limiter (single acquire), a *second concurrent* delegation does block (cap intact); depth unchanged across fallback; fallback skipped when secretary already in chain (`secretary→specialist→fallback-secretary` → honest fail, no loop); fallback child has `delegate_task` disabled; safety-net predicate unit (empty+failing→True; recovered→False; non-empty→False); global per-turn budget caps attempts.

**Security property test (required, 4.2)** — fuzz parent bounds; assert `effective(fallback).tools ⊆ effective(original_attempt).tools` for every failure class. If it can't be written cleanly, the fallback bounds path is wrong.

**Gateway journeys (`tests/smoke/`, `tests/journeys/`)** — mirror `test_e8_s1_delegate_task_telegram_smoke.py`: (A) a child that **raises** → the parent's final user message is **honest + non-empty** (not silent); (B) **fallback recovery** — specialist fails, secretary (same floor) succeeds → user gets the answer with the attributed lead-in; (C) **no-escalation** — a *narrow* specialist whose delegated task needs an out-of-floor tool fails, fallback-to-secretary is **narrow** and also fails → honest failure (the secretary did NOT gain the tool); (D) **cycle** → honest failure, no loop/hang.

---

## 8. Out of scope / deferred (tracked)
| Item | Why | Where |
|---|---|---|
| LLM relevance-judge (is a non-empty answer actually good/on-goal?) | fuzzy + per-delegation LLM cost on the weak box; structural detection catches the swallowed give-ups | follow-up story |
| Cross-bounds "useful" escalation fallback | IS the escalation backdoor; the shared-floor rule is the secure model | never (by design) |
| Retry idempotency keys (a child that did a side-effect before failing → retry duplicates it) | bigger design; document the caveat | Phase-2 |
| Configurable retry count / backoff; per-status strategy registry | YAGNI — retry-once hardcoded, one ladder | — |
| Making delegated children durable (resume on failure) | delegation + durability are disjoint today; large | Phase-2 |
| Confidence-tiering of output-derived triggers (empty/refusal lower-confidence) | budget already bounds spam; refinement | Phase-2 |
