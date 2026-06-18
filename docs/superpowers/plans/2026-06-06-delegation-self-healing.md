# Delegation Self-Healing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make owl-to-owl delegation honest (no swallowed failures) and resilient (retry-once → fallback-to-secretary → honest surfacing), without ever escalating privilege.

**Architecture:** 3a makes outcomes honest — `A2ADelegator.delegate()` returns a structured `A2AResult` (status governor-decided from the child's `final_state`, killing the `""`-swallow), target-missing is distinct from wrong-target, and a `delegation_chain` threaded through `TraceContext`/`PipelineState` gives real cycle detection. 3b adds a bounded recovery ladder in the tool (retry-once → fallback-to-secretary reusing the SAME `child_floor` so it can never escalate) plus a safety-net that surfaces an honest message when the ladder is exhausted.

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen), asyncio, contextvars, pytest, ruff, mypy --strict. Code under `v2/`. Tests: `uv run pytest <path> -v` (NO `--timeout`; targeted paths only — full suite hangs).

---

## ⚠️ Reuse Ledger — NO DUPLICATE CODE (read first)

Operator's standing complaint: ~50% of written code is duplicated. **Every task extends existing seams.** Each implementer MUST grep for the existing impl first and report reused-vs-created. Pre-made decisions:

| Concern | Decision | Single source of truth |
|---|---|---|
| Fallback bounds floor | **REUSE** — the ladder passes the SAME `parent_state` (built once with `child_floor(caller, …)`) to every attempt incl. fallback; only `to_owl` changes → same floor for free, no recompute. | `delegate_task._run_delegation` |
| Fallback-with-delegation-disabled (loop guard) | **REUSE** — the fallback child runs at `delegation_depth ≥ 1`; `execute.py`'s existing `_CHILD_EXCLUDED_TOOLS` already excludes `delegate_task` at depth>0. No new mechanism; verify with a test. | `execute.py` (existing) |
| Result envelope | **EXTEND** `results.py` — new builders all funnel through the existing `ok_result(record, t0, note=…)` like `refusal_result` does. Stable `{note, record}` JSON shape. | `tools/agents/results.py` |
| `delegation_chain` threading | **EXTEND** the exact `delegation_depth` pattern (TraceContext contextvar+token+start+reset+get; PipelineState field; both backends). | mirror `delegation_depth` |
| Secretary lookup | **CREATE** one public `OwlRegistry.secretary_name` accessor over the existing private `_SECRETARY_NAME` + `has_secretary()` — no hardcoded `"secretary"` literal in delegation code. | `owls/registry.py` |
| Resolution policy | **EXTEND** `resolve_target_owl` to return a structured result distinguishing explicit-not-found; one resolver (no second resolution path). | `tools/agents/resolver.py` |
| Safety-net | **EXTEND** `critical_failure.py` with a sibling predicate; the existing `surface_critical_failure` flow + call sites unchanged. | `pipeline/critical_failure.py` |
| Status interpretation | The ladder keys ONLY off the governor-decided `A2AResult.status` enum — never re-parses child text (security). | tool |

---

## Decomposition: 3a (T1–T5) then 3b (T6–T10). 3a is shippable + green (system stops lying; no recovery yet).

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/pipeline/state.py` | Modify | add `delegation_chain: tuple[str,...] = ()` |
| `src/stackowl/infra/trace.py` | Modify | thread `delegation_chain` (contextvar/token/start/reset/get) |
| `src/stackowl/pipeline/backends/asyncio_backend.py` + `langgraph_backend.py` | Modify | pass `delegation_chain` to `TraceContext.start` |
| `src/stackowl/owls/a2a_delegation.py` | Modify | `A2AResult`/`DelegationStatus`; `delegate()` returns it; `_run_specialist` fidelity + chain stamp |
| `src/stackowl/messaging/a2a.py` | Modify | `A2AMessage` `status`/`error` fields (frozen+defaulted) |
| `src/stackowl/tools/agents/resolver.py` | Modify | structured resolution (explicit-not-found distinct) |
| `src/stackowl/tools/agents/results.py` | Modify | new builders (cycle/target_not_found/truncated/child_error/recovered) |
| `src/stackowl/tools/agents/delegate_task.py` | Modify | cycle check; map `A2AResult.status`; the recovery ladder; attempt budget |
| `src/stackowl/owls/registry.py` | Modify | `secretary_name` accessor |
| `src/stackowl/owls/delegation_limits.py` | Modify | `MAX_DELEGATION_ATTEMPTS_PER_TURN` |
| `src/stackowl/pipeline/critical_failure.py` | Modify | swallowed-delegation predicate |
| tests (per task) | Create | units + 4 gateway/smoke journeys |

---

## STORY 3a — Honest delegation outcomes

### Task 1: `delegation_chain` plumbing

**Files:** Modify `pipeline/state.py` (after `delegation_depth`, line 51); `infra/trace.py` (mirror `delegation_depth`); both backends (`backends/asyncio_backend.py:42-50`, `backends/langgraph_backend.py:105-113`). Test: `tests/infra/test_trace_delegation_chain.py` (Create).

- [ ] **Step 1: Write the failing test**

```python
# tests/infra/test_trace_delegation_chain.py
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.state import PipelineState


