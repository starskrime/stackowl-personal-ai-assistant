# Agent `owl_build` Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an owl (the model, mid-turn) a consent-gated, fail-closed tool to create/edit/retire specialist owls — the self-extending owl-builder — without opening an escalation surface.

**Architecture:** Two sub-stories. **Aa** adds provenance/authority fields (`origin`/`created_by`/`creation_ceiling`) to the frozen `OwlAgentManifest`, serializes them to `stackowl.yaml`, and adds a boot-time `revalidate_agent_owls` pass that re-clamps `origin="agent"` owls (`bounds ∩ creation_ceiling`; fail-closed to empty bounds when an agent owl has no ceiling). Aa ships with NO create ability. **Ab** adds the `owl_build` tool in `tools/meta/owl_build.py`, mirroring the shipped `tools/meta/tool_build.py` self-extension pattern 1:1 (consequential, consent fail-closed off-TTY, persist+audit+register-with-rollback). Authority is forced server-side; the agent-facing `OwlBuildSpec` has no authority fields. The bounds clamp is a no-op for an unbounded creator, so **consent is the real gate**: the consent prompt renders the resolved toolset, and unbounded creators get a conservative `SAFE_DEFAULT_CEILING` so consequential tools require explicit human widening.

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen, `extra="forbid"`), asyncio, pytest, ruff, mypy --strict. Code under `v2/src/stackowl/`, tests under `v2/tests/`. Run from `v2/`: `uv run pytest <path> -v` (NO `--timeout`; targeted paths only).

**Spec:** `docs/superpowers/specs/2026-06-06-agent-owl-build-design.md` (read it first).

