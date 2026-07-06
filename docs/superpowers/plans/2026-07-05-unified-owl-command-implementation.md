# Unified `/owl` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the three overlapping owl/agent surfaces (`/owls`, the `/owl` alias, and `/agent`) into a single `/owl` dispatcher whose every mutating subcommand funnels through the one `owl_build` engine, adding `pause`/`resume`, a free-text `boundaries` guardrail, and a preset `evolution_strategy`.

**Architecture:** `owl_build.OwlBuildTool.execute()` stays the single implementation for all owl mutation (create/edit/rename/retire/pause/resume); both chat (NL tool call) and the `/owl` slash command build one `OwlBuildSpec` and call it — there is only ever one code path from "user intent" to "persisted owl". Scheduler pause/resume reuse the existing `JobScheduler.pause`/`resume` primitives on the owl's deterministic owned job row (`_job_id_for(name)`); no new manifest `paused` field. `boundaries` folds into the rendered system prompt at the one `DNAPromptInjector.inject` seam; `evolution_strategy` scales the finalized per-trait deltas at the one chokepoint inside `EvolutionCoordinator._evolve_one`.

**Tech Stack:** Python 3.13, pydantic / pydantic-settings (frozen models), pytest / pytest-asyncio, ruff, mypy (strict), uv.

## Global Constraints
- Run every verification with `uv run` (`uv run pytest <file>::<test>`, `uv run ruff check src/`, `uv run mypy src/`). NEVER run the full test suite — it hangs on this box; always scope pytest to the specific affected file(s).
- 4-point logging (entry / decision / step / exit) on every new `execute`-class method; never leave an `except` silent — `log.<module>.error(..., exc_info=err, extra={"_fields": {...}})`.
- Frozen pydantic models: mutate only via `model_copy(update=...)`; new fields are additive + default-safe so every existing owl and every existing `OwlBuildSpec` caller loads byte-identical.
- Minimal root-cause diffs. Reuse existing helpers/primitives (`JobScheduler.pause/resume`, `_job_id_for`, `manifest_to_yaml_entry`, `build_agent_manifest`, `DNAPromptInjector.inject`); do not add a second persistence, consent, DNA-capture, or scheduler-poke path.
- Each task ends green (its own targeted tests pass) before the next begins; commit at each task boundary. Deletion (Task 7) is gated on the retargeted suites (Task 6) being green — capability loss is proven absent by tests, not by inspection.
- Line numbers below were read on 2026-07-05 and may have shifted; re-`grep`/`Read` the anchor before editing.

---

### Task 1: Schema fields (`boundaries`, `evolution_strategy`) + free-text-create name fix + forge + YAML round-trip

**Files:**
- Modify: `src/stackowl/owls/manifest.py:91-92` (insert two fields after `creation_ceiling`)
- Modify: `src/stackowl/tools/meta/owl_build_spec.py:22` (`name` → optional) and `:40-42` (add two fields after `lifecycle`)
- Modify: `src/stackowl/tools/meta/owl_build_authz.py:127-138` (forge: carry both fields onto the manifest)
- Modify: `src/stackowl/commands/owls_helpers.py:370-372` (`manifest_to_yaml_entry`: write both when non-default)
- Modify: `src/stackowl/tools/meta/owl_build.py:216-249` (add both to the tool `parameters` schema)
- Test: `tests/tools/meta/test_owl_build_spec.py`, `tests/tools/meta/test_owl_build_authz.py`, `tests/commands/test_owl_new_fields.py` (new)

**Interfaces:**
- Produces: `OwlAgentManifest.boundaries: str = ""`, `OwlAgentManifest.evolution_strategy: Literal["conservative","adaptive","experimental"] = "adaptive"` (consumed by Task 2).
- Produces: `OwlBuildSpec.boundaries: str | None = None`, `OwlBuildSpec.evolution_strategy: Literal["conservative","adaptive","experimental"] | None = None`, `OwlBuildSpec.name: str = ""` (consumed by Tasks 3, 4, 5).
- Produces: `manifest_to_yaml_entry(manifest)` writes `boundaries`/`evolution_strategy` keys when non-default (consumed by round-trip persistence).
- Consumes: existing `build_agent_manifest(spec, *, creator, parent_ceiling, registry) -> tuple[OwlAgentManifest, frozenset[str]]`, `validate_owl_build_spec(spec) -> str | MissingFields | None`, `_reg_with(name, bounds)` test helper.

- [ ] **Step 1: Write the failing tests**

`tests/commands/test_owl_new_fields.py` (new):
```python
"""Task 1 — boundaries + evolution_strategy schema round-trip, and the
free-text-create name fix (name optional so elicitation can ask for it)."""
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.tools.meta.owl_build_spec import (
    MissingFields,
    OwlBuildSpec,
    validate_owl_build_spec,
)


def test_manifest_defaults_are_additive() -> None:
    m = OwlAgentManifest(name="x", role="r", system_prompt="p", model_tier="fast")
    assert m.boundaries == ""
    assert m.evolution_strategy == "adaptive"


def test_yaml_entry_round_trips_boundaries_and_strategy() -> None:
    m = OwlAgentManifest(
        name="x", role="r", system_prompt="p", model_tier="fast",
        boundaries="never share raw urls", evolution_strategy="experimental",
    )
    entry = manifest_to_yaml_entry(m)
    assert entry["boundaries"] == "never share raw urls"
    assert entry["evolution_strategy"] == "experimental"
    back = OwlAgentManifest.model_validate(entry)
    assert back.boundaries == "never share raw urls"
    assert back.evolution_strategy == "experimental"


def test_yaml_entry_omits_defaults() -> None:
    m = OwlAgentManifest(name="x", role="r", system_prompt="p", model_tier="fast")
    entry = manifest_to_yaml_entry(m)
    assert "boundaries" not in entry
    assert "evolution_strategy" not in entry


def test_spec_accepts_boundaries_and_strategy() -> None:
    s = OwlBuildSpec(
        action="create", name="x", preset="researcher", specialty="z",
        boundaries="no raw urls", evolution_strategy="conservative",
    )
    assert s.boundaries == "no raw urls"
    assert s.evolution_strategy == "conservative"
    assert validate_owl_build_spec(s) is None


def test_spec_name_optional_lets_freetext_create_elicit_name() -> None:
    # Regression: `name: str` (required) made `/owls create <text>` fail spec
    # construction BEFORE elicitation. Now name defaults to "" so the validator
    # reports it as a recoverable MissingField the tool can ASK for.
    s = OwlBuildSpec(action="create", specialty="a research owl")
    assert s.name == ""
    check = validate_owl_build_spec(s)
    assert isinstance(check, MissingFields)
    assert "name" in check.fields
```

Append to `tests/tools/meta/test_owl_build_authz.py`:
```python
def test_build_agent_manifest_carries_boundaries_and_strategy() -> None:
    reg = _reg_with("secretary", None)
    spec = OwlBuildSpec(
        action="create", name="scout", preset="researcher", specialty="recon",
        boundaries="never share raw urls", evolution_strategy="experimental",
    )
    manifest, _ = build_agent_manifest(
        spec, creator="secretary", parent_ceiling=None, registry=reg
    )
    assert manifest.boundaries == "never share raw urls"
    assert manifest.evolution_strategy == "experimental"
```

- [ ] **Step 2: Run tests to verify they fail**
Run: `uv run pytest tests/commands/test_owl_new_fields.py tests/tools/meta/test_owl_build_authz.py::test_build_agent_manifest_carries_boundaries_and_strategy -v`
Expected: FAIL — `test_manifest_defaults_are_additive` errors with `AttributeError: 'OwlAgentManifest' object has no attribute 'boundaries'`; `test_spec_name_optional...` errors with pydantic `ValidationError` (name required); the forge test fails the boundaries assertion.

- [ ] **Step 3: Write minimal implementation**

`src/stackowl/owls/manifest.py` — insert after line 91 (`creation_ceiling: BoundsSpec | None = None`), before the `@property display`:
```python
    creation_ceiling: BoundsSpec | None = None  # creator's effective bounds at mint
    # Free-text behavioural guardrail folded into the rendered system prompt
    # (e.g. "has web_fetch but never share raw URLs with the user"). Additive +
    # defaulted → every existing owl loads byte-identical. See DNAPromptInjector.
    boundaries: str = ""
    # Per-owl evolution aggressiveness (design decision 3). Scales the mutation
    # deltas the EvolutionCoordinator applies. Defaulted to "adaptive" (1× — the
    # current behaviour) so existing owls evolve exactly as before.
    evolution_strategy: Literal["conservative", "adaptive", "experimental"] = "adaptive"
```
(`Literal` is already imported at the top of the file.)

