# Tool-Scope Envelope + Resume-Monotonicity (E2-S2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread a task-scoped capability envelope through the pipeline so effective tool bounds are `owl.bounds(now) ∩ creation_ceiling ∩ task_envelope`, enforced at the single dispatch seam, persisted across durable kill/resume, and propagated as a floor to delegated children.

**Architecture:** A three-way narrowing intersection. `owl.bounds(now)` is the live owl manifest bounds (S1). `creation_ceiling` is a snapshot of the owl's bounds taken at durable-task creation and persisted — a resume-monotonicity ratchet (TOCTOU guard). `task_envelope` is an always-`None` slot in S2 that E2-S3's preflight planner will fill. Pure narrowing logic lives in `authz/` (no `services` import); the registry-reading composition lives in the pipeline layer. Fail-closed everywhere: a not-yet-enforced axis can't be silently narrowed, a bounded-owl computation error denies, and a missing ceiling falls back to owl-bounds (never global-unrestricted).

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen models), asyncio, SQLite (raw SQL migrations), pytest (`uv run pytest`), ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-06-04-authorization-envelope-design.md`

**Run tests from `v2/`. Use a timeout and targeted paths (never the unbounded full suite on this box):**
`uv run pytest <path> -v -x` (add `--timeout=60`).

**Commit discipline:** stage `v2/` only. Commit message footer:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## File Structure

**Create:**
- `src/stackowl/authz/enforcement.py` — `ENFORCED_AXES`, `unenforced_narrowing()`, `assert_task_narrowing_enforceable()` (pure).
- `src/stackowl/pipeline/authz_compose.py` — `resolve_owl_bounds()`, `compute_effective_bounds()` (reads the owl registry; pipeline layer).
- `src/stackowl/db/migrations/0048_tasks_creation_ceiling.sql` — additive nullable column.
- Tests: `tests/authz/test_bounds_roundtrip.py`, `tests/authz/test_effective_bounds.py`, `tests/authz/test_enforcement.py`, `tests/pipeline/test_pipeline_state_bounds.py`, `tests/pipeline/test_authz_compose.py`, `tests/durable/test_store_creation_ceiling.py`, `tests/durable/test_runner_ceiling_snapshot.py`, `tests/durable/test_recovery_ceiling.py`, `tests/pipeline/test_child_floor.py`, `tests/journeys/test_tool_scope_envelope.py`.

**Modify:**
- `src/stackowl/authz/bounds_guard.py` — add `effective_bounds()`, `check_effective_bounds()`; refactor `check_tool_bounds()` to a wrapper.
- `src/stackowl/pipeline/state.py` — add `creation_ceiling`, `task_envelope` fields.
- `src/stackowl/pipeline/steps/execute.py` — seam uses `compute_effective_bounds()` + fail-closed DENY.
- `src/stackowl/pipeline/durable/task.py` — add `creation_ceiling` field.
- `src/stackowl/pipeline/durable/store.py` — persist/read `creation_ceiling`.
- `src/stackowl/pipeline/durable/task_runner.py` — snapshot owl bounds at creation.
- `src/stackowl/pipeline/durable/recovery.py` — thread persisted ceiling into resumed state.
- `src/stackowl/tools/agents/delegate_task.py`, `sessions_spawn.py`, `sessions_send.py` — set child `creation_ceiling` = parent-owl floor.

---

## Task 1: BoundsSpec JSON round-trips cleanly (no schema change)

Proves serialization before anything depends on it. `None` (unrestricted) and `frozenset()` (deny-all) are opposite and must both survive distinctly.

**Files:**
- Test: `tests/authz/test_bounds_roundtrip.py`

- [ ] **Step 1: Write the failing tests**

```python
"""E2-S2 — BoundsSpec survives a JSON round-trip (SQLite persistence prereq).

frozenset/tuple axes round-trip by VALUE (order-insensitive); None (unrestricted)
and frozenset() (deny-all) are opposite and both must survive distinctly. Assert on
the MODEL, never the JSON string (frozenset dump order is non-deterministic).
"""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.authz.bounds import NetworkRule, ResourceCaps


def test_roundtrip_tools_and_axes_by_value() -> None:
    b = BoundsSpec(
        tools=frozenset({"read_file", "web_fetch"}),
        fs_read_roots=("/a", "/b"),
        network=(NetworkRule(host="example.com", port=443),),
        caps=ResourceCaps(max_steps=5),
    )
    assert BoundsSpec.model_validate_json(b.model_dump_json()) == b


def test_roundtrip_none_tools_is_unrestricted() -> None:
    b = BoundsSpec(tools=None)
    out = BoundsSpec.model_validate_json(b.model_dump_json())
    assert out.tools is None


def test_roundtrip_empty_allowlist_is_deny_all_not_none() -> None:
    b = BoundsSpec(tools=frozenset())
    out = BoundsSpec.model_validate_json(b.model_dump_json())
    assert out.tools == frozenset()
    assert out.tools is not None  # deny-all, NOT unrestricted


def test_equality_is_order_insensitive() -> None:
    a = BoundsSpec(tools=frozenset({"x", "y"}))
    b = BoundsSpec(tools=frozenset({"y", "x"}))
    assert a == b