def test_pipeline_state_delegation_chain_defaults_empty():
    s = PipelineState(trace_id="t", session_id="s", input_text="i", channel="cli",
                      owl_name="secretary", pipeline_step="start")
    assert s.delegation_chain == ()
    assert s.evolve(delegation_chain=("a", "b")).delegation_chain == ("a", "b")


def test_trace_context_carries_delegation_chain():
    tok = TraceContext.start("s", trace_id="t", delegation_chain=("secretary", "scout"))
    try:
        assert TraceContext.get()["delegation_chain"] == ("secretary", "scout")
    finally:
        TraceContext.reset(tok)
    # after reset → default
    assert TraceContext.get()["delegation_chain"] == ()
```

- [ ] **Step 2: Run — verify FAIL** (`delegation_chain` unknown).

`cd v2 && uv run pytest tests/infra/test_trace_delegation_chain.py -v`

- [ ] **Step 3: Implement**

`pipeline/state.py` — add after `delegation_depth: int = 0`:
```python
    # Owl-name ancestry of the current delegation (governor-stamped, model-untouchable).
    # Powers cycle detection (refuse if a target is already in the chain). len() == delegation_depth.
    delegation_chain: tuple[str, ...] = ()
```

`infra/trace.py` — mirror `delegation_depth` exactly:
- add contextvar: `_delegation_chain: ContextVar[tuple[str, ...]] = ContextVar("delegation_chain", default=())`
- add `delegation_chain: Token[tuple[str, ...]]` to `_TraceToken`
- add `delegation_chain: tuple[str, ...] = ()` kwarg to `start(...)`; in the returned `_TraceToken`: `delegation_chain=cls._delegation_chain.set(delegation_chain),`
- in `reset(...)`: `cls._delegation_chain.reset(token.delegation_chain)`
- in `get()`: `"delegation_chain": cls._delegation_chain.get(),`

Both backends — add `delegation_chain=state.delegation_chain,` to the `TraceContext.start(...)` call.

- [ ] **Step 4: Run — verify PASS** (2) + regression `cd v2 && uv run pytest tests/pipeline/ tests/infra/ -q` (no regressions from the additive field).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/state.py v2/src/stackowl/infra/trace.py v2/src/stackowl/pipeline/backends/asyncio_backend.py v2/src/stackowl/pipeline/backends/langgraph_backend.py v2/tests/infra/test_trace_delegation_chain.py
git commit -m "feat(v2): thread delegation_chain through state + trace + backends (delegation-healing T1)"
```

---

### Task 2: `A2AResult`/`DelegationStatus` + `A2AMessage` status/error fields

**Files:** Modify `owls/a2a_delegation.py` (add the value object near the top); `messaging/a2a.py` (`A2AMessage`). Test: `tests/messaging/test_a2a_message_status.py` (Create).

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_a2a_message_status.py
from stackowl.messaging.a2a import A2AMessage
from stackowl.owls.a2a_delegation import A2AResult


def test_a2a_message_status_error_default_none_and_settable():
    m = A2AMessage.now(from_owl="a", to_owl="b", content="x", message_type="response", trace_id="t")
    assert m.status is None and m.error is None
    m2 = A2AMessage.now(from_owl="a", to_owl="b", content="", message_type="response",
                        trace_id="t", status="child_error", error="boom")
    assert m2.status == "child_error" and m2.error == "boom"


def test_a2a_result_shape():
    r = A2AResult(status="ok", content="hi", child_detail="", resolved_owl="scout")
    assert r.status == "ok" and r.content == "hi" and r.resolved_owl == "scout"
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement**

`messaging/a2a.py` — add to `A2AMessage` (frozen, `extra="forbid"`) after `timestamp`:
```python
    status: str | None = None
    error: str | None = None
```
and add `status: str | None = None, error: str | None = None` params to `.now()` (keyword-only) + pass them to the constructor.

`owls/a2a_delegation.py` — add near the top (after imports):
```python
from typing import Literal

DelegationStatus = Literal[
    "ok", "empty", "timeout", "child_error", "truncated", "refused", "cycle", "target_not_found"
]


class A2AResult(BaseModel):
    """Structured outcome of one delegation round-trip (replaces the bare ``str``).

    ``status`` is GOVERNOR-DECIDED from observed facts (exception/timeout/empty/
    final_state.errors) — never parsed from child output, so a child cannot fake a
    status to steer the recovery ladder. ``child_detail`` is sanitized untrusted data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DelegationStatus
    content: str = ""
    child_detail: str = ""
    resolved_owl: str = ""
```
(Import `BaseModel, ConfigDict` from pydantic if not present.)