**Standing rules (from project memory — non-negotiable):** check existing before writing new (this plan reuses `SpecialistOwlBuilder.build`, `manifest_to_yaml_entry`, `_upsert_to_yaml`, `child_floor`, `effective_bounds`, `cosine_similarity`, `tool_build`'s consent shape — do NOT re-create them); no silent errors (every `catch`/`except` logs via `log.<ns>`); 4-point logging on `execute()`; no hardcoded English keywords (name-quality is structural, not a wordlist); minimal code changes; no vendor names in `src/`; never pipe pytest to `tail` in a `&&` chain (it masks the exit code); commit at sub-task granularity; stage `v2/` only.

---

## Reuse Ledger (what already exists — wire it, don't rebuild)

| Need | Existing thing | Location |
|---|---|---|
| Build a manifest from a spec | `SpecialistOwlBuilder().build(OwlSpec)` | `owls/builder.py:57` |
| Owl spec shape | `OwlSpec` (frozen dataclass) | `owls/builder.py:20-40` |
| Tool presets / router tools | `PRESETS`, `ROUTER_TOOLS`, `OwlPreset` | `owls/tool_presets.py:14-47` |
| Bounds intersection | `BoundsSpec.intersect`, `effective_bounds(*specs)` | `authz/bounds.py:103`, `authz/bounds_guard.py:42` |
| Delegation floor | `child_floor(parent, parent_ceiling, registry)` | `pipeline/authz_compose.py:44` |
| Per-task creation ceiling | `TraceContext.creation_ceiling()`, `TraceContext.get()` | `infra/trace.py:125,136` |
| Consent (fail-closed off-TTY) | `tool_build._consent_or_refuse` shape; `get_services().consent_gate.policy.request(...)` | `tools/meta/tool_build.py:350-389` |
| yaml serialization (field-by-field) | `manifest_to_yaml_entry` | `commands/owls_helpers.py:314-340` |
| yaml persistence seam | `_upsert_to_yaml` | `commands/owls_command.py:275-296` |
| Registry register/replace/deregister | `OwlRegistry.register/replace/deregister` | `owls/registry.py:110,121,167` |
| Boot hydrate precedent (get→model_copy→replace) | `apply_dna_overlay`, `hydrate_dna` | `owls/dna_hydrator.py:25,74` |
| Child-excluded tools enforcement | `_CHILD_EXCLUDED_TOOLS` | `pipeline/steps/execute.py:44` |
| Embedding for similarity | `get_services().embedding_registry.get().embed([text])` | `pipeline/services.py:61` |
| Cosine helper | `cosine_similarity(a, b)` | `memory/sqlite_helpers.py:35` |
| Tool registration | `ToolRegistry.with_defaults()` block | `tools/registry.py:445-450` |
| Test template | `test_tool_build_gateway.py`, `test_owls_builder.py` | `tests/tools/meta/`, `tests/commands/` |

---

# STORY Aa — Persisted-owl safety infrastructure (no create ability)

### Task 1: Add provenance/authority fields to `OwlAgentManifest`

**Files:**
- Modify: `src/stackowl/owls/manifest.py` (after the `bounds` field at `:51`)
- Test: `tests/owls/test_manifest_provenance.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_manifest_provenance.py
import pytest
from pydantic import ValidationError
from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest


def _manifest(**kw):
    base = dict(name="scout", role="scout", system_prompt="p", model_tier="balanced")
    base.update(kw)
    return OwlAgentManifest(**base)


def test_provenance_defaults_to_human_unclamped():
    m = _manifest()
    assert m.origin == "human"
    assert m.created_by is None
    assert m.creation_ceiling is None


def test_agent_origin_fields_round_trip():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    m = _manifest(origin="agent", created_by="secretary", creation_ceiling=ceiling)
    assert m.origin == "agent"
    assert m.created_by == "secretary"
    assert m.creation_ceiling == ceiling


def test_origin_rejects_unknown_value():
    with pytest.raises(ValidationError):
        _manifest(origin="rogue")


def test_manifest_is_frozen():
    m = _manifest()
    with pytest.raises(ValidationError):
        m.origin = "agent"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/owls/test_manifest_provenance.py -v`
Expected: FAIL — `OwlAgentManifest` has no field `origin` (extra="forbid" rejects the kwarg).

- [ ] **Step 3: Add the fields**

In `src/stackowl/owls/manifest.py`, immediately after the `bounds: BoundsSpec | None = None` line (`:51`), add (note `Literal` and `BoundsSpec` are already imported at `:7` and `:11`):

```python
    # Provenance + authority (Phase-2 owl_build). Default keeps legacy owls trusted:
    # the security gates key on origin == "agent", which the default is not.
    origin: Literal["human", "builtin", "agent"] = "human"
    created_by: str | None = None  # the owl that minted this owl (agent origin only)
    creation_ceiling: BoundsSpec | None = None  # creator's effective bounds at mint
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/owls/test_manifest_provenance.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Type-check and commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/manifest.py && uv run ruff check src/stackowl/owls/manifest.py
git add v2/src/stackowl/owls/manifest.py v2/tests/owls/test_manifest_provenance.py
git commit -m "feat(v2): owl provenance fields (origin/created_by/creation_ceiling) — owl_build Aa"
```

---

### Task 2: Serialize the new fields in `manifest_to_yaml_entry`

**Files:**
- Modify: `src/stackowl/commands/owls_helpers.py:314-340` (`manifest_to_yaml_entry`)
- Test: `tests/commands/test_owls_provenance_yaml.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/commands/test_owls_provenance_yaml.py
from stackowl.authz.bounds import BoundsSpec
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.owls.manifest import OwlAgentManifest


def _m(**kw):
    base = dict(name="scout", role="scout", system_prompt="p", model_tier="balanced")
    base.update(kw)
    return OwlAgentManifest(**base)


def test_human_owl_omits_agent_only_keys():
    entry = manifest_to_yaml_entry(_m())
    assert entry["origin"] == "human"
    assert "created_by" not in entry  # None → omitted
    assert "creation_ceiling" not in entry  # None ceiling must NOT serialize as {}


def test_agent_owl_serializes_ceiling_sorted():
    ceiling = BoundsSpec(tools=frozenset({"web_fetch", "read_file"}))
    entry = manifest_to_yaml_entry(_m(origin="agent", created_by="secretary", creation_ceiling=ceiling))
    assert entry["origin"] == "agent"
    assert entry["created_by"] == "secretary"
    # frozenset serialized deterministically (sorted list), mirroring the bounds axis
    assert entry["creation_ceiling"]["tools"] == ["read_file", "web_fetch"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/commands/test_owls_provenance_yaml.py -v`
Expected: FAIL — `KeyError: 'origin'` (not yet serialized).

- [ ] **Step 3: Add serialization**

In `manifest_to_yaml_entry` (`owls_helpers.py:314-340`), after the `bounds` serialization block (`:331-339`) and before `return entry`, add (mirror the existing `bounds` branch exactly — `model_dump(mode="json", exclude_none=True)` then sort the `tools` list):

```python
    entry["origin"] = manifest.origin
    if manifest.created_by is not None:
        entry["created_by"] = manifest.created_by
    if manifest.creation_ceiling is not None:
        ceiling = manifest.creation_ceiling.model_dump(mode="json", exclude_none=True)
        if isinstance(ceiling.get("tools"), list):
            ceiling["tools"] = sorted(ceiling["tools"])
        entry["creation_ceiling"] = ceiling
```

> Note: a `None` ceiling is OMITTED (an empty `BoundsSpec` `{}` means "deny all", a real clamp — never emit it for "no ceiling"). Match whatever the existing `bounds` block does for the `tools` sort; if it uses a different key set, replicate that here.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/commands/test_owls_provenance_yaml.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/commands/owls_helpers.py && uv run ruff check src/stackowl/commands/owls_helpers.py
git add v2/src/stackowl/commands/owls_helpers.py v2/tests/commands/test_owls_provenance_yaml.py
git commit -m "feat(v2): serialize owl provenance to yaml (None ceiling omitted) — owl_build Aa"
```

---

### Task 3: Stamp `origin` at the two existing construction sites

**Files:**
- Modify: `src/stackowl/owls/registry.py:286-303` (`register_builtin_personas`) — stamp `"builtin"`
- Modify: `src/stackowl/commands/owls_command.py:143-164` (`_add`) — stamp `"human"`
- Test: `tests/owls/test_origin_stamping.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_origin_stamping.py
from stackowl.owls.registry import OwlRegistry


def test_builtin_personas_are_stamped_builtin():
    reg = OwlRegistry()
    reg.register_builtin_personas()
    for m in reg.all():
        assert m.origin == "builtin", f"{m.name} not stamped builtin"
```

(The `_add`→"human" path is covered by the existing `tests/commands/test_owls_builder.py` add tests once we assert origin there in Step 3b.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/owls/test_origin_stamping.py -v`
Expected: FAIL — personas default to `origin="human"`, not `"builtin"`.

- [ ] **Step 3a: Stamp builtin personas**

In `register_builtin_personas` (`registry.py:286-303`), the personas come from `_BUILTIN_PERSONA_FACTORIES`. Stamp at the point each manifest is constructed/registered. If the loop registers a `manifest` variable, change the registration to use `manifest.model_copy(update={"origin": "builtin"})`. Concretely, where it currently does `self.register(manifest, ...)` (or builds `manifest = factory()`), insert before registering:

```python
            manifest = manifest.model_copy(update={"origin": "builtin"})
```

(If the factories build manifests directly, stamp each: read `registry.py:286-303` and apply the single `model_copy` at the existing register call — minimal change.)

- [ ] **Step 3b: Stamp the human CLI path**

In `_add` (`owls_command.py:143-164`), where it builds `manifest` (`:155`) before `registry.register(manifest)` (`:156`), insert:

```python
    manifest = manifest.model_copy(update={"origin": "human"})
```

(This is the default already, but explicit-is-better and guards against a future default change.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/owls/test_origin_stamping.py tests/commands/test_owls_builder.py -v`
Expected: PASS (new test + existing builder tests still green).

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/registry.py src/stackowl/commands/owls_command.py
uv run ruff check src/stackowl/owls/registry.py src/stackowl/commands/owls_command.py
git add v2/src/stackowl/owls/registry.py v2/src/stackowl/commands/owls_command.py v2/tests/owls/test_origin_stamping.py
git commit -m "feat(v2): stamp origin builtin/human at construction sites — owl_build Aa"
```

---

### Task 4: `revalidate_agent_owls` boot re-clamp (the AgentOwlHydrator)

**Files:**
- Create: `src/stackowl/owls/owl_revalidator.py`
- Test: `tests/owls/test_owl_revalidator.py` (create)

This mirrors `dna_hydrator.apply_dna_overlay`'s get→model_copy→replace shape (`dna_hydrator.py:25-50`), but clamps bounds. Fail-safe per owl.

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_owl_revalidator.py
from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.owl_revalidator import revalidate_agent_owls
from stackowl.owls.registry import OwlRegistry


def _m(name, **kw):
    base = dict(name=name, role=name, system_prompt="p", model_tier="balanced")
    base.update(kw)
    return OwlAgentManifest(**base)


def _reg(*manifests):
    reg = OwlRegistry()
    for m in manifests:
        reg.register(m, source_name="test")
    return reg


def test_reclamps_agent_owl_whose_bounds_exceed_ceiling():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    wide = _m("scout", origin="agent", created_by="secretary",
              creation_ceiling=ceiling, bounds=BoundsSpec(tools=frozenset({"read_file", "shell"})))
    reg = _reg(wide)
    revalidate_agent_owls(reg)
    assert reg.get("scout").bounds.tools == frozenset({"read_file"})  # shell clamped away


def test_agent_owl_without_ceiling_fails_closed_to_empty_bounds():
    rogue = _m("ghost", origin="agent", created_by="x",
               creation_ceiling=None, bounds=BoundsSpec(tools=frozenset({"shell"})))
    reg = _reg(rogue)
    revalidate_agent_owls(reg)
    assert reg.get("ghost").bounds.tools == frozenset()  # deny-all, loads but powerless


def test_human_and_builtin_owls_untouched():
    human = _m("h", origin="human", bounds=BoundsSpec(tools=frozenset({"shell"})))
    builtin = _m("b", origin="builtin", bounds=None)
    reg = _reg(human, builtin)
    revalidate_agent_owls(reg)
    assert reg.get("h").bounds.tools == frozenset({"shell"})
    assert reg.get("b").bounds is None


def test_is_idempotent():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    wide = _m("scout", origin="agent", created_by="s", creation_ceiling=ceiling,
              bounds=BoundsSpec(tools=frozenset({"read_file", "shell"})))
    reg = _reg(wide)
    revalidate_agent_owls(reg)
    revalidate_agent_owls(reg)
    assert reg.get("scout").bounds.tools == frozenset({"read_file"})


def test_one_bad_owl_does_not_abort_the_rest():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    good = _m("good", origin="agent", created_by="s", creation_ceiling=ceiling,
              bounds=BoundsSpec(tools=frozenset({"read_file", "shell"})))
    rogue = _m("ghost", origin="agent", created_by="x", creation_ceiling=None,
               bounds=BoundsSpec(tools=frozenset({"shell"})))
    reg = _reg(good, rogue)
    revalidate_agent_owls(reg)  # must not raise
    assert reg.get("good").bounds.tools == frozenset({"read_file"})
    assert reg.get("ghost").bounds.tools == frozenset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/owls/test_owl_revalidator.py -v`
Expected: FAIL — `ModuleNotFoundError: stackowl.owls.owl_revalidator`.

- [ ] **Step 3: Implement**

```python
# src/stackowl/owls/owl_revalidator.py
"""Boot re-clamp for agent-minted owls (owl_build Aa).

Consistency belt-and-suspenders (partial write / hot reload / a bounded creator),
NOT an anti-tamper control — the single-user owner of the filesystem is out of scope.
Runs after from_settings + register_builtin_personas, before serve.
"""
from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.authz.bounds_guard import effective_bounds
from stackowl.logger import log

_DENY_ALL = BoundsSpec(tools=frozenset())


def revalidate_agent_owls(registry: "OwlRegistry") -> None:  # noqa: F821 (forward ref via TYPE_CHECKING below)
    """Re-clamp every origin='agent' owl to bounds ∩ creation_ceiling. Fail-safe per owl."""
    for manifest in list(registry.all()):
        if manifest.origin != "agent":
            continue
        name = manifest.name
        try:
            if manifest.creation_ceiling is None:
                # The tool ALWAYS persists a ceiling for agent owls; absence = corruption/tamper.
                log.engine.error(
                    "owl_revalidator: agent owl missing ceiling, failing closed to deny-all",
                    extra={"owl": name},
                )
                registry.replace(manifest.model_copy(update={"bounds": _DENY_ALL}))
                continue
            current = manifest.bounds if manifest.bounds is not None else BoundsSpec()
            clamped = effective_bounds(current, manifest.creation_ceiling)
            if clamped.tools != (manifest.bounds.tools if manifest.bounds else None):
                log.engine.info(
                    "owl_revalidator: re-clamped agent owl bounds",
                    extra={"owl": name, "tools": sorted(clamped.tools or frozenset())},
                )
                registry.replace(manifest.model_copy(update={"bounds": clamped}))
        except Exception as exc:  # one bad owl must not abort boot
            log.engine.error(
                "owl_revalidator: failed to re-clamp owl, forcing deny-all",
                exc,
                extra={"owl": name},
            )
            try:
                registry.replace(manifest.model_copy(update={"bounds": _DENY_ALL}))
            except Exception as inner:  # last resort: log, never crash boot
                log.engine.error("owl_revalidator: deny-all fallback failed", inner, extra={"owl": name})


if False:  # pragma: no cover — typing only
    from stackowl.owls.registry import OwlRegistry
```

> Note on `effective_bounds(current, ceiling)`: with two non-None specs it intersects the `tools` frozensets (verify against `bounds_guard.py:42-57` — if `effective_bounds` returns a spec whose `tools` is `None` for "unbounded", an empty-tools `current` must still clamp correctly; the test `test_reclamps...` will catch a wrong direction). Adjust the `if clamped.tools != ...` comparison to whatever `replace`-triggering condition is correct, but the four behaviors the tests assert are the contract.

Replace the `log.engine.error(..., exc, extra=...)` / `log.engine.info(..., extra=...)` calls with whatever the actual `log` API in this repo is (check `dna_hydrator.py` for the exact signature — it may be `log.engine.error("msg", err, {...})` positional, not `extra=`). Match the existing call style exactly.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/owls/test_owl_revalidator.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/owl_revalidator.py && uv run ruff check src/stackowl/owls/owl_revalidator.py
git add v2/src/stackowl/owls/owl_revalidator.py v2/tests/owls/test_owl_revalidator.py
git commit -m "feat(v2): revalidate_agent_owls boot re-clamp (fail-closed, fail-safe) — owl_build Aa"
```

---

### Task 5: Wire `revalidate_agent_owls` into orchestrator boot

**Files:**
- Modify: `src/stackowl/startup/orchestrator.py:180-182` (right after `hydrate_dna`)
- Test: `tests/startup/test_orchestrator_owl_revalidate.py` (create) — assert it's invoked in the boot sequence

- [ ] **Step 1: Write the failing test**

```python
# tests/startup/test_orchestrator_owl_revalidate.py
import inspect
from stackowl.startup import orchestrator


def test_orchestrator_calls_revalidate_agent_owls_after_personas():
    src = inspect.getsource(orchestrator)
    assert "revalidate_agent_owls" in src, "boot must re-clamp agent owls"
    # ordering: personas registered, then DNA hydrated, then owl bounds revalidated
    i_personas = src.index("register_builtin_personas")
    i_reval = src.index("revalidate_agent_owls")
    assert i_personas < i_reval, "revalidate must run after personas are registered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/startup/test_orchestrator_owl_revalidate.py -v`
Expected: FAIL — `revalidate_agent_owls` not referenced.

- [ ] **Step 3: Wire it in**

In `orchestrator.py`, immediately after the `await hydrate_dna(owl_registry, db_pool)` line (`:182`), add:

```python
        from stackowl.owls.owl_revalidator import revalidate_agent_owls

        revalidate_agent_owls(owl_registry)
```

(Synchronous — it only touches the in-memory registry. Mirror the local-import style already used for `hydrate_dna` at `:180`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/startup/test_orchestrator_owl_revalidate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/startup/orchestrator.py && uv run ruff check src/stackowl/startup/orchestrator.py
git add v2/src/stackowl/startup/orchestrator.py v2/tests/startup/test_orchestrator_owl_revalidate.py
git commit -m "feat(v2): wire revalidate_agent_owls into boot (after personas+dna) — owl_build Aa"
```

> **Aa is complete here.** A persisted `origin=agent` owl is now provably safe (re-clamped or deny-all'd at boot) even though nothing can create one yet.

---

# STORY Ab — the `owl_build` tool

### Task 6: `OwlBuildSpec` envelope (no authority fields)

**Files:**
- Create: `src/stackowl/tools/meta/owl_build_spec.py`
- Test: `tests/tools/meta/test_owl_build_spec.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/meta/test_owl_build_spec.py
import pytest
from pydantic import ValidationError
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec, validate_owl_build_spec


def test_create_with_preset_is_valid():
    s = OwlBuildSpec(action="create", name="researcher", preset="researcher", specialty="literature review")
    assert validate_owl_build_spec(s) is None


def test_preset_xor_explicit_tools():
    s = OwlBuildSpec(action="create", name="x", preset="researcher",
                     explicit_tools=["read_file"], specialty="z")
    assert validate_owl_build_spec(s) is not None  # both set → error string


def test_create_requires_specialty():
    s = OwlBuildSpec(action="create", name="x", preset="researcher")
    assert validate_owl_build_spec(s) is not None


def test_no_authority_fields_accepted():
    # extra="forbid" → supplying origin/bounds/creation_ceiling is a hard error
    with pytest.raises(ValidationError):
        OwlBuildSpec(action="create", name="x", preset="researcher",
                     specialty="z", origin="agent")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        OwlBuildSpec(action="create", name="x", preset="researcher",
                     specialty="z", creation_ceiling={})  # type: ignore[call-arg]


def test_retire_needs_only_name():
    s = OwlBuildSpec(action="retire", name="researcher")
    assert validate_owl_build_spec(s) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/meta/test_owl_build_spec.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

```python
# src/stackowl/tools/meta/owl_build_spec.py
"""Agent-facing envelope for owl_build. Deliberately carries NO authority fields
(origin/created_by/creation_ceiling/bounds) — the tool forces those server-side."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class OwlBuildSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["create", "edit", "retire"]
    name: str
    preset: str | None = None
    explicit_tools: list[str] | None = None
    specialty: str | None = None
    model_tier: str | None = None


def validate_owl_build_spec(spec: OwlBuildSpec) -> str | None:
    """Structural validation. Returns an error string, or None when valid. Never raises."""
    if not spec.name or not spec.name.strip():
        return "owl name is required."
    if spec.action == "retire":
        return None  # name-only
    if spec.action == "create":
        if spec.preset and spec.explicit_tools:
            return "provide either 'preset' or 'explicit_tools', not both."
        if not spec.preset and not spec.explicit_tools:
            return "create requires a 'preset' or 'explicit_tools'."
        if not spec.specialty or not spec.specialty.strip():
            return "create requires a 'specialty' describing the owl's standing role."
        return None
    # edit
    if spec.preset and spec.explicit_tools:
        return "provide either 'preset' or 'explicit_tools', not both."
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/meta/test_owl_build_spec.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/tools/meta/owl_build_spec.py && uv run ruff check src/stackowl/tools/meta/owl_build_spec.py
git add v2/src/stackowl/tools/meta/owl_build_spec.py v2/tests/tools/meta/test_owl_build_spec.py
git commit -m "feat(v2): OwlBuildSpec envelope (no authority fields) — owl_build Ab"
```

---

### Task 7: Authority core — `SAFE_DEFAULT_CEILING`, `resolve_creation_ceiling`, `clamp_bounds`, `build_agent_manifest`

**Files:**
- Create: `src/stackowl/tools/meta/owl_build_authz.py`
- Test: `tests/tools/meta/test_owl_build_authz.py` (create)

These are pure-ish functions (no consent, no I/O) so the security math is unit-testable in isolation.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/meta/test_owl_build_authz.py
from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.tools.meta.owl_build_authz import (
    SAFE_DEFAULT_CEILING, clamp_bounds, resolve_creation_ceiling, build_agent_manifest,
)
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec


def _reg_with(name, bounds):
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name=name, role=name, system_prompt="p",
                                  model_tier="balanced", bounds=bounds), source_name="t")
    return reg


def test_unbounded_creator_gets_safe_default_ceiling():
    reg = _reg_with("secretary", None)  # unbounded
    ceiling = resolve_creation_ceiling("secretary", None, reg)
    assert ceiling == SAFE_DEFAULT_CEILING
    assert "shell" not in (ceiling.tools or frozenset())
    assert "read_file" in (ceiling.tools or frozenset())


def test_bounded_creator_ceiling_is_its_floor():
    reg = _reg_with("narrow", BoundsSpec(tools=frozenset({"read_file"})))
    ceiling = resolve_creation_ceiling("narrow", None, reg)
    assert ceiling.tools == frozenset({"read_file"})


def test_clamp_drops_tools_above_ceiling():
    requested = BoundsSpec(tools=frozenset({"read_file", "shell"}))
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    clamped, dropped = clamp_bounds(requested, ceiling)
    assert clamped.tools == frozenset({"read_file"})
    assert dropped == frozenset({"shell"})


def test_build_agent_manifest_forces_authority():
    reg = _reg_with("secretary", None)
    spec = OwlBuildSpec(action="create", name="scout", preset="researcher", specialty="recon")
    manifest, dropped = build_agent_manifest(spec, creator="secretary", parent_ceiling=None, registry=reg)
    assert manifest.origin == "agent"
    assert manifest.created_by == "secretary"
    assert manifest.creation_ceiling == SAFE_DEFAULT_CEILING
    assert manifest.name == "scout"
    # researcher preset has no shell anyway; the clamp is the safety net
    assert "shell" not in (manifest.bounds.tools or frozenset())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/meta/test_owl_build_authz.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

```python
# src/stackowl/tools/meta/owl_build_authz.py
"""owl_build authority core (no consent, no I/O). The clamp is a no-op for an
unbounded creator, so unbounded creators get SAFE_DEFAULT_CEILING — consequential
tools must then be explicitly widened by the human at consent."""
from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.authz.bounds_guard import effective_bounds
from stackowl.owls.builder import OwlSpec, SpecialistOwlBuilder
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.tool_presets import ROUTER_TOOLS
from stackowl.pipeline.authz_compose import child_floor
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec

# Read-only-ish: research/read + the discovery+delegation router tools. NO shell /
# execute / write / process. Must NOT be frozenset() (that denies discovery — footgun).
SAFE_DEFAULT_CEILING = BoundsSpec(
    tools=frozenset({"read_file", "memory", "web_search", "web_fetch"}) | ROUTER_TOOLS
)


def resolve_creation_ceiling(
    creator: str, parent_ceiling: BoundsSpec | None, registry: OwlRegistry
) -> BoundsSpec:
    """The creator's effective floor, or SAFE_DEFAULT_CEILING when the creator is unbounded."""
    floor = child_floor(creator, parent_ceiling, registry)
    return floor if floor is not None else SAFE_DEFAULT_CEILING


def clamp_bounds(requested: BoundsSpec, ceiling: BoundsSpec) -> tuple[BoundsSpec, frozenset[str]]:
    """Return (requested ∩ ceiling, dropped_tools)."""
    clamped = effective_bounds(requested, ceiling)
    req = requested.tools or frozenset()
    kept = clamped.tools or frozenset()
    return clamped, req - kept


def build_agent_manifest(
    spec: OwlBuildSpec, *, creator: str, parent_ceiling: BoundsSpec | None, registry: OwlRegistry
) -> tuple[OwlAgentManifest, frozenset[str]]:
    """Build via SpecialistOwlBuilder, then force authority + clamp. Returns (manifest, dropped)."""
    owl_spec = OwlSpec(
        name=spec.name,
        specialty=spec.specialty or spec.name,
        preset=spec.preset,
        explicit_tools=tuple(spec.explicit_tools) if spec.explicit_tools else None,
        model_tier=spec.model_tier,
    )
    built = SpecialistOwlBuilder().build(owl_spec)
    ceiling = resolve_creation_ceiling(creator, parent_ceiling, registry)
    clamped, dropped = clamp_bounds(built.bounds or BoundsSpec(), ceiling)
    manifest = built.model_copy(
        update={
            "bounds": clamped,
            "origin": "agent",
            "created_by": creator,
            "creation_ceiling": ceiling,
        }
    )
    return manifest, dropped
```

> **IMPORTANT — verify `OwlSpec`'s real field names** against `owls/builder.py:20-40` before running. The kwargs above (`name`/`specialty`/`preset`/`explicit_tools`/`model_tier`) are the expected shape; if the dataclass uses `role` instead of `specialty` or `tools` instead of `explicit_tools`, adjust the `OwlSpec(...)` call to match. Do NOT change `OwlSpec` — adapt the call. Likewise confirm `SpecialistOwlBuilder` is instantiated `()` then `.build(spec)` (vs a classmethod) per `builder.py:57`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/meta/test_owl_build_authz.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/tools/meta/owl_build_authz.py && uv run ruff check src/stackowl/tools/meta/owl_build_authz.py
git add v2/src/stackowl/tools/meta/owl_build_authz.py v2/tests/tools/meta/test_owl_build_authz.py
git commit -m "feat(v2): owl_build authority core (safe-default ceiling, clamp, manifest forge) — owl_build Ab"
```

---

### Task 8: Tool skeleton — manifest, consent, dispatch; register it; child-exclude it

**Files:**
- Create: `src/stackowl/tools/meta/owl_build.py`
- Modify: `src/stackowl/tools/registry.py:445-450` (register `OwlBuildTool`)
- Modify: `src/stackowl/pipeline/steps/execute.py:44-46` (add `"owl_build"` to `_CHILD_EXCLUDED_TOOLS`)
- Test: `tests/tools/meta/test_owl_build_skeleton.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/meta/test_owl_build_skeleton.py
from stackowl.pipeline.steps.execute import _CHILD_EXCLUDED_TOOLS
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ToolRegistry


def test_owl_build_is_consequential_and_isolated():
    m = OwlBuildTool().manifest
    assert m.name == "owl_build"
    assert m.action_severity == "consequential"
    assert m.toolset_group  # has its own isolated group (not the default)


def test_owl_build_registered_in_defaults():
    reg = ToolRegistry.with_defaults()
    assert reg.get("owl_build") is not None


def test_owl_build_is_child_excluded():
    assert "owl_build" in _CHILD_EXCLUDED_TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/meta/test_owl_build_skeleton.py -v`
Expected: FAIL — module/tool not defined.

- [ ] **Step 3a: Create the tool skeleton**

```python
# src/stackowl/tools/meta/owl_build.py
"""owl_build — the self-extending owl-builder (Phase-2 A). Mirrors tools/meta/tool_build.py:
consequential, consent fail-closed off-TTY, persist+audit+register-with-rollback.
Authority is forced server-side; OwlBuildSpec carries none."""
from __future__ import annotations

from typing import Any

from stackowl.infra.trace import TraceContext
from stackowl.logger import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec, validate_owl_build_spec

_TOOLSET_GROUP = "owl_admin"  # isolated: a read-only owl never gets this hydrated
_CONSENT_CATEGORY = "owl_build"
_SOURCE_NAME = "agent_owls"
_VALID_ACTIONS = ("create", "edit", "retire")
# Tools whose presence in a new owl is a real privilege; flagged at consent.
# Sourced from severity later; a name-set is the minimal first cut.
_CONSEQUENTIAL_TOOL_NAMES = frozenset(
    {"shell", "execute_code", "write_file", "process", "sessions_spawn", "web_fetch"}
)


class OwlBuildTool(Tool):
    @property
    def name(self) -> str:
        return "owl_build"

    @property
    def description(self) -> str:
        return (
            "RARE. Create/edit/retire a SPECIALIST OWL — a standing, named, reusable persona. "
            "Almost every request is NOT this: first answer directly; if it needs a specialist, "
            "delegate_task to an EXISTING owl; only mint a new owl for a recurring role the human "
            "will reuse. Doing a research task once is NOT a reason to mint a research owl — do the "
            "task. Requires human approval (consequential). Fails closed with no interactive user."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_VALID_ACTIONS)},
                "name": {"type": "string", "description": "owl name (identity; lowercase)"},
                "preset": {"type": "string", "description": "a built-in preset role (XOR explicit_tools)"},
                "explicit_tools": {"type": "array", "items": {"type": "string"}},
                "specialty": {"type": "string", "description": "the standing role (required for create)"},
                "model_tier": {"type": "string"},
            },
            "required": ["action", "name"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            toolset_group=_TOOLSET_GROUP,
        )

    async def execute(self, **kwargs: Any) -> str:
        log.tool.debug("owl_build.execute: entry", {"action": kwargs.get("action"), "name": kwargs.get("name")})
        try:
            spec = OwlBuildSpec.model_validate(kwargs)
        except Exception as exc:
            log.tool.error("owl_build.execute: malformed spec", exc, {"args": list(kwargs.keys())})
            return self._err(f"invalid owl_build request: {exc}")
        err = validate_owl_build_spec(spec)
        if err is not None:
            log.tool.debug("owl_build.execute: spec rejected", {"reason": err})
            return self._err(err)
        # Defense-in-depth: depth-0 only (also enforced at execute.py dispatch).
        ctx = TraceContext.get()
        if int(ctx.get("delegation_depth", 0) or 0) > 0:
            log.tool.error("owl_build.execute: refused at depth>0", None, {"depth": ctx.get("delegation_depth")})
            return self._err("owl_build is only available to the root owl (refused for sub-agents).")
        try:
            if spec.action == "create":
                return await self._create(spec)
            if spec.action == "edit":
                return await self._edit(spec)
            return await self._retire(spec)
        except Exception as exc:
            log.tool.error("owl_build.execute: unhandled failure", exc, {"action": spec.action, "name": spec.name})
            return self._err(f"owl_build failed: {exc}")

    # --- consent (fail-closed off-TTY; mirrors tool_build._consent_or_refuse) ---
    async def _consent_or_refuse(self, summary: str, name: str) -> str | None:
        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")
        if not interactive or not channel or not session_id:
            log.tool.error("owl_build: no interactive user, failing closed", None, {"owl": name})
            return (f"refused: creating/modifying owl '{name}' needs your approval and no "
                    "interactive user is present (fail closed).")
        gate = get_services().consent_gate
        if gate is None:
            log.tool.error("owl_build: no consent gate available", None, {"owl": name})
            return f"refused: no consent gate available to approve owl '{name}'."
        try:
            allowed = await gate.policy.request(
                tool_name=self.name, channel=channel, session_id=session_id,
                category=_CONSENT_CATEGORY, summary=summary,
            )
        except Exception as exc:
            log.tool.error("owl_build: consent check failed", exc, {"owl": name})
            return f"refused: consent check failed for owl '{name}'."
        if not allowed:
            return f"declined by user — owl '{name}' was not changed."
        return None

    # placeholders implemented in Tasks 9 & 10
    async def _create(self, spec: OwlBuildSpec) -> str:
        raise NotImplementedError

    async def _edit(self, spec: OwlBuildSpec) -> str:
        raise NotImplementedError

    async def _retire(self, spec: OwlBuildSpec) -> str:
        raise NotImplementedError

    @staticmethod
    def _ok(msg: str) -> str:
        return msg

    @staticmethod
    def _err(msg: str) -> str:
        return f"owl_build: {msg}"