```

- [ ] **Step 2: Run to verify (expect PASS — this characterizes existing behavior)**

Run: `uv run pytest tests/authz/test_bounds_roundtrip.py -v`
Expected: PASS. If `test_roundtrip_empty_allowlist_is_deny_all_not_none` FAILS (e.g. `frozenset()` coerces to `None` through JSON), STOP — that is a serialization bug the rest of the plan depends on; fix `BoundsSpec` serialization before continuing and report it.

- [ ] **Step 3: Commit**

```bash
git add v2/tests/authz/test_bounds_roundtrip.py
git commit -m "test(v2): pin BoundsSpec JSON round-trip (Epic2 S2 prereq)"
```

---

## Task 2: PipelineState carries the two envelope fields

**Files:**
- Modify: `src/stackowl/pipeline/state.py`
- Test: `tests/pipeline/test_pipeline_state_bounds.py`

- [ ] **Step 1: Write the failing tests**

```python
"""E2-S2 — PipelineState carries creation_ceiling + task_envelope across evolve()."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.pipeline.state import PipelineState


def _state(**kw: object) -> PipelineState:
    base = dict(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def test_fields_default_none() -> None:
    s = _state()
    assert s.creation_ceiling is None
    assert s.task_envelope is None


def test_evolve_carries_creation_ceiling_by_identity() -> None:
    b = BoundsSpec(tools=frozenset({"x"}))
    s = _state().evolve(creation_ceiling=b)
    # identity (is) confirms evolve() is model_copy, not dump/reload
    assert s.creation_ceiling is b


def test_evolve_unrelated_field_preserves_envelope() -> None:
    b = BoundsSpec(tools=frozenset({"x"}))
    s = _state(creation_ceiling=b).evolve(input_text="changed")
    assert s.creation_ceiling is b
    assert s.input_text == "changed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_pipeline_state_bounds.py -v`
Expected: FAIL — `creation_ceiling`/`task_envelope` are not valid fields.

- [ ] **Step 3: Add the fields**

In `src/stackowl/pipeline/state.py`, add an import at the top of the imports block:

```python
from stackowl.authz.bounds import BoundsSpec
```

Then insert these two fields immediately after the `task_id` field (after line 57, before `durable_owner_id`):

```python
    # E2-S2 — the task-scoped authorization envelope, a three-way narrowing:
    # effective = owl.bounds(now) ∩ creation_ceiling ∩ task_envelope.
    #
    # creation_ceiling — a snapshot of the owl's bounds taken at DURABLE task
    # creation, persisted on the task row. It narrows nothing on a normal run
    # (owl ∩ owl = owl); its sole effect is on RESUME after the owl's bounds were
    # widened mid-task, where owl.bounds(now) ∩ creation_ceiling clamps to the
    # narrower historical set (resume-monotonicity / TOCTOU ratchet). None for a
    # non-durable turn — no clamp. A missing ceiling is therefore NEVER
    # global-unrestricted, because owl.bounds(now) always remains a factor.
    creation_ceiling: BoundsSpec | None = None
    # task_envelope — the least-privilege-per-task slot. ALWAYS None in S2; the
    # E2-S3 preflight planner fills it with a goal-derived (tighter) spec. Carried
    # here now so S3 populates an existing field rather than re-threading.
    task_envelope: BoundsSpec | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/test_pipeline_state_bounds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/state.py v2/tests/pipeline/test_pipeline_state_bounds.py
git commit -m "feat(v2): PipelineState carries creation_ceiling + task_envelope (Epic2 S2)"
```

---

## Task 3: Pure narrowing combiner + enforcement-honesty guard (no seam wiring)

The riskiest unit, committed in isolation. `effective_bounds()` is total and narrowing; the single-arg fold MUST be identity so the back-compat wrapper never tightens an existing caller.

**Files:**
- Modify: `src/stackowl/authz/bounds_guard.py`
- Create: `src/stackowl/authz/enforcement.py`
- Test: `tests/authz/test_effective_bounds.py`, `tests/authz/test_enforcement.py`

- [ ] **Step 1: Write the failing combiner tests**

`tests/authz/test_effective_bounds.py`:

```python
"""E2-S2 — effective_bounds(): total, narrowing-only fold of N optional specs."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.authz.bounds_guard import check_effective_bounds, effective_bounds

A = BoundsSpec(tools=frozenset({"a"}))
AB = BoundsSpec(tools=frozenset({"a", "b"}))
B = BoundsSpec(tools=frozenset({"b"}))
UNRESTRICTED = BoundsSpec(tools=None)


def test_no_args_is_none() -> None:
    assert effective_bounds() is None


def test_all_none_is_none() -> None:
    assert effective_bounds(None, None) is None


def test_single_arg_is_identity() -> None:
    # CRITICAL: the back-compat wrapper relies on this — a single spec is unchanged.
    assert effective_bounds(AB) == AB


def test_none_skipped() -> None:
    assert effective_bounds(None, AB, None) == AB


def test_intersection_narrows() -> None:
    assert effective_bounds(AB, A).tools == frozenset({"a"})


def test_cannot_widen() -> None:
    # task names a tool the owl lacks → still excluded
    assert effective_bounds(A, B).tools == frozenset()


def test_disjoint_is_deny_all_not_union() -> None:
    eff = effective_bounds(A, B)
    assert eff.tools == frozenset()
    assert eff.tools is not None


def test_unrestricted_term_does_not_widen() -> None:
    assert effective_bounds(A, UNRESTRICTED).tools == frozenset({"a"})


@pytest.mark.parametrize(
    "eff,tool,permitted",
    [
        (None, "anything", True),
        (BoundsSpec(tools=frozenset({"a"})), "a", True),
        (BoundsSpec(tools=frozenset({"a"})), "b", False),
        (BoundsSpec(tools=frozenset()), "a", False),
    ],
)
def test_check_effective_bounds(eff: BoundsSpec | None, tool: str, permitted: bool) -> None:
    block = check_effective_bounds(eff, tool)
    assert (block is None) == permitted
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/authz/test_effective_bounds.py -v`
Expected: FAIL — `effective_bounds` / `check_effective_bounds` don't exist.

- [ ] **Step 3: Implement the combiner**

In `src/stackowl/authz/bounds_guard.py`, add these two functions ABOVE `check_tool_bounds` (after the imports). Note the module currently imports `OwlAgentManifest` only under `TYPE_CHECKING`; add `from stackowl.authz.bounds import BoundsSpec` to the top-level imports.

```python
def effective_bounds(*specs: BoundsSpec | None) -> BoundsSpec | None:
    """Fold N optional bounds specs into one, narrowing-only.

    None terms are skipped (an absent constraint never widens). With no defined
    term the result is None (genuinely unbounded). Otherwise the defined terms
    are intersected left-to-right via BoundsSpec.intersect (TOOLS axis composed
    for real; other axes keep self, per S1). Total + narrowing: every defined
    term can only tighten. A SINGLE defined term is returned unchanged (identity)
    — the back-compat wrapper depends on this.
    """
    acc: BoundsSpec | None = None
    for spec in specs:
        if spec is None:
            continue
        acc = spec if acc is None else acc.intersect(spec)
    return acc


def check_effective_bounds(effective: BoundsSpec | None, tool_name: str) -> str | None:
    """Return a block-reason if effective bounds forbid the tool, else None.

    None effective bounds (no constraint anywhere) → unrestricted → None.
    """
    if effective is None or effective.permits_tool(tool_name):
        return None
    return (
        f"The action '{tool_name}' is not permitted by this owl's bounds and was "
        "not run. This owl is restricted to a fixed set of tools; choose one of its "
        "permitted tools or answer the user directly."
    )
