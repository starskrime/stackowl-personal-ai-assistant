# Preflight Planner (E2-S3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For durable tasks, compute a goal-derived least-privilege tool set at creation, bias the owl's *presented* tools toward it (drift prevention), and emit *drift telemetry* when a task acts off-plan — without changing the hard authorization boundary.

**Architecture:** The `task_envelope` is telemetry + presentation only, NOT enforcement. Enforcement stays `owl ∩ creation_ceiling`. A `PreflightPlanner` (LLM proposer ∪ mandatory discovery, single-verdict) runs once in `DurableTaskRunner.run`, persists the envelope on the task row (migration 0049, restored on resume — deterministic, no re-plan). The dispatch seam presents `restrict_to = envelope.tools` (hiding off-plan tools, with self-DoS guards) and emits an observe-only drift event when an off-plan tool runs. Fail-open total: any planner failure → `task_envelope=None` → byte-for-byte S2.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, SQLite raw-SQL migrations, pytest (`uv run pytest`), ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-06-05-preflight-planner-design.md`

**Run tests from `v2/`. NO `pytest-timeout` plugin — never pass `--timeout`. Targeted paths only (the full suite hangs on this box).** Stage `v2/` only. Commit footer:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## File Structure

**Create:**
- `src/stackowl/pipeline/planner/__init__.py` — exports `PreflightPlanner`, `ToolProposer`.
- `src/stackowl/pipeline/planner/proposer.py` — `ToolProposer` (LLM fast-tier → exact-validated tool names).
- `src/stackowl/pipeline/planner/planner.py` — `PreflightPlanner` (proposer ∪ discovery, single-verdict, honesty-guard).
- `src/stackowl/db/migrations/0049_tasks_task_envelope.sql` — additive nullable column.
- Tests: `tests/pipeline/planner/test_proposer.py`, `tests/pipeline/planner/test_planner.py`, `tests/tools/test_presentation_restrict_to.py`, `tests/pipeline/durable/test_store_task_envelope.py`, `tests/pipeline/durable/test_runner_envelope_plan.py`, `tests/pipeline/durable/test_recovery_envelope.py`, `tests/pipeline/steps/test_execute_drift_telemetry.py`, `tests/journeys/test_preflight_envelope.py`.

**Modify:**
- `src/stackowl/pipeline/authz_compose.py` — drop `task_envelope` from enforcement.
- `src/stackowl/tools/registry.py` + `src/stackowl/tools/_infra/presentation.py` — `restrict_to` param.
- `src/stackowl/pipeline/durable/task.py` + `store.py` + `task_runner.py` + `recovery.py` — persist/restore/compute the envelope.
- `src/stackowl/pipeline/steps/execute.py` — presentation `restrict_to` wiring + drift telemetry.
- `tests/pipeline/test_authz_compose.py` — assert enforcement ignores `task_envelope`.

---

## Task 1: Enforcement ignores `task_envelope` (decouple)

The envelope must not affect the hard boundary. Behavior-preserving for S2 (where `task_envelope` was always `None`).

**Files:**
- Modify: `src/stackowl/pipeline/authz_compose.py`
- Test: `tests/pipeline/test_authz_compose.py`

- [ ] **Step 1: Write the failing test** — append to `tests/pipeline/test_authz_compose.py`:

```python
def test_task_envelope_is_ignored_by_enforcement() -> None:
    # E2-S3 — task_envelope is telemetry/presentation only; enforcement is owl ∩ ceiling.
    s = _state(
        creation_ceiling=None,
        task_envelope=BoundsSpec(tools=frozenset({"a"})),  # would narrow if folded
    )
    eff = compute_effective_bounds(s, _reg(BoundsSpec(tools=frozenset({"a", "b"}))))
    assert eff.tools == frozenset({"a", "b"})  # NOT narrowed by the envelope
```

(`_state` and `_reg` already exist in this file; `_state` must accept `task_envelope` — it forwards `**kw` to `PipelineState`, so this works as-is.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_authz_compose.py::test_task_envelope_is_ignored_by_enforcement -v`
Expected: FAIL (envelope currently folded → `eff.tools == {"a"}`).

- [ ] **Step 3: Drop `task_envelope` from the fold**

In `src/stackowl/pipeline/authz_compose.py::compute_effective_bounds`, change the return from folding three specs to two:

```python
    owl_bounds = resolve_owl_bounds(state.owl_name, owl_registry)
    # E2-S3 — enforcement is owl ∩ creation_ceiling ONLY. task_envelope is a
    # least-privilege DEFAULT used for presentation + drift telemetry, never for
    # enforcement (the hard boundary must not depend on an LLM-derived hint).
    return effective_bounds(owl_bounds, state.creation_ceiling)
```

Update the function's docstring's formula line from `owl ∩ creation_ceiling ∩ task_envelope` to `owl ∩ creation_ceiling` with a note that `task_envelope` is excluded by design (E2-S3).

- [ ] **Step 4: Run the new test + the full authz/compose suite**