`src/stackowl/tools/meta/owl_build_spec.py` — change line 21-22 and add fields after line 42:
```python
    action: Literal["create", "edit", "retire", "rename"]
    # name defaults to "" (not required): a free-text `/owl create <sentence>`
    # constructs a spec with no name so the validator reports it as a recoverable
    # MissingField and the tool ASKS for it (elicitation). Every action's own
    # hard-error branch in validate_owl_build_spec still rejects an empty name.
    name: str = ""
```
and after the `lifecycle: ... | None = None` line:
```python
    lifecycle: Literal["on_demand", "scheduled"] | None = None
    # Free-text behavioural guardrail (design decision 4) — distinct from tool
    # grants; forwarded to the manifest and folded into the system prompt.
    boundaries: str | None = None
    # Preset evolution aggressiveness (design decision 3). All optional +
    # default-safe: an on-demand create omitting both is byte-identical.
    evolution_strategy: Literal["conservative", "adaptive", "experimental"] | None = None
```

`src/stackowl/tools/meta/owl_build_authz.py` — in `build_agent_manifest`, insert after the schedule block (after line ~137, before `manifest = built.model_copy(update=update)`):
```python
    # Design decisions 3 & 4 — carry the guardrail + evolution preset onto the
    # manifest (single path: both chat and /owl create reach this one forge).
    boundaries = (spec.boundaries or "").strip()
    if boundaries:
        update["boundaries"] = boundaries
    if spec.evolution_strategy is not None:
        update["evolution_strategy"] = spec.evolution_strategy
    manifest = built.model_copy(update=update)
```

`src/stackowl/commands/owls_helpers.py` — in `manifest_to_yaml_entry`, after the `if manifest.display_name:` block (line ~370-371):
```python
    if manifest.display_name:
        entry["display_name"] = manifest.display_name
    # Additive: written only when non-default so existing owls keep byte-identical
    # yaml entries (mirrors the display_name/lifecycle conditional-write pattern).
    if manifest.boundaries:
        entry["boundaries"] = manifest.boundaries
    if manifest.evolution_strategy != "adaptive":
        entry["evolution_strategy"] = manifest.evolution_strategy
```

`src/stackowl/tools/meta/owl_build.py` — add two properties to the `parameters` dict (inside `"properties"`, e.g. after the `lifecycle` entry, before the closing `}` and `"required"`):
```python
                "boundaries": {
                    "type": "string",
                    "description": (
                        "Optional behavioural guardrail folded into the owl's "
                        "system prompt (e.g. 'has web_fetch but never shares raw "
                        "URLs with the user'). Distinct from tool grants."
                    ),
                },
                "evolution_strategy": {
                    "type": "string",
                    "enum": ["conservative", "adaptive", "experimental"],
                    "description": (
                        "Optional. How aggressively this owl's DNA evolves over "
                        "time: conservative (slow), adaptive (default), or "
                        "experimental (fast)."
                    ),
                },
```

- [ ] **Step 4: Run tests to verify they pass**
Run: `uv run pytest tests/commands/test_owl_new_fields.py tests/tools/meta/test_owl_build_authz.py tests/tools/meta/test_owl_build_spec.py -v`
Expected: PASS (all, including the pre-existing authz/spec tests — the new fields are additive).

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/owls/manifest.py src/stackowl/tools/meta/owl_build_spec.py src/stackowl/tools/meta/owl_build_authz.py src/stackowl/commands/owls_helpers.py src/stackowl/tools/meta/owl_build.py tests/commands/test_owl_new_fields.py tests/tools/meta/test_owl_build_authz.py
git commit -m "feat(owl): add boundaries + evolution_strategy schema, make spec name optional for free-text create"
```

---

### Task 2: Wire `boundaries` into the system prompt + `evolution_strategy` into mutation scaling

**Files:**
- Modify: `src/stackowl/owls/dna_injector.py:83-119` (`DNAPromptInjector.inject` — fold `boundaries` into the base prompt)
- Modify: `src/stackowl/owls/evolution.py:60-62` (add `_EVOLUTION_STRATEGY_FACTOR` + `_scale_deltas`) and `:282-289` (scale deltas in `_evolve_one`)
- Test: `tests/owls/test_boundaries_injection.py` (new), `tests/owls/test_evolution_strategy_scaling.py` (new)

**Interfaces:**
- Consumes: `OwlAgentManifest.boundaries`, `OwlAgentManifest.evolution_strategy` (from Task 1).
- Produces: `_scale_deltas(deltas: dict[str, float], strategy: str) -> dict[str, float]` (module-level in `evolution.py`); `DNAPromptInjector.inject(manifest, dna, *, lean=False) -> str` unchanged signature, now boundaries-aware.

- [ ] **Step 1: Write the failing tests**

`tests/owls/test_boundaries_injection.py` (new):
```python
"""Task 2 — boundaries fold into the rendered system prompt (neutral DNA)."""
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.manifest import OwlAgentManifest


def _owl(**kw: object) -> OwlAgentManifest:
    base = dict(name="x", role="r", system_prompt="base prompt", model_tier="fast")
    base.update(kw)
    return OwlAgentManifest(**base)  # type: ignore[arg-type]


def test_inject_folds_boundaries_when_present() -> None:
    out = DNAPromptInjector().inject(_owl(boundaries="never share raw URLs"), OwlDNA())
    assert "base prompt" in out
    assert "Boundaries: never share raw URLs" in out


def test_inject_without_boundaries_is_byte_identical() -> None:
    # Neutral DNA + no boundaries → the raw system prompt, unchanged.
    assert DNAPromptInjector().inject(_owl(), OwlDNA()) == "base prompt"
```

`tests/owls/test_evolution_strategy_scaling.py` (new):
```python
"""Task 2 — evolution_strategy scales the finalized per-trait deltas."""
from stackowl.owls.evolution import _scale_deltas


def test_conservative_halves() -> None:
    assert _scale_deltas({"curiosity": 0.2}, "conservative") == {"curiosity": 0.1}


def test_experimental_doubles() -> None:
    assert _scale_deltas({"curiosity": 0.2}, "experimental") == {"curiosity": 0.4}


def test_adaptive_is_unchanged_identity() -> None:
    d = {"curiosity": 0.2}
    assert _scale_deltas(d, "adaptive") is d  # 1× → no new dict allocated


def test_unknown_strategy_is_unchanged_identity() -> None:
    d = {"curiosity": 0.2}
    assert _scale_deltas(d, "bogus") is d
```

- [ ] **Step 2: Run tests to verify they fail**
Run: `uv run pytest tests/owls/test_boundaries_injection.py tests/owls/test_evolution_strategy_scaling.py -v`
Expected: FAIL — `test_inject_folds_boundaries_when_present` fails the `"Boundaries: ..."` assertion; the scaling tests error with `ImportError: cannot import name '_scale_deltas'`.

- [ ] **Step 3: Write minimal implementation**

`src/stackowl/owls/dna_injector.py` — inside `inject`, compute a boundaries-aware base once, then use it in both return paths. Replace the body from the `directives: list[str] = []` line through the final `return result`:
```python
        # Fold the behavioural guardrail into the base prompt FIRST (design
        # decision 4), so it survives whether or not DNA also modulates. Empty
        # boundaries → byte-identical to the prior behaviour.
        base = manifest.system_prompt
        boundaries = (manifest.boundaries or "").strip()
        if boundaries:
            base = f"{manifest.system_prompt}\n\nBoundaries: {boundaries}"

        directives: list[str] = []
        for trait, directive in _HIGH_DIRECTIVES:
            if lean and trait in _LEAN_SUPPRESSED_TRAITS:
                continue
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.high_state(manifest.name, trait, value):
                directives.append(directive)
        for trait, directive in _LOW_DIRECTIVES:
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.low_state(manifest.name, trait, value):
                directives.append(directive)
        if not directives:
            log.engine.debug(
                "[dna] injector.inject: exit — no modulation",
                extra={"_fields": {"owl": manifest.name, "lean": lean}},
            )
            return base
        joined = "\n- ".join(directives)
        result = f"{base}\n\nBehavioural modulation (from owl DNA):\n- {joined}"
        log.engine.debug(
            "[dna] injector.inject: exit — directives appended",
            extra={"_fields": {"owl": manifest.name, "lean": lean, "directive_count": len(directives)}},
        )
        return result