```

Now refactor the existing `check_tool_bounds` body so it delegates (keeping its exact public signature and return contract). Replace the decision/step/exit logic (current lines ~52–86) so the function becomes:

```python
    # 2. DECISION — no manifest or no bounds → unbounded (legacy behavior).
    if owl_manifest is None or owl_manifest.bounds is None:
        log.engine.debug(
            "[authz] bounds_guard.check: no bounds — unrestricted",
            extra={"_fields": {"tool": tool_name}},
        )
        return None
    # 3+4. Delegate to the shared combiner+checker. effective_bounds(single) is
    # identity, so this is byte-for-byte the prior owl-only verdict.
    block = check_effective_bounds(effective_bounds(owl_manifest.bounds), tool_name)
    if block is None:
        log.engine.debug(
            "[authz] bounds_guard.check: tool permitted by bounds",
            extra={"_fields": {"tool": tool_name, "owl": owl_manifest.name}},
        )
    else:
        log.engine.debug(
            "[authz] bounds_guard.check: tool outside owl bounds — blocking",
            extra={"_fields": {"tool": tool_name, "owl": owl_manifest.name, "axis": "tools"}},
        )
    return block
```

- [ ] **Step 4: Write the failing enforcement-honesty tests**

`tests/authz/test_enforcement.py`:

```python
"""E2-S2 — a task envelope may not silently narrow a not-yet-enforced axis."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.authz.enforcement import (
    ENFORCED_AXES,
    assert_task_narrowing_enforceable,
    unenforced_narrowing,
)
from stackowl.exceptions import DomainError

OWL = BoundsSpec(tools=frozenset({"a", "b"}))


def test_only_tools_enforced_today() -> None:
    assert ENFORCED_AXES == frozenset({"tools"})


def test_tools_narrowing_is_enforceable() -> None:
    task = BoundsSpec(tools=frozenset({"a"}))
    assert unenforced_narrowing(OWL, task) == set()
    assert_task_narrowing_enforceable(OWL, task)  # no raise


def test_ceiling_equal_to_owl_passes() -> None:
    # creation_ceiling is an exact copy of owl.bounds → narrows nothing.
    assert_task_narrowing_enforceable(OWL, OWL)


def test_network_narrowing_is_refused() -> None:
    from stackowl.authz.bounds import NetworkRule

    task = BoundsSpec(tools=frozenset({"a"}), network=(NetworkRule(host="x"),))
    assert "network" in unenforced_narrowing(OWL, task)
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(OWL, task)


def test_fs_narrowing_is_refused() -> None:
    task = BoundsSpec(tools=frozenset({"a"}), fs_read_roots=("/safe",))
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(OWL, task)
```

- [ ] **Step 5: Implement `authz/enforcement.py`**

```python
"""enforcement — which BoundsSpec axes a dispatch seam actually ENFORCES (E2-S2).

A BoundsSpec models five axes; in S2 only TOOLS is enforced (at the dispatch
seam). The other four are modeled for Epic 3+. A *task envelope* that narrows an
axis no seam enforces would manufacture false confidence (e.g. ``network: none``
that does not block the network). So a task-scoped narrowing of an unenforced
axis is REFUSED at construction — fail closed. The creation_ceiling (a copy of
the owl's own bounds) narrows nothing relative to the owl, so it always passes.

ENFORCED_AXES grows as Epic 3 wires the fs/network seams; nothing else changes.
"""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.exceptions import DomainError

#: Axes with a live enforcement seam. TOOLS only, in S2.
ENFORCED_AXES = frozenset({"tools"})

#: All axes a task spec can carry, paired with their "unset / unrestricted" value.
_AXIS_UNSET: dict[str, object] = {
    "tools": None,
    "fs_read_roots": None,
    "fs_write_roots": None,
    "network": None,
    "data_owner_id": None,
    "data_namespaces": None,
}


def unenforced_narrowing(owl: BoundsSpec | None, task: BoundsSpec) -> set[str]:
    """Return the unenforced axes on which ``task`` is stricter than ``owl``.

    An axis is "narrowed" when the task sets a non-unset value that differs from
    the owl's value on that axis. ``caps`` is excluded (a ResourceCaps object is
    always present; its enforcement is E2-S4/S5 and is handled there).
    """
    narrowed: set[str] = set()
    for axis, unset in _AXIS_UNSET.items():
        if axis in ENFORCED_AXES:
            continue
        task_val = getattr(task, axis)
        if task_val == unset:
            continue  # task does not constrain this axis
        owl_val = getattr(owl, axis) if owl is not None else None
        if task_val != owl_val:
            narrowed.add(axis)
    return narrowed


def assert_task_narrowing_enforceable(owl: BoundsSpec | None, task: BoundsSpec) -> None:
    """Raise DomainError if ``task`` narrows any axis no seam enforces (fail closed)."""
    bad = unenforced_narrowing(owl, task)
    if bad:
        raise DomainError(
            "task envelope narrows axes with no enforcement seam "
            f"({sorted(bad)}); refusing to imply a guarantee that is not enforced "
            "(only these axes are enforced today: " + ", ".join(sorted(ENFORCED_AXES)) + ")"
        )
```

> Note: confirm `DomainError` is importable from `stackowl.exceptions` (it is — `BoundsViolation` subclasses it). If `DomainError.__init__` requires specific args, pass the message positionally as shown.

- [ ] **Step 6: Run both test files**

Run: `uv run pytest tests/authz/test_effective_bounds.py tests/authz/test_enforcement.py -v`
Expected: PASS

- [ ] **Step 7: Confirm no regression on the existing bounds tests**

Run: `uv run pytest tests/authz/ -v --timeout=60`
Expected: PASS (the refactored `check_tool_bounds` is byte-for-byte equivalent).

- [ ] **Step 8: Commit**

```bash
git add v2/src/stackowl/authz/bounds_guard.py v2/src/stackowl/authz/enforcement.py \
        v2/tests/authz/test_effective_bounds.py v2/tests/authz/test_enforcement.py
git commit -m "feat(v2): effective_bounds combiner + enforcement-honesty guard (Epic2 S2)"
```

---

## Task 4: Wire the seam — dispatch uses effective bounds + fail-closed DENY

**Files:**
- Create: `src/stackowl/pipeline/authz_compose.py`
- Modify: `src/stackowl/pipeline/steps/execute.py`
- Test: `tests/pipeline/test_authz_compose.py`, extend `tests/authz/test_bounds_dispatch.py`

- [ ] **Step 1: Write the failing compose tests**

`tests/pipeline/test_authz_compose.py`:

```python
"""E2-S2 — compute_effective_bounds: owl(now) ∩ ceiling ∩ envelope, fail-closed."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.exceptions import DomainError
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.authz_compose import compute_effective_bounds
from stackowl.pipeline.state import PipelineState