```

> Match `Tool`'s actual base contract (`name`/`description`/`parameters` may be class attrs not properties; `execute` signature `(**kwargs)` vs `(self, args: dict)`). Read `tools/base.py` and `tools/meta/tool_build.py:57-127` and mirror EXACTLY. Confirm the `log.tool.error(msg, exc, fields)` positional signature against `tool_build.py`.

- [ ] **Step 3b: Register the tool** — in `tools/registry.py`, after the `registry.register(ToolBuildTool())` block (`:445-450`):

```python
    # owl_build — self-extending owl-builder (Phase-2 A): consequential, consent-gated,
    # child-excluded. Lets the root owl mint/edit/retire specialist owls.
    from stackowl.tools.meta.owl_build import OwlBuildTool

    registry.register(OwlBuildTool())
```

(Place the import with the other meta-tool imports near `:357` if that's the file's convention; otherwise the local import above is fine — match the existing style.)

- [ ] **Step 3c: Child-exclude it** — in `pipeline/steps/execute.py:44-46`, add `"owl_build"`:

```python
_CHILD_EXCLUDED_TOOLS = frozenset(
    {"delegate_task", "sessions_spawn", "sessions_send", "process", "execute_code", "owl_build"}
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/meta/test_owl_build_skeleton.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/tools/meta/owl_build.py src/stackowl/tools/registry.py src/stackowl/pipeline/steps/execute.py
uv run ruff check src/stackowl/tools/meta/owl_build.py src/stackowl/tools/registry.py src/stackowl/pipeline/steps/execute.py
git add v2/src/stackowl/tools/meta/owl_build.py v2/src/stackowl/tools/registry.py v2/src/stackowl/pipeline/steps/execute.py v2/tests/tools/meta/test_owl_build_skeleton.py
git commit -m "feat(v2): owl_build tool skeleton (consent, dispatch, registered, child-excluded) — owl_build Ab"
```

---

### Task 9: `_create` — guardrails + persist + audit + register-with-rollback

**Files:**
- Modify: `src/stackowl/tools/meta/owl_build.py` (`_create` + helpers)
- Create: `src/stackowl/tools/meta/owl_build_guards.py` (name-quality, existence-check, soft-cap, consent summary — pure/testable)
- Test: `tests/tools/meta/test_owl_build_guards.py`, extend `tests/tools/meta/test_owl_build_gateway.py` (Task 11)

- [ ] **Step 1: Write the failing test (guards unit)**

```python
# tests/tools/meta/test_owl_build_guards.py
from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.meta.owl_build_guards import (
    name_quality_error, count_agent_owls, consent_summary, MAX_AGENT_OWLS,
)


def _reg(*names):
    reg = OwlRegistry()
    for n in names:
        reg.register(OwlAgentManifest(name=n, role=n, system_prompt="p",
                                      model_tier="balanced", origin="agent",
                                      created_by="secretary",
                                      creation_ceiling=BoundsSpec(tools=frozenset()),
                                      bounds=BoundsSpec(tools=frozenset())), source_name="t")
    return reg


def test_name_quality_rejects_trailing_digit_duplicate():
    reg = _reg("researcher")
    # "researcher2" is structurally a near-dup of "researcher" (strip trailing digits)
    assert name_quality_error("researcher2", reg) is not None


def test_name_quality_rejects_too_short_or_numeric():
    reg = OwlRegistry()
    assert name_quality_error("a", reg) is not None
    assert name_quality_error("123", reg) is not None


def test_name_quality_accepts_distinct_name():
    reg = _reg("researcher")
    assert name_quality_error("planner", reg) is None


def test_count_agent_owls():
    reg = _reg("a", "b")
    assert count_agent_owls(reg) == 2


def test_consent_summary_flags_consequential_and_lists_dropped():
    summary = consent_summary(
        name="coder", role="writes code",
        resolved_tools=frozenset({"read_file", "shell"}),
        dropped=frozenset({"process"}),
        roster=("secretary", "researcher"),
        why="needs to run builds",
    )
    assert "coder" in summary
    assert "shell" in summary and "⚠" in summary  # consequential flagged
    assert "process" in summary  # dropped surfaced
    assert "researcher" in summary  # roster surfaced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/meta/test_owl_build_guards.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3a: Implement guards (language-neutral name-quality — NO English wordlist)**

```python
# src/stackowl/tools/meta/owl_build_guards.py
"""Pure guardrails for owl_build: name quality, soft-cap, consent summary rendering.
Name-quality is STRUCTURAL (no hardcoded English keywords — platform is multilingual)."""
from __future__ import annotations

import re
import unicodedata

from stackowl.owls.registry import OwlRegistry
from stackowl.tools.meta.owl_build_authz import _CONSEQUENTIAL_TOOL_NAMES  # reuse the flag set

MAX_AGENT_OWLS = 5
_TRAILING_DIGITS = re.compile(r"\d+$")


def count_agent_owls(registry: OwlRegistry) -> int:
    return sum(1 for m in registry.all() if m.origin == "agent")


def _grapheme_len(s: str) -> int:
    return len([c for c in s if not unicodedata.combining(c)])


def name_quality_error(name: str, registry: OwlRegistry) -> str | None:
    """Reject low-information / near-duplicate names. Structural only. Returns error or None."""
    n = name.strip().lower()
    if _grapheme_len(n) < 2:
        return "owl name is too short to be a meaningful identity."
    if n.isdigit():
        return "owl name must not be purely numeric."
    existing = {m.name.lower() for m in registry.all()}
    if n in existing:
        return f"an owl named '{name}' already exists."
    # near-dup: strip a trailing run of digits and compare to existing stems
    stem = _TRAILING_DIGITS.sub("", n)
    if stem and stem != n and stem in existing:
        return f"'{name}' is a near-duplicate of existing owl '{stem}' — delegate to it instead."
    return None


def consent_summary(
    *, name: str, role: str, resolved_tools: frozenset[str], dropped: frozenset[str],
    roster: tuple[str, ...], why: str,
) -> str:
    """Render the human-facing consent prompt. The human is the real clamp, so show everything."""
    def fmt(t: str) -> str:
        return f"⚠ {t}" if t in _CONSEQUENTIAL_TOOL_NAMES else t

    tools_line = ", ".join(fmt(t) for t in sorted(resolved_tools)) or "(none)"
    lines = [
        f"Create owl '{name}' — {role}",
        f"Tools (after clamp): {tools_line}",
    ]
    if dropped:
        lines.append(f"Dropped (above your authority): {', '.join(sorted(dropped))}")
    if roster:
        lines.append(f"You already have {len(roster)} owl(s): {', '.join(roster)}")
    lines.append(f"Model's stated reason: {why}")
    lines.append("⚠ flags consequential tools (shell/exec/write/network). Approve only if intended.")
    return "\n".join(lines)
```

- [ ] **Step 3b: Implement `_create` in `owl_build.py`** (replace the `NotImplementedError` body)

```python
    async def _create(self, spec: OwlBuildSpec) -> str:
        from stackowl.commands.owls_helpers import manifest_to_yaml_entry
        from stackowl.commands.owls_command import _upsert_to_yaml
        from stackowl.owls.registry import _SECRETARY_NAME
        from stackowl.tools.meta.owl_build_authz import build_agent_manifest
        from stackowl.tools.meta.owl_build_guards import (
            consent_summary, count_agent_owls, name_quality_error, MAX_AGENT_OWLS,
        )
        from stackowl.tools.meta.owl_build_existence import existing_near_match

        svc = get_services()
        registry = svc.owl_registry
        ctx = TraceContext.get()
        creator = ctx.get("owl_name") or _SECRETARY_NAME

        # 1. collision / reserved names
        if spec.name.lower() == _SECRETARY_NAME or registry.get(spec.name) is not None:
            return self._err(f"an owl named '{spec.name}' already exists (or is reserved).")
        # 2. structural name quality
        nq = name_quality_error(spec.name, registry)
        if nq is not None:
            return self._err(nq)
        # 3. soft cap (hard gate BEFORE consent so a confused loop can't spam approvals)
        if count_agent_owls(registry) >= MAX_AGENT_OWLS:
            return self._err(
                f"you already have {MAX_AGENT_OWLS} agent-created owls — retire one or "
                "delegate to an existing owl instead of minting another."
            )
        # 4. existence redirect (semantic near-match → suggest delegate; fail-open)
        match = await existing_near_match(spec, registry, svc)
        if match is not None:
            return self._err(
                f"an existing owl '{match}' already covers this — delegate_task to it instead of minting a duplicate."
            )
        # 5. forge the clamped, authority-stamped manifest
        manifest, dropped = build_agent_manifest(
            spec, creator=creator, parent_ceiling=TraceContext.creation_ceiling(), registry=registry
        )
        log.tool.debug("owl_build._create: clamped", {"owl": spec.name, "dropped": sorted(dropped)})
        # 6. consent — the real gate (fail-closed off-TTY; prompt shows the resolved toolset)
        summary = consent_summary(
            name=manifest.name, role=manifest.role,
            resolved_tools=(manifest.bounds.tools or frozenset()) if manifest.bounds else frozenset(),
            dropped=dropped,
            roster=tuple(m.name for m in registry.all() if m.origin == "agent"),
            why=spec.specialty or "",
        )
        refusal = await self._consent_or_refuse(summary, manifest.name)
        if refusal is not None:
            return refusal
        # 7. persist (snapshot for rollback) → audit → register-with-rollback
        snapshot = self._yaml_snapshot()
        try:
            _upsert_to_yaml(manifest_to_yaml_entry(manifest))
        except Exception as exc:
            log.tool.error("owl_build._create: persist failed", exc, {"owl": manifest.name})
            return self._err(f"failed to persist owl '{manifest.name}': {exc}")
        self._audit("create", manifest.name, creator)
        try:
            registry.register(manifest, source_name=_SOURCE_NAME)
        except Exception as exc:
            log.tool.error("owl_build._create: register failed, rolling back yaml", exc, {"owl": manifest.name})
            self._yaml_restore(snapshot)
            return self._err(f"failed to register owl '{manifest.name}' (rolled back): {exc}")
        log.tool.debug("owl_build._create: exit", {"owl": manifest.name, "tools": sorted((manifest.bounds.tools or frozenset()))})
        return self._ok(
            f"Created owl '{manifest.name}' ({manifest.role}). "
            f"Tools: {', '.join(sorted(manifest.bounds.tools or frozenset())) or '(none)'}. "
            + (f"Dropped above your authority: {', '.join(sorted(dropped))}. " if dropped else "")
            + "Delegate to it with delegate_task."
        )
```

Add the yaml snapshot/restore + audit helpers to the class:

```python
    @staticmethod
    def _yaml_snapshot() -> bytes | None:
        from stackowl.paths import config_file
        path = config_file()
        try:
            return path.read_bytes() if path.exists() else None
        except Exception as exc:
            log.tool.error("owl_build: yaml snapshot failed", exc, {})
            return None

    @staticmethod
    def _yaml_restore(snapshot: bytes | None) -> None:
        from stackowl.paths import config_file
        path = config_file()
        try:
            if snapshot is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(snapshot)
        except Exception as exc:
            log.tool.error("owl_build: yaml rollback failed", exc, {})

    @staticmethod
    def _audit(action: str, name: str, actor: str) -> None:
        store = get_services().skill_store
        if store is None:
            log.engine.info("owl_build.audit", {"action": action, "owl": name, "actor": actor})
            return
        try:
            store.audit_write(source="learned", action=f"owl_{action}", name=name, actor=actor)
        except Exception as exc:
            log.tool.error("owl_build: audit_write failed", exc, {"owl": name, "action": action})
```

> **Verify against the codebase:** (a) `get_services()` exposes `owl_registry` and `skill_store` — confirm field names in `pipeline/services.py`; if `owl_registry` isn't on services, find how `execute.py` reaches the registry and use that path. (b) `paths.config_file()` is the actual accessor for `stackowl.yaml` — check `paths.py`/`owls_command.py:275-296` for how `_upsert_to_yaml` locates the file, and snapshot the SAME file. (c) `skill_store.audit_write(...)` signature — mirror `tool_build.py:391-420` exactly (reuse its `source="learned"` lane). (d) `TraceContext.get()` key for the owl is `owl_name` and for depth is `delegation_depth` — confirm in `infra/trace.py`.

- [ ] **Step 3c: Implement the existence-check (fail-open)**

```python
# src/stackowl/tools/meta/owl_build_existence.py
"""Semantic near-match: refuse a create that duplicates an existing owl, redirect to delegate.
Fails OPEN (no embedder → name-equality only, already covered by collision-check)."""
from __future__ import annotations

from stackowl.logger import log
from stackowl.memory.sqlite_helpers import cosine_similarity
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec

_SIMILARITY_THRESHOLD = 0.85


async def existing_near_match(spec: OwlBuildSpec, registry: OwlRegistry, services) -> str | None:
    """Return the name of a semantically near-identical existing owl, or None."""
    reg = getattr(services, "embedding_registry", None)
    if reg is None:
        return None  # fail-open
    others = [m for m in registry.all()]
    if not others:
        return None
    query = f"{spec.name} {spec.specialty or ''}".strip()
    try:
        provider = reg.get()
        texts = [query] + [f"{m.name} {m.role}" for m in others]
        vectors = await provider.embed(texts)
    except Exception as exc:
        log.tool.error("owl_build.existence: embed failed, failing open", exc, {"owl": spec.name})
        return None
    q = vectors[0]
    best_name, best_score = None, -1.0
    for m, vec in zip(others, vectors[1:]):
        score = cosine_similarity(q, vec)
        if score is not None and score > best_score:
            best_name, best_score = m.name, score
    if best_name is not None and best_score >= _SIMILARITY_THRESHOLD:
        log.tool.debug("owl_build.existence: near-match", {"owl": spec.name, "match": best_name, "score": best_score})
        return best_name
    return None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tools/meta/test_owl_build_guards.py -v`
Expected: PASS (5 tests). (Full `_create` path is exercised by the gateway journeys in Task 11.)

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/tools/meta/owl_build.py src/stackowl/tools/meta/owl_build_guards.py src/stackowl/tools/meta/owl_build_existence.py
uv run ruff check src/stackowl/tools/meta/owl_build.py src/stackowl/tools/meta/owl_build_guards.py src/stackowl/tools/meta/owl_build_existence.py
git add v2/src/stackowl/tools/meta/owl_build.py v2/src/stackowl/tools/meta/owl_build_guards.py v2/src/stackowl/tools/meta/owl_build_existence.py v2/tests/tools/meta/test_owl_build_guards.py
git commit -m "feat(v2): owl_build create path (guards, existence-redirect, persist+rollback) — owl_build Ab"
```

---

### Task 10: `_edit` / `_retire` — no-edit-your-betters + re-clamp + re-consent on widening

**Files:**
- Modify: `src/stackowl/tools/meta/owl_build.py` (`_edit`, `_retire`)
- Test: `tests/tools/meta/test_owl_build_betters.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/meta/test_owl_build_betters.py
from stackowl.tools.meta.owl_build import can_modify  # pure guard extracted for testing


def _owl(origin, created_by):
    class M:
        pass
    m = M()
    m.origin, m.created_by, m.name = origin, created_by, "x"
    return m


def test_cannot_edit_secretary():
    assert can_modify(_owl("builtin", None), caller="secretary", target_name="secretary") is not None


def test_cannot_edit_human_owl():
    assert can_modify(_owl("human", None), caller="secretary", target_name="planner") is not None


def test_cannot_edit_another_agents_owl():
    assert can_modify(_owl("agent", "other_owl"), caller="secretary", target_name="scout") is not None


def test_can_edit_own_agent_owl():
    assert can_modify(_owl("agent", "secretary"), caller="secretary", target_name="scout") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/meta/test_owl_build_betters.py -v`
Expected: FAIL — `can_modify` not defined.

- [ ] **Step 3: Implement `can_modify`, `_edit`, `_retire`**

Add the module-level guard to `owl_build.py`:

```python
def can_modify(manifest, *, caller: str, target_name: str) -> str | None:
    """no-edit-your-betters: only an agent owl YOU minted may be edited/retired. Returns error or None."""
    from stackowl.owls.registry import _SECRETARY_NAME
    if target_name.lower() == _SECRETARY_NAME:
        return "the secretary owl cannot be modified or retired."
    if manifest.origin != "agent":
        return f"'{target_name}' is a {manifest.origin} owl and cannot be modified by owl_build."
    if manifest.created_by != caller:
        return f"'{target_name}' was created by another owl — you may only modify owls you created."
    return None
```

Implement `_edit` (re-clamp against the ORIGINAL persisted ceiling — the monotone ratchet — and re-consent only on widening):

```python
    async def _edit(self, spec: OwlBuildSpec) -> str:
        from stackowl.commands.owls_helpers import manifest_to_yaml_entry
        from stackowl.commands.owls_command import _upsert_to_yaml
        from stackowl.tools.meta.owl_build_authz import build_agent_manifest
        from stackowl.tools.meta.owl_build_guards import consent_summary

        svc = get_services()
        registry = svc.owl_registry
        ctx = TraceContext.get()
        creator = ctx.get("owl_name") or "secretary"
        current = registry.get(spec.name)
        if current is None:
            return self._err(f"no owl named '{spec.name}' to edit.")
        guard = can_modify(current, caller=creator, target_name=spec.name)
        if guard is not None:
            return self._err(guard)
        rebuilt, dropped = build_agent_manifest(
            spec, creator=creator, parent_ceiling=TraceContext.creation_ceiling(), registry=registry
        )
        # monotone ratchet: never widen past the ORIGINAL mint ceiling
        if current.creation_ceiling is not None:
            from stackowl.tools.meta.owl_build_authz import clamp_bounds
            clamped, more_dropped = clamp_bounds(rebuilt.bounds or current.creation_ceiling, current.creation_ceiling)
            rebuilt = rebuilt.model_copy(update={"bounds": clamped, "creation_ceiling": current.creation_ceiling})
            dropped = dropped | more_dropped
        old_tools = (current.bounds.tools or frozenset()) if current.bounds else frozenset()
        new_tools = (rebuilt.bounds.tools or frozenset()) if rebuilt.bounds else frozenset()
        widening = new_tools - old_tools
        if widening:  # re-consent only when adding tools
            summary = consent_summary(
                name=rebuilt.name, role=rebuilt.role, resolved_tools=new_tools, dropped=dropped,
                roster=tuple(m.name for m in registry.all() if m.origin == "agent"),
                why=f"edit adds: {', '.join(sorted(widening))}",
            )
            refusal = await self._consent_or_refuse(summary, rebuilt.name)
            if refusal is not None:
                return refusal
        snapshot = self._yaml_snapshot()
        try:
            _upsert_to_yaml(manifest_to_yaml_entry(rebuilt))
            registry.replace(rebuilt)
        except Exception as exc:
            log.tool.error("owl_build._edit: failed, rolling back", exc, {"owl": rebuilt.name})
            self._yaml_restore(snapshot)
            return self._err(f"failed to edit owl '{rebuilt.name}' (rolled back): {exc}")
        self._audit("edit", rebuilt.name, creator)
        return self._ok(f"Updated owl '{rebuilt.name}'. Tools: {', '.join(sorted(new_tools)) or '(none)'}.")
```

Implement `_retire`:

```python
    async def _retire(self, spec: OwlBuildSpec) -> str:
        from stackowl.commands.owls_command import _remove_from_yaml  # see note
        svc = get_services()
        registry = svc.owl_registry
        ctx = TraceContext.get()
        creator = ctx.get("owl_name") or "secretary"
        current = registry.get(spec.name)
        if current is None:
            return self._err(f"no owl named '{spec.name}' to retire.")
        guard = can_modify(current, caller=creator, target_name=spec.name)
        if guard is not None:
            return self._err(guard)
        snapshot = self._yaml_snapshot()
        try:
            registry.deregister(spec.name)  # already secretary-guarded (defense-in-depth)
            _remove_from_yaml(spec.name)
        except Exception as exc:
            log.tool.error("owl_build._retire: failed, rolling back", exc, {"owl": spec.name})
            self._yaml_restore(snapshot)
            return self._err(f"failed to retire owl '{spec.name}' (rolled back): {exc}")
        self._audit("retire", spec.name, creator)
        return self._ok(f"Retired owl '{spec.name}'.")
```

> **Verify / DRY:** `_remove_from_yaml` may not exist. Check `owls_command.py` for the existing removal helper (there should be one behind `/owls remove`). If it exists, reuse it; if it doesn't, the `_yaml_restore`/snapshot approach already covers durability — implement removal by loading the yaml, dropping the entry by name, and saving, factored into ONE helper next to `_upsert_to_yaml` (do not duplicate yaml-load/save logic — reuse `_upsert_to_yaml`'s load/save primitives). Also confirm `registry.replace`/`deregister` signatures (`registry.py:121,167`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/meta/test_owl_build_betters.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/tools/meta/owl_build.py && uv run ruff check src/stackowl/tools/meta/owl_build.py
git add v2/src/stackowl/tools/meta/owl_build.py v2/tests/tools/meta/test_owl_build_betters.py
git commit -m "feat(v2): owl_build edit/retire (no-edit-your-betters, monotone re-clamp, re-consent on widen) — owl_build Ab"
```

---

### Task 11: Gateway journey tests (J1–J5)

**Files:**
- Create: `tests/tools/meta/test_owl_build_gateway.py` (mirror `test_tool_build_gateway.py`)

These drive `OwlBuildTool.execute` through a REAL `StepServices` (real `ToolRegistry.with_defaults()`, real consent gate, real `OwlRegistry`) with only the AI provider and the TTY/consent toggled. This is the mandatory gateway-layer integration suite (project rule: integration tests from business requirements).

- [ ] **Step 1: Write the journey tests**

```python
# tests/tools/meta/test_owl_build_gateway.py
import pytest
from stackowl.authz.bounds import BoundsSpec
from stackowl.infra.trace import TraceContext
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry, _SECRETARY_NAME
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.registry import ConsequentialActionGate
from stackowl.tools.meta.owl_build import OwlBuildTool

# Reuse the StepServices/_state construction idioms from test_tool_build_gateway.py.
# Build helpers _services(auto_consent=bool) and _state(interactive, channel, owl_name, depth, ceiling)
# that set the StepServices.owl_registry + consent_gate and enter a TraceContext.
# (Copy the _gate/_services/_state scaffolding verbatim, swapping category "tool_build"→"owl_build"
#  and adding an owl_registry to StepServices.)


async def _run(tool, services, ctx_kwargs, **args):
    with TraceContext.scope(**ctx_kwargs):  # match the real TraceContext entry API
        # bind services per however execute.py exposes get_services() in tests
        return await tool.execute(**args)


@pytest.mark.asyncio
async def test_J1_root_mints_researcher_persists_and_survives_restart(owl_build_env):
    """Consent granted → owl persisted + registered + survives a fresh from_settings + revalidate."""
    svc, ctx = owl_build_env(auto_consent=True, interactive=True, creator="secretary")
    out = await OwlBuildTool().execute(action="create", name="scout", preset="researcher", specialty="recon")
    assert "Created owl 'scout'" in out
    assert svc.owl_registry.get("scout") is not None
    # simulate restart: rebuild registry from the persisted yaml + revalidate
    from stackowl.owls.owl_revalidator import revalidate_agent_owls
    reloaded = OwlRegistry.from_settings(svc.settings)
    revalidate_agent_owls(reloaded)
    rebuilt = reloaded.get("scout")
    assert rebuilt is not None and rebuilt.origin == "agent"


@pytest.mark.asyncio
async def test_J2a_unbounded_creator_drops_shell_without_widening(owl_build_env):
    """Default ceiling is read-only-ish → an explicit shell request is dropped."""
    svc, ctx = owl_build_env(auto_consent=True, interactive=True, creator="secretary")
    out = await OwlBuildTool().execute(action="create", name="coder",
                                       explicit_tools=["read_file", "shell"], specialty="builds")
    m = svc.owl_registry.get("coder")
    assert m is not None and "shell" not in (m.bounds.tools or frozenset())
    assert "Dropped" in out


@pytest.mark.asyncio
async def test_J2b_off_tty_refuses_entirely(owl_build_env):
    """No interactive user → fail closed, nothing persisted."""
    svc, ctx = owl_build_env(auto_consent=True, interactive=False, creator="secretary")
    out = await OwlBuildTool().execute(action="create", name="scout", preset="researcher", specialty="recon")
    assert "fail closed" in out.lower() or "refused" in out.lower()
    assert svc.owl_registry.get("scout") is None


@pytest.mark.asyncio
async def test_J3a_cannot_retire_secretary(owl_build_env):
    svc, ctx = owl_build_env(auto_consent=True, interactive=True, creator="secretary")
    out = await OwlBuildTool().execute(action="retire", name=_SECRETARY_NAME)
    assert "cannot be" in out.lower()
    assert svc.owl_registry.get(_SECRETARY_NAME) is not None


@pytest.mark.asyncio
async def test_J3b_cannot_edit_human_owl(owl_build_env):
    svc, ctx = owl_build_env(auto_consent=True, interactive=True, creator="secretary")
    svc.owl_registry.register(
        OwlAgentManifest(name="planner", role="plans", system_prompt="p",
                         model_tier="balanced", origin="human"), source_name="cfg")
    out = await OwlBuildTool().execute(action="edit", name="planner", preset="researcher", specialty="x")
    assert "human owl" in out.lower() or "cannot be modified" in out.lower()


@pytest.mark.asyncio
async def test_J4_sub_agent_cannot_mint(owl_build_env):
    svc, ctx = owl_build_env(auto_consent=True, interactive=True, creator="secretary", depth=1)
    out = await OwlBuildTool().execute(action="create", name="scout", preset="researcher", specialty="recon")
    assert "root owl" in out.lower() or "sub-agent" in out.lower()
    assert svc.owl_registry.get("scout") is None


@pytest.mark.asyncio
async def test_J5_narrow_creator_floor_clamps_new_owl(owl_build_env):
    """A narrow delegator can't mint a broader owl than its own floor."""
    svc, ctx = owl_build_env(auto_consent=True, interactive=True, creator="narrow")
    svc.owl_registry.register(
        OwlAgentManifest(name="narrow", role="narrow", system_prompt="p", model_tier="balanced",
                         bounds=BoundsSpec(tools=frozenset({"read_file"}))), source_name="cfg")
    out = await OwlBuildTool().execute(action="create", name="scout",
                                       explicit_tools=["read_file", "web_fetch"], specialty="recon")
    m = svc.owl_registry.get("scout")
    assert m is not None and (m.bounds.tools or frozenset()) <= frozenset({"read_file"})
```

> **Scaffolding note:** copy the `_ScriptedProvider`/`_gate`/`_services`/`_state` fixtures from `tests/tools/meta/test_tool_build_gateway.py:35-110` and adapt: (1) add `owl_registry=OwlRegistry()` (with the secretary registered) to the `StepServices`; (2) swap consent category `"tool_build"`→`"owl_build"`; (3) provide an `owl_build_env` fixture returning `(services, ctx)` and entering the real `TraceContext` with `owl_name`/`interactive`/`channel`/`session_id`/`delegation_depth`/creation-ceiling set; (4) ensure `get_services()` inside the tool resolves to the test `StepServices` (mirror exactly how `test_tool_build_gateway.py` binds services — it uses the same `get_services()` seam). Confirm `TraceContext`'s real context-manager/entry API (`scope`/`bind`/`enter`) from `infra/trace.py` and the existing journey tests — use whatever they use.

- [ ] **Step 2: Run the journeys**

Run: `uv run pytest tests/tools/meta/test_owl_build_gateway.py -v`
Expected: All FAIL first (fixtures/wiring), then iterate to GREEN. **Per the project rule, if a journey fails because of broken wiring, STOP and report — do not silently rewrite the test to pass.**

- [ ] **Step 3: Make them pass** — fix real wiring uncovered by the journeys (do NOT weaken assertions).

- [ ] **Step 4: Full targeted re-run**

Run: `uv run pytest tests/tools/meta/test_owl_build_gateway.py tests/tools/meta/test_owl_build_skeleton.py tests/owls/test_owl_revalidator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run ruff check tests/tools/meta/test_owl_build_gateway.py
git add v2/tests/tools/meta/test_owl_build_gateway.py
git commit -m "test(v2): owl_build gateway journeys J1-J5 (mint/clamp/off-tty/betters/depth/floor) — owl_build Ab"
```

---

## Self-Review (run against the spec)

**1. Spec coverage:**
- §3.1 manifest fields → Task 1. §3.2 yaml serialization (None-ceiling omitted) → Task 2. §3.3 revalidate (clamp / fail-closed / skip / fail-safe / idempotent) → Task 4, wired Task 5. builtin/human origin stamping → Task 3.
- §4.1 `OwlBuildSpec` no-authority → Task 6. §4.2 `_authorize_and_clamp` + conservative default ceiling → Task 7. §4.3 existence-redirect/name-quality/soft-cap/preset-forced-persona/decision-ladder-description → Tasks 8 (description) + 9 (guards+existence). §4.4 consent fail-closed off-TTY + toolset shown + re-consent on widen → Tasks 8 (consent), 9 (create consent), 10 (edit re-consent). §4.5 create/edit/retire + no-edit-your-betters + monotone re-clamp → Tasks 9, 10.
- §5 security spine: consent-is-gate (8/9), default ceiling (7), authority-forced-server-side (6/7), no-edit-your-betters (10), depth-0-only (8c + 8 runtime check), boot re-clamp (4/5), delegation-floor-holds (J5 in 11). All covered.
- §7 testing: Aa units (1,2,4), Ab units (6,7,9,10), gateway J1-J5 (11). Covered.

**2. Placeholder scan:** No "TBD/TODO". The `NotImplementedError` bodies in Task 8 are intentional skeleton stubs replaced in Tasks 9/10 (explicitly stated). The "verify against codebase" notes are real-codebase-binding instructions (field-name confirmation), not deferred work — each names the exact file:line to confirm. No spec requirement left unimplemented.

**3. Type consistency:** `OwlBuildSpec` fields identical across Tasks 6/7/9/10. `resolve_creation_ceiling`/`clamp_bounds`/`build_agent_manifest`/`SAFE_DEFAULT_CEILING`/`_CONSEQUENTIAL_TOOL_NAMES` defined in Task 7, reused in 9/10 (guards imports `_CONSEQUENTIAL_TOOL_NAMES` from authz). `can_modify` defined+used in Task 10. `revalidate_agent_owls` Task 4, called Task 5/11. `consent_summary`/`name_quality_error`/`count_agent_owls`/`MAX_AGENT_OWLS` defined Task 9 guards, used in create/edit. `_yaml_snapshot`/`_yaml_restore`/`_audit`/`_consent_or_refuse` defined Task 8/9, used 9/10. Consistent.

**Known codebase-binding risks (flagged inline for implementers, not gaps):** exact `OwlSpec` field names (`builder.py:20-40`); `get_services()` exposes `owl_registry`/`skill_store`/`settings`/`embedding_registry` (`services.py`); `paths.config_file()` vs the real yaml accessor; `_remove_from_yaml` existence; `TraceContext` entry API and key names; `log.tool.error(msg, exc, fields)` positional signature; `Tool` base contract (property vs attr). Every one names the file to confirm against — the implementer binds them in-task.

---

## Phase-2 Backlog (tracked, not dropped)
| Item | Why deferred | Revisit |
|---|---|---|
| Agent-authored freehand `system_prompt` | weak-model persona safety; preset-derived only in v1 | follow-up, validated+gated |
| Capability-scaled `SAFE_DEFAULT_CEILING` / `MAX_AGENT_OWLS` | constants for v1 | after host-probe lands |
| Per-axis (fs/network/data) clamp reporting | only tools axis enforced today | Epic 3 |
| `_CONSEQUENTIAL_TOOL_NAMES` → derive from registry severity | name-set is the minimal first cut | when registry exposes severity lookup |
| Owl rename | name = identity (rename = retire+create) | — (by design) |