```

`src/stackowl/owls/evolution.py` — add near line 61 (after `_EVOLUTION_RETRY_BACKOFF_SECONDS`):
```python
# Design decision 3 — per-owl evolution aggressiveness. Scales the FINALIZED
# per-trait deltas before they are applied: conservative halves drift,
# experimental doubles it, adaptive (the default) is unchanged. bound_dna still
# clamps the resulting DNA, so experimental can never breach the safe governor
# band — this only tunes how fast the owl moves within it.
_EVOLUTION_STRATEGY_FACTOR: dict[str, float] = {
    "conservative": 0.5,
    "adaptive": 1.0,
    "experimental": 2.0,
}


def _scale_deltas(deltas: dict[str, float], strategy: str) -> dict[str, float]:
    """Scale each trait delta by the owl's evolution strategy. Returns the input
    unchanged (same object) for the 1× / unknown-strategy case (no allocation)."""
    factor = _EVOLUTION_STRATEGY_FACTOR.get(strategy, 1.0)
    if factor == 1.0:
        return deltas
    return {trait: delta * factor for trait, delta in deltas.items()}
```
Then in `_evolve_one`, right after the `if not deltas:` guard (the block ending `return False`) and before `checkpoint_id = await self._checkpointer.checkpoint(...)`:
```python
        # Apply the owl's evolution strategy to the finalized deltas (single
        # chokepoint — uniform whether the deltas came from attribution or LLM).
        deltas = _scale_deltas(deltas, manifest.evolution_strategy)
        log.engine.debug(
            "[dna] coordinator.evolve_one: deltas scaled by evolution strategy",
            extra={"_fields": {
                "owl": manifest.name, "strategy": manifest.evolution_strategy,
                "n_deltas": len(deltas),
            }},
        )
        # 3. STEP — checkpoint + apply mutations
        checkpoint_id = await self._checkpointer.checkpoint(manifest.name, manifest.dna)
```

- [ ] **Step 4: Run tests to verify they pass**
Run: `uv run pytest tests/owls/test_boundaries_injection.py tests/owls/test_evolution_strategy_scaling.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/owls/dna_injector.py src/stackowl/owls/evolution.py tests/owls/test_boundaries_injection.py tests/owls/test_evolution_strategy_scaling.py
git commit -m "feat(owl): fold boundaries into system prompt + scale evolution by strategy"
```

---

### Task 3: `owl_build` `pause`/`resume` actions

**Files:**
- Modify: `src/stackowl/tools/meta/owl_build_spec.py:21` (`action` Literal + `+2` and merge validator branch at `:87-90`)
- Modify: `src/stackowl/tools/meta/owl_build.py:60` (`_VALID_ACTIONS`), `:186-190` (parameters enum + description), `:319-325` (dispatch), and add `_pause`/`_resume`/`_toggle_schedule` methods
- Test: `tests/tools/meta/test_owl_build_spec.py`, `tests/tools/meta/test_owl_build_pause_resume.py` (new)

**Interfaces:**
- Consumes: `JobScheduler(db=..., tz=...).pause(job_id)`/`.resume(job_id)`, `_job_id_for(name) -> str` (imported privately — the established pattern already used at `owl_build.py:423`/`:451`), `get_services().owl_registry`/`.db_pool`/`.settings`, `OwlBuildSpec.name`.
- Produces: `OwlBuildSpec.action` includes `"pause"`,`"resume"`; `_VALID_ACTIONS = ("create","edit","retire","rename","pause","resume")`; `OwlBuildTool._pause(spec, t0)` / `._resume(spec, t0)` returning `ToolResult`.

Decision (documented per design note): import the existing private `_job_id_for` rather than promoting it — `owl_build.py` already imports it in `_scheduled_job_exists`/`_scheduled_next_run`, so reuse is the smaller, drift-free diff and keeps the id derivation single-sourced with the projection writer.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tools/meta/test_owl_build_spec.py`:
```python
def test_pause_needs_only_name() -> None:
    assert validate_owl_build_spec(OwlBuildSpec(action="pause", name="scout")) is None


def test_resume_needs_only_name() -> None:
    assert validate_owl_build_spec(OwlBuildSpec(action="resume", name="scout")) is None


def test_pause_empty_name_rejected() -> None:
    assert validate_owl_build_spec(OwlBuildSpec(action="pause", name="  ")) is not None
```

`tests/tools/meta/test_owl_build_pause_resume.py` (new):
```python
"""Task 3 — owl_build pause/resume reuse the scheduler primitives on the owl's
owned job row, and refuse cleanly for an owl with no schedule."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.tools.meta.owl_build import OwlBuildTool

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _scheduled_owl(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="watcher", system_prompt="p", model_tier="fast",
        lifecycle="scheduled", trigger=CronTrigger(schedule="every 10m", prompt="do it"),
    )


async def _job_row(db: DbPool, job_id: str) -> dict:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert rows, f"expected a job row for {job_id}"
    return rows[0]


async def test_pause_then_resume_toggles_the_owned_row(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(_scheduled_owl("scout"), source_name="t")
    await reconcile_owl_schedules(reg, db)  # project the owned job row
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        paused = await OwlBuildTool().execute(action="pause", name="scout")
        assert paused.success, paused.error
        row = await _job_row(db, _job_id_for("scout"))
        assert int(row["enabled"]) == 0 and row["status"] == "failed"

        resumed = await OwlBuildTool().execute(action="resume", name="scout")
        assert resumed.success, resumed.error
        row = await _job_row(db, _job_id_for("scout"))
        assert int(row["enabled"]) == 1 and row["status"] == "pending"
    finally:
        reset_services(token)


async def test_pause_refuses_on_demand_owl(db: DbPool) -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(name="resty", role="r", system_prompt="p", model_tier="fast"),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        result = await OwlBuildTool().execute(action="pause", name="resty")
        assert not result.success
        assert "no schedule to pause" in result.error
    finally:
        reset_services(token)
```

- [ ] **Step 2: Run tests to verify they fail**
Run: `uv run pytest tests/tools/meta/test_owl_build_pause_resume.py tests/tools/meta/test_owl_build_spec.py::test_pause_needs_only_name -v`
Expected: FAIL — the spec test errors constructing `OwlBuildSpec(action="pause", ...)` (`pause` not in the `action` Literal → `ValidationError`); the pause/resume tests fail because `execute(action="pause")` is rejected as an invalid action.

- [ ] **Step 3: Write minimal implementation**

`src/stackowl/tools/meta/owl_build_spec.py` — extend the Literal (line 21) and merge the retire/pause/resume validator branch (replace the `if spec.action == "retire":` block at line 87-90):
```python
    action: Literal["create", "edit", "retire", "rename", "pause", "resume"]
```
```python
    if spec.action in ("retire", "pause", "resume"):
        # Cadence-only or removal actions need nothing but a name (design
        # decision 2: pause/resume touch no tools/authority/persona).
        if not spec.name or not spec.name.strip():
            return "owl name is required."
        return None
```