def _state(**kw: object) -> PipelineState:
    base = dict(trace_id="t", session_id="s", input_text="hi", channel="cli",
                owl_name="o", pipeline_step="")
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _reg(bounds: BoundsSpec | None) -> OwlRegistry:
    r = OwlRegistry()
    r.register(OwlAgentManifest(name="o", role="r", system_prompt="s",
                                model_tier="fast", bounds=bounds))
    return r


def test_owl_only_when_no_envelope() -> None:
    eff = compute_effective_bounds(_state(), _reg(BoundsSpec(tools=frozenset({"a"}))))
    assert eff.tools == frozenset({"a"})


def test_ceiling_narrows_owl() -> None:
    s = _state(creation_ceiling=BoundsSpec(tools=frozenset({"a"})))
    eff = compute_effective_bounds(s, _reg(BoundsSpec(tools=frozenset({"a", "b"}))))
    assert eff.tools == frozenset({"a"})


def test_unbounded_owl_no_envelope_is_none() -> None:
    assert compute_effective_bounds(_state(), _reg(None)) is None


def test_no_registry_is_none() -> None:
    assert compute_effective_bounds(_state(), None) is None


def test_unknown_owl_is_none() -> None:
    # owl not in registry → unbounded (byte-for-byte S1 for unknown owls)
    assert compute_effective_bounds(_state(owl_name="ghost"), _reg(None)) is None


