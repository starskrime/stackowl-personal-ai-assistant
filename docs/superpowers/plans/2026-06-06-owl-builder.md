# Owl-Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human build/edit a *specialist owl* via `/owls` — a curated toolset (Epic-2 `BoundsSpec`) + a generated persona that delegates out-of-scope work — persisted durably to `stackowl.yaml`.

**Architecture:** A single pure builder (`SpecialistOwlBuilder.build`) turns an `OwlSpec` (preset OR explicit tools) into an `OwlAgentManifest`: it unions the derived tools with the **boundary-router** tools (`delegate_task` + discovery) and generates a persona instructing delegation for out-of-scope work (the persona IS the "compass"). Persistence reuses the existing yaml helpers (made atomic) + a new `registry.replace` verb. The agent `owl_build` tool, LLM-suggest, clone, and skill-instruction-injection are deferred.

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen), pytest, ruff, mypy --strict. Code under `v2/`. Tests: `uv run pytest <path> -v` (NO `--timeout` flag — plugin absent; targeted paths only — full suite hangs).

---

## ⚠️ Reuse Ledger — NO DUPLICATE CODE (read first)

The operator's standing complaint: ~50% of written code is duplicated. **Every task below must extend existing code, not recreate it.** Before writing ANY function, `grep`/read for an existing impl and wire into it. Each implementer subagent MUST report **reused-vs-created** in its summary. The decisions are pre-made here:

| Concern | Decision | Why (single source of truth) |
|---|---|---|
| Manifest construction | **EXTEND** — `SpecialistOwlBuilder.build` is the ONE constructor; `owls_helpers.build_owl_manifest` becomes a thin adapter that delegates to it. | Two `OwlAgentManifest(...)` call sites = duplication. The default-persona generation currently inline in `build_owl_manifest` MOVES into the builder (one persona generator). |
| YAML write | **EXTEND** — make existing `config_helpers.save_yaml` atomic (temp + `os.replace`). Do NOT write a second writer. | One fix, every config writer (`/provider`, `/config`, `/owls`) becomes crash-safe. |
| YAML read / path | **REUSE** `config_helpers.load_yaml` + `config_path()` verbatim. | Already correct. |
| add vs edit yaml mutation | **EXTEND** — rename `_append_to_yaml` → `_upsert_to_yaml` (find-by-name → replace, else append); both `add` and `edit` call it. | An `edit` that wrote its own yaml path = duplicate of append logic. |
| Registry update | **CREATE** `OwlRegistry.replace` (genuinely new verb — no update method exists; `register` guards duplicate, `replace` guards existence). | Distinct invariant, not duplication. |
| YAML serialization of bounds | **EXTEND** `manifest_to_yaml_entry` (currently omits bounds/capability_profile/skills). | One serializer; loader already round-trips. |
| Tool-name validation | **REUSE** `ToolRegistry.all()` → names; **REUSE** `ToolProposer`'s exact-name pattern conceptually (do NOT call it — suggest is deferred). | No new catalog scanner. |
| Delegation | **REUSE** the existing `delegate_task` tool — the builder just includes its name in bounds. | Boundary-router adds zero new runtime. |
| Command dispatch / parse | **EXTEND** `owls_command.handle()` (add one `elif`) + `parse_add_args` (add flags); add `parse_edit_args` mirroring `parse_add_args`. | Follow the established command shape. |

If a task tempts you to write something resembling existing code, STOP and extend the original.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/owls/manifest.py` | Modify | add `skills: tuple[str, ...] = ()` |
| `src/stackowl/commands/config_helpers.py` | Modify | make `save_yaml` atomic (temp + `os.replace`) |
| `src/stackowl/owls/tool_presets.py` | **Create** | `OwlPreset` + `PRESETS` (role→curated allowlist + capability_profile + specialty); boundary-router tool-name constants |
| `src/stackowl/owls/builder.py` | **Create** | `OwlSpec` (request) + `SpecialistOwlBuilder.build` (THE pure constructor) + `generate_persona` |
| `src/stackowl/owls/registry.py` | Modify | add `replace(manifest)` |
| `src/stackowl/commands/owls_helpers.py` | Modify | `build_owl_manifest`→delegate to builder; extend `parse_add_args`; add `parse_edit_args`; extend `manifest_to_yaml_entry`; `_append_to_yaml`→`_upsert_to_yaml` |
| `src/stackowl/commands/owls_command.py` | Modify | add `edit` subcommand + `_edit`; route `add` through extended flags |
| `tests/owls/test_builder.py` | **Create** | builder + preset + persona units |
| `tests/owls/test_registry_replace.py` | **Create** | `replace` verb |
| `tests/commands/test_owls_builder.py` | **Create** | add(preset/explicit) + edit + Secretary-guard + yaml round-trip |
| `tests/config/test_save_yaml_atomic.py` | **Create** | atomic write |
| `tests/journeys/test_owl_builder_journey.py` | **Create** | gateway journey |

---

## Task 1: Add `skills` field to the manifest

**Files:**
- Modify: `src/stackowl/owls/manifest.py` (after `tools: list[str] = []`)
- Test: `tests/owls/test_manifest_skills.py` (Create)

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_manifest_skills.py
from stackowl.owls.manifest import OwlAgentManifest


def _base(**kw):
    return OwlAgentManifest(name="n", role="r", system_prompt="p", model_tier="fast", **kw)


def test_skills_defaults_to_empty_tuple():
    assert _base().skills == ()


def test_skills_accepts_tuple_and_is_frozen():
    m = _base(skills=("research", "writing"))
    assert m.skills == ("research", "writing")


def test_legacy_manifest_without_skills_still_valid():
    # extra="forbid" + default => owls predating the field load unchanged
    assert _base().skills == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/owls/test_manifest_skills.py -v`