`src/stackowl/tools/meta/owl_build.py`:
- Line 60: `_VALID_ACTIONS: tuple[str, ...] = ("create", "edit", "retire", "rename", "pause", "resume")`
- Parameters `action` entry (line 186-190): `"description": "create | edit | retire | rename | pause | resume",`
- Dispatch (replace lines 319-325 in `execute`):
```python
            if spec.action == "create":
                return await self._create(spec, t0)
            if spec.action == "edit":
                return await self._edit(spec, t0)
            if spec.action == "rename":
                return await self._rename(spec, t0)
            if spec.action == "pause":
                return await self._pause(spec, t0)
            if spec.action == "resume":
                return await self._resume(spec, t0)
            return await self._retire(spec, t0)
```
- Add these methods (e.g. after `_rename`, before `_retire`):
```python
    async def _pause(self, spec: OwlBuildSpec, t0: float) -> ToolResult:
        """Pause a scheduled owl's cadence (design decision 2) — suspends the pokes
        without touching persona/DNA/history. Reuses JobScheduler.pause on the owl's
        deterministic owned job row. Refuses (loud, no silent no-op) when the owl
        has no schedule to pause."""
        return await self._toggle_schedule(spec, t0, resume=False)

    async def _resume(self, spec: OwlBuildSpec, t0: float) -> ToolResult:
        """Resume a paused scheduled owl's cadence — the mirror of _pause; the
        schedule continues from wherever it naturally lands."""
        return await self._toggle_schedule(spec, t0, resume=True)

    async def _toggle_schedule(self, spec: OwlBuildSpec, t0: float, *, resume: bool) -> ToolResult:
        op = "resume" if resume else "pause"
        # 1. ENTRY
        log.tool.info(
            "owl_build.execute: toggle schedule",
            extra={"_fields": {"op": op, "name": spec.name}},
        )
        svc = get_services()
        registry = svc.owl_registry
        db = svc.db_pool
        if registry is None or db is None:
            log.tool.error(
                "owl_build.execute: no registry/db wired — cannot toggle schedule",
                exc_info=None,
                extra={"_fields": {"op": op, "name": spec.name}},
            )
            return self._err(f"owl scheduling unavailable — cannot {op} an owl.", t0)
        # 2. DECISION — the owl must exist AND be scheduled (manifest = truth; a
        #    scheduled owl owns exactly one projected job row keyed on _job_id_for).
        try:
            current = registry.get(spec.name)
        except Exception:  # OwlNotFoundError — the not-found path is expected
            return self._err(f"no owl named '{spec.name}' to {op}.", t0)
        if getattr(current, "lifecycle", "on_demand") != "scheduled":
            log.tool.info(
                "owl_build.execute: refusing schedule toggle on on-demand owl",
                extra={"_fields": {"op": op, "name": spec.name}},
            )
            return self._err(f"'{spec.name}' has no schedule to {op}.", t0)
        # 3. STEP — reuse the existing scheduler primitive on the owned job row.
        from stackowl.scheduler.owl_lifecycle import _job_id_for
        from stackowl.scheduler.scheduler import JobScheduler

        settings = svc.settings
        tz = settings.system.timezone if settings is not None else "UTC"
        scheduler = JobScheduler(db=db, tz=tz or "UTC")
        job_id = _job_id_for(spec.name)
        if resume:
            await scheduler.resume(job_id)
        else:
            await scheduler.pause(job_id)
        creator = str(TraceContext.get().get("owl_name") or _SECRETARY_NAME)
        await self._audit(op, spec.name, creator)
        # 4. EXIT
        display = getattr(current, "display", None) or spec.name
        tail = (
            "it will reach you on its schedule again"
            if resume
            else "it won't run again until you resume it"
        )
        return self._ok(f"{'Resumed' if resume else 'Paused'} {display} — {tail}.",
                        t0, extra={"owl": spec.name, "op": op})
```

- [ ] **Step 4: Run tests to verify they pass**
Run: `uv run pytest tests/tools/meta/test_owl_build_pause_resume.py tests/tools/meta/test_owl_build_spec.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/tools/meta/owl_build_spec.py src/stackowl/tools/meta/owl_build.py tests/tools/meta/test_owl_build_pause_resume.py tests/tools/meta/test_owl_build_spec.py
git commit -m "feat(owl_build): add pause/resume actions reusing scheduler primitives"
```

---

### Task 4: `/owl` unified dispatcher (single path to `owl_build` for every mutation)

**Sub-part A (Steps 1-5): edit-scope parity — `can_edit` + `_edit_unbound`.**
Capability-loss gap found while planning: `owl_build._edit` gates on `can_modify`,
which only allows editing an `origin="agent"` owl YOU created — it refuses
`builtin`/`human` owls outright. Today's `/owls edit` CAN change e.g. the
Secretary's `model_tier` (no creator/ceiling check at all — it's a raw
validate-and-persist). Routing `/owl edit` through unmodified `owl_build._edit`
would silently drop that capability the day `/owls` is deleted (Task 7). Fix:
`_edit` gets its OWN gate (`can_edit`, looser for `builtin`/`human`) and, for
those origins, skips the agent-authority ratchet machinery entirely (re-forge/
clamp-against-`creation_ceiling`/consent-on-widening) — that machinery exists to
bound what a MINTED owl can widen to; a `builtin`/`human` owl was never
ceiling-bound in the first place, so running it would incorrectly refuse them
for having no `creation_ceiling` (see the existing fail-closed check at
`owl_build.py:938-948`) instead of just applying the edit.

**Sub-part B (Steps 6-10): the dispatcher itself.**

**Files:**
- Modify: `src/stackowl/tools/meta/owl_build.py` (add `can_edit` near `can_modify`/`can_retire`/`can_rename`; `_edit` branches to a new `_edit_unbound` for `builtin`/`human` origin, unchanged agent-origin path otherwise)
- Modify: `src/stackowl/commands/owls_helpers.py` (add `parse_owl_build_flags`)
- Modify: `src/stackowl/commands/owls_command.py:674-686` (rewrite the `OwlCommand` alias into the unified dispatcher; add `_OWL_META`)
- Modify: `src/stackowl/commands/assembly.py:230-236` (registration unchanged in shape — same deps; only the class behaviour changes, so this is a no-op verification unless the constructor signature drifts)
- Test: `tests/tools/meta/test_owl_build_betters.py` (add `can_edit` cases), `tests/tools/meta/test_owl_build_edit_unbound.py` (new), `tests/commands/test_owl_dispatcher.py` (new)

**Interfaces:**
- Consumes: `OwlBuildTool().execute(action=..., **kwargs) -> ToolResult` (Tasks 1/3), `TraceContext.start(...)`/`reset(...)`, inherited `OwlsCommand._list`/`_dna`/`_reset_dna`/`_health`/`_objectives`/`_objective`/`_objective_cancel`.
- Produces: `can_edit(manifest, *, caller, target_name) -> str | None`; `OwlBuildTool._edit_unbound(spec, current, registry, t0, creator) -> ToolResult`; `parse_owl_build_flags(rest: str) -> dict[str, Any]`; `OwlCommand` whose `command == "owl"` routes create/edit/rename/pause/resume/retire through `owl_build` and list/dna/reset-dna/health/objectives* through the inherited registry surface.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tools/meta/test_owl_build_betters.py`:
```python
def test_can_edit_secretary():
    """Edit's own gate is looser than can_modify for builtin/human — preserves
    /owls edit's historical ability to change the Secretary's tier."""
    assert can_edit(_Owl("builtin", None, "secretary"), caller="secretary", target_name="secretary") is None


def test_can_edit_human_owl():
    assert can_edit(_Owl("human", None, "planner"), caller="secretary", target_name="planner") is None


def test_cannot_edit_another_agents_owl_via_can_edit():
    assert can_edit(_Owl("agent", "other_owl", "scout"), caller="secretary", target_name="scout") is not None


def test_can_edit_own_agent_owl_via_can_edit():
    assert can_edit(_Owl("agent", "secretary", "scout"), caller="secretary", target_name="scout") is None
```
(add `can_edit` to the existing `from stackowl.tools.meta.owl_build import ...` line at the top of the file)

`tests/tools/meta/test_owl_build_edit_unbound.py` (new):
```python
"""Task 4 sub-part A — editing a builtin/human owl's tier/specialty works
directly (no agent-authority ratchet), preserving /owls edit's historical scope."""
from __future__ import annotations

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.meta.owl_build import OwlBuildTool

pytestmark = pytest.mark.asyncio


async def test_edit_builtin_owl_tier() -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="scout", role="research-scout", system_prompt="p",
            model_tier="fast", origin="builtin",
        ),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=None))
    try:
        result = await OwlBuildTool().execute(action="edit", name="scout", model_tier="powerful")
        assert result.success, result.error
        assert reg.get("scout").model_tier == "powerful"
    finally:
        reset_services(token)


async def test_edit_refuses_another_agents_owl() -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="helper", role="r", system_prompt="p", model_tier="fast",
            origin="agent", created_by="other_owl",
        ),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=None))
    try:
        result = await OwlBuildTool().execute(action="edit", name="helper", model_tier="powerful")
        assert not result.success
        assert "you may only modify owls you created" in result.error
    finally:
        reset_services(token)
```

- [ ] **Step 2: Run tests to verify they fail**
Run: `uv run pytest tests/tools/meta/test_owl_build_betters.py tests/tools/meta/test_owl_build_edit_unbound.py -v`
Expected: FAIL — `can_edit` does not exist (`ImportError`); the edit-unbound tests fail because `_edit` currently refuses `origin="builtin"` via `can_modify` ("cannot be modified by owl_build").

- [ ] **Step 3: Write minimal implementation**

`src/stackowl/tools/meta/owl_build.py` — add near `can_modify`/`can_retire`/`can_rename`:
```python
def can_edit(manifest: object, *, caller: str, target_name: str) -> str | None:
    """Edit's own gate — looser than can_modify for origin in {'builtin','human'}.
    Those owls were never creator/ceiling-bound (operator-configured, not
    agent-minted), so the authority-ratchet machinery in _edit (re-forge/clamp
    against creation_ceiling) doesn't apply to them and must not run for them —
    it would incorrectly refuse them for having no ceiling to ratchet against.
    Preserves /owls edit's historical scope (e.g. changing the Secretary's tier)
    so retiring /owls loses no capability. Still refuses an agent-minted owl you
    did not create — unchanged from can_modify."""
    origin = getattr(manifest, "origin", None)
    if origin in ("builtin", "human"):
        return None
    if origin != "agent":
        return f"'{target_name}' is a {origin} owl and cannot be edited by owl_build."
    if getattr(manifest, "created_by", None) != caller:
        return f"'{target_name}' was created by another owl — you may only modify owls you created."
    return None