- [ ] **Step 4: Run — verify PASS** + regression `cd v2 && uv run pytest tests/messaging/ -q`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/messaging/a2a.py v2/src/stackowl/owls/a2a_delegation.py v2/tests/messaging/test_a2a_message_status.py
git commit -m "feat(v2): A2AResult + A2AMessage status/error fields (delegation-healing T2)"
```

---

### Task 3: `delegate()` + `_run_specialist` fidelity (kill the `""`-swallow) + chain stamp

**Files:** Modify `owls/a2a_delegation.py` (`delegate` ~75-151; `_run_specialist` ~179-255). Test: `tests/owls/test_a2a_fidelity.py` (Create).

`delegate()` widens from `-> str` to `-> A2AResult`. **Single caller** (`delegate_task._run_delegation`) — updated in T5.

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_a2a_fidelity.py
import pytest

from stackowl.messaging.a2a import A2AMessage, A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState


def _parent(**kw):
    return PipelineState(trace_id="t", session_id="s", input_text="go", channel="cli",
                         owl_name="secretary", pipeline_step="dispatch", **kw)


@pytest.mark.asyncio
async def test_timeout_returns_timeout_status_not_empty_string(monkeypatch):
    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=0.05)
    # no specialist will respond → receive times out
    res = await deleg.delegate(from_owl="secretary", to_owl="ghost", sub_task="x", parent_state=_parent())
    assert res.status == "timeout"
    assert res.content == ""
```

(Add a child-error test once the implementer can inject a failing child — see Step 3 note. At minimum the timeout test must prove the return is a structured `A2AResult`, not `""`.)

- [ ] **Step 2: Run — verify FAIL** (`delegate` returns `str`).

- [ ] **Step 3: Implement**

In `delegate()`: replace the two `return ""` (timeout ~116, receive-error ~124) with `return A2AResult(status="timeout", resolved_owl=to_owl)` and `return A2AResult(status="child_error", resolved_owl=to_owl, child_detail=_sanitize(str(exc)))`. On success (~151), read the response message and build the result from its `status`/`error`/`content`:
```python
        status = response.status or ("empty" if not response.content.strip() else "ok")
        return A2AResult(status=status, content=response.content,
                         child_detail=response.error or "", resolved_owl=to_owl)
```

In `_run_specialist`: stamp the chain in `sub_state` (alongside the existing `delegation_depth+1`):
```python
            delegation_chain=parent_state.delegation_chain + (to_owl,),
```
Encode the child outcome on the reply message (governor-decided from `final_state`):
```python
        status = "ok"
        detail = ""
        # (inside the try, after final_state is computed)
        if final_state.errors:
            if any(e.startswith("budget:") for e in final_state.errors):
                status = "truncated"
            else:
                status = "child_error"
            detail = _sanitize("; ".join(final_state.errors))
        elif not response_text.strip():
            status = "empty"
        # on the StackOwlError except path: status = "child_error"; detail = _sanitize(str(exc))
        reply = A2AMessage.now(from_owl=to_owl, to_owl=from_owl, content=response_text,
                               message_type="response", trace_id=parent_state.trace_id,
                               status=status, error=detail)
```
Add a module-level `_sanitize(text: str) -> str` that strips control chars + caps length (e.g. 500) — **reuse** any existing truncation/redaction helper if one exists (grep `redact`/`truncate` in infra/); only create if none. (`detail` is untrusted data — capped, no secrets.)

NOTE: the `budget:` prefix check must match the real BudgetBreach marker written to `final_state.errors` — confirm the exact marker by reading `pipeline/budget/` + `execute.py` (recon: `budget:stop:<cap>`). Use the real prefix.

- [ ] **Step 4: Run — verify PASS** + regression `cd v2 && uv run pytest tests/owls/ -q`. (Some existing tests may assert `delegate()` returns a str — those are updated in T5 where the caller changes; if an `owls/` test directly asserts the old str return, update it minimally to the `A2AResult` and note it.)

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/a2a_delegation.py v2/tests/owls/test_a2a_fidelity.py
git commit -m "feat(v2): delegate() returns governor-decided A2AResult + chain stamp (delegation-healing T3)"
```

---

### Task 4: Resolver — target-missing distinct from wrong-target

**Files:** Modify `tools/agents/resolver.py`. Test: `tests/tools/agents/test_resolver.py` (Create or extend).

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/agents/test_resolver.py
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.agents.resolver import resolve_target


def _reg():
    r = OwlRegistry.with_default_secretary()
    r.register(OwlAgentManifest(name="scout", role="research", system_prompt="p", model_tier="fast"))
    return r


def test_explicit_missing_is_target_not_found():
    res = resolve_target(registry=_reg(), to_owl="ghost", role=None, caller="secretary")
    assert res.name is None and res.reason == "target_not_found"


def test_explicit_present_ok():
    res = resolve_target(registry=_reg(), to_owl="scout", role=None, caller="secretary")
    assert res.name == "scout" and res.reason is None


def test_default_pick_when_no_explicit():
    res = resolve_target(registry=_reg(), to_owl=None, role=None, caller="secretary")
    assert res.name == "scout" and res.reason is None
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement** — add a structured result + a new `resolve_target` (keep `resolve_target_owl` as a thin back-compat shim returning `.name`, OR migrate its single caller in T5). Cleanest: rename to `resolve_target` returning a frozen `TargetResolution(name: str | None, reason: str | None)`; `reason="target_not_found"` ONLY when an explicit `to_owl` was given but `registry.get` raised `OwlNotFoundError` (do NOT fall through to default in that case); `reason="unresolved"` when no candidate at all; else `name` set, `reason=None`. Keep role/default logic identical otherwise.

```python
@dataclass(frozen=True)
class TargetResolution:
    name: str | None
    reason: str | None  # None=ok | "target_not_found" | "unresolved"