Run: `uv run pytest tests/pipeline/test_authz_compose.py tests/authz/ tests/pipeline/test_child_floor.py -v`
Expected: PASS (all S2 enforcement/ceiling/child-floor tests still green — they use `creation_ceiling`, not `task_envelope`). If any S2 test set `task_envelope` and asserted narrowing, STOP and report — that would mean S2 relied on envelope enforcement (it should not).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/authz_compose.py v2/tests/pipeline/test_authz_compose.py
git commit -m "refactor(v2): task_envelope is telemetry-only, not enforcement (Epic2 S3)"
```

---

## Task 2: Presentation `restrict_to` (pure, no wiring)

**Files:**
- Modify: `src/stackowl/tools/registry.py`, `src/stackowl/tools/_infra/presentation.py`
- Test: `tests/tools/test_presentation_restrict_to.py`

- [ ] **Step 1: Write the failing tests** — `tests/tools/test_presentation_restrict_to.py`:

```python
"""E2-S3 — restrict_to narrows the presented set to planned ∪ discovery."""

from __future__ import annotations

from stackowl.tools.registry import ToolRegistry
# Reuse the repo's tool-test doubles. Find a sibling presentation/registry test
# (grep tests for to_provider_schema) and copy its minimal Tool/ToolManifest stub
# + how it registers tools + the protocol string used.


def _registry_with(names: list[str]) -> ToolRegistry:
    r = ToolRegistry()
    for n in names:
        r.register(_stub_tool(n))   # _stub_tool mirrors the sibling test's stub
    return r


def _present_names(schemas: list[dict]) -> set[str]:
    # extract tool names from provider schemas (anthropic {"name"} / openai {"function":{"name"}})
    out = set()
    for s in schemas:
        n = s.get("name") or (s.get("function") or {}).get("name")
        if n:
            out.add(n)
    return out


def test_restrict_to_none_is_unchanged() -> None:
    r = _registry_with(["tool_search", "tool_describe", "read_file", "shell", "alpha"])
    base = _present_names(r.to_provider_schema("anthropic"))
    same = _present_names(r.to_provider_schema("anthropic", restrict_to=None))
    assert base == same


def test_restrict_to_empty_yields_discovery_only() -> None:
    r = _registry_with(["tool_search", "tool_describe", "read_file", "shell", "alpha"])
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset()))
    assert out == {"tool_search", "tool_describe"}  # NOT base+groups (is-not-None, not truthiness)


def test_restrict_to_set_is_planned_plus_discovery() -> None:
    r = _registry_with(["tool_search", "tool_describe", "read_file", "shell", "alpha"])
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset({"alpha"})))
    assert out == {"alpha", "tool_search", "tool_describe"}  # base 'shell'/'read_file' dropped


def test_restrict_to_drops_unknown_names() -> None:
    r = _registry_with(["tool_search", "tool_describe", "alpha"])
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset({"alpha", "ghost"})))
    assert out == {"alpha", "tool_search", "tool_describe"}
```

> Replace `_stub_tool` and the protocol string with the sibling test's REAL stubs (grep `to_provider_schema` under `tests/`). If the base set / always_present names differ from `tool_search`/`tool_describe`, read `presentation.py` `_DEFAULT_ALWAYS` and use those names in the assertions.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/tools/test_presentation_restrict_to.py -v`
Expected: FAIL — `restrict_to` is not a parameter.

- [ ] **Step 3: Thread `restrict_to` through the API**

In `src/stackowl/tools/registry.py::to_provider_schema`, add the parameter and pass it to `ToolPresentation.select`:

```python
    def to_provider_schema(
        self,
        protocol: str,
        *,
        profile: list[str] | None = None,
        pins: list[str] | None = None,
        hydrated: set[str] | None = None,
        restrict_to: frozenset[str] | None = None,
    ) -> list[dict[str, object]]:
        # ... existing setup ...
        # pass restrict_to into the select(...) call (see presentation.py)
```
(Read the current body and add `restrict_to=restrict_to` to the `ToolPresentation(...).select(...)` call.)

In `src/stackowl/tools/_infra/presentation.py::ToolPresentation.select`, add `restrict_to: frozenset[str] | None = None` to the signature and, at the TOP of the method, branch:

```python
        # E2-S3 — least-privilege presentation. When a plan exists, present ONLY
        # discovery (always_present) + the planned set ∩ catalog. The broad base
        # set + profile groups are dropped for this turn; always_present stays
        # non-evictable. NOTE: `is not None`, NOT truthiness — an empty plan must
        # yield discovery-only, never fall back to base+groups.
        if restrict_to is not None:
            cfg = self._config
            always = sorted(n for n in cfg.always_present if n in by_name)
            taken = set(always)
            planned = sorted(n for n in restrict_to if n in by_name and n not in taken)
            ordered = list(always)
            budget = max(cfg.cap - len(ordered), 0)
            ordered.extend(planned[:budget])
            return ordered
        # ... existing union+cap logic unchanged below ...
```
(Match `by_name` / `cfg` / `self._config` / the return type to the real method — read it first. The existing non-restrict path must be byte-for-byte unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/tools/test_presentation_restrict_to.py -v`
Expected: PASS

- [ ] **Step 5: Regression — existing presentation/registry tests**

Run: `uv run pytest tests/tools/ -v`
Expected: PASS (restrict_to defaults None → every existing call unchanged).

- [ ] **Step 6: Commit**

```bash
git add v2/src/stackowl/tools/registry.py v2/src/stackowl/tools/_infra/presentation.py v2/tests/tools/test_presentation_restrict_to.py
git commit -m "feat(v2): restrict_to presentation param — least-privilege tool schema (Epic2 S3)"
```

---

## Task 3: `ToolProposer` (LLM → exact-validated tool names)

**Files:**
- Create: `src/stackowl/pipeline/planner/__init__.py`, `src/stackowl/pipeline/planner/proposer.py`
- Test: `tests/pipeline/planner/test_proposer.py`

- [ ] **Step 1: Write the failing tests** — `tests/pipeline/planner/test_proposer.py`:

```python
"""E2-S3 — ToolProposer: LLM picks tools, validated by EXACT membership."""

from __future__ import annotations

import pytest

from stackowl.pipeline.planner.proposer import ToolProposer
from stackowl.providers.base import CompletionResult


class _FakeProvider:
    def __init__(self, content: str | Exception) -> None:
        self._content = content

    async def complete(self, messages, model="", **kw):  # noqa: ANN001
        if isinstance(self._content, Exception):
            raise self._content
        return CompletionResult(content=self._content)  # adapt to the real CompletionResult ctor


class _FakeRegistry:
    def __init__(self, provider) -> None:  # noqa: ANN001
        self._p = provider

    def get_with_cascade(self, tier: str):  # noqa: ANN001
        return self._p


CATALOG = [("note_search", "Search notes"), ("summarize_text", "Summarize"), ("shell", "Run shell")]


async def test_parses_json_and_validates_exact() -> None:
    p = ToolProposer(_FakeRegistry(_FakeProvider('{"tools": ["note_search", "summarize_text", "made_up"]}')))
    got = await p.propose("summarize my notes", CATALOG)
    assert got == frozenset({"note_search", "summarize_text"})  # made_up dropped


async def test_hallucination_not_fuzzy_matched() -> None:
    p = ToolProposer(_FakeRegistry(_FakeProvider('{"tools": ["shel", "note_serch"]}')))
    got = await p.propose("x", CATALOG)
    assert got == frozenset()  # 'shel' is NOT matched to 'shell'


async def test_provider_error_returns_empty() -> None:
    p = ToolProposer(_FakeRegistry(_FakeProvider(RuntimeError("boom"))))
    got = await p.propose("x", CATALOG)
    assert got == frozenset()


async def test_no_registry_returns_empty() -> None:
    p = ToolProposer(None)
    assert await p.propose("x", CATALOG) == frozenset()
```

> Open `src/stackowl/providers/base.py` and confirm the `CompletionResult` constructor (it may need more than `content`); adapt `_FakeProvider`. Confirm `Message` import path if the proposer builds messages.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/planner/test_proposer.py -v`
Expected: FAIL — module absent.

- [ ] **Step 3: Implement `proposer.py`**

```python
"""ToolProposer — fast-tier LLM proposes the minimal tool set for a goal (E2-S3).

Returns tool names validated by EXACT membership against the live catalog —
hallucinated names are dropped, NEVER fuzzy-matched (so 'shel' can never become
'shell'). Any provider/parse failure returns an empty set; the planner treats
that as fail-open. Tool descriptions are length-capped before being shown to the
model (a cheap Catalog-Poisoning mitigation; the boundary is owl∩ceiling anyway).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.providers.registry import ProviderRegistry

_DESC_CAP = 200  # chars of each tool description fed to the planner


def _parse_names(text: str, valid: frozenset[str]) -> frozenset[str]:
    """Permissive parse → only names that EXACTLY match the catalog."""
    names: set[str] = set()
    try:
        data = json.loads(text)
        raw = data.get("tools") if isinstance(data, dict) else data
        if isinstance(raw, list):
            names = {n for n in raw if isinstance(n, str) and n in valid}
    except Exception:  # noqa: BLE001 — malformed LLM output is expected; fall through
        names = set()
    if names:
        return frozenset(names)
    # Fallback: exact catalog names appearing verbatim in the text.
    return frozenset(n for n in valid if n in text)


class ToolProposer:
    """Proposes a minimal tool-name set for a goal via a fast-tier model."""

    def __init__(self, provider_registry: "ProviderRegistry | None") -> None:
        self._providers = provider_registry

    async def propose(self, goal: str, catalog: list[tuple[str, str]]) -> frozenset[str]:
        # 1. ENTRY
        log.engine.debug("[planner] proposer.propose: entry", extra={"_fields": {"tools": len(catalog)}})
        if self._providers is None or not catalog:
            return frozenset()
        valid = frozenset(name for name, _ in catalog)
        listing = "\n".join(f"- {name}: {desc[:_DESC_CAP]}" for name, desc in catalog)
        messages = [
            Message(role="system", content=(
                "You select the MINIMAL set of tools needed to accomplish a goal. "
                'Reply with ONLY a JSON object: {"tools": ["name", ...]} using exact '
                "tool names from the provided list. Include nothing the goal does not need."
            )),
            Message(role="user", content=f"GOAL:\n{goal}\n\nTOOLS:\n{listing}"),
        ]
        try:
            provider = self._providers.get_with_cascade("fast")
            result = await provider.complete(messages, model="")
        except Exception as exc:  # noqa: BLE001 — fail-open; planner decides
            log.engine.warning("[planner] proposer.propose: provider failed — empty", exc_info=exc)
            return frozenset()
        names = _parse_names(result.content or "", valid)
        log.engine.debug("[planner] proposer.propose: exit", extra={"_fields": {"selected": len(names)}})
        return names
```

`src/stackowl/pipeline/planner/__init__.py`:
```python
"""Preflight planner — least-privilege task envelope (E2-S3)."""

from stackowl.pipeline.planner.planner import PreflightPlanner
from stackowl.pipeline.planner.proposer import ToolProposer

__all__ = ["PreflightPlanner", "ToolProposer"]
```
(Add `PreflightPlanner` to `__init__` now even though it lands in Task 4 — or create `__init__` exporting only `ToolProposer` here and extend it in Task 4. To avoid an import error, export only `ToolProposer` in this task and add `PreflightPlanner` in Task 4.)

For THIS task `__init__.py` is:
```python
"""Preflight planner — least-privilege task envelope (E2-S3)."""

from stackowl.pipeline.planner.proposer import ToolProposer

__all__ = ["ToolProposer"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/planner/test_proposer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/planner/ v2/tests/pipeline/planner/test_proposer.py
git commit -m "feat(v2): ToolProposer — fast-tier least-privilege tool selection (Epic2 S3)"
```

---

## Task 4: `PreflightPlanner` (single-verdict compose)

**Files:**
- Create: `src/stackowl/pipeline/planner/planner.py`; Modify: `src/stackowl/pipeline/planner/__init__.py`
- Test: `tests/pipeline/planner/test_planner.py`

- [ ] **Step 1: Write the failing tests** — `tests/pipeline/planner/test_planner.py`:

```python
"""E2-S3 — PreflightPlanner: proposer ∪ discovery, single-verdict (trustworthy set | None)."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.pipeline.planner.planner import MANDATORY_DISCOVERY, PreflightPlanner


class _Proposer:
    def __init__(self, result: frozenset[str]) -> None:
        self._r = result

    async def propose(self, goal, catalog):  # noqa: ANN001
        return self._r


CATALOG = [("note_search", "d"), ("summarize_text", "d"), ("tool_search", "d"), ("tool_describe", "d")]
OWL = BoundsSpec(tools=frozenset({"note_search", "summarize_text", "shell", "tool_search", "tool_describe"}))


async def test_unions_mandatory_discovery() -> None:
    planner = PreflightPlanner(_Proposer(frozenset({"note_search"})))
    env = await planner.plan("g", OWL, CATALOG)
    assert env is not None
    assert MANDATORY_DISCOVERY <= env.tools
    assert "note_search" in env.tools


async def test_empty_proposer_returns_none() -> None:
    # discovery-only would hide the whole real toolset (self-DoS) → decline.
    planner = PreflightPlanner(_Proposer(frozenset()))
    assert await planner.plan("g", OWL, CATALOG) is None


async def test_tools_only_envelope_passes_honesty_guard() -> None:
    planner = PreflightPlanner(_Proposer(frozenset({"note_search"})))
    env = await planner.plan("g", OWL, CATALOG)
    assert env is not None and env.fs_read_roots is None and env.network is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/planner/test_planner.py -v`
Expected: FAIL — module absent.

- [ ] **Step 3: Implement `planner.py`**

```python
"""PreflightPlanner — compose the least-privilege task envelope (E2-S3).

Single verdict: returns a TRUSTWORTHY non-empty BoundsSpec, or None. There is no
degraded-but-non-None state — `restrict_to` keys off this same verdict, so a
garbage/empty plan can never hide tools (the Restrict-To-Decoupling self-DoS fix).
The result is the proposer's validated set UNIONed with mandatory discovery tools
(the escape hatch). If the proposer contributed nothing, an envelope of
discovery-only would hide the entire real toolset — so we return None (fail-open).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.authz.bounds import BoundsSpec
from stackowl.authz.enforcement import assert_task_narrowing_enforceable
from stackowl.exceptions import DomainError
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.pipeline.planner.proposer import ToolProposer

#: Discovery meta-tools always granted so a too-narrow plan is escapable.
MANDATORY_DISCOVERY = frozenset({"tool_search", "tool_describe"})


class PreflightPlanner:
    """Computes a goal-derived least-privilege task envelope, or None (fail-open)."""

    def __init__(self, proposer: "ToolProposer") -> None:
        self._proposer = proposer

    async def plan(
        self, goal: str, owl_bounds: BoundsSpec | None, catalog: list[tuple[str, str]]
    ) -> BoundsSpec | None:
        # 1. ENTRY
        log.engine.debug("[planner] plan: entry", extra={"_fields": {"tools": len(catalog)}})
        try:
            selected = await self._proposer.propose(goal, catalog)
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.engine.warning("[planner] plan: proposer raised — no envelope", exc_info=exc)
            return None
        if not selected:
            # discovery-only would hide everything → decline (fail-open).
            log.engine.info("[planner] plan: proposer empty — no envelope (fail-open)")
            return None
        candidate = BoundsSpec(tools=frozenset(selected | MANDATORY_DISCOVERY))
        try:
            assert_task_narrowing_enforceable(owl_bounds, candidate)
        except DomainError as exc:  # a non-tools axis crept in → decline
            log.engine.warning("[planner] plan: envelope failed honesty guard — none", exc_info=exc)
            return None
        log.engine.info("[planner] plan: envelope set", extra={"_fields": {"tools": len(candidate.tools or ())}})
        return candidate
```

Update `src/stackowl/pipeline/planner/__init__.py`:
```python
"""Preflight planner — least-privilege task envelope (E2-S3)."""

from stackowl.pipeline.planner.planner import PreflightPlanner
from stackowl.pipeline.planner.proposer import ToolProposer

__all__ = ["PreflightPlanner", "ToolProposer"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/planner/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/planner/ v2/tests/pipeline/planner/test_planner.py
git commit -m "feat(v2): PreflightPlanner — single-verdict least-privilege envelope (Epic2 S3)"
```

---

## Task 5: Persist `task_envelope` on the durable task (migration 0049 + store)

Mirror `creation_ceiling` exactly (the S2 pattern in these same files).

**Files:**
- Create: `src/stackowl/db/migrations/0049_tasks_task_envelope.sql`
- Modify: `src/stackowl/pipeline/durable/task.py`, `src/stackowl/pipeline/durable/store.py`
- Test: `tests/pipeline/durable/test_store_task_envelope.py`

- [ ] **Step 1: Migration** — `src/stackowl/db/migrations/0049_tasks_task_envelope.sql` (mirror `0048`'s header style):

```sql
-- 0049_tasks_task_envelope.sql
-- E2-S3 — persist the preflight planner's least-privilege task_envelope as JSON.
-- Telemetry + presentation only (NOT an enforcement boundary). NULL = no plan
-- (planner declined / failed / non-durable). Additive + nullable → legacy rows
-- unchanged. Restored on resume so a durable task keeps its original plan.
ALTER TABLE tasks ADD COLUMN task_envelope TEXT;
```

- [ ] **Step 2: Model field** — in `src/stackowl/pipeline/durable/task.py`, after `creation_ceiling`:

```python
    #: Preflight-planner least-privilege envelope (E2-S3). NULL when the planner
    #: declined/failed or for legacy rows. Telemetry + presentation only.
    task_envelope: BoundsSpec | None = None
```
(`BoundsSpec` is already imported in this file from Task-5-of-S2.)

- [ ] **Step 3: Failing store test** — `tests/pipeline/durable/test_store_task_envelope.py` (mirror `tests/pipeline/durable/test_store_creation_ceiling.py` — copy its `pool` fixture + `_task` helper, adding `task_envelope`):

```python
"""E2-S3 — DurableTaskStore round-trips task_envelope; NULL ⇄ None."""

from __future__ import annotations

from datetime import UTC, datetime

from stackowl.authz import BoundsSpec
from stackowl.pipeline.durable.task import DurableTask
# reuse the pool/store fixture from test_store_creation_ceiling.py


def _task(task_id: str, envelope: BoundsSpec | None) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id, owner_id="principal-default", goal="g", status="running",
        owl_name="o", channel="cli", task_envelope=envelope,
        created_at=now, updated_at=now,
    )


async def test_roundtrips_envelope(store) -> None:  # noqa: ANN001
    env = BoundsSpec(tools=frozenset({"a", "tool_search"}))
    await store.create(_task("task-env-1", env))
    assert (await store.get("task-env-1")).task_envelope == env


async def test_none_envelope_is_sql_null(store, db_pool) -> None:  # noqa: ANN001
    await store.create(_task("task-env-2", None))
    rows = await db_pool.fetch_all("SELECT task_envelope FROM tasks WHERE task_id = ?", ("task-env-2",))
    assert rows[0]["task_envelope"] is None
    assert (await store.get("task-env-2")).task_envelope is None
```

Run → EXPECT FAIL.

- [ ] **Step 4: Wire the store** — in `src/stackowl/pipeline/durable/store.py`:
  - add `task_envelope` to `_SELECT_FIELDS` (next to `creation_ceiling`);
  - in `create()`'s column dict: `"task_envelope": (task.task_envelope.model_dump_json() if task.task_envelope is not None else None),`
  - in `_row_to_task()`: `raw_env = row.get("task_envelope")` then `envelope = BoundsSpec.model_validate_json(str(raw_env)) if raw_env is not None else None`, and pass `task_envelope=envelope` to `DurableTask(...)`.

- [ ] **Step 5: Run + durable regression**

Run: `uv run pytest tests/pipeline/durable/test_store_task_envelope.py tests/pipeline/durable/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add v2/src/stackowl/db/migrations/0049_tasks_task_envelope.sql v2/src/stackowl/pipeline/durable/task.py v2/src/stackowl/pipeline/durable/store.py v2/tests/pipeline/durable/test_store_task_envelope.py
git commit -m "feat(v2): persist durable task_envelope (migration 0049, Epic2 S3)"
```

---

## Task 6: Plan at creation + restore on resume

**Files:**
- Modify: `src/stackowl/pipeline/durable/task_runner.py`, `src/stackowl/pipeline/durable/recovery.py`
- Test: `tests/pipeline/durable/test_runner_envelope_plan.py`, `tests/pipeline/durable/test_recovery_envelope.py`

- [ ] **Step 1: Failing runner test** — `tests/pipeline/durable/test_runner_envelope_plan.py` (mirror `test_runner_ceiling_snapshot.py`'s real store + `_FakeBackend` capturing `ran_with`):

```python
"""E2-S3 — runner.run computes + persists task_envelope; fail-open → None."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
# reuse real DurableTaskStore + a _FakeBackend(capture) + a tool registry with tools


class _StubPlanner:
    def __init__(self, result):  # noqa: ANN001
        self._r = result

    async def plan(self, goal, owl_bounds, catalog):  # noqa: ANN001
        return self._r


async def test_run_sets_and_persists_envelope(monkeypatch) -> None:  # noqa: ANN001
    env = BoundsSpec(tools=frozenset({"a", "tool_search", "tool_describe"}))
    # Inject the stub planner into the runner. If runner builds its own
    # PreflightPlanner, monkeypatch the constructor it uses (e.g. patch
    # `stackowl.pipeline.durable.task_runner.PreflightPlanner` to return _StubPlanner(env)).
    ...
    # drive runner.run(goal="g", state=_state()) with a registry-bound store + fake backend
    # assert store.created.task_envelope == env AND backend.ran_with.task_envelope == env


async def test_run_failopen_envelope_none(monkeypatch) -> None:  # noqa: ANN001
    # planner returns None → task_envelope None on both task + state, no raise
    ...
```

> Decide the injection seam when you read `task_runner.py`: cleanest is to construct `PreflightPlanner(ToolProposer(get_services().provider_registry))` inside `run`, so the test monkeypatches `stackowl.pipeline.durable.task_runner.PreflightPlanner`/`ToolProposer`. Fill the `...` against the real fixtures.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/durable/test_runner_envelope_plan.py -v`
Expected: FAIL.

- [ ] **Step 3: Compute in `task_runner.run`** — add imports:
```python
from stackowl.pipeline.planner import PreflightPlanner, ToolProposer
from stackowl.pipeline.services import get_services
```
In `run`, after `creation_ceiling` is resolved and BEFORE `self._store.create(...)`:
```python
        # E2-S3 — preflight plan a least-privilege envelope (durable tasks only).
        # Best-effort, fail-open: any failure → None → byte-for-byte S2 (no plan).
        task_envelope = None
        try:
            services = get_services()
            tool_registry = services.tool_registry
            if tool_registry is not None and services.provider_registry is not None:
                catalog = [(t.name, t.description) for t in tool_registry.all()]
                planner = PreflightPlanner(ToolProposer(services.provider_registry))
                task_envelope = await planner.plan(goal, creation_ceiling, catalog)
        except Exception as exc:  # noqa: BLE001 — never block task creation on planning
            log.tasks.warning("[tasks] runner.run: planner failed — no envelope", exc_info=exc)
            task_envelope = None
```
Add `task_envelope=task_envelope,` to the `DurableTask(...)` constructor, and to the durable-state evolve:
```python
        durable_state = state.evolve(
            task_id=task_id, durable_owner_id=owner_id,
            creation_ceiling=creation_ceiling, task_envelope=task_envelope,
        )
```
(`creation_ceiling` is passed as `owl_bounds` to the planner — for a bounded owl it equals the owl's bounds; the honesty guard only needs *an* owl-bounds reference. If `creation_ceiling` is None (unbounded owl), the guard still passes for a tools-only envelope.)

- [ ] **Step 4: Run runner test → PASS.** `uv run pytest tests/pipeline/durable/test_runner_envelope_plan.py -v`

- [ ] **Step 5: Failing recovery test** — `tests/pipeline/durable/test_recovery_envelope.py` (mirror `test_recovery_ceiling.py`):

```python
"""E2-S3 — recovery restores task_envelope; resume does NOT re-plan."""

from __future__ import annotations

from stackowl.authz import BoundsSpec


async def test_reconstruct_restores_envelope(recovery, store) -> None:  # noqa: ANN001
    env = BoundsSpec(tools=frozenset({"a", "tool_search"}))
    await _seed_running_task(store, "task-renv-1", task_envelope=env)  # mirror sibling seeding
    state = await recovery._reconstruct_state(await store.get("task-renv-1"))
    assert state.task_envelope == env


async def test_reconstruct_null_envelope_is_none(recovery, store) -> None:  # noqa: ANN001
    await _seed_running_task(store, "task-renv-2", task_envelope=None)
    state = await recovery._reconstruct_state(await store.get("task-renv-2"))
    assert state.task_envelope is None
```

- [ ] **Step 6: Thread in `recovery._reconstruct_state`** — add `task_envelope=task.task_envelope` to BOTH `base.evolve(...)` branches (alongside `creation_ceiling`). Run → PASS.

- [ ] **Step 7: Durable regression**

Run: `uv run pytest tests/pipeline/durable/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add v2/src/stackowl/pipeline/durable/task_runner.py v2/src/stackowl/pipeline/durable/recovery.py v2/tests/pipeline/durable/test_runner_envelope_plan.py v2/tests/pipeline/durable/test_recovery_envelope.py
git commit -m "feat(v2): plan least-privilege envelope at creation + restore on resume (Epic2 S3)"
```

---

## Task 7: Drift telemetry at the seam (observe-only)

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py`
- Test: `tests/pipeline/steps/test_execute_drift_telemetry.py`

- [ ] **Step 1: Write the failing test** — `tests/pipeline/steps/test_execute_drift_telemetry.py`. Mirror `tests/authz/test_bounds_dispatch.py`'s `_drive` harness (real `_run_with_tools`, two recording tools, owl registry, `set_services`). Drive a state with `task_envelope` set to allow only `allowed_tool`; the scripted owl calls both; assert the off-plan tool STILL RUNS (no block) and a drift WARNING was logged for it (capture via `caplog` or a recording log handler the repo provides).

```python
async def test_off_plan_tool_runs_and_is_audited(caplog) -> None:  # noqa: ANN001
    # owl permits both; envelope lists only allowed_tool. forbidden_tool is OFF-PLAN.
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    envelope = BoundsSpec(tools=frozenset({"allowed_tool"}))
    allowed, forbidden, provider = await _drive(owl_bounds, task_envelope=envelope)
    assert allowed.executed is True
    assert forbidden.executed is True          # OBSERVE-ONLY: not blocked
    assert any("off-plan tool used" in r.message or "drift" in r.message.lower() for r in caplog.records)


async def test_on_plan_tool_no_drift_event(caplog) -> None:  # noqa: ANN001
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    envelope = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))  # both on-plan
    await _drive(owl_bounds, task_envelope=envelope)
    assert not any("drift" in r.message.lower() for r in caplog.records)


async def test_no_envelope_no_drift_event(caplog) -> None:  # noqa: ANN001
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    await _drive(owl_bounds, task_envelope=None)
    assert not any("drift" in r.message.lower() for r in caplog.records)
```

> Extend `_drive` (copy from `test_bounds_dispatch.py`) to accept `task_envelope` and set it on the state. Confirm how this repo asserts logs — if `caplog` doesn't capture the structured logger, use the repo's log-capture helper (grep tests for `caplog`/`log_records`); adapt the assertion to the real capture surface. If logs are truly uncapturable in a unit test, instead assert on a recorded `drift_audited` set exposed on the returned state, and add that to the implementation.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/steps/test_execute_drift_telemetry.py -v`
Expected: FAIL — no drift event yet (and forbidden_tool may currently be blocked if S2 enforcement folded the envelope — but Task 1 removed that, so it runs; the missing piece is the telemetry).

- [ ] **Step 3: Emit drift telemetry in `_dispatch`**

In `src/stackowl/pipeline/steps/execute.py::_run_with_tools`, near the `denied_this_run: set[str] = set()` declaration, add:
```python
    drift_audited: set[str] = set()  # E2-S3 — off-plan tools already drift-logged this run
```
In `_dispatch`, AFTER the bounds check PERMITS the tool (i.e. after `bounds_block` is confirmed `None`) and BEFORE the consent gate, add:
```python
        # E2-S3 — drift telemetry (OBSERVE-ONLY). A durable task carries a
        # least-privilege task_envelope; a tool outside it still runs (the hard
        # boundary is owl∩ceiling, already checked) but is logged once as drift.
        # Honest-case telemetry, NOT adversarial detection.
        te = state.task_envelope
        if te is not None and te.tools is not None and name not in te.tools and name not in drift_audited:
            drift_audited.add(name)
            log.engine.warning(
                "[authz] drift: off-plan tool used",
                extra={"_fields": {"tool": name, "owl": state.owl_name, "trace_id": state.trace_id}},
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/steps/test_execute_drift_telemetry.py tests/authz/test_bounds_dispatch.py -v`
Expected: PASS (and S2 dispatch tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/steps/execute.py v2/tests/pipeline/steps/test_execute_drift_telemetry.py
git commit -m "feat(v2): off-plan drift telemetry at the dispatch seam (observe-only, Epic2 S3)"
```

---

## Task 8: Wire presentation `restrict_to` into the seam

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py`
- Test: extend `tests/pipeline/steps/test_execute_drift_telemetry.py` (or a new presentation-wiring test)

- [ ] **Step 1: Write the failing test** — assert the schema the provider receives is restricted when an envelope is set, and unrestricted (parity) when it's None. The `_drive` harness's `_TwoToolProvider` records the `tool_schemas` it was given (extend the fake provider to capture `tool_schemas` from `complete_with_tools`):

```python
async def test_presentation_restricted_when_envelope_set() -> None:
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    envelope = BoundsSpec(tools=frozenset({"allowed_tool"}))
    _a, _f, provider = await _drive(owl_bounds, task_envelope=envelope)
    presented = _schema_names(provider.seen_schemas)   # helper extracting names
    assert "forbidden_tool" not in presented           # off-plan hidden
    assert "allowed_tool" in presented


async def test_presentation_parity_when_no_envelope() -> None:
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    _a, _f, provider = await _drive(owl_bounds, task_envelope=None)
    presented = _schema_names(provider.seen_schemas)
    assert {"allowed_tool", "forbidden_tool"} <= presented  # full toolset (S2 parity)
```

> The `_TwoToolProvider` in `test_bounds_dispatch.py` receives `tool_schemas=` in `complete_with_tools`; have it store them as `self.seen_schemas`. Note: the always-present discovery tools (`tool_search`/`tool_describe`) may or may not be registered in this harness — if they aren't, presentation will still include only the restricted set; assert on `allowed_tool`/`forbidden_tool` membership which is sufficient.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/steps/test_execute_drift_telemetry.py -v -k presentation`
Expected: FAIL — restrict_to not wired; forbidden_tool still presented.

- [ ] **Step 3: Pass `restrict_to` in `_run_with_tools`**

In `src/stackowl/pipeline/steps/execute.py::_run_with_tools`, find the `tool_schemas = tool_registry.to_provider_schema(provider.protocol, profile=profile, pins=pins)` call and change it to:
```python
        # E2-S3 — least-privilege presentation: when the task has a planned
        # envelope, restrict the presented set to plan ∪ discovery (drift
        # prevention). None envelope → restrict_to=None → byte-for-byte S2.
        restrict_to = state.task_envelope.tools if state.task_envelope is not None else None
        tool_schemas = tool_registry.to_provider_schema(
            provider.protocol, profile=profile, pins=pins, restrict_to=restrict_to
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/steps/test_execute_drift_telemetry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/pipeline/steps/execute.py v2/tests/pipeline/steps/test_execute_drift_telemetry.py
git commit -m "feat(v2): wire least-privilege presentation restrict_to at the seam (Epic2 S3)"
```

---

## Task 9: Gateway journey — hide off-plan, surface via tool_search, audit on use

**Files:**
- Test: `tests/journeys/test_preflight_envelope.py`

- [ ] **Step 1: Write the journey** — mirror `tests/journeys/test_tool_scope_envelope.py` scaffolding (real adapter→scanner→AsyncioBackend; scripted owl as only mock). Set `state.task_envelope` to allow only `allowed_tool` (+ discovery). The scripted owl calls the off-plan `forbidden_tool` directly. Assert:
  - `forbidden_tool` is hidden from the presented schema the provider saw,
  - but when called it **runs** (boundary permits) and a **drift telemetry** event fired,
  - `allowed_tool` ran, the turn delivered a reply.

```python
async def test_durable_envelope_hides_offplan_audits_on_use(caplog) -> None:  # noqa: ANN001
    owl_bounds = BoundsSpec(tools=frozenset({_ALLOWED_TOOL, _FORBIDDEN_TOOL}))
    env = _build(_ScriptedBoundedOwl(), bounds=owl_bounds)
    reply = await _turn(env, "do the task", task_envelope=BoundsSpec(tools=frozenset({_ALLOWED_TOOL})))
    assert env.allowed.runs == 1
    assert env.forbidden.runs == 1                    # OBSERVE-ONLY: off-plan still runs
    assert any("drift" in r.message.lower() for r in caplog.records)
    assert _REPLY_FRAGMENT in reply
```

> Extend `_turn` to thread `task_envelope` into the `PipelineState(...)` it builds. If the journey provider captures presented schemas, add an assertion that `_FORBIDDEN_TOOL` was absent from the presented set. Use the repo's real log-capture surface for the drift assertion (mirror Task 7).

- [ ] **Step 2: Run the journey**

Run: `uv run pytest tests/journeys/test_preflight_envelope.py -v`
Expected: PASS

- [ ] **Step 3: Full feature regression + lint/type**

Run:
```bash
uv run pytest tests/pipeline/planner/ tests/tools/test_presentation_restrict_to.py tests/pipeline/test_authz_compose.py tests/pipeline/durable/ tests/pipeline/steps/test_execute_drift_telemetry.py tests/journeys/test_preflight_envelope.py tests/journeys/test_tool_scope_envelope.py tests/authz/ -v
uv run ruff check src/stackowl/pipeline/planner src/stackowl/pipeline/authz_compose.py src/stackowl/pipeline/steps/execute.py src/stackowl/tools/_infra/presentation.py src/stackowl/tools/registry.py src/stackowl/pipeline/durable
uv run mypy src/stackowl/pipeline/planner src/stackowl/pipeline/authz_compose.py
```
Expected: all green. Fix any finding before committing.

- [ ] **Step 4: Commit**

```bash
git add v2/tests/journeys/test_preflight_envelope.py
git commit -m "test(v2): gateway journey — least-privilege presentation + drift audit (Epic2 S3)"
```

---

## Definition of Done

- [ ] Enforcement unchanged (`owl ∩ creation_ceiling`); `task_envelope` never affects the boundary; all S2 tests green.
- [ ] `PreflightPlanner` is single-verdict (trustworthy non-empty `BoundsSpec` | `None`); proposer-empty → `None`; hallucinations dropped (exact, no fuzzy).
- [ ] Envelope computed once at creation, persisted (migration 0049), restored on resume — zero re-plan / zero planner calls on the resume path.
- [ ] Presentation: `restrict_to` hides off-plan tools (incl. base set) but keeps discovery; `is not None` (empty ≠ fallback); `always_present` non-evictable; fail-open parity when `None`.
- [ ] Drift telemetry fires once per off-plan tool, observe-only (tool still runs); never fires without an envelope.
- [ ] Gateway journey: off-plan hidden → runs on direct call → audited; reply delivered.
- [ ] ruff + mypy clean on touched modules; each task committed separately.

## Out of scope (spec §8)

Adversarial injection detection (E2-S4); embedding floor (data-driven, later); per-child planning; non-durable planning; fs/network/data axes (Epic 3).