def test_bounded_owl_compute_error_raises_for_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    # A registry that HAS the bounded owl but throws on a second access path must
    # surface (caller turns this into DENY). Simulate by a registry.get that raises.
    reg = _reg(BoundsSpec(tools=frozenset({"a"})))

    def boom(name: str):  # noqa: ANN202
        raise RuntimeError("registry fault")

    monkeypatch.setattr(reg, "get", boom)
    with pytest.raises(RuntimeError):
        compute_effective_bounds(_state(), reg)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_authz_compose.py -v`
Expected: FAIL — module/func absent.

- [ ] **Step 3: Implement `pipeline/authz_compose.py`**

```python
"""authz_compose — resolve an owl's live bounds and compose effective bounds.

Lives in the PIPELINE layer (not authz) because it reads the OwlRegistry; the
pure narrowing math stays in authz.bounds_guard (no services import). The single
source of truth for "what bounds apply to this dispatch", reused by the dispatch
seam AND the delegation-floor at child-spawn sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.authz.bounds_guard import effective_bounds
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.authz.bounds import BoundsSpec
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.state import PipelineState


def resolve_owl_bounds(owl_name: str, owl_registry: "OwlRegistry | None") -> "BoundsSpec | None":
    """Best-effort live bounds for an owl. None registry / unknown owl → None.

    A genuine lookup is attempted; an UNKNOWN owl (not registered) is treated as
    unbounded (None) — byte-for-byte S1 for unknown owls. Note this does NOT
    swallow arbitrary faults: an OwlNotFoundError means "unknown owl"; any other
    exception propagates (the caller decides fail-closed).
    """
    if owl_registry is None:
        return None
    from stackowl.owls.registry import OwlNotFoundError

    try:
        return owl_registry.get(owl_name).bounds
    except OwlNotFoundError:
        log.engine.debug(
            "[authz] compose.resolve: unknown owl — unbounded",
            extra={"_fields": {"owl": owl_name}},
        )
        return None


def compute_effective_bounds(
    state: "PipelineState", owl_registry: "OwlRegistry | None"
) -> "BoundsSpec | None":
    """effective = owl.bounds(now) ∩ creation_ceiling ∩ task_envelope.

    Fail-closed contract for the CALLER: a non-OwlNotFound exception propagates so
    the dispatch seam denies (never falls through on an error in a security path).
    A genuinely unbounded owl with no envelope returns None (unrestricted) — S1.
    """
    owl_bounds = resolve_owl_bounds(state.owl_name, owl_registry)
    return effective_bounds(owl_bounds, state.creation_ceiling, state.task_envelope)
```

- [ ] **Step 4: Run compose tests**

Run: `uv run pytest tests/pipeline/test_authz_compose.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing seam test (extend dispatch suite)**

Append to `tests/authz/test_bounds_dispatch.py` — first extend the `_drive` helper to accept an optional ceiling, then add tests. The existing `_drive` builds `_state()`; add a `ceiling` param:

```python
async def test_ceiling_narrows_below_owl_bounds() -> None:
    # owl permits {allowed_tool, forbidden_tool}; a task ceiling permits only
    # {allowed_tool} → forbidden_tool blocked BY THE TASK even though the owl allows it.
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    ceiling = BoundsSpec(tools=frozenset({"allowed_tool"}))
    allowed, forbidden, provider = await _drive(owl_bounds, ceiling=ceiling)
    assert allowed.executed is True
    assert forbidden.executed is False
    assert "not permitted by this owl's bounds" in provider.results["forbidden_tool"]
```

And the P1 invariant — bounds is the ceiling, consent is a door in it: an
out-of-bounds tool is refused BEFORE the consent gate, so consent can never
re-admit it. Add a recording consent gate to prove it is never consulted for a
blocked tool (mirror the `ConsequentialActionGate` use in the J4 journey; the
recording gate counts `check()` calls):

```python
async def test_out_of_bounds_tool_never_reaches_consent() -> None:
    # forbidden_tool is consequential AND out of the ceiling. It must be blocked by
    # bounds before consent is consulted (consent cannot launder an out-of-bounds call).
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    ceiling = BoundsSpec(tools=frozenset({"allowed_tool"}))
    gate = _RecordingConsentGate()  # .checked: list[str] of tool names check() saw
    allowed, forbidden, provider = await _drive(owl_bounds, ceiling=ceiling, consent_gate=gate)
    assert forbidden.executed is False
    assert "forbidden_tool" not in gate.checked
```

Extend `_drive` to thread `consent_gate` into `StepServices(...)` (default `None`),
and add a tiny `_RecordingConsentGate` whose `check()` appends the tool name and
returns `True` (so the only thing stopping `forbidden_tool` is bounds, not consent).

Update `_drive` (and the `_state()` helper it calls) so the ceiling is set on the state:

```python
async def _drive(
    bounds: BoundsSpec | None, *, ceiling: BoundsSpec | None = None
) -> tuple[_RecordingTool, _RecordingTool, _TwoToolProvider]:
    ...
    state = _state()
    if ceiling is not None:
        state = state.evolve(creation_ceiling=ceiling)
    token = set_services(StepServices(tool_registry=registry, owl_registry=owl_registry))
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    return allowed, forbidden, provider
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/authz/test_bounds_dispatch.py::test_ceiling_narrows_below_owl_bounds -v`
Expected: FAIL — the seam still checks owl-only bounds, so `forbidden_tool` runs.

- [ ] **Step 7: Wire the seam in `execute.py`**

In `src/stackowl/pipeline/steps/execute.py::_run_with_tools._dispatch`, replace the S1 bounds block (current lines ~144–167, the `from stackowl.authz.bounds_guard import check_tool_bounds` … `return bounds_block`) with:

```python
        # E2-S2 (FR33/FR35-adjacent) — BOUNDS check against EFFECTIVE bounds:
        # owl.bounds(now) ∩ state.creation_ceiling ∩ state.task_envelope. Checked
        # before consent/execution. Fail-closed: a bounded-owl computation error
        # DENIES (never falls through on a security path); an unbounded owl with
        # no envelope yields None → unchanged (byte-for-byte S1).
        from stackowl.authz.bounds_guard import check_effective_bounds
        from stackowl.pipeline.authz_compose import compute_effective_bounds

        try:
            effective = compute_effective_bounds(state, get_services().owl_registry)
        except Exception as exc:
            denied_this_run.add(name)
            log.engine.error(
                "[pipeline] execute: bounds computation failed — denying (fail closed)",
                exc_info=exc,
                extra={"_fields": {"tool": name, "owl": state.owl_name, "trace_id": state.trace_id}},
            )
            return (
                f"The action '{name}' could not be authorized (bounds check failed) and "
                "was not run. Respond to the user instead."
            )
        bounds_block = check_effective_bounds(effective, name)
        if bounds_block is not None:
            denied_this_run.add(name)
            # Provenance on the deny branch only (no per-dispatch recompute on the
            # allow path): owl-only verdict tells us whether the TASK was the narrower.
            owl_only = check_effective_bounds(
                compute_effective_bounds(state.evolve(creation_ceiling=None, task_envelope=None),
                                         get_services().owl_registry),
                name,
            )
            denied_by = "owl" if owl_only is not None else "task"
            log.engine.warning(
                "[pipeline] execute: tool refused by bounds",
                extra={"_fields": {
                    "tool": name, "owl": state.owl_name, "trace_id": state.trace_id,
                    "axis": "tools", "denied_by": denied_by,
                }},
            )
            return bounds_block
```

The `acting_owl_manifest` capture earlier in `_run_with_tools` (lines ~75–91) is now unused by `_dispatch` for the bounds check; leave it (it may feed other logic) — verify with `grep acting_owl_manifest src/stackowl/pipeline/steps/execute.py` and if it has no other reader, delete the now-dead capture in this same commit.

- [ ] **Step 8: Run the seam tests + the full authz/dispatch suite**

Run: `uv run pytest tests/authz/test_bounds_dispatch.py tests/pipeline/test_authz_compose.py -v --timeout=60`
Expected: PASS (incl. the existing S1 tests — unbounded owl still runs both tools).

- [ ] **Step 9: Commit**

```bash
git add v2/src/stackowl/pipeline/authz_compose.py v2/src/stackowl/pipeline/steps/execute.py \
        v2/tests/pipeline/test_authz_compose.py v2/tests/authz/test_bounds_dispatch.py
git commit -m "feat(v2): dispatch seam enforces effective bounds + fail-closed deny (Epic2 S2)"
```

---

## Task 5: Persist creation_ceiling on the durable task (migration + store)

**Files:**
- Create: `src/stackowl/db/migrations/0048_tasks_creation_ceiling.sql`
- Modify: `src/stackowl/pipeline/durable/task.py`, `src/stackowl/pipeline/durable/store.py`
- Test: `tests/durable/test_store_creation_ceiling.py`

- [ ] **Step 1: Add the migration**

`src/stackowl/db/migrations/0048_tasks_creation_ceiling.sql` (mirror the 0047 header style):

```sql
-- 0048_tasks_creation_ceiling.sql
-- E2-S2 — persist the task's creation-time bounds snapshot (the resume-monotonicity
-- ceiling) as JSON. NULL = no ceiling: on resume the task runs under the owl's
-- CURRENT bounds (never global-unrestricted, because owl.bounds(now) is always a
-- factor of effective bounds). Additive + nullable → every legacy row is unchanged.
ALTER TABLE tasks ADD COLUMN creation_ceiling TEXT;
```

- [ ] **Step 2: Add the model field**

In `src/stackowl/pipeline/durable/task.py`, add an import and a field. Top imports:

```python
from stackowl.authz.bounds import BoundsSpec
```

After the `channel` field (line 53), add:

```python
    #: Snapshot of the owl's bounds at task CREATION — the resume-monotonicity
    #: ceiling (E2-S2). NULL on legacy rows (pre-0048) and on a task created under
    #: an unbounded owl → None → resume uses the owl's current bounds.
    creation_ceiling: BoundsSpec | None = None
```

- [ ] **Step 3: Write the failing store tests**

`tests/durable/test_store_creation_ceiling.py` — follow the existing durable store test setup (look at a sibling test in `tests/durable/` for the temp-DB + migrated `DbPool` + owner-scoped `DurableTaskStore` fixture; reuse it verbatim). The new assertions:

```python
"""E2-S2 — DurableTaskStore round-trips creation_ceiling; NULL stays None."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stackowl.authz import BoundsSpec
from stackowl.pipeline.durable.task import DurableTask

# Reuse the durable-store fixture from the sibling store test module. If it is not
# a shared conftest fixture, copy its temp-DB + migrate + DurableTaskStore setup here.


def _task(task_id: str, ceiling: BoundsSpec | None) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id, owner_id="principal-default", goal="g", status="running",
        owl_name="o", channel="cli", creation_ceiling=ceiling,
        created_at=now, updated_at=now,
    )


async def test_create_get_roundtrips_ceiling(store) -> None:  # noqa: ANN001
    ceiling = BoundsSpec(tools=frozenset({"a", "b"}))
    await store.create(_task("task-ceil-1", ceiling))
    got = await store.get("task-ceil-1")
    assert got.creation_ceiling == ceiling


async def test_none_ceiling_persists_as_sql_null(store, db_pool) -> None:  # noqa: ANN001
    await store.create(_task("task-ceil-2", None))
    # raw column IS NULL, not the string "null"
    rows = await db_pool.fetch_all(
        "SELECT creation_ceiling FROM tasks WHERE task_id = ?", ("task-ceil-2",)
    )
    assert rows[0]["creation_ceiling"] is None
    got = await store.get("task-ceil-2")
    assert got.creation_ceiling is None