def resolve_target(*, registry, to_owl, role, caller) -> TargetResolution:
    if registry is None:
        return TargetResolution(None, "unresolved")
    if to_owl:
        try:
            registry.get(to_owl)
            return TargetResolution(to_owl, None)
        except OwlNotFoundError:
            return TargetResolution(None, "target_not_found")   # do NOT fall through
    candidates = [m for m in registry.list() if m.name != caller]
    if role:
        wanted = role.casefold()
        for m in candidates:
            if m.role.casefold() == wanted:
                return TargetResolution(m.name, None)
    if candidates:
        return TargetResolution(candidates[0].name, None)
    return TargetResolution(None, "unresolved")
```
Update the single caller (delegate_task) in T5.

- [ ] **Step 4: Run — verify PASS.**

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/tools/agents/resolver.py v2/tests/tools/agents/test_resolver.py
git commit -m "feat(v2): resolver distinguishes target-not-found from wrong-target (delegation-healing T4)"
```

---

### Task 5: Wire 3a into `delegate_task` — cycle check, status mapping, new builders

**Files:** Modify `tools/agents/results.py` (new builders); `tools/agents/delegate_task.py` (cycle check pre-slot; resolve→`resolve_target`; map `A2AResult.status`). Modify `tools/agents/schema.py` (prose guidance). Test: extend `tests/tools/agents/test_delegate_task.py`.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.asyncio
async def test_cycle_refused_before_spawn():
    fake = _FakeDelegator("x")
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli",
                               owl_name="secretary", delegation_chain=("scout",))
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="scout")
    finally:
        TraceContext.reset(trace); reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "cycle"
    assert fake.calls == []        # refused PRE-spawn, no delegation