```

In `_edit`, replace the guard call and branch before the re-forge step:
```python
        guard = can_edit(current, caller=creator, target_name=spec.name)
        if guard is not None:
            return self._err(guard, t0)

        if current.origin in ("builtin", "human"):
            return await self._edit_unbound(spec, current, registry, t0, creator)

        # 3. Re-forge — clamps to the creator's CURRENT floor (authority forced server-side).
        rebuilt, dropped = build_agent_manifest(
```
(the rest of `_edit`'s agent-origin body — re-forge/ratchet/consent/persist — is
UNCHANGED; only the guard line and the new branch above it move.)

Add `_edit_unbound` (e.g. directly above `_edit`):
```python
    async def _edit_unbound(
        self, spec: OwlBuildSpec, current: object, registry: object, t0: float, creator: str,
    ) -> ToolResult:
        """Edit path for builtin/human owls (can_edit already cleared it) — no
        creator/ceiling to ratchet against, so apply the given fields directly,
        the same scope /owls edit historically had (tier/specialty), without
        owl_build's agent-authority machinery (re-forge/clamp/consent-on-
        widening), which does not apply to an owl that was never
        authority-bounded."""
        updates: dict[str, object] = {}
        if spec.model_tier is not None:
            updates["model_tier"] = spec.model_tier
        if spec.specialty is not None:
            updates["system_prompt"] = spec.specialty
        rebuilt = current.model_copy(update=updates) if updates else current
        snapshot = self._yaml_snapshot()
        try:
            OwlsCommand()._upsert_to_yaml(manifest_to_yaml_entry(rebuilt))  # noqa: SLF001
            registry.replace(rebuilt)
        except Exception as exc:  # B5 — no-hidden-errors, roll back the yaml
            log.tool.error(
                "owl_build.execute: builtin/human edit persist failed — rolling back yaml",
                exc_info=exc, extra={"_fields": {"owl": rebuilt.name}},
            )
            self._yaml_restore(snapshot)
            return self._err(f"failed to edit owl '{rebuilt.name}' ({exc}) — rolled back.", t0)
        await self._audit("edit", rebuilt.name, creator)
        return self._ok(
            f"Updated owl '{rebuilt.name}'.", t0, extra={"owl": rebuilt.name, "op": "edit"}
        )
```

- [ ] **Step 4: Run tests to verify they pass**
Run: `uv run pytest tests/tools/meta/test_owl_build_betters.py tests/tools/meta/test_owl_build_edit_unbound.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/tools/meta/owl_build.py tests/tools/meta/test_owl_build_betters.py tests/tools/meta/test_owl_build_edit_unbound.py
git commit -m "fix(owl_build): edit builtin/human owls without the agent-authority ratchet"
```

- [ ] **Step 6: Write the failing test**

`tests/commands/test_owl_dispatcher.py` (new):
```python
"""Task 4 — /owl funnels every mutation through ONE owl_build.execute call, no
matter whether the caller used flags or free text (kills add-vs-create drift)."""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.owls_command import OwlCommand
from stackowl.commands.owls_helpers import parse_owl_build_flags
from stackowl.tools.base import ToolResult

pytestmark = pytest.mark.asyncio


class _State:
    session_id = "s1"
    trace_id = "t1"
    channel = "cli"
    reply_target = None


def test_parse_flags_freetext_maps_to_specialty() -> None:
    assert parse_owl_build_flags("a research owl that reads arxiv") == {
        "specialty": "a research owl that reads arxiv"
    }


def test_parse_flags_structured() -> None:
    kwargs = parse_owl_build_flags(
        '--name Sage --preset researcher --specialty "reads arxiv" '
        '--schedule "every 2h" --boundaries "no raw urls" '
        "--evolution_strategy conservative"
    )
    assert kwargs == {
        "name": "Sage", "preset": "researcher", "specialty": "reads arxiv",
        "schedule": "every 2h", "boundaries": "no raw urls",
        "evolution_strategy": "conservative",
    }


def test_parse_flags_explicit_tools_comma_list() -> None:
    kwargs = parse_owl_build_flags("--name S --explicit_tools read_file,memory")
    assert kwargs["explicit_tools"] == ["read_file", "memory"]


async def test_owl_create_freetext_routes_to_owl_build(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeTool:
        async def execute(self, **kw: Any) -> ToolResult:
            seen.update(kw)
            return ToolResult(success=True, output="Created owl 'x'.", duration_ms=1.0)

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _FakeTool)
    out = await OwlCommand().handle("create a research owl that reads arxiv", _State())
    assert out == "Created owl 'x'."
    assert seen == {"action": "create", "specialty": "a research owl that reads arxiv"}


async def test_owl_pause_routes_to_owl_build(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeTool:
        async def execute(self, **kw: Any) -> ToolResult:
            seen.update(kw)
            return ToolResult(success=True, output="Paused x.", duration_ms=1.0)

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _FakeTool)
    out = await OwlCommand().handle("pause Sage", _State())
    assert out == "Paused x."
    assert seen == {"action": "pause", "name": "Sage"}


async def test_owl_rename_routes_positional_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeTool:
        async def execute(self, **kw: Any) -> ToolResult:
            seen.update(kw)
            return ToolResult(success=True, output="Renamed.", duration_ms=1.0)

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _FakeTool)
    await OwlCommand().handle('rename Sage "Sage the Scholar"', _State())
    assert seen == {"action": "rename", "name": "Sage", "display_name": "Sage the Scholar"}


async def test_owl_list_uses_inherited_registry_surface() -> None:
    # No registry wired → the inherited _list returns the honest no-registry note.
    out = await OwlCommand().handle("list", _State())
    assert "no owl registry" in out.lower()
```

- [ ] **Step 7: Run test to verify it fails**
Run: `uv run pytest tests/commands/test_owl_dispatcher.py -v`
Expected: FAIL — `parse_owl_build_flags` does not exist (`ImportError`); `OwlCommand.handle("pause Sage", ...)` currently inherits `OwlsCommand.handle` which has no `pause` subcommand and returns usage text, not the routed result.

- [ ] **Step 8: Write minimal implementation**

`src/stackowl/commands/owls_helpers.py` — add:
```python
# /owl create|edit flag grammar → owl_build.execute kwargs. Free text with no
# --flags is treated as a specialty sentence (the free-text create path).
_OWL_BUILD_FLAGS: dict[str, str] = {
    "--name": "name",
    "--preset": "preset",
    "--specialty": "specialty",
    "--schedule": "schedule",
    "--goal": "goal",
    "--lifecycle": "lifecycle",
    "--boundaries": "boundaries",
    "--evolution_strategy": "evolution_strategy",
    "--tier": "model_tier",
    "--model_tier": "model_tier",
}


def parse_owl_build_flags(rest: str) -> dict[str, Any]:
    """Parse a `/owl create|edit` payload into owl_build.execute kwargs.

    ``--explicit_tools`` takes a comma list; every other flag takes one value.
    A payload with no ``--flags`` is a free-text specialty sentence → the
    free-text create path (elicits any missing fields). Raises CommandParseError
    on a malformed flag pairing or an unknown flag."""
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise CommandParseError("owl", f"could not tokenise arguments: {exc}") from exc
    if not tokens:
        return {}
    if not any(t.startswith("--") for t in tokens):
        return {"specialty": rest.strip()}
    if len(tokens) % 2 != 0:
        raise CommandParseError("owl", "every --flag requires a value")
    kwargs: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        key, value = tokens[i], tokens[i + 1]
        if key == "--explicit_tools":
            kwargs["explicit_tools"] = [t.strip() for t in value.split(",") if t.strip()]
        elif key in _OWL_BUILD_FLAGS:
            kwargs[_OWL_BUILD_FLAGS[key]] = value
        else:
            raise CommandParseError("owl", f"unknown flag: {key}")
        i += 2
    return kwargs