```

> Adapt `store`/`db_pool` to the sibling test's actual fixture names and the real `DbPool` query API (`fetch_all`/`execute` signature). Do NOT invent an API — copy the sibling's usage.

- [ ] **Step 4: Run to verify it fails**

Run: `uv run pytest tests/durable/test_store_creation_ceiling.py -v`
Expected: FAIL — store doesn't read/write the new column.

- [ ] **Step 5: Wire the store**

In `src/stackowl/pipeline/durable/store.py`:

(a) Add `creation_ceiling` to `_SELECT_FIELDS`:

```python
_SELECT_FIELDS = (
    "task_id, owner_id, goal, status, current_step, "
    "thread_id, result, owl_name, channel, creation_ceiling, created_at, updated_at"
)
```

(b) In `create()`, add to the inserted dict:

```python
        "creation_ceiling": (
            task.creation_ceiling.model_dump_json() if task.creation_ceiling is not None else None
        ),
```

(c) In `_row_to_task()`, decode the column BEFORE constructing the model (SQL NULL → None; never call `model_validate_json(None)`):

```python
    raw_ceiling = row.get("creation_ceiling")
    ceiling = (
        BoundsSpec.model_validate_json(str(raw_ceiling)) if raw_ceiling is not None else None
    )
```

and pass `creation_ceiling=ceiling` into the `DurableTask(...)` call. Add `from stackowl.authz.bounds import BoundsSpec` to the store imports.

- [ ] **Step 6: Run to verify it passes + no durable regression**

Run: `uv run pytest tests/durable/test_store_creation_ceiling.py tests/durable/ -v --timeout=120 -x`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add v2/src/stackowl/db/migrations/0048_tasks_creation_ceiling.sql \
        v2/src/stackowl/pipeline/durable/task.py v2/src/stackowl/pipeline/durable/store.py \
        v2/tests/durable/test_store_creation_ceiling.py
git commit -m "feat(v2): persist durable task creation_ceiling (migration 0048, Epic2 S2)"
```

---

## Task 6: Snapshot owl bounds at creation + thread through recovery

**Files:**
- Modify: `src/stackowl/pipeline/durable/task_runner.py`, `src/stackowl/pipeline/durable/recovery.py`
- Test: `tests/durable/test_runner_ceiling_snapshot.py`, `tests/durable/test_recovery_ceiling.py`

- [ ] **Step 1: Write the failing runner test**

`tests/durable/test_runner_ceiling_snapshot.py` — drive `DurableTaskRunner.run` with a fake store + fake backend, services carrying an owl registry with a bounded owl; assert the created task + durable state carry the snapshot. Mirror the existing runner test in `tests/durable/` for the fake-store/fake-backend doubles.

```python
"""E2-S2 — runner.run snapshots the acting owl's bounds into the task + state."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
# Reuse FakeStore / FakeBackend from the sibling runner test module.


async def test_run_snapshots_owl_bounds() -> None:
    bounds = BoundsSpec(tools=frozenset({"a"}))
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name="o", role="r", system_prompt="s",
                                  model_tier="fast", bounds=bounds))
    store = FakeStore(owner_id="principal-default")  # captures created task
    backend = FakeBackend()  # captures the state it was run with
    token = set_services(StepServices(owl_registry=reg))
    try:
        runner = DurableTaskRunner(store, backend)
        state = PipelineState(trace_id="t", session_id="s", input_text="g",
                              channel="cli", owl_name="o", pipeline_step="")
        await runner.run(goal="g", state=state)
    finally:
        reset_services(token)
    assert store.created.creation_ceiling == bounds
    assert backend.ran_with.creation_ceiling == bounds


async def test_run_unbounded_owl_snapshots_none() -> None:
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name="o", role="r", system_prompt="s", model_tier="fast"))
    store = FakeStore(owner_id="principal-default")
    backend = FakeBackend()
    token = set_services(StepServices(owl_registry=reg))
    try:
        await DurableTaskRunner(store, backend).run(
            goal="g",
            state=PipelineState(trace_id="t", session_id="s", input_text="g",
                                channel="cli", owl_name="o", pipeline_step=""),
        )
    finally:
        reset_services(token)
    assert store.created.creation_ceiling is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/durable/test_runner_ceiling_snapshot.py -v`
Expected: FAIL — runner does not snapshot.

- [ ] **Step 3: Snapshot in `task_runner.run`**

In `src/stackowl/pipeline/durable/task_runner.py::run`, compute the ceiling from the acting owl (best-effort via the shared resolver) and thread it into BOTH the created task and the durable state. Add the import at top:

```python
from stackowl.pipeline.authz_compose import resolve_owl_bounds
from stackowl.pipeline.services import get_services
```

Inside `run`, after `task_id = ...` and before `self._store.create(...)`:

```python
        # E2-S2 — snapshot the acting owl's bounds as the resume-monotonicity
        # ceiling. Best-effort: no registry / unbounded owl → None (no clamp).
        creation_ceiling = resolve_owl_bounds(state.owl_name, get_services().owl_registry)
```

Add `creation_ceiling=creation_ceiling,` to the `DurableTask(...)` constructor, and change the durable-state evolve:

```python
        durable_state = state.evolve(
            task_id=task_id, durable_owner_id=owner_id, creation_ceiling=creation_ceiling,
        )
```

- [ ] **Step 4: Run runner tests**

Run: `uv run pytest tests/durable/test_runner_ceiling_snapshot.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing recovery test**

`tests/durable/test_recovery_ceiling.py` — using the recovery test scaffold in `tests/durable/`, persist a task with a ceiling, run recovery's `_reconstruct_state`, assert the resumed state carries it. Cover both branches (with/without checkpoint blob) and the NULL case.

```python
"""E2-S2 — recovery threads the persisted creation_ceiling into the resumed state."""

from __future__ import annotations

from stackowl.authz import BoundsSpec


async def test_reconstruct_threads_ceiling(recovery, store) -> None:  # noqa: ANN001
    ceiling = BoundsSpec(tools=frozenset({"a"}))
    # create an orphaned 'running' task with a ceiling (no checkpoint blob)
    await _seed_running_task(store, "task-rec-1", ceiling)
    state = await recovery._reconstruct_state(await store.get("task-rec-1"))
    assert state.creation_ceiling == ceiling


async def test_reconstruct_null_ceiling_is_none(recovery, store) -> None:  # noqa: ANN001
    await _seed_running_task(store, "task-rec-2", None)
    state = await recovery._reconstruct_state(await store.get("task-rec-2"))
    assert state.creation_ceiling is None