@pytest.mark.asyncio
async def test_target_not_found_distinct_no_spawn():
    fake = _FakeDelegator("x")
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="ghost")
    finally:
        TraceContext.reset(trace); reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "target_not_found"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_child_error_status_mapped():
    fake = _FakeDelegator(A2AResult(status="child_error", child_detail="boom", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="scout")
    finally:
        TraceContext.reset(trace); reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "child_error"
```

NOTE: `_FakeDelegator.delegate` must now return an `A2AResult` (update the fake). The existing 15 tests that asserted `record["status"]=="ok"`/`"timeout_or_empty"` must be updated: the happy fake returns `A2AResult(status="ok", content="...", resolved_owl="scout")`; the empty case returns `A2AResult(status="empty", ...)` → mapped to `record["status"]=="empty"` (was `timeout_or_empty`). Update those assertions (this is a real, intended status refinement — note it, do not silently weaken).

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement**

`results.py` — add builders mirroring `refusal_result` (all via `ok_result`, stable shape). Prose-first, multilingual notes (route user/model-surfaced phrasing through the existing i18n/message helper if one exists; else a module constant is acceptable for the model-facing note but DO flag it):
```python
def cycle_result(t0, *, target, chain):
    return ok_result({"status": "cycle", "to_owl": target,
                      "detail": f"delegating to '{target}' would form a loop ({' -> '.join(chain)} -> {target}); "
                                "do not delegate again — answer the user directly or tell them you cannot."},
                     t0, note="delegation cycle prevented")

def target_not_found_result(t0, *, to_owl):
    return ok_result({"status": "target_not_found", "to_owl": to_owl,
                      "detail": f"no owl named '{to_owl}' exists; do not delegate again — "
                                "answer the user directly or tell them you cannot."},
                     t0, note="delegation target not found")

def child_error_result(t0, *, target, detail):
    return ok_result({"status": "child_error", "to_owl": target, "detail": detail,
                      "result": ""}, t0, note=f"{target} failed; do not delegate again — handle it yourself")

def truncated_result(t0, *, target, result, detail):
    return ok_result({"status": "truncated", "to_owl": target, "result": result, "detail": detail},
                     t0, note=f"{target}'s answer was cut off (resource cap); treat as incomplete")
```
(`recovered_result` lands in T7.)

`delegate_task.execute` — replace the resolve block with `resolve_target(...)`:
```python
        resolution = resolve_target(registry=services.owl_registry, to_owl=args.to_owl, role=args.role, caller=caller)
        if resolution.reason == "target_not_found":
            return target_not_found_result(t0, to_owl=args.to_owl or "")
        if resolution.name is None:
            return refusal_result(t0, reason="unresolved_target", detail="...")  # keep existing
        target = resolution.name
        # CYCLE check — AFTER resolve, BEFORE width-acquire (no slot leak):
        chain = tuple(ctx.get("delegation_chain") or ())
        if target in chain or target == caller:
            return cycle_result(t0, target=target, chain=(*chain, caller) if caller not in chain else chain)
```
Build `parent_state` with the chain: add `delegation_chain=chain` to the `PipelineState(...)` in `_run_delegation`. Map `A2AResult.status` → record (no re-inference): in `_run_delegation`, after `result = await delegator.delegate(...)` (now an `A2AResult`):
```python
        if result.status == "ok":
            record = {"status": "ok", "to_owl": target, "result": result.content + provenance_footer(target)}
            return ok_result(record, t0, note=f"{target} handled the sub-task")
        if result.status == "empty":
            return ok_result({"status": "empty", "to_owl": target, "result": ""}, t0, note=f"{target} produced no result")
        if result.status == "truncated":
            return truncated_result(t0, target=target, result=result.content, detail=result.child_detail)
        # timeout / child_error
        return child_error_result(t0, target=target, detail=result.child_detail or result.status)
```
(The retry/fallback ladder replaces this straight-line mapping in T7 — for now it's single-attempt with honest statuses.)

`schema.py` — extend `DELEGATE_TASK_DESCRIPTION`: add a sentence — *"If a result status is 'cycle', 'target_not_found', 'child_error', 'timeout', or 'empty', the delegation failed: do NOT call delegate_task again for this request — do the work yourself or tell the user plainly. Prefer specifying 'to_owl' explicitly."* (Dr. Quinn: nail the loop door shut + name the exits.) Do NOT add a chain parameter (chain is governor-only).

- [ ] **Step 4: Run — verify PASS** (new + updated existing) `cd v2 && uv run pytest tests/tools/agents/ -v`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/tools/agents/results.py v2/src/stackowl/tools/agents/delegate_task.py v2/src/stackowl/tools/agents/schema.py v2/tests/tools/agents/
git commit -m "feat(v2): honest delegation statuses — cycle/target-not-found/child-error/truncated (delegation-healing T5)"
```

---

## STORY 3b — Recovery ladder + surfacing

### Task 6: `secretary_name` accessor + attempt-budget constant

**Files:** Modify `owls/registry.py`; `owls/delegation_limits.py`. Test: `tests/owls/test_registry_secretary_name.py` (Create).

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_registry_secretary_name.py
from stackowl.owls.registry import OwlRegistry


def test_secretary_name_present_and_absent():
    r = OwlRegistry.with_default_secretary()
    assert r.secretary_name() is not None
    assert OwlRegistry().secretary_name() is None   # empty registry → None
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement** — `owls/registry.py`:
```python
    def secretary_name(self) -> str | None:
        """The mandatory generalist owl's name, or None if not registered (fallback target)."""
        return _SECRETARY_NAME if self.has_secretary() else None
```
`owls/delegation_limits.py` — add: `MAX_DELEGATION_ATTEMPTS_PER_TURN = 12` (with a comment: cumulative delegate() attempts per trace incl. retries/fallbacks; an amplification ceiling above the structural depth×width×ladder bound).

- [ ] **Step 4: Run — verify PASS.**

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/registry.py v2/src/stackowl/owls/delegation_limits.py v2/tests/owls/test_registry_secretary_name.py
git commit -m "feat(v2): OwlRegistry.secretary_name + per-turn attempt-budget constant (delegation-healing T6)"
```

---

### Task 7: The bounded recovery ladder

**Files:** Modify `tools/agents/delegate_task.py` (`_run_delegation` → ladder; `_attempts` counter); `tools/agents/results.py` (`recovered_result`). Test: extend `tests/tools/agents/test_delegate_task.py`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_retry_then_fallback_recovers(monkeypatch):
    # script: attempt timeout, retry timeout, fallback-to-secretary ok
    fake = _ScriptedDelegator([
        A2AResult(status="timeout", resolved_owl="scout"),
        A2AResult(status="timeout", resolved_owl="scout"),
        A2AResult(status="ok", content="done", resolved_owl="secretary"),
    ])
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="scout")
    finally:
        TraceContext.reset(trace); reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "recovered_via_secretary"
    assert "done" in rec["result"]
    assert len(fake.calls) == 3            # attempt + retry + fallback (proves retry-once + one fallback)
    assert [c["to_owl"] for c in fake.calls] == ["scout", "scout", "secretary"]


@pytest.mark.asyncio
async def test_fallback_skipped_when_caller_is_secretary():
    # caller==secretary: no self-fallback; exhausted → honest child_error/timeout
    fake = _ScriptedDelegator([A2AResult(status="child_error", resolved_owl="scout"),
                               A2AResult(status="child_error", resolved_owl="scout")])
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="scout")
    finally:
        TraceContext.reset(trace); reset_services(token)
    rec = _record(res.output)
    assert rec["status"] in {"child_error", "timeout"}   # secretary handles inline next turn
    assert len(fake.calls) == 2                            # attempt + retry, NO fallback (caller IS secretary)
    assert all(c["to_owl"] == "scout" for c in fake.calls)
```

`_ScriptedDelegator` returns successive `A2AResult`s per call; records calls (mirror `_FakeDelegator` shape but scripted). Add it to the test file.

NOTE on `caller==secretary`: when the owl running the ladder IS the secretary, the fallback target == caller → skip the fallback rung (no self-delegation); the honest failure tells the secretary to do it itself (its broad bounds let it). When the caller is a narrow specialist, the secretary fallback DOES fire (clamped to the same narrow floor).

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement** — rewrite `_run_delegation`'s single-attempt mapping (from T5) into the bounded ladder. Keep ONE width slot (acquired in `execute`), reuse the SAME `parent_state` (same `child_floor`) for every attempt:
```python
    _RETRIABLE = frozenset({"timeout", "empty", "child_error"})

    async def _run_delegation(self, *, delegator, args, caller, target, depth, trace_id, session_id, channel, t0):
        sub_task = compose_sub_task(args.goal, args.context)
        chain = tuple(TraceContext.get().get("delegation_chain") or ())
        parent_state = PipelineState(
            trace_id=trace_id or "delegate-task", session_id=session_id, input_text=sub_task,
            channel=channel, owl_name=caller, pipeline_step="dispatch", delegation_depth=depth,
            delegation_chain=chain,
            creation_ceiling=child_floor(caller, TraceContext.creation_ceiling(), get_services().owl_registry),
        )

        async def _attempt(to_owl: str) -> "A2AResult":
            if not self._charge_attempt(trace_id):           # global per-turn budget
                return A2AResult(status="refused", resolved_owl=to_owl)
            return await delegator.delegate(from_owl=caller, to_owl=to_owl, sub_task=sub_task, parent_state=parent_state)

        result = await _attempt(target)                       # attempt
        if result.status in self._RETRIABLE:
            result = await _attempt(target)                   # retry-once
        secretary = get_services().owl_registry.secretary_name() if get_services().owl_registry else None
        if (result.status in self._RETRIABLE and secretary is not None
                and secretary != caller and secretary != target and secretary not in chain):
            fb = await _attempt(secretary)                    # fallback-to-secretary (SAME parent_state = SAME floor)
            if fb.status == "ok":
                return recovered_result(t0, original=target, via=secretary, result=fb.content)
            result = fb if fb.status not in self._RETRIABLE else result
        # map terminal status (reuse the T5 mapping for ok/empty/truncated/child_error/timeout)
        return self._map_terminal(result, target, t0)
```
`recovered_result` (results.py) — attributed lead-in (multilingual via the i18n helper if present):
```python
def recovered_result(t0, *, original, via, result):
    lead = f"[{original} was unavailable, so {via} handled this:]\n"
    return ok_result({"status": "recovered_via_secretary", "to_owl": via, "original": original,
                      "result": lead + result + provenance_footer(via)}, t0,
                     note=f"recovered: {via} handled the sub-task after {original} failed")
```
Add `_charge_attempt(trace_id)` (the global budget) mirroring `_try_acquire`: an `_attempts: dict[str,int]` counter incremented per attempt, returns False past `MAX_DELEGATION_ATTEMPTS_PER_TURN`; bound the dict size (clear if > 256 traces) to avoid a leak. Extract `_map_terminal` from the T5 mapping (DRY — one terminal-status mapper used by both the straight path and the ladder).

CRITICAL invariants (assert via the tests above + T8): the ladder makes ≤3 `delegate()` calls; all attempts share the ONE width slot (no re-acquire — never call `_try_acquire` inside the ladder); depth is NOT incremented per attempt (each `delegate()` spawns at `parent_state.delegation_depth+1` uniformly via `_run_specialist`); the fallback uses the SAME `parent_state` ⇒ SAME `creation_ceiling`/`child_floor`.

- [ ] **Step 4: Run — verify PASS** + regression `cd v2 && uv run pytest tests/tools/agents/ -v`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/tools/agents/delegate_task.py v2/src/stackowl/tools/agents/results.py v2/tests/tools/agents/
git commit -m "feat(v2): bounded recovery ladder — retry-once + fallback-to-secretary (delegation-healing T7)"
```

---

### Task 8: Security property test — fallback never escalates

**Files:** Test: `tests/tools/agents/test_fallback_no_escalation.py` (Create). No production change (the invariant holds by construction — same `parent_state`/floor — this task PROVES it).

- [ ] **Step 1: Write the property test**

```python
# tests/tools/agents/test_fallback_no_escalation.py
"""The fallback delegation must NEVER widen the tool axis beyond the original attempt's
effective bounds (Murat P0-1). The ladder reuses the same parent_state ⇒ same child_floor,
so effective(fallback) = secretary ∩ child_floor ⊆ child_floor = effective(attempt)."""
import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.authz_compose import child_floor, effective_bounds, resolve_owl_bounds


@pytest.mark.parametrize("narrow_tools", [
    frozenset({"read_file"}), frozenset({"web_fetch", "delegate_task"}),
    frozenset(), frozenset({"read_file", "memory"}),
])
def test_fallback_floor_is_subset_of_attempt_floor(narrow_tools):
    # a narrow specialist delegates; both attempt and fallback are clamped to the SAME child_floor
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="narrow", role="r", system_prompt="p", model_tier="fast",
                                  bounds=BoundsSpec(tools=narrow_tools)))
    floor = child_floor("narrow", None, reg)   # the floor both attempt + fallback use
    # the secretary, clamped to that floor, can never exceed it on the tools axis
    sec_effective = effective_bounds(resolve_owl_bounds(reg.secretary_name(), reg), floor)
    if sec_effective is not None and sec_effective.tools is not None and floor is not None and floor.tools is not None:
        assert sec_effective.tools <= floor.tools     # subset: no escalation