Expected: FAIL (`skills` not a field / unexpected keyword).

- [ ] **Step 3: Add the field**

In `src/stackowl/owls/manifest.py`, immediately after `tools: list[str] = []`:

```python
    # Skills this owl owns (records ownership; feeds capability_profile). A tuple
    # for frozen-model hashability. Additive + defaulted: owls predating this
    # field load unchanged. Skill INSTRUCTION-injection is a later story.
    skills: tuple[str, ...] = ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/owls/test_manifest_skills.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/manifest.py v2/tests/owls/test_manifest_skills.py
git commit -m "feat(v2): add skills field to OwlAgentManifest (owl-builder T1)"
```

---

## Task 2: Make `save_yaml` atomic (extend the existing writer)

**Files:**
- Modify: `src/stackowl/commands/config_helpers.py:46-49` (`save_yaml`)
- Test: `tests/config/test_save_yaml_atomic.py` (Create)

**Current code (DO NOT duplicate — replace in place):**
```python
def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        _yaml().dump(data, fh)
```

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_save_yaml_atomic.py
from pathlib import Path

import pytest

from stackowl.commands.config_helpers import load_yaml, save_yaml


def test_save_yaml_round_trips(tmp_path: Path):
    p = tmp_path / "x.yaml"
    save_yaml(p, {"owls": [{"name": "a"}]})
    assert load_yaml(p) == {"owls": [{"name": "a"}]}


def test_save_yaml_leaves_no_temp_files(tmp_path: Path):
    p = tmp_path / "x.yaml"
    save_yaml(p, {"k": 1})
    # atomic write must not leave .tmp siblings behind
    assert [f.name for f in tmp_path.iterdir()] == ["x.yaml"]