```

`src/stackowl/commands/owls_command.py` — add imports at top: extend the metadata import to include what `_OWL_META` uses (already imports `Arg, CommandMeta, Example, SubCommand, render_usage`) and add `parse_owl_build_flags` to the `owls_helpers` import block. Then replace the whole `OwlCommand(OwlsCommand)` class (lines 674-686) with:
```python
_OWL_META = CommandMeta(
    grammar="verb",
    group="Owls",
    subcommands=(
        SubCommand(
            name="create",
            summary="Create an owl (free text or flags) — elicits anything missing",
            description=(
                "Describe the owl in plain language, or pass flags "
                "(--name --preset|--explicit_tools --specialty --schedule --goal "
                "--lifecycle --boundaries --evolution_strategy). Missing required "
                "fields are asked for interactively, the same way chat creation works."
            ),
            args=(Arg(name="text_or_flags", summary="free-text description, or --flags"),),
            examples=(
                Example(invocation="/owl create a research assistant that reads arxiv daily"),
                Example(invocation='/owl create --name Sage --preset researcher --schedule "every 2h"'),
            ),
        ),
        SubCommand(
            name="edit",
            summary="Update fields on an owl you created",
            args=(Arg(name="name", summary="owl name"),),
            examples=(Example(invocation='/owl edit Sage --preset writer'),),
        ),
        SubCommand(
            name="rename",
            summary="Change an owl's display name (cosmetic)",
            args=(Arg(name="name", summary="owl name"), Arg(name="display_name", summary="new label")),
            examples=(Example(invocation='/owl rename Sage "Sage the Scholar"'),),
        ),
        SubCommand(name="pause", summary="Suspend a scheduled owl's cadence",
                   args=(Arg(name="name", summary="owl name"),)),
        SubCommand(name="resume", summary="Resume a paused owl's cadence",
                   args=(Arg(name="name", summary="owl name"),)),
        SubCommand(name="retire", summary="Remove an owl you created",
                   args=(Arg(name="name", summary="owl name"),)),
        SubCommand(name="list", summary="Show your assistants"),
        SubCommand(name="dna", summary="Show DNA traits, current versus authored",
                   args=(Arg(name="name", summary="owl name"),)),
    ),
)

_OWL_BUILD_ACTIONS: frozenset[str] = frozenset(
    {"create", "edit", "rename", "pause", "resume", "retire"}
)


class OwlCommand(OwlsCommand):
    """``/owl`` — the ONE owl surface. Every mutation (create/edit/rename/pause/
    resume/retire) funnels through the single owl_build engine so there is exactly
    one path from user intent to persisted owl (killing the /owls add-vs-create
    divergence). Inspection (list/dna/reset-dna/health/objectives) reuses the
    inherited registry-backed handlers unchanged."""

    @property
    def command(self) -> str:
        return "owl"

    @property
    def description(self) -> str:
        return "Manage your owls: create, edit, rename, pause, resume, retire, list, dna."

    @property
    def meta(self) -> CommandMeta:
        return _OWL_META

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "[commands] owl.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "create":
                return await self._build("create", parse_owl_build_flags(rest), state)
            if sub == "edit":
                name, flag_rest = _split_name(rest)
                return await self._build("edit", {"name": name, **parse_owl_build_flags(flag_rest)}, state)
            if sub == "rename":
                name, display = _two_positional(rest)
                return await self._build("rename", {"name": name, "display_name": display}, state)
            if sub in ("pause", "resume", "retire", "remove"):
                name, _ = _split_name(rest)
                action = "retire" if sub in ("retire", "remove") else sub
                return await self._build(action, {"name": name}, state)
            if sub == "list":
                return self._list()
            if sub == "dna":
                return await self._dna(rest)
            if sub == "reset-dna":
                return await self._reset_dna(rest)
            if sub == "health":
                return await self._health()
            if sub == "objectives":
                return await self._objectives()
            if sub == "objective":
                return await self._objective(rest)
            if sub == "objective-cancel":
                return await self._objective_cancel(rest)
            log.gateway.debug("[commands] owl.handle: unknown subcommand",
                              extra={"_fields": {"sub": sub}})
            return render_usage("owl", _OWL_META)
        except CommandParseError as exc:
            log.gateway.warning("[commands] owl.handle: parse error",
                                extra={"_fields": {"sub": sub, "error": str(exc)}})
            return f"✗ {exc}\n\n{render_usage('owl', _OWL_META)}"
        except (ManifestValidationError, OwlNotFoundError) as exc:
            log.gateway.warning("[commands] owl.handle: domain error",
                                extra={"_fields": {"sub": sub, "error": str(exc)}})
            return f"✗ /owl {sub}: {exc}"
        except Exception as exc:
            log.gateway.error("[commands] owl.handle: subcommand crashed",
                              exc_info=exc, extra={"_fields": {"sub": sub}})
            return f"✗ /owl {sub}: {exc}"

    async def _build(self, action: str, kwargs: dict[str, Any], state: PipelineState) -> str:
        """Route one /owl mutation through owl_build — the ONE mutation engine.

        Wraps the call in an interactive TraceContext (as /owls create already
        does) so owl_build's consent gate + elicitation can reach the user."""
        log.gateway.debug("[commands] owl._build: entry",
                          extra={"_fields": {"action": action, "keys": sorted(kwargs)}})
        # Lazy import — owl_build imports OwlsCommand at module top, so a top-level
        # import here is circular; also keeps OwlBuildTool monkeypatchable at origin.
        from stackowl.tools.meta.owl_build import OwlBuildTool

        token = TraceContext.start(
            session_id=state.session_id,
            trace_id=state.trace_id,
            interactive=True,
            channel=state.channel,
            reply_target=state.reply_target,
        )
        try:
            result = await OwlBuildTool().execute(action=action, **kwargs)
        finally:
            TraceContext.reset(token)
        log.gateway.info("[commands] owl._build: exit",
                        extra={"_fields": {"action": action, "success": result.success}})
        return result.output if result.success else f"✗ /owl {action}: {result.error}"