```

(Confirm `effective_bounds`/`resolve_owl_bounds` signatures from `authz_compose.py`; the test asserts the clamp the ladder relies on is genuinely narrowing-only.)

- [ ] **Step 2: Run — verify it PASSES** (the invariant should already hold — if it FAILS, the fallback floor path is wrong; STOP and report). Run: `cd v2 && uv run pytest tests/tools/agents/test_fallback_no_escalation.py -v`

- [ ] **Step 3:** (no impl — the property must hold by construction; if red, fix the ladder's floor reuse, not the test.)

- [ ] **Step 5: Commit**

```bash
git add v2/tests/tools/agents/test_fallback_no_escalation.py
git commit -m "test(v2): fallback never escalates — floor-subset property test (delegation-healing T8)"
```

---

### Task 9: Safety-net — surface swallowed delegation failures

**Files:** Modify `pipeline/critical_failure.py`. Test: `tests/pipeline/test_critical_failure_delegation.py` (Create).

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_critical_failure_delegation.py
import json

from stackowl.pipeline.critical_failure import _delegation_failed_with_no_answer
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.state import ToolCall  # confirm where tool results live on state


def _state_with_delegate(status: str, response: str = ""):
    rec = json.dumps({"note": "n", "record": {"status": status, "to_owl": "scout"}})
    # construct a PipelineState whose tool results contain the delegate record + given response
    ... # mirror how execute records ToolResults on state (read state.py / execute.py)


def test_failing_delegation_no_answer_trips():
    assert _delegation_failed_with_no_answer(_state_with_delegate("child_error", response="")) is True


def test_recovered_delegation_does_not_trip():
    assert _delegation_failed_with_no_answer(_state_with_delegate("recovered_via_secretary", response="")) is False


def test_nonempty_answer_does_not_trip():
    assert _delegation_failed_with_no_answer(_state_with_delegate("child_error", response="here you go")) is False
```