def test_save_yaml_existing_file_not_truncated_on_serialize_error(tmp_path: Path, monkeypatch):
    p = tmp_path / "x.yaml"
    save_yaml(p, {"good": 1})

    class Unrepresentable:
        pass

    with pytest.raises(Exception):
        save_yaml(p, {"bad": Unrepresentable()})
    # original survives because we write to a temp file then os.replace
    assert load_yaml(p) == {"good": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/config/test_save_yaml_atomic.py -v`
Expected: FAIL on `test_save_yaml_existing_file_not_truncated_on_serialize_error` (in-place `"w"` truncates first) and possibly the temp-file test.

- [ ] **Step 3: Make the write atomic**

Replace `save_yaml` in `src/stackowl/commands/config_helpers.py` with:

```python
def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Atomically persist *data* as YAML: serialize to a temp file in the same
    directory, then ``os.replace`` over the target. A serialization failure or
    crash never leaves a half-written config that would corrupt every owl."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _yaml().dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
```

Add at top of file if absent: `import os`, `import tempfile` (check existing imports first — do not duplicate).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/config/test_save_yaml_atomic.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/commands/config_helpers.py v2/tests/config/test_save_yaml_atomic.py
git commit -m "feat(v2): atomic save_yaml (temp+os.replace) — crash-safe config writes (owl-builder T2)"
```

---

## Task 3: Serialize bounds/capability_profile/skills in `manifest_to_yaml_entry`

**Files:**
- Modify: `src/stackowl/commands/owls_helpers.py:247-260` (`manifest_to_yaml_entry`)
- Test: `tests/owls/test_yaml_round_trip.py` (Create)

**Current code (extend — do NOT add a second serializer):**
```python
def manifest_to_yaml_entry(manifest: OwlAgentManifest) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": manifest.name, "role": manifest.role,
        "system_prompt": manifest.system_prompt,
        "model_tier": manifest.model_tier, "temperature": manifest.temperature,
    }
    if manifest.provider_name:
        entry["provider_name"] = manifest.provider_name
    if manifest.tools:
        entry["tools"] = list(manifest.tools)
    return entry
```

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_yaml_round_trip.py
from stackowl.authz.bounds import BoundsSpec
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.owls.manifest import OwlAgentManifest


def _m():
    return OwlAgentManifest(
        name="researcher", role="research", system_prompt="p", model_tier="fast",
        tools=["read_file", "web_fetch", "delegate_task"],
        capability_profile=["research"], skills=("research",),
        bounds=BoundsSpec(tools=frozenset({"read_file", "web_fetch", "delegate_task"})),
    )


def test_entry_serializes_bounds_capability_profile_skills():
    e = manifest_to_yaml_entry(_m())
    assert e["capability_profile"] == ["research"]
    assert e["skills"] == ["research"]
    # bounds.tools (frozenset) must serialize to a JSON-friendly list, not a frozenset
    assert sorted(e["bounds"]["tools"]) == ["delegate_task", "read_file", "web_fetch"]
    assert not isinstance(e["bounds"]["tools"], frozenset)


def test_round_trip_reconstructs_equal_manifest():
    e = manifest_to_yaml_entry(_m())
    rebuilt = OwlAgentManifest(**e)
    assert rebuilt.bounds == _m().bounds          # frozenset equality survives
    assert rebuilt.skills == _m().skills
    assert rebuilt.capability_profile == _m().capability_profile


def test_omits_empty_optional_fields():
    bare = OwlAgentManifest(name="n", role="r", system_prompt="p", model_tier="fast")
    e = manifest_to_yaml_entry(bare)
    assert "bounds" not in e and "skills" not in e and "capability_profile" not in e
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/owls/test_yaml_round_trip.py -v`
Expected: FAIL (`KeyError: 'capability_profile'` / `'bounds'`).

- [ ] **Step 3: Extend the serializer**

In `manifest_to_yaml_entry`, before `return entry`, append:

```python
    if manifest.capability_profile:
        entry["capability_profile"] = list(manifest.capability_profile)
    if manifest.skills:
        entry["skills"] = list(manifest.skills)
    if manifest.bounds is not None:
        # model_dump(mode="json") turns frozenset/tuple into list — ruamel cannot
        # represent frozenset/tuple and would raise RepresenterError otherwise.
        entry["bounds"] = manifest.bounds.model_dump(mode="json", exclude_none=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/owls/test_yaml_round_trip.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/commands/owls_helpers.py v2/tests/owls/test_yaml_round_trip.py
git commit -m "feat(v2): serialize bounds/capability_profile/skills to yaml (owl-builder T3)"
```

---

## Task 4: `OwlRegistry.replace` — the missing update verb

**Files:**
- Modify: `src/stackowl/owls/registry.py` (near `register`/`deregister`)
- Test: `tests/owls/test_registry_replace.py` (Create)

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_registry_replace.py
import pytest

from stackowl.exceptions import OwlNotFoundError
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry


def _m(name, role="r"):
    return OwlAgentManifest(name=name, role=role, system_prompt="p", model_tier="fast")


def test_replace_swaps_existing_in_place():
    r = OwlRegistry()
    r.register(_m("a", role="old"))
    r.replace(_m("a", role="new"))
    assert r.get("a").role == "new"
    assert len(r.list()) == 1  # no duplicate, no empty window


def test_replace_unknown_raises():
    r = OwlRegistry()
    with pytest.raises(OwlNotFoundError):
        r.replace(_m("ghost"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/owls/test_registry_replace.py -v`
Expected: FAIL (`AttributeError: ... 'replace'`).

- [ ] **Step 3: Add the verb**

In `src/stackowl/owls/registry.py`, after `register`:

```python
    def replace(self, manifest: OwlAgentManifest) -> None:
        """Atomically swap an existing owl's manifest (the edit path).

        Guards existence (the dual of ``register``'s duplicate guard). A single
        dict assignment — the owl is never absent mid-edit (no deregister+register
        empty window). The mandatory-Secretary policy is enforced one layer up at
        the command, not here (``replace`` is a general verb)."""
        if manifest.name not in self._owls:
            raise OwlNotFoundError(manifest.name)
        self._owls[manifest.name] = manifest
        log.startup.debug(
            "[owls] registry.replace: owl replaced",
            extra={"_fields": {"name": manifest.name, "role": manifest.role}},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/owls/test_registry_replace.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/registry.py v2/tests/owls/test_registry_replace.py
git commit -m "feat(v2): OwlRegistry.replace — atomic owl update verb (owl-builder T4)"
```

---

## Task 5: Role presets + boundary-router constants

**Files:**
- Create: `src/stackowl/owls/tool_presets.py`
- Test: `tests/owls/test_tool_presets.py` (Create)

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_tool_presets.py
from stackowl.owls.tool_presets import PRESETS, ROUTER_TOOLS


def test_known_presets_present():
    assert set(PRESETS) == {"researcher", "coder", "writer", "analyst"}


def test_researcher_is_least_privilege():
    p = PRESETS["researcher"]
    assert "shell" not in p.tools and "write_file" not in p.tools
    assert "read_file" in p.tools and "web_fetch" in p.tools


def test_coder_has_execution_tools():
    assert {"write_file", "shell"} <= PRESETS["coder"].tools


def test_router_tools_are_delegate_and_discovery():
    assert ROUTER_TOOLS == frozenset({"delegate_task", "tool_search", "tool_describe"})


def test_each_preset_declares_specialty_and_capability_profile():
    for p in PRESETS.values():
        assert p.specialty and p.capability_profile
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/owls/test_tool_presets.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Create the presets module**

```python
# src/stackowl/owls/tool_presets.py
"""Role presets for the owl-builder — curated, least-privilege tool allowlists.

Each preset is safe-by-construction: it grants only the tools its role needs.
The builder always adds ROUTER_TOOLS on top (the boundary-router): delegate_task
(so the owl can hand off out-of-scope work) + the discovery meta-tools (so a
present-but-narrow tools allowlist never strands the owl — an empty/over-narrow
frozenset would otherwise deny tool_search itself; see BoundsSpec footgun)."""

from __future__ import annotations

from dataclasses import dataclass

# The boundary-router: every built specialist gets these on top of its preset.
ROUTER_TOOLS: frozenset[str] = frozenset({"delegate_task", "tool_search", "tool_describe"})


@dataclass(frozen=True)
class OwlPreset:
    """A named role template: a curated tool allowlist + presentation metadata."""

    tools: frozenset[str]
    specialty: str
    capability_profile: tuple[str, ...]


PRESETS: dict[str, OwlPreset] = {
    "researcher": OwlPreset(
        tools=frozenset({"read_file", "note_search", "web_search", "web_fetch", "summarize"}),
        specialty="research and information gathering",
        capability_profile=("research",),
    ),
    "coder": OwlPreset(
        tools=frozenset({"read_file", "write_file", "edit", "search_files", "execute_code", "shell"}),
        specialty="reading, writing and running code",
        capability_profile=("coding",),
    ),
    "writer": OwlPreset(
        tools=frozenset({"read_file", "write_file", "web_fetch", "summarize"}),
        specialty="drafting and editing written content",
        capability_profile=("writing",),
    ),
    "analyst": OwlPreset(
        tools=frozenset({"read_file", "search_files", "web_search", "web_fetch", "summarize"}),
        specialty="analysis and synthesis",
        capability_profile=("analysis",),
    ),
}
```

**Implementer note:** confirm each tool name exists via `ToolRegistry` (grep `def name` in `src/stackowl/tools/`). Drop any name not actually registered on this box rather than inventing one; record which you kept/dropped in your summary.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/owls/test_tool_presets.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/tool_presets.py v2/tests/owls/test_tool_presets.py
git commit -m "feat(v2): role presets + boundary-router tool constants (owl-builder T5)"
```

---

## Task 6: `SpecialistOwlBuilder` — the one pure constructor

**Files:**
- Create: `src/stackowl/owls/builder.py`
- Test: `tests/owls/test_builder.py` (Create)

**This is the SINGLE manifest-construction site.** Task 7 makes `build_owl_manifest` delegate here; do not construct `OwlAgentManifest` anywhere else.

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_builder.py
import pytest

from stackowl.owls.builder import OwlSpec, SpecialistOwlBuilder


def _build(**kw):
    spec = OwlSpec(name=kw.pop("name", "r"), role=kw.pop("role", "research"),
                   model_tier=kw.pop("tier", "fast"), **kw)
    return SpecialistOwlBuilder().build(spec)


def test_preset_specialist_has_bounds_excluding_shell():
    m = _build(name="rsr", preset="researcher")
    assert m.bounds is not None
    assert "shell" not in m.bounds.tools
    assert "read_file" in m.bounds.tools


def test_boundary_router_tools_always_included():
    m = _build(name="rsr", preset="researcher")
    assert {"delegate_task", "tool_search", "tool_describe"} <= m.bounds.tools


def test_persona_instructs_delegation_of_out_of_scope_work():
    m = _build(name="rsr", preset="researcher")
    assert "delegate_task" in m.system_prompt


def test_explicit_tools_validated_against_catalog_drops_unknown():
    m = _build(name="x", explicit_tools=("read_file", "totally_fake_tool"),
               valid_tools=frozenset({"read_file"}))
    assert "read_file" in m.bounds.tools
    assert "totally_fake_tool" not in m.bounds.tools


def test_capability_profile_and_skills_carried():
    m = _build(name="rsr", preset="researcher", skills=("research",))
    assert m.capability_profile == ["research"]
    assert m.skills == ("research",)


def test_no_preset_no_tools_makes_unbounded_general_owl():
    # back-compat: today's bare `/owls add` (no --preset/--tools) is unchanged
    m = _build(name="plain")
    assert m.bounds is None


def test_explicit_system_prompt_overrides_generated_persona():
    m = _build(name="rsr", preset="researcher", system_prompt="custom")
    assert m.system_prompt == "custom"


def test_requires_preset_xor_explicit():
    with pytest.raises(ValueError):
        _build(name="x", preset="researcher", explicit_tools=("read_file",))
```

`valid_tools` is an optional `build()` arg (the live tool catalog). When `None`, validation is fail-open (no catalog available → keep names, log a warning).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/owls/test_builder.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Create the builder**

```python
# src/stackowl/owls/builder.py
"""SpecialistOwlBuilder — the single pure constructor for owl manifests.

One lifecycle: derive (preset|explicit) → validate → instantiate. Pure: no I/O,
no persistence (the command layer persists). The generated persona is the owl's
"compass": it states the specialty AND instructs delegating out-of-scope work to
the secretary via delegate_task (the boundary-router), so a narrow owl is additive
and self-healing rather than a dead-end."""

from __future__ import annotations

from dataclasses import dataclass, field

from stackowl.authz.bounds import BoundsSpec
from stackowl.logger import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.tool_presets import PRESETS, ROUTER_TOOLS


@dataclass(frozen=True)
class OwlSpec:
    """A build request. Provide a ``preset`` OR ``explicit_tools`` (not both);
    neither => an unbounded general owl (today's bare ``/owls add``)."""

    name: str
    role: str
    model_tier: str
    preset: str | None = None
    explicit_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    capability_profile: tuple[str, ...] = ()
    provider_name: str | None = None
    temperature: float = 0.7
    system_prompt: str | None = None
    specialty: str | None = None
    valid_tools: frozenset[str] | None = field(default=None)


def generate_persona(name: str, role: str, specialty: str) -> str:
    """The compass + boundary-router instruction. Language-neutral (no hardcoded
    English keywords beyond the tool name / structure)."""
    return (
        f"Persona: {name}. Role: {role}. Specialty: {specialty}. "
        f"Handle {specialty} directly using your own tools. "
        f"For any request outside {specialty}, hand it off to the secretary using "
        f"the delegate_task tool — do not attempt tools you do not have. "
        f"Respond in the language of the user."
    )


class SpecialistOwlBuilder:
    """Turns an :class:`OwlSpec` into a validated :class:`OwlAgentManifest`."""

    def build(self, spec: OwlSpec) -> OwlAgentManifest:
        log.startup.debug(
            "[owls] builder.build: entry",
            extra={"_fields": {"name": spec.name, "preset": spec.preset,
                               "explicit": len(spec.explicit_tools)}},
        )
        if spec.preset and spec.explicit_tools:
            raise ValueError("provide a preset OR explicit tools, not both")

        bounds: BoundsSpec | None = None
        capability_profile = list(spec.capability_profile)
        specialty = spec.specialty or spec.role

        base: frozenset[str] | None = None
        if spec.preset:
            if spec.preset not in PRESETS:
                raise ValueError(f"unknown preset: {spec.preset!r} (known: {sorted(PRESETS)})")
            preset = PRESETS[spec.preset]
            base = preset.tools
            specialty = spec.specialty or preset.specialty
            if not capability_profile:
                capability_profile = list(preset.capability_profile)
        elif spec.explicit_tools:
            base = frozenset(spec.explicit_tools)

        if base is not None:
            base = self._validate(base, spec.valid_tools)
            tools = base | ROUTER_TOOLS
            bounds = BoundsSpec(tools=tools)

        system_prompt = spec.system_prompt or generate_persona(spec.name, spec.role, specialty)
        manifest = OwlAgentManifest(
            name=spec.name,
            role=spec.role,
            system_prompt=system_prompt,
            model_tier=spec.model_tier,
            provider_name=spec.provider_name,
            temperature=spec.temperature,
            tools=sorted(bounds.tools) if bounds is not None else [],
            capability_profile=capability_profile,
            skills=spec.skills,
            bounds=bounds,
            dna=OwlDNA(),
        )
        log.startup.debug(
            "[owls] builder.build: exit",
            extra={"_fields": {"name": manifest.name, "bounded": bounds is not None}},
        )
        return manifest

    @staticmethod
    def _validate(requested: frozenset[str], valid: frozenset[str] | None) -> frozenset[str]:
        if valid is None:
            log.startup.warning(
                "[owls] builder._validate: no catalog — skipping tool validation (fail-open)",
                extra={"_fields": {"requested": len(requested)}},
            )
            return requested
        kept = requested & valid
        dropped = requested - valid
        if dropped:
            log.startup.warning(
                "[owls] builder._validate: dropped unknown tools",
                extra={"_fields": {"dropped": sorted(dropped)}},
            )
        return kept
```

**Implementer note:** confirm the `OwlDNA` import path (`stackowl.owls.dna` — grep; `build_owl_manifest` imports it today). Reuse the exact same import, do not introduce a parallel one.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/owls/test_builder.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/builder.py v2/tests/owls/test_builder.py
git commit -m "feat(v2): SpecialistOwlBuilder — pure builder + boundary-router persona (owl-builder T6)"
```

---

## Task 7: Route `build_owl_manifest` through the builder + extend `parse_add_args`

**Files:**
- Modify: `src/stackowl/commands/owls_helpers.py` (`parse_add_args` 145-210; `build_owl_manifest` 213-244)
- Test: `tests/commands/test_owls_parse.py` (Create)

**`build_owl_manifest` MUST delegate to `SpecialistOwlBuilder.build` — do not keep a second `OwlAgentManifest(...)` construction.** The inline default-prompt is removed (the builder owns persona generation).

- [ ] **Step 1: Write the failing test**

```python
# tests/commands/test_owls_parse.py
import pytest

from stackowl.commands.owls_helpers import build_owl_manifest, parse_add_args
from stackowl.exceptions import CommandParseError


def test_parse_preset_and_skills():
    p = parse_add_args("rsr --role research --tier fast --preset researcher --skills research,writing")
    assert p["preset"] == "researcher"
    assert p["skills"] == ["research", "writing"]


def test_parse_capability_profile_and_system_prompt():
    p = parse_add_args('x --role r --tier fast --capability-profile research --system-prompt "be terse"')
    assert p["capability_profile"] == ["research"]
    assert p["system_prompt"] == "be terse"


def test_build_from_preset_delegates_to_builder_with_bounds():
    p = parse_add_args("rsr --role research --tier fast --preset researcher")
    m = build_owl_manifest(p)
    assert m.bounds is not None and "shell" not in m.bounds.tools
    assert "delegate_task" in m.bounds.tools  # boundary-router


def test_build_bare_owl_still_unbounded():
    p = parse_add_args("plain --role helper --tier fast")
    assert build_owl_manifest(p).bounds is None


def test_unknown_preset_rejected():
    p = parse_add_args("x --role r --tier fast --preset nope")
    with pytest.raises((ValueError, CommandParseError)):
        build_owl_manifest(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/commands/test_owls_parse.py -v`
Expected: FAIL (`KeyError: 'preset'` / bounds None for preset).

- [ ] **Step 3a: Extend `parse_add_args`**

In the `params` dict initializer add the new keys:
```python
        "preset": None,
        "skills": [],
        "capability_profile": [],
        "system_prompt": None,
```
In the flag-parsing `while` loop add `elif` branches (before the final `else: unknown flag`):
```python
        elif key == "--preset":
            params["preset"] = value
        elif key == "--skills":
            params["skills"] = [s.strip() for s in value.split(",") if s.strip()]
        elif key == "--capability-profile":
            params["capability_profile"] = [s.strip() for s in value.split(",") if s.strip()]
        elif key == "--system-prompt":
            params["system_prompt"] = value
```

- [ ] **Step 3b: Make `build_owl_manifest` delegate to the builder**

Replace the body of `build_owl_manifest` (keep the signature `def build_owl_manifest(params, *, valid_tools=None)` — add the optional kwarg) with:

```python
def build_owl_manifest(
    params: dict[str, Any], *, valid_tools: frozenset[str] | None = None
) -> OwlAgentManifest:
    """Adapter: parsed CLI params -> OwlSpec -> the single SpecialistOwlBuilder.

    No manifest is constructed here — the builder is the one constructor (DRY)."""
    log.gateway.debug(
        "[commands] owls.build_owl_manifest: entry",
        extra={"_fields": {"name": params.get("name"), "preset": params.get("preset")}},
    )
    temperature_raw = params.get("temperature")
    spec = OwlSpec(
        name=params["name"],
        role=params["role"],
        model_tier=params["tier"],
        preset=params.get("preset"),
        explicit_tools=tuple(params.get("tools") or ()),
        skills=tuple(params.get("skills") or ()),
        capability_profile=tuple(params.get("capability_profile") or ()),
        provider_name=params.get("provider"),
        temperature=float(temperature_raw) if temperature_raw is not None else 0.7,
        system_prompt=params.get("system_prompt"),
        valid_tools=valid_tools,
    )
    try:
        manifest = SpecialistOwlBuilder().build(spec)
    except ValueError as exc:
        raise CommandParseError("owls add", str(exc)) from exc
    log.gateway.debug(
        "[commands] owls.build_owl_manifest: exit",
        extra={"_fields": {"name": manifest.name}},
    )
    return manifest
```

Add imports at the top of `owls_helpers.py` (check for existing — do not duplicate): `from stackowl.owls.builder import OwlSpec, SpecialistOwlBuilder`. Remove the now-unused `OwlDNA` import here ONLY if nothing else in the file uses it (grep first).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/commands/test_owls_parse.py tests/owls -v`
Expected: PASS. Also run `cd v2 && uv run pytest tests/commands/test_owls_command.py -v` (or the existing owls command test file) to confirm no regression in the existing `add`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/commands/owls_helpers.py v2/tests/commands/test_owls_parse.py
git commit -m "feat(v2): /owls add delegates to builder; preset/skills/cap-profile flags (owl-builder T7)"
```

---

## Task 8: `_upsert_to_yaml` + wire add persistence; pass catalog to the command

**Files:**
- Modify: `src/stackowl/commands/owls_command.py` (`_add` 135-152; constructor; `handle` add `elif`) and its `_append_to_yaml`
- Test: `tests/commands/test_owls_builder.py` (Create — covers add persistence + round-trip)

- [ ] **Step 1: Write the failing test**

```python
# tests/commands/test_owls_builder.py
from pathlib import Path
from typing import Any

import pytest
import yaml

from stackowl.commands.owls_command import OwlsCommand  # confirm class name via grep
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState  # confirm import path used by sibling tests


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(yaml.dump({"owls": []}), encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


def _load(cfg: Path) -> dict[str, Any]:
    return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}


def _state() -> Any:
    # mirror the sibling command test's PipelineState construction
    return PipelineState(session_id="t", user_input="", trace_id="t")


@pytest.mark.asyncio
async def test_add_preset_persists_bounds_to_yaml(tmp_yaml: Path):
    reg = OwlRegistry.from_settings_default() if hasattr(OwlRegistry, "from_settings_default") else OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg)
    out = await cmd.handle("add rsr --role research --tier fast --preset researcher", _state())
    assert "✓" in out
    entry = next(e for e in _load(tmp_yaml)["owls"] if e["name"] == "rsr")
    assert "shell" not in entry["bounds"]["tools"]
    assert "delegate_task" in entry["bounds"]["tools"]
    assert reg.get("rsr").bounds is not None
```

**Implementer:** adjust `OwlsCommand(...)` construction + `PipelineState(...)` to match the EXACT signatures the existing `tests/commands/test_owls_command.py` uses (read it first — do not invent a constructor).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/commands/test_owls_builder.py -v`
Expected: FAIL (bounds not persisted / wiring).

- [ ] **Step 3a: Rename `_append_to_yaml` → `_upsert_to_yaml`**

Replace the append body so it replaces an existing entry by name, else appends (one method for add + edit):

```python
    def _upsert_to_yaml(self, entry: dict[str, Any]) -> None:
        """Insert or replace an owl entry in stackowl.yaml's owls: list (by name)."""
        path = config_path()
        data = load_yaml(path)
        owls_list = data.get("owls")
        if not isinstance(owls_list, list):
            owls_list = []
        name = entry.get("name")
        replaced = False
        for i, e in enumerate(owls_list):
            if isinstance(e, dict) and e.get("name") == name:
                owls_list[i] = entry
                replaced = True
                break
        if not replaced:
            owls_list.append(entry)
        data["owls"] = owls_list
        save_yaml(path, data)  # atomic (Task 2)
        log.gateway.debug(
            "[commands] owls._upsert_to_yaml: written",
            extra={"_fields": {"path": str(path), "name": name, "replaced": replaced}},
        )
```

Update the `_add` call site `self._append_to_yaml(...)` → `self._upsert_to_yaml(...)`.

- [ ] **Step 3b: Thread the tool catalog into the command (fail-open)**

So the builder can validate explicit tool names. In `OwlsCommand.__init__`, add `tool_registry: ToolRegistry | None = None` and store it. In `_add`, pass the catalog to `build_owl_manifest`:

```python
        valid = (
            frozenset(t.name for t in self._tool_registry.all())
            if self._tool_registry is not None else None
        )
        manifest = build_owl_manifest(params, valid_tools=valid)
```

At the construction site (grep `OwlsCommand(`), pass the existing tool registry if one is in scope; otherwise leave `None` (fail-open — preset names are trusted constants, so presets work without a catalog; only explicit `--tools` validation needs it).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/commands/test_owls_builder.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/commands/owls_command.py v2/tests/commands/test_owls_builder.py
git commit -m "feat(v2): _upsert_to_yaml + catalog-validated add persistence (owl-builder T8)"
```

---

## Task 9: `/owls edit` subcommand (Secretary-guarded)

**Files:**
- Modify: `src/stackowl/commands/owls_command.py` (`handle` add `elif sub == "edit"`; add `_edit`); `src/stackowl/commands/owls_helpers.py` (add `parse_edit_args`)
- Test: extend `tests/commands/test_owls_builder.py`

- [ ] **Step 1: Write the failing test (append to test_owls_builder.py)**

```python
@pytest.mark.asyncio
async def test_edit_changes_field_and_repersists(tmp_yaml: Path):
    reg = OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg)
    await cmd.handle("add rsr --role research --tier fast --preset researcher", _state())
    out = await cmd.handle("edit rsr --tier powerful", _state())
    assert "✓" in out
    assert reg.get("rsr").model_tier == "powerful"
    # other fields preserved (bounds carried through model_copy)
    assert reg.get("rsr").bounds is not None and "delegate_task" in reg.get("rsr").bounds.tools
    entry = next(e for e in _load(tmp_yaml)["owls"] if e["name"] == "rsr")
    assert entry["model_tier"] == "powerful"


@pytest.mark.asyncio
async def test_edit_secretary_rejected(tmp_yaml: Path):
    reg = OwlRegistry()
    # registry seeds the mandatory Secretary; confirm its name via grep (_SECRETARY_NAME)
    out = await cmd_with_secretary(reg).handle("edit secretary --tier fast", _state())
    assert "✗" in out


@pytest.mark.asyncio
async def test_edit_unknown_owl_errors(tmp_yaml: Path):
    out = await OwlsCommand(owl_registry=OwlRegistry()).handle("edit ghost --tier fast", _state())
    assert "✗" in out
```

**Implementer:** replace `cmd_with_secretary`/seed with however the existing tests obtain a registry containing the Secretary (read `registry.py` `_SECRETARY_NAME` + any `from_settings`/default-seed; reuse it).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/commands/test_owls_builder.py -k edit -v`
Expected: FAIL (unknown subcommand → usage string, no `_edit`).

- [ ] **Step 3a: `parse_edit_args` in `owls_helpers.py`**

Mirror `parse_add_args` but ALL flags optional except the name; role/tier not required (edit is partial):

```python
def parse_edit_args(rest: str) -> dict[str, Any]:
    """Parse ``/owls edit <name> [--flag value ...]`` — every flag optional."""
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise CommandParseError("owls edit", f"could not tokenise arguments: {exc}") from exc
    if not tokens:
        raise CommandParseError("owls edit", "missing owl name")
    name, flags = tokens[0], tokens[1:]
    if len(flags) % 2 != 0:
        raise CommandParseError("owls edit", "every --flag requires a value")
    changes: dict[str, Any] = {"name": name}
    mapping = {
        "--role": "role", "--tier": "model_tier", "--provider": "provider_name",
        "--system-prompt": "system_prompt",
    }
    i = 0
    while i < len(flags):
        key, value = flags[i], flags[i + 1]
        if key in mapping:
            changes[mapping[key]] = value
        elif key == "--temperature":
            try:
                changes["temperature"] = float(value)
            except ValueError as exc:
                raise CommandParseError("owls edit", f"--temperature must be float, got {value!r}") from exc
        elif key == "--skills":
            changes["skills"] = tuple(s.strip() for s in value.split(",") if s.strip())
        elif key == "--capability-profile":
            changes["capability_profile"] = [s.strip() for s in value.split(",") if s.strip()]
        elif key == "--tier" and value not in _VALID_TIERS:
            raise CommandParseError("owls edit", f"--tier invalid: {value!r}")
        else:
            raise CommandParseError("owls edit", f"unknown flag: {key}")
        i += 2
    if changes.get("model_tier") and changes["model_tier"] not in _VALID_TIERS:
        raise CommandParseError("owls edit", f"--tier must be one of {sorted(_VALID_TIERS)}")
    return changes
```

- [ ] **Step 3b: `_edit` + dispatch in `owls_command.py`**

In `handle`, after the `add` branch:
```python
        elif sub == "edit":
            result = await self._edit(rest)
```
Add the handler (reuses `registry.get` + `model_copy` + `replace` + `_upsert_to_yaml` — no new construction/persist path):
```python
    async def _edit(self, rest: str) -> str:
        if self._registry is None:
            return _NO_REGISTRY
        changes = parse_edit_args(rest)
        name = changes.pop("name")
        from stackowl.owls.registry import _SECRETARY_NAME  # reuse the canonical guard constant
        if name == _SECRETARY_NAME:
            return f"✗ /owls edit: {name} is mandatory and cannot be edited"
        current = self._registry.get(name)            # raises OwlNotFoundError -> handled in handle()
        updated = current.model_copy(update=changes)   # frozen-safe; bounds/skills carried automatically
        self._registry.replace(updated)
        self._upsert_to_yaml(manifest_to_yaml_entry(updated))
        if self._bus is not None:
            self._bus.emit("owl_edited", {"name": updated.name})
        log.gateway.info("[commands] owls.edit: exit", extra={"_fields": {"name": updated.name}})
        return f"✓ owl '{updated.name}' updated (tier={updated.model_tier})"
```

Ensure `parse_edit_args` is imported alongside `parse_add_args` in `owls_command.py`. Add `edit` to the `_USAGE` string.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/commands/test_owls_builder.py -v`
Expected: PASS (all add + edit tests).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/commands/owls_command.py v2/src/stackowl/commands/owls_helpers.py v2/tests/commands/test_owls_builder.py
git commit -m "feat(v2): /owls edit (Secretary-guarded) via replace+upsert (owl-builder T9)"
```

---

## Task 10: Gateway journey — build a specialist, route, bounds-block, delegate, persist

**Files:**
- Create: `tests/journeys/test_owl_builder_journey.py`

Mirror an existing journey (read `tests/journeys/test_preflight_envelope.py` for the harness: how it builds the gateway/pipeline, mocks ONLY the AI provider, registers owls, and drives a turn). Only the AI provider is mocked.

- [ ] **Step 1: Write the journey test**

```python
# tests/journeys/test_owl_builder_journey.py
"""J: a human builds a researcher specialist via /owls; a turn routed to it uses
an in-bounds tool, is bounds-blocked from shell, delegates out-of-scope work, and
the specialist survives a config reload."""
import pytest

# Reuse the journey harness/fixtures from the sibling journey test (import or copy
# the minimal builder). Implementer: match test_preflight_envelope.py exactly.


@pytest.mark.asyncio
async def test_built_specialist_is_bounded_and_delegates(owl_builder_harness):
    h = owl_builder_harness
    # 1. human builds the specialist
    out = await h.run_command("/owls add rsr --role research --tier fast --preset researcher")
    assert "✓" in out

    # 2. a turn routed to rsr: scripted owl tries web_fetch (in bounds) then shell (out of bounds)
    result = await h.route_turn(owl="rsr", script=["web_fetch:https://example.com", "shell:ls"])
    assert h.tool_ran("web_fetch")            # permitted
    assert h.tool_blocked("shell")            # Epic-2 bounds enforcement at the dispatch seam
    assert "delegate_task" in h.presented_tools("rsr")  # boundary-router is available

    # 3. persistence: reload settings from yaml -> the specialist + bounds survive
    reloaded = h.reload_registry()
    assert reloaded.get("rsr").bounds is not None
    assert "shell" not in reloaded.get("rsr").bounds.tools
```

**Implementer:** if the sibling journey has no reusable harness fixture, build the smallest real wiring (real `OwlRegistry`, real pipeline `execute` step, real bounds enforcement, mock provider scripted to emit the two tool calls). Do NOT mock the bounds layer — the point is to prove real enforcement. Keep names/paths matching the codebase.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd v2 && uv run pytest tests/journeys/test_owl_builder_journey.py -v`
Expected: FAIL (harness/feature not wired).

- [ ] **Step 3: Implement minimal wiring to pass**

Wire whatever the journey needs using EXISTING components (registry, execute seam, provider mock). No new production code should be required beyond Tasks 1–9 — if the journey reveals a missing wire, that is a real finding: STOP and report it (do not silently patch the test). See memory: AI must not silently fix failing integration tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd v2 && uv run pytest tests/journeys/test_owl_builder_journey.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/tests/journeys/test_owl_builder_journey.py
git commit -m "test(v2): owl-builder gateway journey — build+route+bounds-block+delegate+persist (owl-builder T10)"
```

---

## Final verification

- [ ] Run the full owl-builder surface:
```bash
cd v2 && uv run pytest tests/owls tests/commands/test_owls_parse.py tests/commands/test_owls_builder.py tests/config/test_save_yaml_atomic.py tests/journeys/test_owl_builder_journey.py -v
```
- [ ] `cd v2 && uv run ruff check src/ && uv run mypy src/stackowl/owls/ src/stackowl/commands/owls_command.py src/stackowl/commands/owls_helpers.py`
- [ ] Regression: existing owls command test green (`cd v2 && uv run pytest tests/commands/test_owls_command.py -v`).
- [ ] Then: subagent-driven-development final reviewer → merge to main + push (per standing prefs).

---

## Spec coverage self-check

| Spec §3 element | Task |
|---|---|
| `SpecialistOwlBuilder.build` (pure, one lifecycle, validate-once) | T6 |
| Presets + explicit derivation | T5, T6 |
| Boundary-router (delegate_task + discovery + persona) | T5, T6 |
| `skills` tuple field | T1 |
| yaml serialization (model_dump json) + round-trip | T3 |
| atomic write-through (temp+os.replace) | T2 |
| `registry.replace` | T4 |
| `/owls add` extended | T7, T8 |
| `/owls edit` + Secretary guard | T9 |
| build/persist split | T6 (build pure) / T8–T9 (persist) |
| gateway journey (build/route/bounds-block/delegate/persist) | T10 |
| DEFERRED: agent tool, LLM-suggest, clone, skill-injection | not in plan ✓ |