```

> `recovery`, `store`, `_seed_running_task` mirror the sibling recovery test's fixtures/helpers — copy them.

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/durable/test_recovery_ceiling.py -v`
Expected: FAIL — recovery does not thread the ceiling.

- [ ] **Step 7: Thread the ceiling in `recovery._reconstruct_state`**

In `src/stackowl/pipeline/durable/recovery.py::_reconstruct_state`, add `creation_ceiling=task.creation_ceiling` to BOTH `base.evolve(...)` calls (the no-checkpoint branch and the mid-transcript branch):

```python
        return base.evolve(
            task_id=task_id, durable_owner_id=self._owner_id,
            creation_ceiling=task.creation_ceiling,
        )
```

and

```python
    return base.evolve(
        task_id=task_id,
        durable_owner_id=self._owner_id,
        creation_ceiling=task.creation_ceiling,
        durable_resume_messages=cp.messages,
        durable_resume_tool_calls=cp.tool_call_records,
        durable_resume_iteration=cp.iteration + 1,
    )
```

- [ ] **Step 8: Run recovery tests + durable suite**

Run: `uv run pytest tests/durable/test_recovery_ceiling.py tests/durable/ -v --timeout=120 -x`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add v2/src/stackowl/pipeline/durable/task_runner.py v2/src/stackowl/pipeline/durable/recovery.py \
        v2/tests/durable/test_runner_ceiling_snapshot.py v2/tests/durable/test_recovery_ceiling.py