**Implementer:** read `critical_failure.py` + `state.py`/`execute.py` to find exactly where tool `ToolResult`s are recorded on the state (the recon shows `_has_usable_response` checks `state.responses`); build the test state to match the real shape. Adjust the helper accordingly.

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement** — add `_delegation_failed_with_no_answer(state)`: returns True iff there is NO usable response (`not _has_usable_response(state)`) AND a delegate-tool record exists in the turn with `status ∈ {timeout, child_error, empty, cycle, target_not_found}` (the FAILING set — exclude `ok`/`recovered_via_secretary`/`truncated-with-content`). Parse the tool result JSON defensively (try/except → treat as non-delegation). Wire it into `detect_critical_failure`:
```python
def detect_critical_failure(state: PipelineState) -> bool:
    if _has_usable_response(state):
        return False
    return bool(_critical_failure_classes(state)) or _delegation_failed_with_no_answer(state)
```
The surfaced message reuses the existing localized apology path in `surface_critical_failure` (multilingual; outcome + traceId only — no owl names / bounds / child detail; those stay in logs). No new call site.

- [ ] **Step 4: Run — verify PASS** + regression `cd v2 && uv run pytest tests/pipeline/ -q` (existing critical_failure tests green).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/critical_failure.py v2/tests/pipeline/test_critical_failure_delegation.py
git commit -m "feat(v2): safety-net surfaces swallowed delegation failures (delegation-healing T9)"
```

---

### Task 10: Gateway/smoke journeys

**Files:** Create `tests/smoke/test_delegation_self_healing_smoke.py`. Mirror `tests/smoke/test_e8_s1_delegate_task_telegram_smoke.py` (real Telegram→pipeline→A2ADelegator→child round-trip; mock ONLY the AI provider).

- [ ] **Step 1: Write the journeys**

Four journeys (adapt to the real harness):
- **(A) child raises → honest visible message:** scripted child provider raises/returns errors → the parent's final Telegram message is **non-empty + honest** (not silent), driven by the model-status OR the safety-net.
- **(B) fallback recovery + attribution:** specialist fails (timeout/child_error), the Secretary (same floor) succeeds → user gets the answer WITH the attributed lead-in (`recovered_via_secretary`).
- **(C) no-escalation:** a NARROW specialist whose delegated task needs an out-of-floor tool (e.g. `shell`) fails; fallback-to-secretary runs clamped to the narrow floor and the Secretary is **denied `shell` at its dispatch seam** → honest failure (the Secretary did NOT gain the tool). Proves presentation/authz separation end-to-end.
- **(D) cycle → honest, no hang:** seed a delegation_chain so a target is already present → `cycle` status, honest message, no loop/hang.

```python
# tests/smoke/test_delegation_self_healing_smoke.py — skeleton; mirror test_e8_s1 harness
import pytest

