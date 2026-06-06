# Budget Governor (E2-S4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the consumption caps of `BoundsSpec.caps` (cost best-effort, steps + time durable) at one execute-step site, stopping a runaway agentic run deterministically with a partial result + breach note (interactive turns get an in-memory raise/stop choice).

**Architecture:** A `BudgetGovernor` (built per drive from the acting owl's effective caps + the `CostTracker`) is checked once per ReAct iteration via the **existing** `on_iteration_complete` callback — no provider signatures change. A `BudgetBreach` raised inside that callback propagates through the providers' direct `await` and breaks the loop; `execute` catches it and delivers the partial result + a structured note. No human → deterministic stop; human present → an in-memory clarify raise/stop (fail-closed on timeout).

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, pytest (`uv run pytest`), ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-06-05-budget-governor-design.md`

**Run tests from `v2/`. NO `pytest-timeout` plugin — never `--timeout`. Targeted paths only.** Stage `v2/` only. Commit footer:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**Verified seams (recon):**
- `IterationCallback = Callable[[ReActIterationState], Awaitable[None]]` (`providers/react_callback.py`); `ReActIterationState(iteration:int, messages:list[dict], tool_call_records:list[dict])`.
- All tool-loop providers (`anthropic_provider.py`, `openai_provider.py`) call `await on_iteration_complete(...)` directly (no fire-and-forget) — a raise propagates + breaks the loop. (Gemini/Mock use the base ABC, no tool loop.)
- `execute._run_with_tools`: `_call_default` passes NO `on_iteration_complete`; `_call_durable` passes `on_iteration_complete=cb` (the checkpoint cb from `make_checkpoint_callback(ctx, store)`). The `try: ... await _call_default()/_call_durable() ... except DurableReplayUncertain ... except Exception` block (~execute.py:446) is where `except BudgetBreach` goes.
- `CostTracker.turn_cost_usd(trace_id) -> float` (sync; `0.0` for unknown) via `get_services().cost_tracker` (may be None).
- `ClarifyGateway.ask(session_id, channel, question, *, choices, blocking) -> clarify_id`; then `wait_for_answer(clarify_id, timeout) -> (answer, outcome)` — mirror `interaction/cost_pause.py`. In `StepServices.clarify_gateway`.
- `compute_effective_bounds(state, owl_registry) -> BoundsSpec | None`; `.caps` is a `ResourceCaps` (never None object; fields `max_cost_usd`/`max_time_s`/`max_steps`/`max_concurrency` may be None).

---

## File Structure

**Create:**
- `src/stackowl/pipeline/budget/__init__.py` — exports `BudgetGovernor`, `make_budget_callback`.
- `src/stackowl/pipeline/budget/governor.py` — `BudgetGovernor` (cost/steps/time, in-memory raise).
- `src/stackowl/pipeline/budget/callback.py` — `make_budget_callback` (the iteration gate closure).
- Tests: `tests/pipeline/budget/test_governor.py`, `tests/pipeline/budget/test_callback.py`, `tests/pipeline/steps/test_execute_budget.py`, `tests/providers/test_budget_breach_propagates.py`, `tests/journeys/test_budget_cap.py`.

**Modify:**
- `src/stackowl/exceptions.py` — add `BudgetBreach`.
- `src/stackowl/pipeline/steps/execute.py` — build governor + budget callback; wire on both paths; catch `BudgetBreach`.

---

## Task 1: `BudgetBreach` + `BudgetGovernor`

**Files:**
- Modify: `src/stackowl/exceptions.py`
- Create: `src/stackowl/pipeline/budget/__init__.py`, `src/stackowl/pipeline/budget/governor.py`
- Test: `tests/pipeline/budget/test_governor.py`

- [ ] **Step 1: Add the exception** — in `src/stackowl/exceptions.py`, add (near the other domain errors; confirm the base class used by siblings — if there's a `StackOwlError`/`DomainError`, subclass the most appropriate; a budget breach is a control-flow signal, so a plain `Exception` subclass is fine):

```python
class BudgetBreach(Exception):
    """Raised (via on_iteration_complete) when a run exceeds a resource cap (E2-S4).

    Carries the breached cap + the partial work so the execute step can deliver a
    partial result + a breach note. A control-flow signal, not an error.
    """

    def __init__(
        self,
        cap: str,
        limit: float,
        actual: float,
        *,
        partial_text: str = "",
        tool_call_records: list[dict[str, object]] | None = None,
    ) -> None:
        self.cap = cap
        self.limit = limit
        self.actual = actual
        self.partial_text = partial_text
        self.tool_call_records = tool_call_records or []
        super().__init__(f"budget cap reached: {cap} limit={limit} actual={actual}")
```

- [ ] **Step 2: Write the failing governor tests** — `tests/pipeline/budget/test_governor.py`:

```python
"""E2-S4 — BudgetGovernor: cost(best-effort)/steps/time ceilings; in-memory raise."""

from __future__ import annotations

from stackowl.authz.bounds import ResourceCaps
from stackowl.pipeline.budget.governor import BudgetGovernor


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def monotonic(self) -> float:
        return self.t


class _CostStub:
    def __init__(self, usd: float) -> None:
        self.usd = usd

    def turn_cost_usd(self, trace_id: str) -> float:
        return self.usd


def _gov(caps: ResourceCaps, *, cost: float = 0.0, clock: _Clock | None = None) -> BudgetGovernor:
    return BudgetGovernor(
        caps, cost_tracker=_CostStub(cost), trace_id="t",
        started_monotonic=0.0, clock=clock or _Clock(),
    )


def test_steps_trips_at_limit_not_before() -> None:
    g = _gov(ResourceCaps(max_steps=2))
    assert g.check(0) is None          # 1 step done
    breach = g.check(1)                # 2 steps done → trip
    assert breach is not None and breach.cap == "steps" and breach.limit == 2


def test_time_trips_on_elapsed() -> None:
    clock = _Clock(0.0)
    g = _gov(ResourceCaps(max_time_s=10.0), clock=clock)
    assert g.check(0) is None
    clock.t = 11.0
    breach = g.check(1)
    assert breach is not None and breach.cap == "time"


def test_cost_trips_when_priced() -> None:
    g = _gov(ResourceCaps(max_cost_usd=1.0), cost=1.5)
    breach = g.check(0)
    assert breach is not None and breach.cap == "cost" and breach.actual == 1.5


def test_zero_cost_never_trips_and_never_disables_steps() -> None:
    # local/unpriced model: cost 0 → cost cap inert, but steps STILL enforce.
    g = _gov(ResourceCaps(max_cost_usd=1.0, max_steps=1), cost=0.0)
    breach = g.check(0)               # 1 step done, cost 0
    assert breach is not None and breach.cap == "steps"   # steps, not cost


def test_all_none_caps_never_trips() -> None:
    g = _gov(ResourceCaps())
    assert g.check(0) is None
    assert g.check(99) is None


def test_first_set_cap_precedence() -> None:
    # steps checked before time before cost (document the order); steps wins here.
    g = _gov(ResourceCaps(max_steps=1, max_time_s=0.0), clock=_Clock(100.0))
    breach = g.check(0)
    assert breach is not None and breach.cap == "steps"


def test_raise_caps_lifts_the_breached_cap() -> None:
    g = _gov(ResourceCaps(max_steps=1))
    assert g.check(0) is not None      # tripped at 1
    g.raise_caps("steps")              # in-memory bump
    assert g.check(1) is None          # now 2 steps allowed → ok
```

- [ ] **Step 3: Run → FAIL** — `uv run pytest tests/pipeline/budget/test_governor.py -v` (module absent).

- [ ] **Step 4: Implement `governor.py`**

```python
"""BudgetGovernor — per-run consumption ceiling for cost/steps/time (E2-S4).

A deterministic ceiling checked once per ReAct iteration. Steps + time are exact;
cost is BEST-EFFORT (depends on provider pricing; 0 on local/unpriced models;
per run-attempt — the in-memory cost ledger resets on resume). A missing/zero
cost signal NEVER disables steps/time. All-None caps → a no-op governor.

Mutable in-memory limits support the interactive raise (raise_caps); the raise is
scoped to this drive and never persisted (durable raise is E2-S5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from stackowl.exceptions import BudgetBreach
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.authz.bounds import ResourceCaps


class _Clock(Protocol):
    def monotonic(self) -> float: ...


class _CostSource(Protocol):
    def turn_cost_usd(self, trace_id: str) -> float: ...


class BudgetGovernor:
    """Checks cost/steps/time against the acting owl's effective caps."""

    def __init__(
        self,
        caps: "ResourceCaps",
        *,
        cost_tracker: _CostSource | None,
        trace_id: str,
        started_monotonic: float,
        clock: _Clock,
    ) -> None:
        # Mutable copies so the interactive raise can lift them in-memory.
        self._max_steps = caps.max_steps
        self._max_time_s = caps.max_time_s
        self._max_cost_usd = caps.max_cost_usd
        self._cost = cost_tracker
        self._trace_id = trace_id
        self._t0 = started_monotonic
        self._clock = clock

    def check(self, iteration: int) -> BudgetBreach | None:
        """Return a BudgetBreach for the FIRST set cap exceeded after this iteration.

        `iteration` is the just-completed 0-based ReAct index (from
        ReActIterationState.iteration) — steps_done = iteration + 1. Order:
        steps, then time, then cost (cost last because it is the weakest signal).
        """
        steps_done = iteration + 1
        if self._max_steps is not None and steps_done >= self._max_steps:
            return BudgetBreach("steps", float(self._max_steps), float(steps_done))
        if self._max_time_s is not None:
            elapsed = self._clock.monotonic() - self._t0
            if elapsed >= self._max_time_s:
                return BudgetBreach("time", self._max_time_s, elapsed)
        if self._max_cost_usd is not None and self._cost is not None:
            spent = self._cost.turn_cost_usd(self._trace_id)
            if spent >= self._max_cost_usd:
                return BudgetBreach("cost", self._max_cost_usd, spent)
        return None

    def raise_caps(self, cap: str) -> None:
        """In-memory raise of the breached cap (interactive Raise). Doubles the limit."""
        if cap == "steps" and self._max_steps is not None:
            self._max_steps *= 2
        elif cap == "time" and self._max_time_s is not None:
            self._max_time_s *= 2
        elif cap == "cost" and self._max_cost_usd is not None:
            self._max_cost_usd *= 2
        log.engine.info("[budget] governor.raise_caps: lifted", extra={"_fields": {"cap": cap}})
```

`src/stackowl/pipeline/budget/__init__.py`:
```python
"""Budget governor — enforce BoundsSpec.caps consumption ceilings (E2-S4)."""

from stackowl.pipeline.budget.governor import BudgetGovernor

__all__ = ["BudgetGovernor"]
```
(Add `make_budget_callback` to `__all__` in Task 2.)

- [ ] **Step 5: Run → PASS** — `uv run pytest tests/pipeline/budget/test_governor.py -v`. Then `uv run ruff check src/stackowl/pipeline/budget/ src/stackowl/exceptions.py` + `uv run mypy src/stackowl/pipeline/budget/governor.py` — clean.

- [ ] **Step 6: Commit**

```bash
git add v2/src/stackowl/exceptions.py v2/src/stackowl/pipeline/budget/ v2/tests/pipeline/budget/test_governor.py
git commit -m "feat(v2): BudgetGovernor + BudgetBreach — cost/steps/time ceilings (Epic2 S4)"
```

---

## Task 2: `make_budget_callback` — the iteration gate

The closure passed as `on_iteration_complete`: check the governor; on breach, either (interactive) clarify Raise/Stop, or raise `BudgetBreach`.

**Files:**
- Create: `src/stackowl/pipeline/budget/callback.py`; Modify: `src/stackowl/pipeline/budget/__init__.py`
- Test: `tests/pipeline/budget/test_callback.py`

- [ ] **Step 1: Write the failing tests** — `tests/pipeline/budget/test_callback.py`:

```python
"""E2-S4 — make_budget_callback: breach → raise; interactive → clarify raise/stop."""

from __future__ import annotations

import pytest

from stackowl.exceptions import BudgetBreach
from stackowl.pipeline.budget.callback import make_budget_callback
from stackowl.providers.react_callback import ReActIterationState


class _GovStub:
    def __init__(self, breach: BudgetBreach | None) -> None:
        self._breach = breach
        self.raised: list[str] = []

    def check(self, iteration: int) -> BudgetBreach | None:
        return self._breach

    def raise_caps(self, cap: str) -> None:
        self.raised.append(cap)
        self._breach = None   # after a raise, the next check passes


class _Clarify:
    def __init__(self, answer: str | None) -> None:
        self._answer = answer

    async def ask(self, session_id, channel, question, *, choices=(), blocking=False):  # noqa: ANN001
        return "cid"

    async def wait_for_answer(self, clarify_id, timeout):  # noqa: ANN001
        return (self._answer, None)


_ITER = ReActIterationState(iteration=1, messages=[{"role": "assistant", "content": "partial"}],
                            tool_call_records=[{"name": "x"}])


async def test_no_breach_is_passthrough() -> None:
    cb = make_budget_callback(_GovStub(None), interactive=False, clarify=None,
                              session_id="s", channel="cli")
    await cb(_ITER)   # no raise


async def test_non_interactive_breach_raises() -> None:
    breach = BudgetBreach("steps", 2, 2)
    cb = make_budget_callback(_GovStub(breach), interactive=False, clarify=None,
                              session_id="s", channel="cli")
    with pytest.raises(BudgetBreach) as ei:
        await cb(_ITER)
    assert ei.value.cap == "steps"
    assert ei.value.partial_text == "partial"          # partial carried from iter_state
    assert ei.value.tool_call_records == [{"name": "x"}]


async def test_interactive_raise_continues() -> None:
    gov = _GovStub(BudgetBreach("steps", 2, 2))
    cb = make_budget_callback(gov, interactive=True, clarify=_Clarify("Raise"),
                              session_id="s", channel="cli")
    await cb(_ITER)            # Raise → no exception
    assert gov.raised == ["steps"]


async def test_interactive_stop_raises() -> None:
    cb = make_budget_callback(_GovStub(BudgetBreach("steps", 2, 2)), interactive=True,
                              clarify=_Clarify("Stop"), session_id="s", channel="cli")
    with pytest.raises(BudgetBreach):
        await cb(_ITER)


async def test_interactive_timeout_fails_closed() -> None:
    # no answer → STOP (fail-closed for a cost control).
    cb = make_budget_callback(_GovStub(BudgetBreach("cost", 1, 2)), interactive=True,
                              clarify=_Clarify(None), session_id="s", channel="cli")
    with pytest.raises(BudgetBreach):
        await cb(_ITER)
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `callback.py`**

```python
"""make_budget_callback — the per-iteration budget gate (E2-S4).

Returned as on_iteration_complete. On a governor breach: a present human gets an
in-memory clarify Raise/Stop (fail-closed: Stop / timeout / no-gateway → raise);
otherwise it raises BudgetBreach immediately. The exception carries the partial
work (last assistant text + tool calls) so execute can deliver a partial result.
Clarify lives HERE (execute layer), never on the provider stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

from stackowl.exceptions import BudgetBreach
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.providers.react_callback import ReActIterationState

_RAISE = "Raise"
_STOP = "Stop"
_WAIT_TIMEOUT_S = 120.0


def _last_assistant_text(messages: list[dict[str, object]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            return str(m["content"])
    return ""


def make_budget_callback(
    governor,  # BudgetGovernor (duck-typed for testability)
    *,
    interactive: bool,
    clarify,  # ClarifyGateway | None
    session_id: str,
    channel: str,
    wait_timeout_s: float = _WAIT_TIMEOUT_S,
) -> "Callable[[ReActIterationState], Awaitable[None]]":
    async def _gate(iter_state: "ReActIterationState") -> None:
        breach = governor.check(iter_state.iteration)
        if breach is None:
            return
        # A present human may raise the cap in-memory; else fail-closed stop.
        if interactive and clarify is not None:
            try:
                cid = await clarify.ask(
                    session_id, channel,
                    f"Budget cap '{breach.cap}' reached (limit {breach.limit}, used "
                    f"{breach.actual}). Raise or Stop?",
                    choices=(_RAISE, _STOP), blocking=True,
                )
                answer, _ = await clarify.wait_for_answer(cid, timeout=wait_timeout_s)
            except Exception as exc:  # noqa: BLE001 — fail-closed STOP on any clarify error
                log.engine.warning("[budget] gate: clarify failed — stopping", exc_info=exc)
                answer = None
            if answer is not None and answer.strip().casefold() == _RAISE.casefold():
                governor.raise_caps(breach.cap)
                log.engine.info("[budget] gate: human raised cap — continuing",
                                extra={"_fields": {"cap": breach.cap}})
                return
        # No human / Stop / timeout → deterministic stop. Carry the partial.
        log.engine.warning(
            "[budget] gate: cap reached — stopping",
            extra={"_fields": {"cap": breach.cap, "limit": breach.limit, "actual": breach.actual}},
        )
        raise BudgetBreach(
            breach.cap, breach.limit, breach.actual,
            partial_text=_last_assistant_text(iter_state.messages),
            tool_call_records=list(iter_state.tool_call_records),
        )

    return _gate
```

Update `__init__.py` to also export `make_budget_callback` (`from stackowl.pipeline.budget.callback import make_budget_callback`; add to `__all__`).

- [ ] **Step 4: Run → PASS** — `uv run pytest tests/pipeline/budget/ -v`. ruff + mypy clean on `src/stackowl/pipeline/budget/`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/budget/callback.py v2/src/stackowl/pipeline/budget/__init__.py v2/tests/pipeline/budget/test_callback.py
git commit -m "feat(v2): budget gate callback — breach→stop, interactive raise/stop (Epic2 S4)"
```

---

## Task 3: Wire into `execute` + catch `BudgetBreach`

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py`
- Test: `tests/pipeline/steps/test_execute_budget.py`

- [ ] **Step 1: Write the failing integration test** — `tests/pipeline/steps/test_execute_budget.py`. Mirror `tests/pipeline/steps/test_execute_drift_telemetry.py` / `tests/authz/test_bounds_dispatch.py` `_drive` harness (real `_run_with_tools`, owl registry with bounded owl, a scripted provider). The owl's bounds carry `caps`; the scripted provider's `complete_with_tools` must actually invoke `on_iteration_complete` per iteration (mirror how the real providers do — call it with a `ReActIterationState(iteration=i, ...)` each loop, and break on a raise). Assert: a non-interactive turn whose owl has `caps.max_steps=2` STOPS after 2 iterations, the returned state carries the partial + a `budget:` marker in `errors`, and the run did not hang/crash.

```python
async def test_non_interactive_step_cap_stops_with_partial() -> None:
    caps = ResourceCaps(max_steps=2)
    owl_bounds = BoundsSpec(tools=frozenset({"loop_tool"}), caps=caps)
    state, _tool = await _drive_capped(owl_bounds, interactive=False, iterations=5)
    # the loop stopped at 2; state carries a budget marker + partial responses
    assert any("budget" in e for e in state.errors)
    assert state.responses  # partial delivered
```

> Fill `_drive_capped` against the real harness: build `StepServices(owl_registry=<bounded owl>, tool_registry=<1 tool>, cost_tracker=None, clarify_gateway=None)`, a scripted provider whose `complete_with_tools` loops up to `iterations`, calling `await on_iteration_complete(ReActIterationState(iteration=i, messages=[{"role":"assistant","content":f"step{i}"}], tool_call_records=[]))` each round and returning on a `BudgetBreach`. Drive `_run_with_tools(state, provider, registry)` and return the final state. Add an interactive variant later if the harness supports a clarify gateway double.

Run → EXPECT FAIL (governor not wired).

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Build the governor + budget callback in `_run_with_tools`**

In `src/stackowl/pipeline/steps/execute.py::_run_with_tools`, after the tool schemas are built and BEFORE the `_call_default`/`_call_durable` definitions, add (read the surrounding code to place it cleanly; `get_services`, `compute_effective_bounds`, `time` are already imported):

```python
    # E2-S4 — budget governor: enforce the acting owl's effective caps (cost best-
    # effort, steps + time) once per ReAct iteration via on_iteration_complete.
    # No caps / unbounded owl → a no-op gate (every current turn unchanged).
    from stackowl.authz.bounds import ResourceCaps
    from stackowl.pipeline.authz_compose import compute_effective_bounds
    from stackowl.pipeline.budget import BudgetGovernor, make_budget_callback

    _services = get_services()
    try:
        _eff = compute_effective_bounds(state, _services.owl_registry)
    except Exception:  # noqa: BLE001 — budget is best-effort; never block the turn on bounds
        _eff = None
    _caps = _eff.caps if _eff is not None else ResourceCaps()

    class _MonotonicClock:
        def monotonic(self) -> float:
            return time.monotonic()

    _governor = BudgetGovernor(
        _caps, cost_tracker=_services.cost_tracker, trace_id=state.trace_id,
        started_monotonic=time.monotonic(), clock=_MonotonicClock(),
    )
    _budget_cb = make_budget_callback(
        _governor, interactive=state.interactive, clarify=_services.clarify_gateway,
        session_id=state.session_id, channel=state.channel,
    )
```

- [ ] **Step 4: Pass the callback on BOTH paths**

In `_call_default`, add `on_iteration_complete=_budget_cb` to BOTH `complete_with_tools(...)` calls (the `persistence_check` branch and the plain branch).

> **Regression heads-up:** `_call_default` now passes `on_iteration_complete` where it didn't before. Any test/scripted provider fake whose `complete_with_tools` does NOT accept that kwarg will break. Most fakes use `**_kwargs` (S3 added it to `_TwoToolProvider`); if Step 6's regression run shows a fake erroring on an unexpected `on_iteration_complete`, add `**_kwargs: object` (or an `on_iteration_complete=None` param) to that fake — do NOT change production to dodge it.

In `_call_durable`, the checkpoint cb `cb` is currently passed as `on_iteration_complete=cb`. Compose budget BEFORE checkpoint so the completed iteration is checkpointed, THEN budget-checked — actually: budget must be able to stop AFTER the work is checkpointed (so durable resume sees the completed iteration). Define a chained callback right after `cb = make_checkpoint_callback(...)`:

```python
        async def _cb_with_budget(s: ReActIterationState) -> None:  # noqa: ANN001
            await cb(s)            # checkpoint the completed iteration first (durable)
            await _budget_cb(s)    # then budget-check (may raise BudgetBreach)
```
and pass `on_iteration_complete=_cb_with_budget` in both `_call_durable` branches instead of `cb`. Add `from stackowl.providers.react_callback import ReActIterationState` to the imports (or under TYPE_CHECKING + a string annotation).

- [ ] **Step 5: Catch `BudgetBreach` + deliver the partial**

In the `try: ... await _call_default()/_call_durable() ...` block, add a handler BETWEEN `except DurableReplayUncertain` and `except Exception`:

```python
    except BudgetBreach as exc:
        # E2-S4 — a hard cap stopped the run. Deliver the partial work + a clear
        # note (NOT an error/crash). Mirrors the normal-exit response/tool_call build.
        log.engine.info(
            "[pipeline] execute: budget cap reached — stopping with partial",
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name,
                               "cap": exc.cap, "limit": exc.limit, "actual": exc.actual}},
        )
        note = f"\n\n[stopped: budget cap '{exc.cap}' reached (limit {exc.limit}, used {exc.actual})]"
        chunks = ()
        if exc.partial_text or note:
            chunks = (ResponseChunk(
                content=(exc.partial_text + note), is_final=False, chunk_index=0,
                trace_id=state.trace_id, owl_name=state.owl_name,
            ),)
        tool_records = tuple(
            ToolCall(tool_name=str(rc.get("name", "")), args=dict(rc.get("args") or {}),
                     result=str(rc.get("result", "")), error=None, duration_ms=0.0)
            for rc in exc.tool_call_records
        )
        marker = f"budget:stop:{exc.cap}:limit={exc.limit}:actual={exc.actual}"
        return state.evolve(
            responses=(*state.responses, *chunks),
            tool_calls=(*state.tool_calls, *tool_records),
            errors=(*state.errors, marker),
        )
```
Add `from stackowl.exceptions import BudgetBreach` to the imports (the `exceptions` import block already exists). Confirm `ResponseChunk` + `ToolCall` are imported (they are — used in the normal exit).

- [ ] **Step 6: Run → PASS + regression**

Run: `uv run pytest tests/pipeline/steps/test_execute_budget.py tests/authz/test_bounds_dispatch.py tests/pipeline/steps/test_execute_drift_telemetry.py tests/pipeline/durable/ -v`
Expected: PASS (the new budget test + all S2/S3/durable tests unchanged — `on_iteration_complete` is now also set on the default path but is a no-op when caps are all-None).
`uv run ruff check src/stackowl/pipeline/steps/execute.py` + `uv run mypy src/stackowl/pipeline/steps/execute.py` — no NEW errors.

- [ ] **Step 7: Commit**

```bash
git add v2/src/stackowl/pipeline/steps/execute.py v2/tests/pipeline/steps/test_execute_budget.py
git commit -m "feat(v2): wire budget governor at the iteration seam + deliver partial on breach (Epic2 S4)"
```

---

## Task 4: Provider propagation regression tests

Guard Amelia's fire-and-forget hazard: a `BudgetBreach` raised from `on_iteration_complete` must break the loop in the real tool-loop providers.

**Files:**
- Test: `tests/providers/test_budget_breach_propagates.py`

- [ ] **Step 1: Write the tests** — drive the REAL `complete_with_tools` of each tool-loop provider with a callback that raises `BudgetBreach`, asserting it propagates (the loop doesn't swallow it). READ a sibling provider test (`grep -rl complete_with_tools tests/providers`) for how to construct a provider with a fake HTTP/transport backend that returns a tool-using response, so the loop reaches `on_iteration_complete`.

```python
"""E2-S4 — a BudgetBreach from on_iteration_complete breaks the provider tool loop."""

from __future__ import annotations

import pytest

from stackowl.exceptions import BudgetBreach
from stackowl.providers.react_callback import ReActIterationState


async def _raise_cb(state: ReActIterationState) -> None:
    raise BudgetBreach("steps", 1, 1)


# One test per tool-loop provider (anthropic, openai). Build the provider with the
# sibling test's fake backend so the loop performs >=1 iteration and invokes the
# callback; assert BudgetBreach propagates out of complete_with_tools.
async def test_anthropic_propagates_budget_breach(...) -> None:   # noqa: ANN001
    provider = _anthropic_with_tool_response(...)
    with pytest.raises(BudgetBreach):
        await provider.complete_with_tools(
            user_text="x", system_text="", tool_schemas=[...],
            tool_dispatcher=_dispatcher, history=[], on_iteration_complete=_raise_cb,
        )


async def test_openai_propagates_budget_breach(...) -> None:   # noqa: ANN001
    ...
```

> Fill the provider construction + fake backend from the real provider tests. If those tests already have a helper that runs one tool round, reuse it and just pass `on_iteration_complete=_raise_cb`. If a provider's tool-loop fake is too heavy to assemble, at minimum assert via a focused unit that the loop `await`s the callback (read the loop and assert structurally) — but prefer the real propagation test.

- [ ] **Step 2: Run → confirm propagation** — `uv run pytest tests/providers/test_budget_breach_propagates.py -v`. (If RED because the breach is swallowed in a provider, that is a real bug — fix that provider to `await` the callback directly, then re-run.)

- [ ] **Step 3: Commit**

```bash
git add v2/tests/providers/test_budget_breach_propagates.py
git commit -m "test(v2): BudgetBreach propagates through the provider tool loops (Epic2 S4)"
```

---

## Task 5: Gateway journeys

**Files:**
- Test: `tests/journeys/test_budget_cap.py`

- [ ] **Step 1: Write the journeys** — mirror `tests/journeys/test_tool_scope_envelope.py` (real adapter→scanner→AsyncioBackend; scripted owl as the only mock). The scripted owl must run a multi-iteration loop that invokes `on_iteration_complete` each round (mirror the real provider contract). Register a bounded owl with `caps.max_steps=2`.

```python
async def test_durable_step_cap_stops_deterministically(caplog) -> None:  # noqa: ANN001
    # Non-interactive durable task, owl caps.max_steps=2 → run stops at step 2,
    # delivers partial + budget note, finalizes (no hang/crash).
    ...
    assert "budget cap" in reply.lower() or any("budget" in r.message.lower() for r in caplog.records)


async def test_interactive_step_cap_raise_continues() -> None:
    # Interactive turn, caps.max_steps=2; clarify answers "Raise" → run continues
    # past step 2 and delivers the full reply.
    ...
```

> Build against the real journey scaffold + a clarify-gateway double that returns "Raise"/"Stop". The two load-bearing assertions: the non-interactive run STOPS at the cap with a partial + note (deterministic, no human), and the interactive Raise continues. Do not weaken assertions.

- [ ] **Step 2: Run the journeys** — `uv run pytest tests/journeys/test_budget_cap.py -v`.

- [ ] **Step 3: Full S4 regression + lint/type sweep**

```bash
uv run pytest tests/pipeline/budget/ tests/pipeline/steps/test_execute_budget.py tests/providers/test_budget_breach_propagates.py tests/journeys/test_budget_cap.py tests/authz/ tests/pipeline/durable/ -v
uv run ruff check src/stackowl/pipeline/budget src/stackowl/pipeline/steps/execute.py src/stackowl/exceptions.py
uv run mypy src/stackowl/pipeline/budget src/stackowl/exceptions.py
```
All green. Fix any finding before committing.

- [ ] **Step 4: Commit**

```bash
git add v2/tests/journeys/test_budget_cap.py
git commit -m "test(v2): gateway journeys — budget cap deterministic stop + interactive raise (Epic2 S4)"
```

---

## Definition of Done

- [ ] `BudgetGovernor` enforces steps (exact) + time (wall-clock) + cost (best-effort, None/zero-safe, never disables steps/time); all-None caps → no-op.
- [ ] Checked once per iteration via the EXISTING `on_iteration_complete` (no provider signatures changed); wired on both `_call_default` and `_call_durable` (durable composes after checkpoint).
- [ ] Non-interactive breach → deterministic STOP; interactive → clarify Raise (in-memory bump, continue) / Stop / timeout → fail-closed STOP.
- [ ] On breach the partial result + a `budget:stop:<cap>` note are delivered; never a crash/hang.
- [ ] `BudgetBreach` propagates through the real provider tool loops (regression test).
- [ ] Every current turn unchanged when caps are all-None (S2/S3/durable suites green).
- [ ] ruff + mypy clean on touched modules; each task committed separately.

## Out of scope (spec §7)

Durable budget negotiation (park + persisted per-resume raise + migration) → **E2-S5**; durable cost (`spent_usd_to_date`); pre-spend cost reservation; `max_concurrency` (as `min()` at the concurrency seam); caps min-composition.