git commit -m "feat(v2): snapshot owl bounds at creation + restore on resume (Epic2 S2)"
```

---

## Task 7: Delegation floor — child can't escalate past the parent owl

The child runs under its OWN owl bounds (S1) already; the hole is a child owl BROADER than the parent. Clamp the child's `creation_ceiling` to the parent owl's bounds so `child_effective = child_owl ∩ parent_owl ⊆ parent_owl`. In S2 the parent's effective bounds equal its owl bounds (no task envelope; a durable parent's ceiling is its own owl snapshot), so resolving by the parent owl name is correct. S3 (which threads real envelopes through delegation) will tighten this further.

**Files:**
- Modify: `src/stackowl/tools/agents/delegate_task.py`, `sessions_spawn.py`, `sessions_send.py`
- Test: `tests/pipeline/test_child_floor.py`

- [ ] **Step 1: Write the failing child-floor test**

`tests/pipeline/test_child_floor.py` — unit-test the shared helper and the delegate path. The clean, deterministic assertion is on the helper + that the child state's `creation_ceiling` equals the parent owl's bounds.

```python
"""E2-S2 — a delegated child inherits the parent owl's bounds as its ceiling floor."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.authz_compose import resolve_owl_bounds


def test_resolve_parent_bounds_is_the_child_floor() -> None:
    parent_bounds = BoundsSpec(tools=frozenset({"read_file"}))
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name="parent", role="r", system_prompt="s",
                                  model_tier="fast", bounds=parent_bounds))
    # the helper the spawn sites call to compute the child's creation_ceiling
    assert resolve_owl_bounds("parent", reg) == parent_bounds
```

Add an end-to-end intersection assertion at the dispatch level: a child whose own owl is broad but whose `creation_ceiling` is the narrow parent bounds is denied the broad tool. Reuse the `_drive`-style harness from `tests/authz/test_bounds_dispatch.py` with a broad owl + a narrow ceiling (this is already proven by Task 4's `test_ceiling_narrows_below_owl_bounds`; here assert the SPAWN sites set that ceiling).

For each spawn site, add a focused test that the constructed child `PipelineState.creation_ceiling` is the parent-owl bounds. Drive the tool's internal state-builder with a services registry containing the parent owl, and assert on the child state passed to the backend (capture via a fake `AsyncioBackend.run`, mirroring the sibling agent-tool tests).

```python
async def test_delegate_task_sets_child_ceiling_to_caller_bounds(monkeypatch) -> None:  # noqa: ANN001
    # Mirror the existing delegate_task tool test harness. Register the caller owl
    # with narrow bounds; invoke the tool; capture the child PipelineState handed to
    # the delegator/backend; assert child.creation_ceiling == caller bounds.
    ...
```

> Fill this in against the real delegate_task test harness in `tests/tools/agents/` (copy its provider/services doubles). If capturing the child state requires a seam the harness doesn't expose, assert instead at the `_run_delegation` boundary on the constructed `parent_state.creation_ceiling`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_child_floor.py -v`
Expected: the `resolve_owl_bounds` test PASSES (helper exists from Task 4); the spawn-site tests FAIL — children don't set `creation_ceiling` yet.

- [ ] **Step 3: Set the child ceiling at `delegate_task.py`**

The parent owl is `caller` and is in scope. Resolve its bounds and set the child's ceiling. Add imports:

```python
from stackowl.pipeline.authz_compose import resolve_owl_bounds
from stackowl.pipeline.services import get_services
```

Change the `parent_state` construction (lines ~238–241) to:

```python
        parent_state = PipelineState(
            trace_id=trace_id or "delegate-task", session_id=session_id, input_text=sub_task,
            channel=channel, owl_name=caller, pipeline_step="dispatch", delegation_depth=depth,
            # E2-S2 delegation floor — the child cannot exceed the PARENT owl's
            # bounds even if its own owl is broader (no-escalation-via-delegation,
            # FR35-runtime). resolve is best-effort: parent unbounded → None (no clamp).
            creation_ceiling=resolve_owl_bounds(caller, get_services().owl_registry),
        )
```

> Note: here the variable is named `parent_state` but it is the state the CHILD/sub-task runs under; `creation_ceiling` set to the caller's bounds is the floor that owl-name `caller`'s delegate executes within.

- [ ] **Step 4: Set the child ceiling at `sessions_spawn.py` and `sessions_send.py`**

These hardcode `delegation_depth=1` and the spawning context's owl is the one invoking the tool. Locate how the tool knows the INVOKING owl (mirror however `delegate_task` obtains `caller`; the sessions tools receive an analogous caller/owl identity from the dispatch — find it). Set the child ceiling to the invoking owl's bounds. In `sessions_spawn.py` (lines ~205–214) add to the `PipelineState(...)`:

```python
            creation_ceiling=resolve_owl_bounds(<invoking_owl_name>, get_services().owl_registry),
```

and the same in `sessions_send.py` (lines ~223–233). Add the same two imports to each file. `get_services()` is already imported in both (they call it just below). If the invoking owl is genuinely not available to these tools, the spawned session owl's OWN bounds (S1) still apply; in that case set `creation_ceiling=None` and add an explicit `# E2-S2 GAP:` comment + a Phase-2 backlog note in the spec's §8 table rather than guessing an owl name.

- [ ] **Step 5: Run the child-floor tests + agent-tool suites**

Run: `uv run pytest tests/pipeline/test_child_floor.py tests/tools/agents/ -v --timeout=120 -x`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add v2/src/stackowl/tools/agents/delegate_task.py v2/src/stackowl/tools/agents/sessions_spawn.py \
        v2/src/stackowl/tools/agents/sessions_send.py v2/tests/pipeline/test_child_floor.py
git commit -m "feat(v2): delegation floor — child clamped to parent owl bounds (Epic2 S2, FR35-runtime)"
```

---

## Task 8: Gateway journeys — task-scope deny + kill/resume monotonicity

The business-outcome proofs. Mirror `tests/journeys/test_j4_tools_bounds.py` (real Telegram adapter → scanner → AsyncioBackend; scripted owl is the only mock).

**Files:**
- Test: `tests/journeys/test_tool_scope_envelope.py`

- [ ] **Step 1: Write journey 1 — task-scope deny end-to-end**

Copy the `_FakeBot`/`_FakeBotApp`/`_ScriptedBoundedOwl`/`_FakeProviderRegistry`/`_RecordingTool`/`_bounded_manifest`/`_build`/`_turn` scaffolding from `tests/journeys/test_j4_tools_bounds.py`. Then:

```python
async def test_task_envelope_denies_tool_owl_would_allow() -> None:
    # Owl permits {allowed_tool, forbidden_tool}; the TURN runs under a ceiling that
    # permits only {allowed_tool}. The scripted owl calls both. Outcome: allowed ran,
    # forbidden blocked BY THE TASK, session continued and delivered a final reply.
    owl_bounds = BoundsSpec(tools=frozenset({_ALLOWED_TOOL, _FORBIDDEN_TOOL}))
    env = _build(_ScriptedBoundedOwl(), bounds=owl_bounds)
    # inject the ceiling into the turn's PipelineState — extend _turn to accept a
    # ceiling kwarg and pass creation_ceiling=ceiling into PipelineState(...).
    reply = await _turn(env, "do the thing", ceiling=BoundsSpec(tools=frozenset({_ALLOWED_TOOL})))
    assert env.allowed.executed is True
    assert env.forbidden.executed is False
    assert _REPLY_FRAGMENT in reply
```

Extend the copied `_turn` to thread the ceiling:

```python
async def _turn(env: _Env, text: str, *, ceiling: BoundsSpec | None = None) -> str:
    ...
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
        creation_ceiling=ceiling,
    )
    ...
```

- [ ] **Step 2: Write journey 2 — kill/resume preserves the ceiling (TOCTOU)**

This is the security-critical proof: a durable task created under narrow owl bounds, the owl is then WIDENED, the task resumes, and the newly-granted tool stays denied because the persisted ceiling held the line. Mirror the J1/J2 kill-resume journey (`tests/journeys/` durable recovery test) for the durable scaffolding (real `DbPool`, `DurableTaskStore`, `recovery`):

```python
async def test_resume_under_widened_owl_stays_clamped_to_ceiling() -> None:
    # 1. create a durable task under owl bounds {allowed_tool} → ceiling persisted.
    # 2. WIDEN the owl registry's manifest to {allowed_tool, forbidden_tool}.
    # 3. resume the task (recovery → runner.resume) with a scripted owl that calls
    #    forbidden_tool.
    # 4. assert forbidden_tool is DENIED (ceiling ∩ widened-owl still excludes it)
    #    and the task finalizes without executing it.
    ...
```

> Build this against the real durable kill/resume journey in `tests/journeys/`. The key assertions: `forbidden.executed is False` after resume, and the persisted `creation_ceiling` (loaded by recovery) is what clamps it. If a full kill/resume journey is too heavy to assemble here, assert the equivalent at the `recovery._reconstruct_state` + one dispatch level: reconstruct the state (ceiling loaded), widen the owl, run `_run_with_tools`, assert the widened tool is denied.

- [ ] **Step 3: Run the journeys**

Run: `uv run pytest tests/journeys/test_tool_scope_envelope.py -v --timeout=120`
Expected: PASS

- [ ] **Step 4: Full targeted regression across everything touched**

Run:
```bash
uv run pytest tests/authz/ tests/pipeline/test_authz_compose.py \
  tests/pipeline/test_pipeline_state_bounds.py tests/pipeline/test_child_floor.py \
  tests/durable/ tests/journeys/test_tool_scope_envelope.py tests/journeys/test_j4_tools_bounds.py \
  -v --timeout=180 -x
```
Expected: PASS

- [ ] **Step 5: Lint + type-check the touched files**

Run:
```bash
uv run ruff check src/stackowl/authz src/stackowl/pipeline src/stackowl/tools/agents
uv run mypy src/stackowl/authz src/stackowl/pipeline/authz_compose.py src/stackowl/pipeline/state.py
```
Expected: clean. Fix any finding before committing.

- [ ] **Step 6: Commit**

```bash
git add v2/tests/journeys/test_tool_scope_envelope.py
git commit -m "test(v2): gateway journeys — task-scope deny + kill/resume monotonicity (Epic2 S2)"
```

---

## Definition of Done

- [ ] `effective = owl.bounds(now) ∩ creation_ceiling ∩ task_envelope` enforced at the single dispatch seam.
- [ ] Unbounded owls with no envelope: byte-for-byte S1 (proven by the unchanged S1 dispatch tests).
- [ ] A task envelope that narrows a not-yet-enforced axis is REFUSED (`assert_task_narrowing_enforceable`).
- [ ] Bounded-owl bounds-computation error → DENY (never falls through).
- [ ] `creation_ceiling` persists (migration 0048), survives kill/resume, and NULL → owl-bounds-only (never global).
- [ ] A delegated child cannot exceed the parent owl's bounds.
- [ ] Two gateway journeys green: task-scope deny, and resume-under-widened-owl stays clamped.
- [ ] `ruff` + `mypy --strict` clean on touched modules.
- [ ] Each task committed separately (8 commits), tree green at each.

## Out of scope (tracked — see spec §8)

E2-S3 preflight planner fills `task_envelope`; fs/network/data/caps enforcement seams (Epic 3, growing `ENFORCED_AXES`); full FR35 manifest-layer parent_owl ∩ child_owl reconciliation (Epic 3); threading a parent's *task envelope* (not just owl bounds) through delegation lands with S3's envelope.