# Reuse the e8_s1 harness (_ScriptedProvider, _turn, real A2ADelegator/queue/governor, OwlRegistry.with_default_secretary()).


@pytest.mark.asyncio
async def test_child_failure_surfaces_honestly(delegation_harness):
    ...  # child errors → final user message non-empty + honest


@pytest.mark.asyncio
async def test_fallback_to_secretary_recovers_with_attribution(delegation_harness):
    ...  # specialist fails, secretary answers, lead-in present


@pytest.mark.asyncio
async def test_narrow_fallback_cannot_escalate(delegation_harness):
    ...  # narrow owl, task needs shell, fallback secretary denied shell → honest fail


@pytest.mark.asyncio
async def test_cycle_surfaces_without_hang(delegation_harness):
    ...  # chain seeded → cycle status, honest, returns promptly
```

**Implementer:** build on the e8_s1 harness; mock ONLY the AI provider. Journey C is load-bearing — it must exercise the REAL dispatch bounds seam (the Secretary fallback denied the out-of-floor tool), proving fallback can't escalate end-to-end. If a journey can't pass without new production code, STOP and report (don't silently patch or weaken).

- [ ] **Step 2: Run — verify FAIL (right reason).**
- [ ] **Step 3: Wire minimal harness (existing components only).**
- [ ] **Step 4: Run — verify PASS.**
- [ ] **Step 5: Commit**

```bash
git add v2/tests/smoke/test_delegation_self_healing_smoke.py
git commit -m "test(v2): delegation self-healing journeys — honest/recover/no-escalate/cycle (delegation-healing T10)"
```

---

## Final verification

- [ ] `cd v2 && uv run pytest tests/tools/agents/ tests/owls/test_a2a_fidelity.py tests/owls/test_registry_secretary_name.py tests/messaging/ tests/infra/test_trace_delegation_chain.py tests/pipeline/test_critical_failure_delegation.py tests/smoke/test_delegation_self_healing_smoke.py -v`
- [ ] `cd v2 && uv run ruff check src/ && uv run mypy src/stackowl/tools/agents/ src/stackowl/owls/a2a_delegation.py src/stackowl/messaging/a2a.py src/stackowl/infra/trace.py src/stackowl/pipeline/critical_failure.py`
- [ ] Regression: `cd v2 && uv run pytest tests/tools tests/owls tests/pipeline tests/messaging tests/infra tests/smoke -q`
- [ ] Final reviewer → merge to main + push (standing prefs).

---

## Spec coverage self-check

| Spec element | Task |
|---|---|
| `delegation_chain` threaded (TraceContext/PipelineState/backends) | T1 |
| `A2AResult` + status enum; `A2AMessage` status/error | T2 |
| `delegate()` governor-decided status (kills `""`-swallow) + chain stamp | T3 |
| target-missing distinct from wrong-target | T4 |
| cycle refusal (pre-slot, resolved identity) + depth backstop kept | T5 |
| new statuses + prose-first model notes + schema guidance | T5 |
| secretary accessor (no hardcoded name) + attempt budget | T6 |
| bounded ladder (retry-once → fallback, same slot, same floor, ≤3, no depth incr) | T7 |
| fallback-with-delegation-disabled (reused depth>0 exclusion) | T7 (relies on existing) + T10-C |
| no-escalation invariant (floor subset) | T8 (property) + T10-C (e2e) |
| attribution (recovered_via_secretary lead-in) | T7 |
| safety-net surfacing (terminal-only, i18n, no leak) | T9 |
| global per-turn attempt budget | T6 + T7 |
| journeys (honest / recover / no-escalate / cycle) | T10 |
| DEFERRED: LLM relevance-judge, retry-idempotency, durable children, cross-bounds escalation | not in plan ✓ |