def _split_name(rest: str) -> tuple[str, str]:
    """Split ``<name> <remainder>`` — name is the first whitespace token."""
    stripped = rest.strip()
    if not stripped:
        raise CommandParseError("owl", "missing owl name")
    parts = stripped.split(maxsplit=1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _two_positional(rest: str) -> tuple[str, str]:
    """Parse ``<name> <display_name>`` (display_name may be quoted)."""
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise CommandParseError("owl", f"could not tokenise arguments: {exc}") from exc
    if len(tokens) < 2:
        raise CommandParseError("owl", "usage: /owl rename <name> <display_name>")
    return tokens[0], " ".join(tokens[1:])
```
Add `import shlex` and `from typing import Any` if not already present at the top of `owls_command.py` (it imports `TYPE_CHECKING, Any` already; add `import shlex`). Extend the `owls_helpers` import to include `parse_owl_build_flags`.

`src/stackowl/commands/assembly.py` — the `/owl` registration (lines 230-236) already constructs `OwlCommand(owl_registry=..., db=..., event_bus=..., tool_registry=...)`; the rewritten `OwlCommand` inherits `OwlsCommand.__init__`, so the constructor signature is unchanged. Update the code comment above it to reflect the new role (unified dispatcher, no longer a plain alias). No functional change to this file in Task 4.

- [ ] **Step 9: Run test to verify it passes**
Run: `uv run pytest tests/commands/test_owl_dispatcher.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**
```bash
git add src/stackowl/commands/owls_helpers.py src/stackowl/commands/owls_command.py src/stackowl/commands/assembly.py tests/commands/test_owl_dispatcher.py
git commit -m "feat(owl): /owl unified dispatcher routes all mutations through owl_build"
```

---

### Task 5: Regression — the 3 old `/agent` use cases as scheduled owls

**Files:**
- Test: `tests/journeys/commands/test_agent_usecases_as_owls.py` (new)

**Interfaces:**
- Consumes: `build_agent_manifest(...)`, `reconcile_owl_schedules(registry, db)`, `_job_id_for(name)`, `OwlBuildSpec`, `OwlRegistry`, the `db` fixture pattern from `tests/scheduler/test_owl_lifecycle_reconcile.py`.

Rationale: the old `/agent` allowlist was `goal_execution` / `morning_brief` / `check_in`. Under the unified model a recurring reminder becomes a `lifecycle="scheduled"` owl whose `cron` trigger projects (per `owl_lifecycle._KIND_TO_HANDLER`) to the `goal_execution` handler running its `goal` each tick. This test proves each of the three cadences re-expressed as a scheduled owl produces the same end-user-visible behaviour: exactly one owned job row, future next-run, carrying the goal — i.e. a proactive message arrives on schedule.

- [ ] **Step 1: Write the failing test**

`tests/journeys/commands/test_agent_usecases_as_owls.py` (new):
```python
"""Migration regression — the 3 legacy /agent use cases (a recurring goal, a
morning brief, a check-in) expressed as scheduled owls all project exactly one
owned scheduler row that fires on schedule with the intended goal."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.scheduler.owl_lifecycle import _job_id_for, reconcile_owl_schedules
from stackowl.tools.meta.owl_build_authz import build_agent_manifest
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _scheduled(name: str, goal: str, schedule: str) -> object:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifestFrom(name, goal, schedule), source_name="usecase"
    )
    return reg


def OwlAgentManifestFrom(name: str, goal: str, schedule: str):  # noqa: N802
    spec = OwlBuildSpec(
        action="create", name=name, preset="researcher",
        specialty=goal, schedule=schedule, goal=goal,
    )
    reg0 = OwlRegistry()  # unbounded secretary creator → SAFE_DEFAULT_CEILING
    from stackowl.owls.manifest import OwlAgentManifest
    reg0.register(
        OwlAgentManifest(name="secretary", role="s", system_prompt="p", model_tier="fast"),
        source_name="t",
    )
    manifest, _ = build_agent_manifest(
        spec, creator="secretary", parent_ceiling=None, registry=reg0
    )
    assert manifest.lifecycle == "scheduled"
    assert manifest.trigger is not None and manifest.trigger.prompt == goal
    return manifest


async def _owned_row(db: DbPool, name: str) -> dict:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (_job_id_for(name),))
    assert rows, f"no projected row for {name}"
    row = dict(rows[0])
    row["params"] = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
    return row


@pytest.mark.parametrize(
    ("name", "goal", "schedule"),
    [
        ("newsowl", "poke me with the latest AI news", "every 2h"),     # goal_execution
        ("briefowl", "give me my morning brief", "daily@09:00"),        # morning_brief
        ("checkowl", "check in on my open tasks", "daily@17:00"),       # check_in
    ],
)
async def test_agent_usecase_projects_one_firing_row(
    db: DbPool, name: str, goal: str, schedule: str
) -> None:
    reg = _scheduled(name, goal, schedule)
    result = await reconcile_owl_schedules(reg, db)
    assert result.created == 1
    row = await _owned_row(db, name)
    assert row["handler_name"] == "goal_execution"
    assert row["params"]["goal"] == goal
    assert row["next_run_at"] and row["status"] == "pending"
    # Idempotent — a second reconcile creates no duplicate (same end behaviour).
    again = await reconcile_owl_schedules(reg, db)
    assert again.created == 0
```

- [ ] **Step 2: Run test to verify it fails, then confirm the migration claim**
Run: `uv run pytest tests/journeys/commands/test_agent_usecases_as_owls.py -v`
Expected: initially this is a NEW test exercising already-shipped reconcile behaviour on top of Task 1's forge fields — it may PASS immediately. If it fails, read the failure: a `KeyError`/schema mismatch on `jobs` columns means the `db` fixture drifted from `tests/scheduler/test_owl_lifecycle_reconcile.py` — re-sync the fixture with that file. The test's PURPOSE is the migration guarantee, so treat a green run as the acceptance evidence for design decision 1.

- [ ] **Step 3: Minimal implementation**
No source change — this task is pure regression coverage proving the scheduled-owl path subsumes the 3 `/agent` handlers. (If Step 2 reveals a genuine gap, STOP and surface it rather than papering it; per repo policy the AI must not silently patch a failing migration-integration test.)

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/journeys/commands/test_agent_usecases_as_owls.py -v`
Expected: PASS (all 3 parametrizations).

- [ ] **Step 5: Commit**
```bash
git add tests/journeys/commands/test_agent_usecases_as_owls.py
git commit -m "test(owl): regression — 3 legacy /agent use cases as scheduled owls"
```

---

### Task 6: Retarget the `/owls` and `/agent` test suites at `/owl` (pre-deletion green gate)

**Files:**
- Modify: `tests/journeys/commands/test_owl_agent_entrypoints.py` (drop the `owls`/`agent`-registered + `add`-inherited assertions; assert `/owl`'s new surface)
- Modify: `tests/journeys/commands/test_owls_command.py` (retarget `add`/`edit`/`create` cases at `/owl`'s owl_build-routed equivalents)
- Test: `tests/commands/test_owl_surface_complete.py` (new — asserts `/owl` covers every capability the deleted surfaces had)

**Interfaces:**
- Consumes: `OwlCommand` (Task 4), `register_all_commands(CommandDeps())`, `CommandRegistry`.

- [ ] **Step 1: Write the failing / retargeted tests**

`tests/commands/test_owl_surface_complete.py` (new):
```python
"""Pre-deletion gate — /owl exposes every capability the old /owls + /agent
surfaces did, so removing them (Task 7) loses nothing (proven by tests)."""
from stackowl.commands.owls_command import OwlCommand


def test_owl_meta_covers_full_lifecycle() -> None:
    subs = {s.name for s in OwlCommand().meta.subcommands}
    # create (was /owls add + /owls create + /agent create), edit, rename,
    # pause/resume (was /agent pause|resume), retire (was /owls remove +
    # /agent stop), list, dna.
    for required in ("create", "edit", "rename", "pause", "resume", "retire", "list", "dna"):
        assert required in subs, f"/owl must expose {required}"


def test_owl_command_token_is_singular() -> None:
    assert OwlCommand().command == "owl"
```

Retarget `tests/journeys/commands/test_owl_agent_entrypoints.py` — replace `test_c_agent_and_owl_entry_points_registered` and `test_c_owl_alias_reaches_owl_surface` with:
```python
def test_owl_entry_point_registered() -> None:
    """/owl registers unconditionally via the assembly spine and is the ONE owl
    surface (legacy /owls + /agent are gone — see Task 7)."""
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    live = {c.command for c in CommandRegistry.instance().list()}
    assert "owl" in live, "the /owl entry point must be reachable"
    assert "owls" not in live, "legacy /owls must be removed"
    assert "agent" not in live, "legacy /agent must be removed"


def test_owl_exposes_unified_surface() -> None:
    from stackowl.commands.owls_command import OwlCommand
    cmd = OwlCommand()
    assert cmd.command == "owl"
    names = {s.name for s in cmd.meta.subcommands}
    assert {"create", "pause", "resume", "retire"} <= names
```
(Keep `test_discovery_nudge_is_a_usable_one_liner` if `discovery_nudge()` still references `/owl` — verify the nudge string; adjust only if it names `/owls`/`/agent`.)

Retarget `tests/journeys/commands/test_owls_command.py` — the `add`/`edit` cases exercised the divergent `build_owl_manifest` path being deleted. Rewrite them to drive `OwlCommand` and monkeypatch `OwlBuildTool` at `stackowl.tools.meta.owl_build.OwlBuildTool`, asserting the routed `execute(action=..., **kwargs)` shape (mirror the pattern in `tests/commands/test_owl_dispatcher.py`). Keep `test_owls_create_freetext_reaches_owl_build_tool` semantics but point it at `OwlCommand().handle("create <text>", state)`.

- [ ] **Step 2: Run the retargeted tests to verify current state**
Run: `uv run pytest tests/commands/test_owl_surface_complete.py tests/journeys/commands/test_owl_agent_entrypoints.py -v`
Expected: `test_owl_surface_complete.py` PASSES against Task 4; the retargeted entrypoint tests FAIL now (`owls`/`agent` are still registered — they are removed in Task 7). This RED is the pre-condition Task 7 turns green.

- [ ] **Step 3: No source change**
This task only retargets tests; the RED assertions (`"owls" not in live`) are the contract Task 7 must satisfy. Do NOT weaken them to pass early.

- [ ] **Step 4: Confirm the surface-completeness test is green**
Run: `uv run pytest tests/commands/test_owl_surface_complete.py -v`
Expected: PASS (proves `/owl` covers the full capability set before anything is deleted).

- [ ] **Step 5: Commit**
```bash
git add tests/commands/test_owl_surface_complete.py tests/journeys/commands/test_owl_agent_entrypoints.py tests/journeys/commands/test_owls_command.py
git commit -m "test(owl): retarget /owls + /agent suites at /owl (pre-deletion gate)"
```

---

### Task 7: Delete `/owls`, `/agent`, and the now-dead command source

**Files:**
- Modify: `src/stackowl/commands/assembly.py` (remove the `/owls` registration at ~219-226 and the `/agent` registration at ~314-322; keep the `/owl` registration)
- Delete: `src/stackowl/commands/agent_create_command.py`, `src/stackowl/commands/agent_create_helpers.py`, `src/stackowl/commands/agents_helpers.py`
- Modify: `src/stackowl/commands/owls_command.py` (remove the divergent `add` path: `_OWLS_META`'s `add` SubCommand, the `_add` method, and the `elif sub == "add"` branch; remove the `parse_add_args`/`build_owl_manifest` imports if now unused. KEEP `_list`/`_dna`/`_reset_dna`/`_health`/`_objectives*`/`_upsert_to_yaml`/`_remove_from_yaml`/`_add_retired_builtin` — reused by `OwlCommand` and by `owl_build`.)
- Modify: `src/stackowl/commands/owls_helpers.py` (remove `parse_add_args` + `build_owl_manifest` only if grep proves no remaining importer)
- Delete/retarget: `tests/test_story_7_2.py`, `tests/test_story_7_2b.py`, `tests/commands/test_agent_meta.py`, `tests/commands/test_owls_meta.py`, `tests/commands/test_owls_builder.py` (the parts covering deleted handlers/parsers) — keep any assertions still valid under `/owl` (e.g. `GoalExecutionHandler` tests in `test_story_7_2.py` cover the scheduler handler, which SURVIVES; keep those, drop only the `AgentCommand`/`format_proposal`/`format_jobs_table` cases whose source is deleted).

**Interfaces:**
- Consumes: nothing new. This task removes surface; the green Task 6 suites are the proof no capability is lost.

- [ ] **Step 1: Establish the RED you are turning GREEN**
Run: `uv run pytest tests/journeys/commands/test_owl_agent_entrypoints.py -v`
Expected: FAIL (`"owls" not in live` / `"agent" not in live` — still registered). This is the target.

- [ ] **Step 2: Prove what still imports the doomed modules (blast-radius check)**
Run:
```bash
grep -rn "agent_create_command\|agent_create_helpers\|agents_helpers\|parse_add_args\|build_owl_manifest\|AgentCommand\b" src/ tests/
```
Expected: only the registration in `assembly.py`, the deleted-module internals, and tests already retargeted in Task 6. Any OTHER live `src/` importer means STOP and reassess before deleting.

- [ ] **Step 3: Delete + unregister (minimal)**
- In `assembly.py`: delete the `# /owls` registration block and the `# /agent` block (leave `# /owl`). Remove the now-unused `AgentCommand`/`ProviderRegistry`/`OwlsCommand` imports local to those blocks if they become dead.
- `git rm src/stackowl/commands/agent_create_command.py src/stackowl/commands/agent_create_helpers.py src/stackowl/commands/agents_helpers.py`
- In `owls_command.py`: remove the `add` SubCommand from `_OWLS_META`, the `elif sub == "add":` branch, and the `_add` method. Remove `parse_add_args`/`build_owl_manifest` from the `owls_helpers` import only if grep (Step 2) shows no remaining use.
- In `owls_helpers.py`: `git rm`-style delete of `parse_add_args` and `build_owl_manifest` functions only if unused. (`OwlsCommand` itself is retained as `OwlCommand`'s base for the inherited inspection surface + yaml helpers.)

- [ ] **Step 4: Run the gate + typecheck to verify GREEN**
Run:
```bash
uv run pytest tests/journeys/commands/test_owl_agent_entrypoints.py tests/commands/test_owl_surface_complete.py tests/commands/test_owl_dispatcher.py -v
uv run ruff check src/
uv run mypy src/stackowl/commands/ src/stackowl/tools/meta/
```
Expected: pytest PASS (owls/agent absent, /owl present + complete); ruff clean (no unused imports left behind); mypy clean.

- [ ] **Step 5: Commit**
```bash
git add -A src/stackowl/commands/ tests/
git commit -m "refactor(owl): remove /owls + /agent surfaces, fold into unified /owl"
```

---

### Task 8: Final verification sweep

**Files:** none (verification only).

- [ ] **Step 1: Run every test file this plan touched (scoped — never the full suite)**
Run:
```bash
uv run pytest \
  tests/commands/test_owl_new_fields.py \
  tests/tools/meta/test_owl_build_authz.py \
  tests/tools/meta/test_owl_build_spec.py \
  tests/owls/test_boundaries_injection.py \
  tests/owls/test_evolution_strategy_scaling.py \
  tests/tools/meta/test_owl_build_pause_resume.py \
  tests/commands/test_owl_dispatcher.py \
  tests/journeys/commands/test_agent_usecases_as_owls.py \
  tests/commands/test_owl_surface_complete.py \
  tests/journeys/commands/test_owl_agent_entrypoints.py \
  tests/journeys/commands/test_owls_command.py \
  tests/scheduler/test_owl_lifecycle_reconcile.py \
  tests/tools/meta/test_owl_build_betters.py \
  tests/tools/meta/test_owl_build_verify.py \
  tests/commands/test_assembly.py \
  -v
```
Expected: all PASS. (`test_assembly.py`, `test_owl_lifecycle_reconcile.py`, `test_owl_build_betters.py`, `test_owl_build_verify.py` are pre-existing suites the changes must keep green.)

- [ ] **Step 2: Lint + strict type-check the whole source tree**
Run:
```bash
uv run ruff check src/
uv run mypy src/
```
Expected: both clean.

- [ ] **Step 3: Manual CLI smoke — the honest note on the harness**
There is NO documented offline slash-command runner in this repo — slash commands are dispatched inside a live gateway turn (they need a `PipelineState` and, for owl_build, an interactive `TraceContext` + wired services), and CLAUDE.md/README expose only `uv run python -m stackowl serve` / `health`, not a "run one slash command" entrypoint. So the end-to-end smoke is done against a running server, not a CLI one-liner:
```bash
uv run python -m stackowl health          # sanity: process + DB + registry up
```
Then, in a live chat/TUI session against `uv run python -m stackowl serve`, exercise the surface on a throwaway owl:
```
/owl create --name Smoke --preset researcher --specialty "reads arxiv" --schedule "every 2h" --boundaries "never share raw URLs"
/owl list
/owl dna Smoke
/owl pause Smoke
/owl resume Smoke
/owl rename Smoke "Smokey"
/owl retire Smoke
/owl create a research assistant that reads hacker news each morning   # free-text elicitation path
```
and confirm: create reports a MEASURED next-run; pause/resume flip the cadence without deleting the owl; retire tears down the owned job row; free-text create asks for the name (proving the Task-1 name-optional fix reaches elicitation). The automated proxy for all of this is Step 1's green suites — the manual pass is the human-in-the-loop confirmation the design's decision 5 requires before the legacy surfaces are considered truly retired.

- [ ] **Step 4: Commit (only if any doc/cleanup fixups were needed)**
```bash
git add -A
git commit -m "chore(owl): final verification fixups for unified /owl command"
```

---

## Self-Review

**Spec coverage — one task per design decision (1-6):**
- Decision 1 (`/agent` folds into owl creation): Task 5 (3 use cases as scheduled owls) + Task 7 (delete `/agent`).
- Decision 2 (pause suspends cadence only, reuse `JobScheduler.pause/resume`, no manifest `paused` field): Task 3.
- Decision 3 (evolution preset scales existing constants): Task 1 (field) + Task 2 (`_scale_deltas`, conservative=0.5×/adaptive=1×/experimental=2×, justified: tune speed within the governor band that still clamps the result).
- Decision 4 (`boundaries` free-text guardrail into system prompt): Task 1 (field) + Task 2 (`DNAPromptInjector.inject`).
- Decision 5 (legacy removed, not aliased, only after tests prove coverage): Task 6 (green gate) → Task 7 (delete) → Task 8 (manual confirm).
- Decision 6 (extend `owl_build`, one path): Tasks 3 + 4 (every `/owl` mutation → one `OwlBuildSpec` → `owl_build.execute`).
- Capability-loss gap found during planning, user-confirmed fix (extend edit scope rather than accept the narrowing): Task 4 sub-part A (`can_edit` + `_edit_unbound`) — `/owl edit` on a `builtin`/`human` owl (e.g. the Secretary's tier) now matches `/owls edit`'s historical scope instead of silently losing it when `/owls` is deleted in Task 7.

**Placeholder scan:** no TBD/TODO/"similar to Task N"; every code step shows real code.

**Type/signature consistency across tasks:** `boundaries`/`evolution_strategy` field types identical on `OwlAgentManifest` (non-optional, defaulted) and `OwlBuildSpec` (optional); `_scale_deltas(deltas, strategy)` defined in Task 2, no other task redefines it; `_job_id_for` consistently imported (never renamed); `_VALID_ACTIONS` tuple and the `action` Literal list the same six actions; `parse_owl_build_flags`/`_split_name`/`_two_positional` defined once (Task 4) and only consumed by `OwlCommand`; `can_edit`/`_edit_unbound` defined once (Task 4 sub-part A), consumed only by `_edit` (Task 4 sub-part A) — no other task redefines or calls them.
