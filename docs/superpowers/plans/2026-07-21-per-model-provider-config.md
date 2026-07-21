# Per-Model Provider Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one `ProviderConfig` entry (one API key/base_url/protocol connection) host multiple models, each independently routable by tier and independently able to override its context/output-token budget, without duplicating the whole provider block for a second model.

**Architecture:** Additive schema (`ProviderConfig.models: tuple[ModelOverride, ...] = ()`, empty by default — zero migration, zero behavior change for every existing single-model config). `ProviderRegistry._tiers` becomes model-aware (`dict[str, tuple[ModelRoute, ...]]`). Four new `*_and_model` registry methods do the real (provider, model) resolution; the existing `get_by_tier`/`get_with_cascade`/`resolve_tier_with_fallback`/`resolve_capable_or_degrade` become thin one-line wrappers around them for the whole migration, so every one of their ~31 existing call sites keeps working, unmodified, at every point in the plan. Each call site is migrated to the `*_and_model` method one package at a time; a final cleanup task removes the temporary wrappers and renames the `*_and_model` methods back to the plain names once nothing depends on the old bare-provider return shape.

**Tech Stack:** Python 3.13, pydantic v2, pytest + pytest-asyncio, ruff, mypy (strict).

## Global Constraints

- **Additive only, zero migration:** `ProviderConfig.models` defaults to `()`. Every existing YAML config (only `default_model`/`tiers`, no `models` key) must load and behave byte-for-byte identically to before this plan, at every single task boundary — never only at the end.
- **Never break an existing call site mid-migration:** `get_by_tier`, `get_with_cascade`, `resolve_tier_with_fallback`, `resolve_capable_or_degrade` keep their EXACT current signatures and return types until the final cleanup task (Task 23). Every task from Task 5 through Task 22 must leave `uv run pytest` green across the whole repo's provider/pipeline/owls/memory/skills/interaction/objectives suites — never just the task's own new test file.
- **4-point logging** on every new/modified `execute()`-style method (entry/decision/step/exit), per `CLAUDE.md`.
- **Minimal diff per task** — change only the exact lines the task specifies; do not opportunistically refactor adjacent code.
- **Never hardcode English/keyword-based logic** — not applicable to this plan's surface (no user-facing NLP), noted for completeness.
- **Test-run discipline:** never run the full repo `pytest` in one command (documented hang risk) — every task specifies its own scoped test command(s); the final task (Task 27) runs an explicit, still-scoped combined list, not a bare `pytest`.
- **Circuit breakers, cost tracking, and secret resolution stay keyed by provider NAME**, never per-model — a model-routing failure still counts against the same provider-level circuit breaker (one connection, shared health). This invariant must not regress in any task.

---

## Task 1: Schema — `ModelOverride` + `ProviderConfig.models`

**Files:**
- Modify: `src/stackowl/config/provider.py`
- Test: `tests/config/test_provider.py` (create if it does not already cover `ProviderConfig` field validation directly — check first; if a `test_provider_config.py` or similar already exists, add to it instead)

**Interfaces:**
- Produces: `ModelOverride` (new class, importable as `from stackowl.config.provider import ModelOverride`) with fields `name: str`, `tiers: tuple[Literal["fast","standard","powerful","local"], ...]`, `max_output_tokens: int | None = None`, `context_chars: int | None = None`.
- Produces: `ProviderConfig.models: tuple[ModelOverride, ...] = ()`.

- [ ] **Step 1: Read the current file in full first**

Read `src/stackowl/config/provider.py` end to end before editing — you need the exact current field order (`name, protocol, enabled, api_key, base_url, default_model, tiers, max_retries, timeout_seconds, rate_limit_rpm, max_output_tokens, tool_max_iterations, context_chars, supports_native_tools, cooldown_hours`) and the exact two existing validators (`_normalize_legacy_tier`, `_validate_tiers`) so your new code matches the file's established style exactly.

- [ ] **Step 2: Write the failing tests**

Add to `tests/config/test_provider.py` (or the existing provider-config test file you found in Step 1):

```python
import pytest
from pydantic import ValidationError

from stackowl.config.provider import ModelOverride, ProviderConfig


def _base_kwargs(**overrides: object) -> dict:
    kwargs = {
        "name": "acme",
        "protocol": "openai",
        "default_model": "acme-v1",
        "tiers": ("fast",),
    }
    kwargs.update(overrides)
    return kwargs


class TestModelOverride:
    def test_accepts_minimal_shape(self) -> None:
        m = ModelOverride(name="acme-v1-mini", tiers=("standard",))
        assert m.name == "acme-v1-mini"
        assert m.tiers == ("standard",)
        assert m.max_output_tokens is None
        assert m.context_chars is None

    def test_accepts_explicit_overrides(self) -> None:
        m = ModelOverride(
            name="acme-v1-mini", tiers=("standard",),
            max_output_tokens=50000, context_chars=80000,
        )
        assert m.max_output_tokens == 50000
        assert m.context_chars == 80000

    def test_rejects_empty_tiers(self) -> None:
        with pytest.raises(ValidationError):
            ModelOverride(name="acme-v1-mini", tiers=())

    def test_rejects_duplicate_tiers(self) -> None:
        with pytest.raises(ValidationError):
            ModelOverride(name="acme-v1-mini", tiers=("fast", "fast"))


class TestProviderConfigModels:
    def test_models_defaults_to_empty(self) -> None:
        cfg = ProviderConfig(**_base_kwargs())
        assert cfg.models == ()

    def test_accepts_one_additional_model(self) -> None:
        cfg = ProviderConfig(**_base_kwargs(
            models=(ModelOverride(name="acme-v1-mini", tiers=("standard",)),),
        ))
        assert len(cfg.models) == 1
        assert cfg.models[0].name == "acme-v1-mini"

    def test_rejects_model_name_colliding_with_default_model(self) -> None:
        with pytest.raises(ValidationError):
            ProviderConfig(**_base_kwargs(
                default_model="acme-v1",
                models=(ModelOverride(name="acme-v1", tiers=("standard",)),),
            ))

    def test_rejects_duplicate_model_names(self) -> None:
        with pytest.raises(ValidationError):
            ProviderConfig(**_base_kwargs(
                models=(
                    ModelOverride(name="acme-v1-mini", tiers=("standard",)),
                    ModelOverride(name="acme-v1-mini", tiers=("powerful",)),
                ),
            ))

    def test_existing_config_without_models_key_unaffected(self) -> None:
        # Simulates loading a legacy YAML dict with no "models" key at all.
        cfg = ProviderConfig.model_validate(_base_kwargs())
        assert cfg.models == ()
```

- [ ] **Step 2b: Run to verify the tests fail**

Run: `uv run pytest tests/config/test_provider.py -v -k "ModelOverride or ProviderConfigModels"`
Expected: FAIL — `ImportError: cannot import name 'ModelOverride'` (the class does not exist yet).

- [ ] **Step 3: Add `ModelOverride` and `ProviderConfig.models`**

In `src/stackowl/config/provider.py`, add the imports needed if not already present (`field_validator` is already imported; confirm `ValidationInfo` is NOT currently imported — add it):

```python
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
```

Add the new `ModelOverride` class immediately BEFORE the `ProviderConfig` class definition:

```python
class ModelOverride(BaseModel):
    """An additional model served by the SAME provider connection (api_key/
    base_url/protocol) as its parent ``ProviderConfig`` — lets one connection
    host multiple models, each independently tier-routable and independently
    able to override its context/output-token budget, without duplicating
    the whole provider block for a second model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tiers: tuple[Literal["fast", "standard", "powerful", "local"], ...]
    # None = inherit the parent ProviderConfig's own value for this field.
    max_output_tokens: int | None = None
    context_chars: int | None = None

    @field_validator("tiers")
    @classmethod
    def _validate_tiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("a model's tiers must contain at least one entry")
        if len(set(value)) != len(value):
            raise ValueError(f"a model's tiers must not contain duplicates: {value}")
        return value
```

(Confirm `ConfigDict` is already imported in this file — it is, used by `ProviderConfig` itself; if not, add `from pydantic import ConfigDict`.)

Add the new field to `ProviderConfig`, immediately after the existing `tiers` field (do not reorder any existing field):

```python
    # Additional models sharing THIS provider's connection (api_key/base_url/
    # protocol) — each independently tier-routable, each able to override its
    # own context/output-token budget. Empty by default: every existing
    # single-model config is completely unaffected.
    models: tuple[ModelOverride, ...] = ()
```

Add a new validator at the end of `ProviderConfig`'s validator block (after the existing `_validate_tiers`):

```python
    @field_validator("models")
    @classmethod
    def _validate_models(
        cls, value: tuple[ModelOverride, ...], info: ValidationInfo
    ) -> tuple[ModelOverride, ...]:
        names = [m.name for m in value]
        default = info.data.get("default_model")
        if default is not None and default in names:
            raise ValueError(f"model name '{default}' collides with default_model")
        if len(set(names)) != len(names):
            raise ValueError(f"models must not contain duplicate names: {names}")
        return value
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `uv run pytest tests/config/test_provider.py -v -k "ModelOverride or ProviderConfigModels"`
Expected: PASS, all 9 new tests.

- [ ] **Step 5: Run the full existing provider config test file + lint + types**

Run: `uv run pytest tests/config/test_provider.py tests/config/test_provider_config.py tests/config/test_provider_tier_migration.py tests/config/test_settings_provider_migration.py -v` (use whichever of these files actually exist per your Step 1 read — do not guess file names, list `tests/config/` first)
Run: `uv run ruff check src/stackowl/config/provider.py`
Run: `uv run mypy src/stackowl/config/provider.py`
Expected: all green, zero new findings.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/config/provider.py tests/config/test_provider.py
git commit -m "feat(config): add ModelOverride + ProviderConfig.models for per-model provider config"
```

---

## Task 2: Registry core — `ModelRoute` + `_tiers` restructure

**Files:**
- Modify: `src/stackowl/providers/registry.py`
- Test: `tests/providers/test_provider_registry_multi_tier.py`, `tests/providers/test_provider_registry_multi_tier_membership.py` (both already exist — extend, do not replace)

**Interfaces:**
- Consumes: `ProviderConfig.models` (Task 1).
- Produces: `ModelRoute` (new `NamedTuple`, `model: str`, `tiers: tuple[str, ...]`), importable as `from stackowl.providers.registry import ModelRoute`. `ProviderRegistry._tiers: dict[str, tuple[ModelRoute, ...]]` (was `dict[str, tuple[str, ...]]`). `register_mock(..., models: tuple[ModelRoute, ...] | None = None)` — new optional kwarg.

- [ ] **Step 1: Read the current file in full**

Read `src/stackowl/providers/registry.py` end to end (you already have prior research showing `_build_into`, `apply_settings`, `get_by_tier`, `get_with_cascade`, `resolve_tier_with_fallback`, `resolve_capable_or_degrade`, `register_mock` — but re-read the live file yourself before editing, since exact line numbers may have shifted).

- [ ] **Step 2: Write the failing test**

Add to `tests/providers/test_provider_registry_multi_tier_membership.py`:

```python
from stackowl.providers.registry import ModelRoute, ProviderRegistry
from stackowl.providers.mock_provider import MockProvider


class TestModelRouteStorage:
    def test_register_mock_default_single_model_route(self) -> None:
        """Backward-compat: register_mock with no `models=` kwarg behaves
        exactly as today — one ModelRoute with model="" (provider's own
        default) and the given tier."""
        registry = ProviderRegistry()
        registry.register_mock("acme", MockProvider(name="acme"), tier="fast")
        routes = registry._tiers["acme"]  # noqa: SLF001 — internal-shape test
        assert routes == (ModelRoute(model="", tiers=("fast",)),)

    def test_register_mock_with_explicit_models(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(
                ModelRoute(model="acme-v1", tiers=("fast",)),
                ModelRoute(model="acme-v1-mini", tiers=("standard",)),
            ),
        )
        routes = registry._tiers["acme"]  # noqa: SLF001
        assert routes == (
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("standard",)),
        )

    def test_from_settings_builds_default_model_plus_models_list(self) -> None:
        from stackowl.config.provider import ModelOverride, ProviderConfig
        from stackowl.config.settings import Settings

        settings = Settings.model_construct(
            providers=[
                ProviderConfig(
                    name="acme", protocol="openai", default_model="acme-v1",
                    tiers=("fast",), api_key=None,
                    models=(ModelOverride(name="acme-v1-mini", tiers=("standard",)),),
                )
            ]
        )
        registry = ProviderRegistry.from_settings(settings)
        routes = registry._tiers["acme"]  # noqa: SLF001
        assert routes == (
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("standard",)),
        )
```

- [ ] **Step 2b: Run to verify it fails**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier_membership.py -v -k TestModelRouteStorage`
Expected: FAIL — `ImportError: cannot import name 'ModelRoute'`.

- [ ] **Step 3: Add `ModelRoute` and restructure `_tiers`**

In `src/stackowl/providers/registry.py`, add near the top (after the existing module-level constants `_TIER_ORDER`/`_CAPABILITY_ORDER`):

```python
class ModelRoute(NamedTuple):
    """One routable (model, tiers) pair under a single provider connection.

    ``model`` is the literal model string to pass as ``ModelProvider.stream(
    ..., model=...)``/``.complete(..., model=...)`` — empty string means "use
    the provider's own default_model" (today's byte-identical behavior).
    """

    model: str
    tiers: tuple[str, ...]
```

Add `from typing import NamedTuple` to the existing `typing` import line (currently `from typing import TYPE_CHECKING, cast` — becomes `from typing import TYPE_CHECKING, NamedTuple, cast`).

Change the `_tiers` type annotation in THREE places — `__init__` (currently `self._tiers: dict[str, tuple[str, ...]] = {}`), `_build_into`'s `tiers: dict[str, tuple[str, ...]]` parameter, and `apply_settings`'s three local `dict[str, tuple[str, ...]]` declarations (`new_tiers`, plus the two `self._tiers.get(name, config.tiers)` fallback reads) — to `dict[str, tuple[ModelRoute, ...]]`.

In `_build_into`, change:
```python
        if config.tiers:
            tiers[config.name] = config.tiers
```
to:
```python
        if config.tiers:
            routes = [ModelRoute(model=config.default_model, tiers=config.tiers)]
            routes.extend(
                ModelRoute(model=m.name, tiers=m.tiers) for m in config.models
            )
            tiers[config.name] = tuple(routes)
```

In `apply_settings`, every occurrence of `self._tiers.get(name, config.tiers)` (there are three — in the secret-resolve-failure preserve branch, the fully-unchanged preserve branch, and the secret-rotation branch) becomes:
```python
                        new_tiers[name] = self._tiers.get(
                            name,
                            (
                                ModelRoute(model=config.default_model, tiers=config.tiers),
                                *(ModelRoute(model=m.name, tiers=m.tiers) for m in config.models),
                            ),
                        )
```
(The `self._tiers.get(name, ...)` default only matters the FIRST time a name is ever seen — in steady state it always hits the cache. Keep this fallback expression identical at all three call sites so a fresh `apply_settings` call on a never-before-seen `name` still builds the right default.)

In `register_mock`, change the signature and body:

```python
    def register_mock(
        self,
        name: str,
        mock: ModelProvider,
        *,
        tier: str = "fast",
        base_url: str | None = None,
        is_local: bool | None = None,
        models: tuple[ModelRoute, ...] | None = None,
    ) -> None:
        """Register a mock provider — for tests only. Bypasses config lookup.

        ``base_url`` lets a test mirror the shipped config shape so locality is
        inferred exactly as production does; ``is_local`` overrides it explicitly.
        ``models`` lets a test register MULTIPLE (model, tiers) routes under one
        mock provider (per-model provider config testing); when omitted (the
        default, and every existing call site), behaves byte-identically to
        today: one route, model="" (the provider's own default), tier=``tier``.
        """
        self._providers[name] = mock
        self._tiers[name] = models if models is not None else (ModelRoute(model="", tiers=(tier,)),)
        self._local[name] = is_local if is_local is not None else is_local_url(base_url)
        self._breakers[name] = CircuitBreaker(provider_name=name, clock=self._clock)
        self._limiters[name] = RateLimiter.from_rpm(name, None, clock=self._clock)
        inject_cost_tracker(mock, self._cost_tracker)  # E8-S0cost single recording site
        _inject_resilience(mock, self._breakers[name], self._limiters[name])
        log.engine.debug(
            "[registry] mock registered",
            extra={"_fields": {"name": name, "tier": tier, "models": len(self._tiers[name])}},
        )
```

- [ ] **Step 4: Run to verify the new tests pass**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier_membership.py -v -k TestModelRouteStorage`
Expected: PASS, all 3 tests.

- [ ] **Step 5: Run the FULL existing registry test suite — this task must not break anything yet**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier.py tests/providers/test_provider_registry_multi_tier_membership.py tests/providers/test_provider_registry_health.py tests/providers/test_provider_hot_reload.py -v`

Expected: every existing test still PASSES. If any fails, it is almost certainly because it reads `registry._tiers[name]` directly expecting the OLD `tuple[str, ...]` shape — find those tests and update their assertions to the new `ModelRoute` shape (e.g. `registry._tiers["acme"] == ("fast",)` becomes `registry._tiers["acme"] == (ModelRoute(model="", tiers=("fast",)),)`). Do NOT change any test that reads the registry through its PUBLIC methods (`get_by_tier`, `tiers_of`, etc.) — those still return their old public shapes at this point in the plan (Tasks 3-4 handle that).

Run: `uv run ruff check src/stackowl/providers/registry.py`
Run: `uv run mypy src/stackowl/providers/registry.py`

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/providers/registry.py tests/providers/test_provider_registry_multi_tier_membership.py
git commit -m "feat(providers): registry._tiers becomes model-aware (ModelRoute)"
```

---

## Task 3: `TierSelector` — ModelRoute-aware matching

**Files:**
- Modify: `src/stackowl/providers/tier_selector.py`
- Test: `tests/providers/test_tier_selector.py` (already exists — extend)

**Interfaces:**
- Consumes: `ModelRoute` (Task 2).
- Produces: `TierSelector.select(tier, providers, tiers: dict[str, tuple[ModelRoute, ...]], breakers) -> str | None` — return type UNCHANGED (still a provider name string, not a model) — only the `tiers` parameter's type and the internal membership check change.

- [ ] **Step 1: Read the current file in full**

Read `src/stackowl/providers/tier_selector.py` — it is short (57 lines); you already have its exact content from prior research, but re-read the live file before editing.

- [ ] **Step 2: Write the failing test**

Add to `tests/providers/test_tier_selector.py`:

```python
from stackowl.providers.registry import ModelRoute


class TestModelRouteAwareSelection:
    def test_selects_provider_whose_route_matches_tier(self) -> None:
        selector = TierSelector()
        tiers = {
            "acme": (ModelRoute(model="acme-v1", tiers=("fast",)),),
            "other": (ModelRoute(model="other-v1", tiers=("standard",)),),
        }
        chosen = selector.select("fast", {"acme": object(), "other": object()}, tiers, {})
        assert chosen == "acme"

    def test_selects_when_one_of_several_routes_on_same_provider_matches(self) -> None:
        """A provider with 2 models in DIFFERENT tiers must still be selectable
        for the tier that ONLY its second model serves."""
        selector = TierSelector()
        tiers = {
            "acme": (
                ModelRoute(model="acme-v1", tiers=("fast",)),
                ModelRoute(model="acme-v1-mini", tiers=("standard",)),
            ),
        }
        chosen = selector.select("standard", {"acme": object()}, tiers, {})
        assert chosen == "acme"

    def test_no_match_returns_none(self) -> None:
        selector = TierSelector()
        tiers = {"acme": (ModelRoute(model="acme-v1", tiers=("fast",)),)}
        chosen = selector.select("powerful", {"acme": object()}, tiers, {})
        assert chosen is None
```

- [ ] **Step 2b: Run to verify it fails**

Run: `uv run pytest tests/providers/test_tier_selector.py -v -k TestModelRouteAwareSelection`
Expected: FAIL — `TypeError` or an assertion failure (the current `tier in t` check treats each `ModelRoute` namedtuple as a 2-element iterable being compared to a string, which never matches).

- [ ] **Step 3: Update the membership check**

In `src/stackowl/providers/tier_selector.py`, change:

```python
    def select(
        self,
        tier: str,
        providers: dict[str, object],
        tiers: dict[str, tuple[str, ...]],
        breakers: dict[str, CircuitBreaker],
    ) -> str | None:
        """Return the next healthy provider NAME for ``tier``, or None if empty/all-OPEN."""
        log.engine.debug("[tier_selector] select: entry", extra={"_fields": {"tier": tier}})
        candidates = [name for name, t in tiers.items() if tier in t and name in providers]
```

to:

```python
    def select(
        self,
        tier: str,
        providers: dict[str, object],
        tiers: dict[str, tuple[ModelRoute, ...]],
        breakers: dict[str, CircuitBreaker],
    ) -> str | None:
        """Return the next healthy provider NAME for ``tier``, or None if empty/all-OPEN.

        A provider matches if ANY of its ModelRoute entries serves ``tier`` —
        which specific model within that provider will be used is decided by
        the caller (ProviderRegistry), not here; this method's contract stays
        "which PROVIDER" only, unchanged.
        """
        log.engine.debug("[tier_selector] select: entry", extra={"_fields": {"tier": tier}})
        candidates = [
            name for name, routes in tiers.items()
            if any(tier in route.tiers for route in routes) and name in providers
        ]
```

Add the import: `from stackowl.providers.registry import ModelRoute` — **wait**: this would create a circular import (`registry.py` already imports `TierSelector` from `tier_selector.py`). Instead, add `ModelRoute` under `if TYPE_CHECKING:` for the type hint only (already-established pattern in this file for `CircuitBreaker`):

```python
if TYPE_CHECKING:
    from stackowl.providers.circuit_breaker import CircuitBreaker
    from stackowl.providers.registry import ModelRoute
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/providers/test_tier_selector.py -v`
Expected: PASS, all tests (new + all pre-existing in this file — the pre-existing ones must ALSO be updated to build `ModelRoute`-shaped `tiers` dicts instead of raw string tuples; update them now if this run shows failures).

Run: `uv run ruff check src/stackowl/providers/tier_selector.py`
Run: `uv run mypy src/stackowl/providers/tier_selector.py`

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/tier_selector.py tests/providers/test_tier_selector.py
git commit -m "feat(providers): TierSelector matches ModelRoute-aware tier membership"
```

---

## Task 4: `RegistryAccessorsMixin.tiers_of()` — flatten `ModelRoute` list

**Files:**
- Modify: `src/stackowl/providers/registry_accessors.py`
- Test: `tests/providers/` — find the existing test file covering `tiers_of` (search `grep -rl "tiers_of" tests/providers/`) and extend it.

**Interfaces:**
- Consumes: `ModelRoute` (Task 2).
- Produces: `tiers_of(provider) -> tuple[str, ...] | None` — return type and CONTRACT completely UNCHANGED (still "all tier memberships for this provider, regardless of model" — the vision selector, its only consumer, must see zero behavior change).

- [ ] **Step 1: Read the current file, and find its existing test**

Read `src/stackowl/providers/registry_accessors.py` in full (short, ~63 lines). Run `grep -rln "tiers_of" tests/` to find its existing test coverage before writing new tests.

- [ ] **Step 2: Write the failing test**

Add to the test file found in Step 1 (match its existing fixture/class style):

```python
def test_tiers_of_flattens_across_model_routes() -> None:
    """A provider with 2 models in DIFFERENT tiers must report BOTH tiers via
    tiers_of — the vision selector's contract is provider-level membership,
    regardless of which model serves which tier."""
    registry = ProviderRegistry()
    mock = MockProvider(name="acme")
    registry.register_mock(
        "acme", mock,
        models=(
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("standard",)),
        ),
    )
    assert registry.tiers_of(mock) == ("fast", "standard")


def test_tiers_of_dedupes_a_tier_served_by_two_models() -> None:
    registry = ProviderRegistry()
    mock = MockProvider(name="acme")
    registry.register_mock(
        "acme", mock,
        models=(
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("fast", "standard")),
        ),
    )
    assert registry.tiers_of(mock) == ("fast", "standard")
```

(Add `from stackowl.providers.registry import ModelRoute, ProviderRegistry` and `from stackowl.providers.mock_provider import MockProvider` to the test file's imports if not already present.)

- [ ] **Step 2b: Run to verify it fails**

Run the two new tests by name (use the file path found in Step 1) — expect a `TypeError` (iterating `ModelRoute` objects as if they were bare tier strings) or a wrong-shape assertion failure.

- [ ] **Step 3: Update `tiers_of`**

In `src/stackowl/providers/registry_accessors.py`, change:

```python
    _tiers: dict[str, tuple[str, ...]]
```
to:
```python
    _tiers: dict[str, "tuple[ModelRoute, ...]"]
```
(Add `from stackowl.providers.registry import ModelRoute` under the existing `if TYPE_CHECKING:` block — same circular-import reasoning as Task 3.)

Change:
```python
    def tiers_of(self, provider: ModelProvider) -> tuple[str, ...] | None:
        """All configured routing tiers for this provider, or None if unknown.

        Returns the full tuple of tier memberships; for a provider in multiple
        tiers, the order reflects the config order (e.g. ``("fast", "powerful")``).
        """
        name = self._name_of(provider)
        if name is None:
            return None
        ptiers = self._tiers.get(name)
        return ptiers
```
to:
```python
    def tiers_of(self, provider: ModelProvider) -> tuple[str, ...] | None:
        """All configured routing tiers for this provider, or None if unknown.

        Flattens across every ModelRoute this provider serves (order-preserving,
        de-duplicated) — the contract is PROVIDER-level tier membership regardless
        of which specific model serves which tier; callers (e.g. the vision
        selector) never needed model granularity here.
        """
        name = self._name_of(provider)
        if name is None:
            return None
        routes = self._tiers.get(name)
        if routes is None:
            return None
        seen: list[str] = []
        for route in routes:
            for t in route.tiers:
                if t not in seen:
                    seen.append(t)
        return tuple(seen)
```

- [ ] **Step 4: Run to verify it passes**

Run the full test file found in Step 1.
Run: `uv run ruff check src/stackowl/providers/registry_accessors.py`
Run: `uv run mypy src/stackowl/providers/registry_accessors.py`

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/registry_accessors.py <test file from Step 1>
git commit -m "fix(providers): tiers_of flattens across ModelRoute, contract unchanged"
```

---

## Task 5: Four new `*_and_model` registry methods; old methods become wrappers

**Files:**
- Modify: `src/stackowl/providers/registry.py`
- Test: `tests/providers/test_provider_registry_multi_tier.py`, `tests/providers/test_provider_registry_multi_tier_membership.py`

**Interfaces:**
- Consumes: `ModelRoute` (Task 2), `TierSelector.select` (Task 3, unchanged signature besides `tiers` type).
- Produces:
  - `get_by_tier_and_model(self, tier: str) -> tuple[ModelProvider, str]`
  - `get_with_cascade_and_model(self, preferred_tier: str) -> tuple[ModelProvider, str]`
  - `resolve_tier_with_fallback_and_model(self, tier: str) -> tuple[ModelProvider, str, str | None]`
  - `resolve_capable_or_degrade_and_model(self, tier: str) -> tuple[ModelProvider, str, str | None]`
  - `get_by_tier`, `get_with_cascade`, `resolve_tier_with_fallback`, `resolve_capable_or_degrade` KEEP their existing signatures/return types (bare provider, or 2-tuple) — become one-line wrappers.

- [ ] **Step 1: Read the current file in full** (you edited it in Task 2 — re-read your own current state before this task, do not work from memory).

- [ ] **Step 2: Write the failing tests**

Add to `tests/providers/test_provider_registry_multi_tier_membership.py`:

```python
class TestAndModelResolution:
    def test_get_by_tier_and_model_returns_default_model_route(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("fast",)),),
        )
        provider, model = registry.get_by_tier_and_model("fast")
        assert provider.name == "acme"
        assert model == "acme-v1"

    def test_get_by_tier_and_model_picks_correct_model_among_several(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(
                ModelRoute(model="acme-v1", tiers=("fast",)),
                ModelRoute(model="acme-v1-mini", tiers=("standard",)),
            ),
        )
        provider, model = registry.get_by_tier_and_model("standard")
        assert provider.name == "acme"
        assert model == "acme-v1-mini"

    def test_get_with_cascade_and_model_returns_model(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("fast",)),),
        )
        provider, model = registry.get_with_cascade_and_model("fast")
        assert provider.name == "acme"
        assert model == "acme-v1"

    def test_resolve_tier_with_fallback_and_model_returns_three_tuple(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("fast",)),),
        )
        provider, model, degraded = registry.resolve_tier_with_fallback_and_model("fast")
        assert provider.name == "acme"
        assert model == "acme-v1"
        assert degraded is None

    def test_resolve_capable_or_degrade_and_model_returns_three_tuple(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("powerful",)),),
        )
        provider, model, degraded = registry.resolve_capable_or_degrade_and_model("powerful")
        assert provider.name == "acme"
        assert model == "acme-v1"
        assert degraded is None


class TestOldMethodsUnchangedDuringMigration:
    """Task 5's core safety invariant: every OLD method keeps its exact
    pre-Task-5 contract — a bare provider (or 2-tuple) — for the whole
    migration. These tests exist so the migration cannot silently regress
    an already-shipped caller before its own task lands."""

    def test_get_by_tier_still_returns_bare_provider(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock("acme", MockProvider(name="acme"), tier="fast")
        result = registry.get_by_tier("fast")
        assert not isinstance(result, tuple)
        assert result.name == "acme"

    def test_get_with_cascade_still_returns_bare_provider(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock("acme", MockProvider(name="acme"), tier="fast")
        result = registry.get_with_cascade("fast")
        assert not isinstance(result, tuple)
        assert result.name == "acme"

    def test_resolve_tier_with_fallback_still_returns_two_tuple(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock("acme", MockProvider(name="acme"), tier="fast")
        result = registry.resolve_tier_with_fallback("fast")
        assert len(result) == 2
        provider, degraded = result
        assert provider.name == "acme"
        assert degraded is None

    def test_resolve_capable_or_degrade_still_returns_two_tuple(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock("acme", MockProvider(name="acme"), tier="powerful")
        result = registry.resolve_capable_or_degrade("powerful")
        assert len(result) == 2
```

- [ ] **Step 2b: Run to verify the `*_and_model` tests fail**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier_membership.py -v -k "AndModel"`
Expected: FAIL — `AttributeError: 'ProviderRegistry' object has no attribute 'get_by_tier_and_model'`.

- [ ] **Step 3: Rename the current method bodies to `*_and_model`, add thin back-compat wrappers**

In `src/stackowl/providers/registry.py`, rename `get_by_tier` to `get_by_tier_and_model`, changing its body to also return the matched model. Change:

```python
    def get_by_tier(self, tier: str) -> ModelProvider:
        """Return the first provider matching the given tier (config order).

        Falls back to the first available provider when no exact match exists.
        Use get_with_cascade() for circuit-aware tier traversal.
        """
        # Snapshot both dict refs together so a concurrent apply_settings() swap
        # (watcher thread) can't make us index a name absent from _providers.
        providers = self._providers
        tiers = self._tiers
        for name, provider_tiers in tiers.items():
            if tier in provider_tiers and name in providers:
                return providers[name]
        if providers:
            fallback_name = next(iter(providers))
            # Loud, actionable degrade: a requested tier with no provider means
            # the roster is incomplete (e.g. no capable model configured). Never
            # silently substitute — surface it so the operator can add/relabel
            # a provider for this tier.
            log.engine.warning(
                "[providers] get_by_tier: no provider serves this tier — "
                "using the first registered provider (degraded); add or relabel "
                "a provider for this tier to fix routing",
                extra={"_fields": {"requested_tier": tier, "returned": fallback_name}},
            )
            return providers[fallback_name]
        raise ProviderNotFoundError(f"tier:{tier}")
```

to:

```python
    def get_by_tier_and_model(self, tier: str) -> tuple[ModelProvider, str]:
        """Return (provider, model) for the first match matching the given
        tier (config order). Falls back to the first available provider's
        first route when no exact match exists. Use
        get_with_cascade_and_model() for circuit-aware tier traversal.
        """
        # Snapshot both dict refs together so a concurrent apply_settings() swap
        # (watcher thread) can't make us index a name absent from _providers.
        providers = self._providers
        tiers = self._tiers
        for name, routes in tiers.items():
            if name not in providers:
                continue
            for route in routes:
                if tier in route.tiers:
                    return providers[name], route.model
        if providers:
            fallback_name = next(iter(providers))
            fallback_model = ""
            fallback_routes = tiers.get(fallback_name)
            if fallback_routes:
                fallback_model = fallback_routes[0].model
            # Loud, actionable degrade: a requested tier with no provider means
            # the roster is incomplete (e.g. no capable model configured). Never
            # silently substitute — surface it so the operator can add/relabel
            # a provider for this tier.
            log.engine.warning(
                "[providers] get_by_tier: no provider serves this tier — "
                "using the first registered provider (degraded); add or relabel "
                "a provider for this tier to fix routing",
                extra={"_fields": {"requested_tier": tier, "returned": fallback_name}},
            )
            return providers[fallback_name], fallback_model
        raise ProviderNotFoundError(f"tier:{tier}")

    def get_by_tier(self, tier: str) -> ModelProvider:
        """Back-compat wrapper — TEMPORARY, removed once every call site
        migrates to get_by_tier_and_model (tracked in the per-model provider
        config plan's final cleanup task). Returns just the provider,
        byte-identical to this method's pre-migration contract."""
        return self.get_by_tier_and_model(tier)[0]
```

Apply the SAME pattern to `get_with_cascade`. Rename its body to `get_with_cascade_and_model`, changing its return type to `tuple[ModelProvider, str]`. Inside the method, after `self._tier_selector.select(...)` returns a `chosen` provider name, look up which `ModelRoute` on that provider actually matched the tier and return its `.model` alongside the provider:

```python
    def get_with_cascade_and_model(self, preferred_tier: str) -> tuple[ModelProvider, str]:
        """Return (first non-OPEN provider, its matched model) starting at
        preferred_tier. Walks tiers in order fast → standard → powerful →
        local, starting at `preferred_tier` and wrapping. Skips providers
        whose CircuitBreaker is OPEN. Raises AllProvidersUnavailableError if
        every provider is OPEN.
        """
        log.engine.debug(
            "[registry] get_with_cascade: entry",
            extra={"_fields": {"preferred_tier": preferred_tier}},
        )

        if preferred_tier in _TIER_ORDER:
            start = _TIER_ORDER.index(preferred_tier)
            tier_walk: tuple[str, ...] = _TIER_ORDER[start:] + _TIER_ORDER[:start]
        else:
            log.engine.warning(
                "[registry] get_with_cascade: unknown tier — using full order",
                extra={"_fields": {"preferred_tier": preferred_tier}},
            )
            tier_walk = _TIER_ORDER

        providers = self._providers
        tiers = self._tiers
        breakers = self._breakers

        details: list[str] = []
        for tier in tier_walk:
            tier_names = [
                name for name, routes in tiers.items()
                if any(tier in route.tiers for route in routes)
            ]

            chosen: str | None = None
            prov: ModelProvider | None = None
            missing_this_tier: set[str] = set()
            for _ in range(len(tier_names) or 1):
                candidate = self._tier_selector.select(tier, cast("dict[str, object]", providers), tiers, breakers)
                if candidate is None:
                    break
                candidate_prov = providers.get(candidate)
                if candidate_prov is None:
                    missing_this_tier.add(candidate)
                    log.engine.warning(
                        "[cascade] selected provider missing from snapshot "
                        "(concurrent removal) — retrying within the same tier",
                        extra={"_fields": {"provider": candidate, "tier": tier}},
                    )
                    if len(missing_this_tier) >= len(tier_names):
                        break
                    continue
                chosen = candidate
                prov = candidate_prov
                break

            if chosen is not None and prov is not None:
                breaker = breakers.get(chosen)
                state = breaker.state if breaker is not None else None
                log.engine.info(
                    "[cascade] selected '%s' (tier=%s, state=%s)",
                    chosen,
                    tier,
                    state.value if state is not None else "no-breaker",
                    extra={
                        "_fields": {
                            "provider": chosen,
                            "tier": tier,
                            "circuit_state": state.value if state is not None else None,
                        }
                    },
                )
                chosen_model = ""
                for route in tiers.get(chosen, ()):
                    if tier in route.tiers:
                        chosen_model = route.model
                        break
                return prov, chosen_model
            candidates = [
                name for name, routes in tiers.items()
                if any(tier in route.tiers for route in routes) and name in providers
            ]
            if candidates:
                open_names = [
                    name for name in candidates
                    if breakers.get(name) is not None and breakers[name].state is CircuitState.OPEN
                ]
                for name in open_names:
                    msg = f"{name}: skipped (circuit open)"
                    log.engine.info(
                        "[cascade] %s: skipped (circuit open)",
                        name,
                        extra={
                            "_fields": {
                                "provider": name,
                                "tier": tier,
                                "retry_after_seconds": breakers[name].retry_after_seconds,
                            }
                        },
                    )
                    details.append(msg)

        log.engine.error(
            "[registry] get_with_cascade: exit — all providers unavailable",
            extra={"_fields": {"details": details}},
        )
        raise AllProvidersUnavailableError(details)

    def get_with_cascade(self, preferred_tier: str) -> ModelProvider:
        """Back-compat wrapper — TEMPORARY, removed in the final cleanup task."""
        return self.get_with_cascade_and_model(preferred_tier)[0]
```

Apply the same pattern to `resolve_tier_with_fallback` (rename to `resolve_tier_with_fallback_and_model`, return `tuple[ModelProvider, str, str | None]`):

```python
    def resolve_tier_with_fallback_and_model(
        self, tier: str,
    ) -> tuple[ModelProvider, str, str | None]:
        """Tier resolution that is circuit-aware ONLY when the chosen provider
        is OPEN. Returns (provider, model, degraded_from). Happy path (chosen
        provider healthy) is byte-identical to get_by_tier_and_model; the
        cascade is only invoked when the chosen provider's circuit is OPEN.
        """
        log.engine.debug(
            "[registry] resolve_tier_with_fallback: entry",
            extra={"_fields": {"tier": tier}},
        )
        providers = self._providers
        tiers = self._tiers
        breakers = self._breakers
        primary_name: str | None = None
        primary_model: str = ""
        for name, routes in tiers.items():
            if name not in providers:
                continue
            for route in routes:
                if tier in route.tiers:
                    primary_name = name
                    primary_model = route.model
                    break
            if primary_name is not None:
                break
        if primary_name is None:
            log.engine.debug(
                "[registry] resolve_tier_with_fallback: no tier match — config degrade",
                extra={"_fields": {"tier": tier}},
            )
            provider, model = self.get_by_tier_and_model(tier)
            return provider, model, None
        breaker = breakers.get(primary_name)
        if breaker is None or breaker.state is not CircuitState.OPEN:
            log.engine.debug(
                "[registry] resolve_tier_with_fallback: exit — healthy primary",
                extra={"_fields": {"tier": tier, "primary": primary_name}},
            )
            return providers[primary_name], primary_model, None
        log.engine.info(
            "[registry] resolve_tier_with_fallback: primary circuit OPEN — cascading",
            extra={"_fields": {"tier": tier, "degraded_from": primary_name}},
        )
        healthy, healthy_model = self.get_with_cascade_and_model(tier)
        return healthy, healthy_model, primary_name

    def resolve_tier_with_fallback(self, tier: str) -> tuple[ModelProvider, str | None]:
        """Back-compat wrapper — TEMPORARY, removed in the final cleanup task."""
        provider, _model, degraded = self.resolve_tier_with_fallback_and_model(tier)
        return provider, degraded
```

Apply the same pattern to `resolve_capable_or_degrade`:

```python
    def resolve_capable_or_degrade_and_model(
        self, tier: str,
    ) -> tuple[ModelProvider, str, str | None]:
        """Resolve a CAPABLE tier, cascading to the most-capable available
        substitute. Returns (provider, model, degraded_from)."""
        log.engine.debug(
            "[registry] resolve_capable_or_degrade: entry",
            extra={"_fields": {"tier": tier}},
        )
        providers = self._providers
        tiers = self._tiers

        for name, routes in tiers.items():
            if name not in providers:
                continue
            for route in routes:
                if tier in route.tiers:
                    return providers[name], route.model, None

        for cand_tier in _CAPABILITY_ORDER:
            if cand_tier == tier:
                continue
            for name, routes in tiers.items():
                if name not in providers:
                    continue
                for route in routes:
                    if cand_tier in route.tiers:
                        log.engine.warning(
                            "[registry] resolve_capable_or_degrade: no provider for "
                            "requested tier — substituting the most-capable available "
                            "tier (DEGRADED); add/relabel a provider to fix routing",
                            extra={"_fields": {
                                "requested_tier": tier,
                                "substitute_tier": cand_tier,
                                "substitute": name,
                            }},
                        )
                        return providers[name], route.model, tier

        log.engine.error(
            "[registry] resolve_capable_or_degrade: no providers registered",
            extra={"_fields": {"tier": tier}},
        )
        raise ProviderNotFoundError(f"tier:{tier}")

    def resolve_capable_or_degrade(self, tier: str) -> tuple[ModelProvider, str | None]:
        """Back-compat wrapper — TEMPORARY, removed in the final cleanup task."""
        provider, _model, degraded = self.resolve_capable_or_degrade_and_model(tier)
        return provider, degraded
```

- [ ] **Step 4: Run to verify ALL tests pass — this is the most important regression gate in the whole plan**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier.py tests/providers/test_provider_registry_multi_tier_membership.py tests/providers/test_provider_registry_health.py tests/providers/test_provider_hot_reload.py tests/providers/test_tier_selector.py -v`
Expected: 100% pass — every OLD-method test (Step 2's `TestOldMethodsUnchangedDuringMigration`) AND every pre-existing test in these files AND the new `*_and_model` tests.

Run: `uv run ruff check src/stackowl/providers/registry.py`
Run: `uv run mypy src/stackowl/providers/registry.py`

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/registry.py tests/providers/test_provider_registry_multi_tier_membership.py
git commit -m "feat(providers): add get_*_and_model registry methods; old methods become temporary wrappers"
```

---

## Task 6: `OpenAIProvider` — per-model override resolution

**Files:**
- Modify: `src/stackowl/providers/openai_provider.py`
- Test: `tests/providers/test_complete_think_strip.py` (existing — extend)

**Interfaces:**
- Consumes: `ProviderConfig.models` (Task 1).
- Produces: a new shared helper `resolve_model_override(config: ProviderConfig, model_name: str) -> tuple[int, int | None]` returning `(effective_max_output_tokens, effective_context_chars)` for the given model — falls back to the provider's own `max_output_tokens`/`context_chars` when `model_name` matches no entry in `config.models` or matches an entry whose override field is `None`.

- [ ] **Step 1: Read the current file's `_output_cap` method and its two callers (`complete`, `complete_with_tools`) in full** — you have prior research showing `_output_cap` (lines ~959-982); re-read the live file before editing, and locate where `context_chars` is read for the window-probe call (search `context_chars` in this file — likely near `resolve_window(...)` inside `stream()`/`complete()`).

- [ ] **Step 2: Write the failing test**

Add to `tests/providers/test_complete_think_strip.py`:

```python
from stackowl.config.provider import ModelOverride
from stackowl.providers.model_config import resolve_model_override


class TestResolveModelOverride:
    def test_falls_back_to_provider_value_when_model_not_in_models_list(self) -> None:
        config = ProviderConfig(
            name="acme", protocol="openai", default_model="acme-v1",
            tiers=("fast",), max_output_tokens=250000, context_chars=None,
        )
        max_tokens, context_chars = resolve_model_override(config, "acme-v1")
        assert max_tokens == 250000
        assert context_chars is None

    def test_uses_model_override_when_set(self) -> None:
        config = ProviderConfig(
            name="acme", protocol="openai", default_model="acme-v1",
            tiers=("fast",), max_output_tokens=250000,
            models=(
                ModelOverride(
                    name="acme-v1-mini", tiers=("standard",),
                    max_output_tokens=50000, context_chars=80000,
                ),
            ),
        )
        max_tokens, context_chars = resolve_model_override(config, "acme-v1-mini")
        assert max_tokens == 50000
        assert context_chars == 80000

    def test_model_in_list_but_override_none_falls_back_to_provider_value(self) -> None:
        config = ProviderConfig(
            name="acme", protocol="openai", default_model="acme-v1",
            tiers=("fast",), max_output_tokens=250000,
            models=(ModelOverride(name="acme-v1-mini", tiers=("standard",)),),
        )
        max_tokens, context_chars = resolve_model_override(config, "acme-v1-mini")
        assert max_tokens == 250000
        assert context_chars is None
```

- [ ] **Step 2b: Run to verify it fails**

Run: `uv run pytest tests/providers/test_complete_think_strip.py -v -k TestResolveModelOverride`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.providers.model_config'`.

- [ ] **Step 3: Create the shared helper module**

Create `src/stackowl/providers/model_config.py`:

```python
"""resolve_model_override — per-model context/output-token override lookup.

Shared by every ModelProvider subclass (OpenAI, Anthropic, Gemini) so a
provider serving multiple models (ProviderConfig.models) resolves each
model's OWN max_output_tokens/context_chars — falling back to the parent
provider's own value when the model has no override, or is the provider's
own default_model (never in .models).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stackowl.config.provider import ProviderConfig


def resolve_model_override(config: ProviderConfig, model_name: str) -> tuple[int, int | None]:
    """Return (effective_max_output_tokens, effective_context_chars) for ``model_name``.

    ``model_name`` matching an entry in ``config.models`` with a non-None
    override uses that model's own value; any other case (no match, or a
    match with the override field left None) falls back to the provider's
    own ``max_output_tokens``/``context_chars``.
    """
    for m in config.models:
        if m.name == model_name:
            max_tokens = m.max_output_tokens if m.max_output_tokens is not None else config.max_output_tokens
            context_chars = m.context_chars if m.context_chars is not None else config.context_chars
            return max_tokens, context_chars
    return config.max_output_tokens, config.context_chars
```

- [ ] **Step 4: Run to verify the new tests pass**

Run: `uv run pytest tests/providers/test_complete_think_strip.py -v -k TestResolveModelOverride`
Expected: PASS, all 3 tests.

- [ ] **Step 5: Wire the helper into `OpenAIProvider._output_cap`**

In `src/stackowl/providers/openai_provider.py`, change `_output_cap`:

```python
    def _output_cap(self, resolved_model: str) -> int:
        """Output-token budget for a generation — as much as the model's window
        allows, never a small fixed cap, but NEVER the whole window either.
        ...
        """
        from stackowl.providers.model_window import cached_window

        window = cached_window(self._name, resolved_model)
        if window is None:
            return self._config.max_output_tokens
        return min(window, self._config.max_output_tokens)
```

to:

```python
    def _output_cap(self, resolved_model: str) -> int:
        """Output-token budget for a generation — as much as the model's window
        allows, never a small fixed cap, but NEVER the whole window either.

        Bounded by the RESOLVED model's own max_output_tokens (per-model
        override if ``resolved_model`` matches a ProviderConfig.models entry
        with one set, else the provider's own value) — see
        providers/model_config.py's resolve_model_override.
        """
        from stackowl.providers.model_config import resolve_model_override
        from stackowl.providers.model_window import cached_window

        effective_max_output_tokens, _effective_context_chars = resolve_model_override(
            self._config, resolved_model
        )
        window = cached_window(self._name, resolved_model)
        if window is None:
            return effective_max_output_tokens
        return min(window, effective_max_output_tokens)
```

Add a test proving `_output_cap` itself now honors a per-model override — add to `tests/providers/test_complete_think_strip.py`:

```python
@pytest.mark.asyncio
async def test_output_cap_uses_per_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.providers import model_window

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setitem(model_window._WINDOW_CACHE, ("ollama", "acme-v1-mini"), 262144)
    config = ProviderConfig(
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="qwen3.5:2b", tiers=("fast",), max_output_tokens=250000,
        models=(
            ModelOverride(name="acme-v1-mini", tiers=("standard",), max_output_tokens=9000),
        ),
    )
    provider = OpenAIProvider(config, api_key="")
    assert provider._output_cap("acme-v1-mini") == 9000  # noqa: SLF001
    assert provider._output_cap("qwen3.5:2b") == 250000  # noqa: SLF001 — default_model, unaffected
```

- [ ] **Step 6: Run to verify everything passes**

Run: `uv run pytest tests/providers/test_complete_think_strip.py -v`
Expected: PASS — every test in the file, including the two you just added and every pre-existing test (`_output_cap` for `default_model` paths must stay byte-identical).

Run: `uv run ruff check src/stackowl/providers/model_config.py src/stackowl/providers/openai_provider.py`
Run: `uv run mypy src/stackowl/providers/model_config.py src/stackowl/providers/openai_provider.py`

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/providers/model_config.py src/stackowl/providers/openai_provider.py tests/providers/test_complete_think_strip.py
git commit -m "feat(providers): OpenAIProvider._output_cap honors per-model max_output_tokens override"
```

---

## Task 7: `AnthropicProvider` + `GeminiProvider` — per-model override resolution

**Files:**
- Modify: `src/stackowl/providers/anthropic_provider.py`, `src/stackowl/providers/gemini_provider.py`
- Test: find or create `tests/providers/test_anthropic_provider.py`, `tests/providers/test_gemini_provider.py` (search `tests/providers/` for existing coverage of these two files first — extend, do not duplicate)

**Interfaces:**
- Consumes: `resolve_model_override` (Task 6).

- [ ] **Step 1: Read `src/stackowl/providers/anthropic_provider.py` and `src/stackowl/providers/gemini_provider.py` in full** before editing (confirmed structure below — re-read the live files first in case they've shifted).

Both files define their OWN local `_max_tokens(kwargs, default=4096)` helper (NOT the same function as `openai_provider.py`'s), and both `stream()`/`complete()` call it as `_max_tokens(kwargs)` — **with no `default=` argument at all**, meaning both currently ALWAYS use the hardcoded `4096` fallback, never `self._config.max_output_tokens`, regardless of what's configured. This is a real pre-existing bug distinct from the per-model-override feature — fixing it (by passing a real default through) is IN scope for this task since it's the same line this task needs to touch anyway; adding window-awareness (an `_output_cap`-equivalent, matching `OpenAIProvider`) is a separate, bigger, unrelated gap and stays OUT of scope.

Confirmed call sites:
- `anthropic_provider.py:122` (inside `stream()`, `resolved_model` assigned at line 115) and `anthropic_provider.py:690` (inside `complete()`, `resolved_model` assigned at line 681) — both `max_tokens=_max_tokens(kwargs)`.
- `anthropic_provider.py:336` and `:576` (both inside `complete_with_tools()`, which has NO `model` parameter in its signature at all — always implicitly targets `self._config.default_model`) — `max_tokens=self._config.max_output_tokens`, direct read, no `_max_tokens()` wrapper. **Leave these two sites untouched in this task** — `complete_with_tools` has no per-call model to key an override off of (Task 22 separately flags whether `complete_with_tools` should gain a `model` parameter at the `ModelProvider` ABC level; that is out of this task's scope).
- `gemini_provider.py:136` (inside `stream()`, `resolved_model` assigned at line 133) and `gemini_provider.py:223` (inside `complete()`, `resolved_model` assigned at line 220) — both `max_output_tokens=_max_tokens(kwargs)`. Gemini has no `complete_with_tools` method at all.

- [ ] **Step 2: Write a failing test per provider**, matching whatever test file(s) you find at `tests/providers/test_anthropic_provider.py` / `tests/providers/test_gemini_provider.py` (search `tests/providers/` first — extend if either exists, create following `tests/providers/test_complete_think_strip.py`'s established fixture style if neither does). Mirror Task 6's `test_output_cap_uses_per_model_override` shape: construct a `ProviderConfig` with one `ModelOverride` entry carrying a distinctive `max_output_tokens` value, construct the provider, and assert the outbound `max_tokens`/`max_output_tokens` request parameter differs correctly between `default_model` and the overridden model — you will need a scripted/fake client recording the kwargs passed to `self._client.messages.stream(...)`/`.create(...)` (Anthropic) or the Gemini SDK's equivalent call, matching whatever mocking convention this codebase's existing Anthropic/Gemini tests already use (search for one before inventing a new pattern).

- [ ] **Step 3: Wire `resolve_model_override` into the four confirmed sites**

In `src/stackowl/providers/anthropic_provider.py`, change line 122 from:
```python
                max_tokens=_max_tokens(kwargs),
```
to:
```python
                max_tokens=_max_tokens(kwargs, default=resolve_model_override(self._config, resolved_model)[0]),
```
(add `from stackowl.providers.model_config import resolve_model_override` to this file's imports). Apply the identical change to line 690 (inside `complete()`), using that method's own `resolved_model` local (assigned at line 681).

In `src/stackowl/providers/gemini_provider.py`, change line 136 from:
```python
            max_output_tokens=_max_tokens(kwargs),
```
to:
```python
            max_output_tokens=_max_tokens(kwargs, default=resolve_model_override(self._config, resolved_model)[0]),
```
(add the same import). Apply the identical change to line 223 (inside `complete()`), using that method's own `resolved_model` local (assigned at line 220).

- [ ] **Step 4: Run the tests you wrote in Step 2, verify pass.**

- [ ] **Step 5: Run each file's full existing test suite + lint + types**

Run whatever test command covers each of the two files (found/created in Step 1).
Run: `uv run ruff check src/stackowl/providers/anthropic_provider.py src/stackowl/providers/gemini_provider.py`
Run: `uv run mypy src/stackowl/providers/anthropic_provider.py src/stackowl/providers/gemini_provider.py`

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/providers/anthropic_provider.py src/stackowl/providers/gemini_provider.py <test files>
git commit -m "feat(providers): Anthropic/Gemini providers honor per-model max_output_tokens override"
```

---

## Task 8: `ToolProviderChoice.model` + `_ensure_tool_capable` + `select_tool_provider_plan`

**Files:**
- Modify: `src/stackowl/pipeline/provider_select.py`
- Test: find the existing test file covering `select_tool_provider_plan`/`ToolProviderChoice` (search `tests/pipeline/` — likely `tests/pipeline/test_provider_select.py` or similar; do not guess, search first) — extend it.

**Interfaces:**
- Consumes: `resolve_tier_with_fallback_and_model` (Task 5).
- Produces: `ToolProviderChoice.model: str` (new field). `_ensure_tool_capable(...) -> tuple[ModelProvider, str]` (was `-> ModelProvider`). `select_tool_provider_plan(...)` populates `.model` on every one of its 3 return branches.

- [ ] **Step 1: Read `src/stackowl/pipeline/provider_select.py` in full** (you have prior research showing its exact content — re-read the live file before editing).

- [ ] **Step 2: Write the failing tests**

In the test file found by your search, add:

```python
class TestToolProviderChoiceModel:
    async def test_owl_named_pin_has_empty_model(self, ...) -> None:
        """An owl-named provider pin has no tier context — model stays "" (use
        the provider's own default), byte-identical to pre-refactor behavior."""
        # Construct a registry + services + state such that registry.get(state.owl_name)
        # succeeds (an owl-named provider is registered) — mirror this test file's
        # OWN existing fixture pattern for the "owl-named provider" test case, do
        # not invent a new one.
        ...
        choice = select_tool_provider_plan(registry, services, state)
        assert choice.model == ""

    async def test_tier_resolved_choice_carries_the_resolved_model(self, ...) -> None:
        """A provider with 2 models in different tiers: select_tool_provider_plan
        for the SECOND tier must return that model's name, not the provider's
        default_model."""
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(
                ModelRoute(model="acme-v1", tiers=("fast",)),
                ModelRoute(model="acme-v1-mini", tiers=("standard",)),
            ),
        )
        # Mirror this file's existing pattern for constructing `services`/`state`
        # such that tier resolution (not a pin) is exercised, with manifest.model_tier
        # (or session tier) = "standard".
        ...
        choice = select_tool_provider_plan(registry, services, state)
        assert choice.model == "acme-v1-mini"
```

(Fill in the `...` fixture construction using THIS test file's own already-established helpers for building `registry`/`services`/`state` — do not invent new fixture shapes; read the rest of the file for the pattern before writing these two tests.)

- [ ] **Step 2b: Run to verify they fail**

Run the two new tests by name.
Expected: FAIL — `AttributeError: 'ToolProviderChoice' object has no attribute 'model'`.

- [ ] **Step 3: Add the `model` field and thread it through**

In `src/stackowl/pipeline/provider_select.py`, change the `ToolProviderChoice` dataclass:

```python
@dataclass(frozen=True)
class ToolProviderChoice:
    """The resolved tool-loop provider PLUS the escalation plan execute needs.
    ...
    """

    provider: ModelProvider
    model: str
    ceiling_tier: str
    pinned: bool
    floor_tier: str = "fast"
```

Change `_ensure_tool_capable`'s signature and every return point:

```python
def _ensure_tool_capable(
    provider: ModelProvider,
    model: str,
    registry: ProviderRegistry,
    state: PipelineState,
    *,
    log_selection: bool,
) -> tuple[ModelProvider, str]:
    """F120 capability gate: for an AGENTIC turn, never return a provider that can't
    act (``supports_tools is False``).
    ...
    """
    if state.intent_class in TOOL_FREE_CLASSES:
        return provider, model
    if getattr(provider, "supports_tools", True):
        return provider, model

    log.engine.warning(
        "[pipeline] execute: selected provider cannot call tools on an agentic turn — "
        "routing to a tool-capable tier",
        extra={"_fields": {
            "owl": state.owl_name,
            "incapable_provider": getattr(provider, "name", type(provider).__name__),
            "intent_class": state.intent_class,
        }},
    )
    seen: set[int] = set()
    for tier in _TOOL_CAPABLE_TIER_WALK:
        try:
            candidate, candidate_model, _degraded = registry.resolve_tier_with_fallback_and_model(tier)
        except AllProvidersUnavailableError:
            continue
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        if getattr(candidate, "supports_tools", True):
            if log_selection:
                log.engine.info(
                    "[pipeline] execute: routed agentic turn to a tool-capable provider",
                    extra={"_fields": {
                        "owl": state.owl_name,
                        "chosen_provider_name": getattr(candidate, "name", "?"),
                        "source": "tool_capability_route_away",
                    }},
                )
            return candidate, candidate_model

    log.engine.error(
        "[pipeline] execute: no tool-capable provider for an agentic turn — flooring honestly",
        extra={"_fields": {"owl": state.owl_name, "intent_class": state.intent_class}},
    )
    raise ToolUseUnsupportedError(getattr(provider, "name", type(provider).__name__))
```

Change `select_tool_provider_plan`'s three return branches. Step 0 (owl-named pin — no tier context, `model=""`):

```python
    try:
        provider = registry.get(state.owl_name)
        if record_recovery:
            _warn_owl_name_shadow(services, state)
        if log_selection:
            log.engine.info(
                "[pipeline] execute: tool provider selected",
                extra={"_fields": {
                    "owl": state.owl_name,
                    "chosen_provider_name": state.owl_name,
                    "source": "owl_named_provider",
                    "pinned": True,
                }},
            )
        capable_provider, capable_model = _ensure_tool_capable(
            provider, "", registry, state, log_selection=log_selection
        )
        return ToolProviderChoice(
            provider=capable_provider,
            model=capable_model,
            ceiling_tier="powerful",
            pinned=True,
            floor_tier="fast",
        )
    except ProviderNotFoundError:
        pass
```

Step 2 (manifest provider_name pin — no tier context, `model=""`):

```python
    if manifest is not None and manifest.provider_name:
        try:
            provider = registry.get(manifest.provider_name)
            if log_selection:
                log.engine.info(
                    "[pipeline] execute: tool provider selected",
                    extra={"_fields": {
                        "owl": state.owl_name,
                        "desired_tier": manifest.model_tier,
                        "chosen_provider_name": manifest.provider_name,
                        "source": "manifest_pin",
                        "pinned": True,
                    }},
                )
            capable_provider, capable_model = _ensure_tool_capable(
                provider, "", registry, state, log_selection=log_selection
            )
            return ToolProviderChoice(
                provider=capable_provider,
                model=capable_model,
                ceiling_tier=manifest.model_tier or "powerful",
                pinned=True,
                floor_tier="fast",
            )
        except ProviderNotFoundError:
            log.engine.warning(
                "[pipeline] execute: manifest provider_name not registered — falling back to tier",
                extra={"_fields": {"owl": state.owl_name, "provider_name": manifest.provider_name}},
            )
```

Step 4 (tier resolution — REAL model threaded through):

```python
    provider, resolved_model, degraded_from = registry.resolve_tier_with_fallback_and_model(desired)
    if degraded_from is not None and record_recovery:
        recovery_context.record_recovery(
            kind="provider_fallback", failed=degraded_from,
            recovered_via=provider.name, user_visible=True,
        )
    pinned = tier_source == "session"
    if log_selection:
        log.engine.info(
            "[pipeline] execute: tool provider selected",
            extra={"_fields": {
                "owl": state.owl_name,
                "desired_tier": desired,
                "chosen_provider_name": getattr(provider, "name", type(provider).__name__),
                "source": tier_source,
                "pinned": pinned,
            }},
        )
    if pinned:
        floor_tier = "fast"
    else:
        _settings = getattr(services, "settings", None)
        _enabled = bool(getattr(_settings, "answer_floor_by_intent", False))
        floor_tier = answer_floor_for_intent(
            state.intent_class, ceiling=desired, enabled=_enabled
        )
    capable_provider, capable_model = _ensure_tool_capable(
        provider, resolved_model, registry, state, log_selection=log_selection
    )
    return ToolProviderChoice(
        provider=capable_provider,
        model=capable_model,
        ceiling_tier=desired,
        pinned=pinned,
        floor_tier=floor_tier,
    )
```

- [ ] **Step 4: Run to verify the new tests pass, and every pre-existing test in this file still passes**

Run the full test file found in Step 1's search.
Expected: PASS. Every pre-existing `ToolProviderChoice(...)` construction in THIS test file's own fixtures now needs a `model=` argument too (since it is a required field with no default) — fix each one to pass `model=""` (matching pre-refactor behavior) unless the test is specifically about tier-resolved model routing, in which case pass the real expected model string.

Run: `uv run ruff check src/stackowl/pipeline/provider_select.py`
Run: `uv run mypy src/stackowl/pipeline/provider_select.py`

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/provider_select.py <test file>
git commit -m "feat(pipeline): ToolProviderChoice carries the resolved model name"
```

---

## Task 9: `pipeline/steps/execute.py` + `pipeline/persistence.py`'s `judge_delivery`

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py`, `src/stackowl/pipeline/persistence.py`
- Test: find the existing test file(s) covering `execute.py`'s `_persistence_check`/`build_persistence_check` (search `tests/pipeline/` — likely `tests/pipeline/steps/test_execute.py` or a dedicated persistence test file) — extend.

**Interfaces:**
- Consumes: `ToolProviderChoice.model` (Task 8), `get_with_cascade_and_model` (Task 5).
- Produces: `judge_delivery(provider, model, ...)` — `persistence.py`'s existing signature gains a `model: str` parameter.

- [ ] **Step 1: Read `src/stackowl/pipeline/persistence.py`'s `judge_delivery` function in full** (confirmed to exist at line ~409 per prior research — read its exact current signature and its internal `.complete(` call to see exactly how `model=` is passed today).

- [ ] **Step 2: Write the failing test**

In `src/stackowl/pipeline/persistence.py`'s existing test coverage (find via `grep -rln "judge_delivery" tests/`), add a test proving `judge_delivery` forwards an explicit `model` argument to the provider's `.complete(...)` call — mirror whatever mock-provider/kwargs-recording pattern that test file already uses (do not invent a new one; if none exists, use a small local fake provider class recording `complete()`'s kwargs, matching the style already established in `tests/test_story_4_5.py`'s `_SlowMockProvider`/`_HoldingMockProvider`).

- [ ] **Step 2b: Run to verify it fails.**

- [ ] **Step 3: Add `model` parameter to `judge_delivery`, thread it into its `.complete(` call**

In `src/stackowl/pipeline/persistence.py`, add `model: str = ""` to `judge_delivery`'s signature (as a new keyword parameter, defaulting to `""` so any OTHER existing caller you have not yet migrated keeps working unchanged), and change its internal `provider.complete(...)` call from `model=""` to `model=model`.

- [ ] **Step 4: Update `execute.py`'s two `get_with_cascade` call sites (lines ~198, ~223) to use `get_with_cascade_and_model` and thread the result into `judge_delivery`**

In `src/stackowl/pipeline/steps/execute.py`'s `build_persistence_check`/`_persistence_check` closure, change:

```python
            judge = primary if primary is not None else (
                preg.get_with_cascade(_judge_tier) if preg is not None else None
            )
            if judge is None:  # no registry → cannot judge (fail open)
                delivered, reason = True, JUDGE_ERROR_REASON
            else:
                delivered, reason = await judge_delivery(
                    judge, state.input_text, draft, tools_tried
                )
```

to:

```python
            judge_model = ""
            if primary is not None:
                judge = primary
            elif preg is not None:
                judge, judge_model = preg.get_with_cascade_and_model(_judge_tier)
            else:
                judge = None
            if judge is None:  # no registry → cannot judge (fail open)
                delivered, reason = True, JUDGE_ERROR_REASON
            else:
                delivered, reason = await judge_delivery(
                    judge, state.input_text, draft, tools_tried, model=judge_model
                )
```

Apply the identical transformation to the fallback-tier branch (`fb = fallback if fallback is not None else preg.get_with_cascade("local")`, lines ~221-229):

```python
            fb_model = ""
            if fallback is not None:
                fb = fallback
            elif preg is not None:
                fb, fb_model = preg.get_with_cascade_and_model("local")
            else:
                fb = None
            if fb is None:  # no fallback available — fail open
                delivered, reason = True, JUDGE_ERROR_REASON
            else:
                delivered, reason = await judge_delivery(
                    fb, state.input_text, draft, tools_tried, model=fb_model
                )
```

(`primary`/`fallback` are injected `ModelProvider | None` params to `build_persistence_check` used by tests — they have no tier context, so `model` stays `""` for them, matching pre-refactor behavior exactly.)

- [ ] **Step 5: Update `execute.py`'s main `provider = choice.provider` site to thread `choice.model` into `_open_stream`**

Find the site `provider = choice.provider` (confirmed ~line 2567 per prior research) and the `_open_stream(provider, manifest, messages)` call (~line 2617). Change `_open_stream`'s signature (in this same file, ~line 2280) to accept a `model: str` parameter:

```python
def _open_stream(
    provider: ModelProvider,
    manifest: OwlAgentManifest | None,
    messages: list[Message],
    model: str = "",
) -> AsyncIterator[str]:
    """Return a guarded stream — every plain-stream call goes through
    OwlResourceGuard, even with no owl manifest."""
    guard = OwlResourceGuard(manifest if manifest is not None else _default_stream_manifest())
    return guard.stream(provider, messages, model=model)
```

Change the call site from `stream_iter = _open_stream(provider, manifest, messages)` to `stream_iter = _open_stream(provider, manifest, messages, choice.model)`.

- [ ] **Step 6: Update `_run_with_tools`'s back-compat bare-`ModelProvider` wrap**

Find the site `if not isinstance(choice, ToolProviderChoice): choice = ToolProviderChoice(provider=choice, ceiling_tier="powerful", pinned=True)` (~line 989). Add `model=""`:

```python
    if not isinstance(choice, ToolProviderChoice):
        choice = ToolProviderChoice(provider=choice, model="", ceiling_tier="powerful", pinned=True)
```

- [ ] **Step 7: Run to verify everything passes**

Run the full test file(s) found in Step 1/2's search, plus: `uv run pytest tests/pipeline/ -k "execute or persistence" -v` (scope this to whatever the actual matching test paths are — list `tests/pipeline/` first if unsure).
Run: `uv run ruff check src/stackowl/pipeline/steps/execute.py src/stackowl/pipeline/persistence.py`
Run: `uv run mypy src/stackowl/pipeline/steps/execute.py src/stackowl/pipeline/persistence.py`

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py src/stackowl/pipeline/persistence.py <test files>
git commit -m "feat(pipeline): execute.py threads the resolved model through _open_stream and judge_delivery"
```

---

## Task 10: `pipeline/steps/assemble.py`

**Files:**
- Modify: `src/stackowl/pipeline/steps/assemble.py`
- Test: find the existing test file covering `assemble.py`'s window-probe logic (search `tests/pipeline/steps/`) — extend.

**Interfaces:**
- Consumes: `select_tool_provider_plan` (Task 8, already exists — this task switches assemble.py from the bare-provider `select_tool_provider` wrapper to the full `select_tool_provider_plan`).

- [ ] **Step 1: Read `src/stackowl/pipeline/steps/assemble.py`'s `run()` function in full**, specifically the window-probe block shown in prior research (lines ~65-91: `_p = select_tool_provider(...)`, `_pc = getattr(_p, "_config", None)`, `resolve_window(..., model=(_pc.default_model if _pc is not None else "") or "", ...)`).

- [ ] **Step 2: Write the failing test**

In the test file found in Step 1, add a test proving that when tier resolution picks a NON-default model (a provider with 2 models in different tiers, tier resolution landing on the second), `assemble.py`'s window probe resolves the window using THAT model's name, not `default_model`. Mirror this test file's own existing fixture pattern for constructing `services`/`state`/a registry with `register_mock(..., models=(...))`.

- [ ] **Step 2b: Run to verify it fails.**

- [ ] **Step 3: Switch to `select_tool_provider_plan`, thread `.model`**

In `src/stackowl/pipeline/steps/assemble.py`, change:

```python
            from stackowl.pipeline.provider_select import select_tool_provider
            from stackowl.providers.model_window import resolve_window
            # Quiet, side-effect-free window probe: no INFO log AND no recovery
            # event (execute's real selection records the provider_fallback once).
            _p = select_tool_provider(
                services.provider_registry, services, state,
                log_selection=False, record_recovery=False,
            )
            _pc = getattr(_p, "_config", None)
            model_window = await resolve_window(
                provider_name=getattr(_p, "name", "") or "",
                base_url=_pc.base_url if _pc is not None else None,
                model=(_pc.default_model if _pc is not None else "") or "",
                context_chars=(_pc.context_chars if _pc is not None else None),
                protocol=getattr(_p, "protocol", "") or "",
                api_key=_safe_resolve_api_key(_pc),
            )
```

to:

```python
            from stackowl.pipeline.provider_select import select_tool_provider_plan
            from stackowl.providers.model_window import resolve_window
            # Quiet, side-effect-free window probe: no INFO log AND no recovery
            # event (execute's real selection records the provider_fallback once).
            _choice = select_tool_provider_plan(
                services.provider_registry, services, state,
                log_selection=False, record_recovery=False,
            )
            _p = _choice.provider
            _pc = getattr(_p, "_config", None)
            _resolved_model = _choice.model or (_pc.default_model if _pc is not None else "") or ""
            model_window = await resolve_window(
                provider_name=getattr(_p, "name", "") or "",
                base_url=_pc.base_url if _pc is not None else None,
                model=_resolved_model,
                context_chars=(_pc.context_chars if _pc is not None else None),
                protocol=getattr(_p, "protocol", "") or "",
                api_key=_safe_resolve_api_key(_pc),
            )
```

(`_choice.model` is `""` for a pinned owl-name/manifest-provider choice — same as before, falls through to `_pc.default_model`. For a tier-resolved choice it is now the REAL resolved model, which is the fix.)

- [ ] **Step 4: Run to verify the new test passes and nothing else breaks**

Run the full test file found in Step 1.
Run: `uv run ruff check src/stackowl/pipeline/steps/assemble.py`
Run: `uv run mypy src/stackowl/pipeline/steps/assemble.py`

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/assemble.py <test file>
git commit -m "fix(pipeline): assemble.py's window probe uses the tier-resolved model, not always default_model"
```

---

## Task 11: `tools/agents/delegate_task.py` + `pipeline/persistence.py`'s `judge_relevance`

**Files:**
- Modify: `src/stackowl/tools/agents/delegate_task.py`, `src/stackowl/pipeline/persistence.py`
- Test: find existing coverage (search `tests/tools/agents/` and `tests/pipeline/` for `judge_relevance`/`delegate_task`) — extend.

**Interfaces:**
- Consumes: `get_with_cascade_and_model` (Task 5).
- Produces: `judge_relevance(provider, model, ...)` — gains a `model: str = ""` parameter, same pattern as Task 9's `judge_delivery`.

- [ ] **Step 1: Read `src/stackowl/pipeline/persistence.py`'s `judge_relevance` (confirmed at line ~165) and `src/stackowl/tools/agents/delegate_task.py`'s `_run_delegation`/`_relevance_gate` (confirmed at lines ~488-590 per prior research) in full.**

- [ ] **Step 2: Write the failing test** in whichever file your search found, following Task 9's pattern exactly (a test proving `judge_relevance` forwards an explicit `model` kwarg to `.complete(`).

- [ ] **Step 2b: Run to verify it fails.**

- [ ] **Step 3: Add `model: str = ""` to `judge_relevance`'s signature**, threading it into its internal `provider.complete(messages, model="")` call (change to `model=model`).

Find `_relevance_gate`'s signature (module-level helper in `delegate_task.py`, called as `_relevance_gate(res, to_owl, sub_task, fast_provider)`) and add a `model: str` parameter, threading it into its own call to `judge_relevance(fast_provider, sub_task, res.content, model=model)`.

Change `_run_delegation`'s call site:

```python
        fast_provider: object = None
        try:
            fast_provider = get_services().provider_registry.get_with_cascade("fast")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — fail-open is intentional: ...
            log.tool.warning(...)
```

to:

```python
        fast_provider: object = None
        fast_model = ""
        try:
            fast_provider, fast_model = get_services().provider_registry.get_with_cascade_and_model("fast")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — fail-open is intentional: ...
            log.tool.warning(...)
```

Update the `_relevance_gate(res, to_owl, sub_task, fast_provider)` call site (inside `_attempt`) to `_relevance_gate(res, to_owl, sub_task, fast_provider, fast_model)`.

- [ ] **Step 4: Run to verify pass; run the full existing test file(s) for both modules; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/tools/agents/delegate_task.py src/stackowl/pipeline/persistence.py <test files>
git commit -m "feat(tools): delegate_task threads the resolved model through judge_relevance"
```

---

## Task 12: `parliament/` package

**Files:**
- Modify: `src/stackowl/parliament/synthesizer.py`, `src/stackowl/parliament/positions_synthesis.py`
- Read (to find `complete_synthesis_with_retry`'s definition — location unconfirmed by prior research, likely `src/stackowl/parliament/synthesis_retry.py` or inline in one of the two files above): grep `def complete_synthesis_with_retry` across `src/stackowl/parliament/` first.
- Test: find existing coverage in `tests/parliament/` — extend.

**Interfaces:**
- Consumes: `resolve_capable_or_degrade_and_model` (Task 5).
- Produces: `complete_synthesis_with_retry(provider, model, ...)` — gains a `model: str = ""` parameter.

- [ ] **Step 1: Locate and read `complete_synthesis_with_retry`'s full definition** (`grep -rn "def complete_synthesis_with_retry" src/stackowl/parliament/`), plus re-read `synthesizer.py`'s `synthesize` method and `positions_synthesis.py`'s `synthesize_positions` function in full (both confirmed in prior research).

- [ ] **Step 2: Write the failing tests** in the test file(s) found for `tests/parliament/` — one test per caller (`synthesizer.py`, `positions_synthesis.py`) proving the resolved model reaches `complete_synthesis_with_retry`'s internal `.complete(` call, mirroring the pattern from Task 9.

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3: Add `model: str = ""` to `complete_synthesis_with_retry`'s signature**, threading into its internal `.complete(` call.

In `src/stackowl/parliament/synthesizer.py`, change:

```python
        provider, degraded_from = self._providers.resolve_capable_or_degrade("powerful")
```
to:
```python
        provider, model, degraded_from = self._providers.resolve_capable_or_degrade_and_model("powerful")
```
and the `complete_synthesis_with_retry(provider=provider, parser=self._parser, messages=messages, correlation_id=session.session_id)` call to also pass `model=model`.

In `src/stackowl/parliament/positions_synthesis.py`, apply the identical transformation to `provider, degraded_from = providers.resolve_capable_or_degrade("powerful")` → `provider, model, degraded_from = providers.resolve_capable_or_degrade_and_model("powerful")`, and thread `model=model` into its own `complete_synthesis_with_retry(...)` call.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

Run: `uv run pytest tests/parliament/ -v` (scope further if this hangs/is too broad — check test count first with `--collect-only`)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/parliament/synthesizer.py src/stackowl/parliament/positions_synthesis.py <synthesis_retry file> <test files>
git commit -m "feat(parliament): thread the resolved model through complete_synthesis_with_retry"
```

---

## Task 13: `owls/` package — `shadow_validator.py`, `evolution.py`, `router.py`

**Files:**
- Modify: `src/stackowl/owls/shadow_validator.py`, `src/stackowl/owls/evolution.py`, `src/stackowl/owls/router.py`
- Test: `tests/owls/` — find existing coverage for each, extend.

**Interfaces:**
- Consumes: `get_with_cascade_and_model`, `get_by_tier_and_model` (Task 5).

- [ ] **Step 1: Read all three files' relevant methods in full** (you have prior research for all three: `shadow_validator.py`'s `validate`/`_score_replay`, `evolution.py`'s `_llm_fallback`, `router.py`'s `route` — re-read the live files before editing).

- [ ] **Step 2: Write failing tests** for each file's existing test coverage in `tests/owls/`, proving the resolved model reaches the `.complete(` call.

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `shadow_validator.py`** — change `critic_provider = self._providers.get_with_cascade(_CRITIC_TIER)` to `critic_provider, critic_model = self._providers.get_with_cascade_and_model(_CRITIC_TIER)`. Add a `model: str` parameter to `_score_replay`'s signature (`self, outcome, result_state, provider: ModelProvider, model: str`), thread it into its `provider.complete(messages, model="")` → `model=model`. Update the call site `await self._score_replay(outcome, result_state, critic_provider)` → `await self._score_replay(outcome, result_state, critic_provider, critic_model)`.

- [ ] **Step 3b: `evolution.py`** — change `provider = self._provider_registry.get_by_tier("fast")` to `provider, model = self._provider_registry.get_by_tier_and_model("fast")`. Change the nearby `provider.complete(messages, model="", max_tokens=512, disable_thinking=True)` to `provider.complete(messages, model=model, max_tokens=512, disable_thinking=True)`.

- [ ] **Step 3c: `router.py`** — change `provider = self._provider_registry.get_with_cascade(_FAST_TIER)` to `provider, model = self._provider_registry.get_with_cascade_and_model(_FAST_TIER)`. Change the nearby `provider.complete(messages, model="", max_tokens=_ROUTING_MAX_TOKENS, temperature=_ROUTING_TEMPERATURE, disable_thinking=True)` to use `model=model`.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

Run: `uv run pytest tests/owls/test_shadow_validator.py tests/owls/test_evolution*.py tests/owls/test_router*.py -v` (adjust exact filenames to what your search in Step 1 found)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/owls/shadow_validator.py src/stackowl/owls/evolution.py src/stackowl/owls/router.py <test files>
git commit -m "feat(owls): thread the resolved model through shadow_validator/evolution/router provider calls"
```

---

## Task 14: `pipeline/` remainder — `delivery_gate.py`, `planner/proposer.py`, `acceptance_llm.py`

**Files:**
- Modify: `src/stackowl/pipeline/delivery_gate.py`, `src/stackowl/pipeline/planner/proposer.py`, `src/stackowl/pipeline/acceptance_llm.py`
- Test: `tests/pipeline/` — find existing coverage for each, extend.

**Interfaces:**
- Consumes: `get_with_cascade_and_model` (Task 5).

- [ ] **Step 1: Read all three files' relevant functions in full** (prior research: `delivery_gate.py`'s `_generate_localized_apology`, `planner/proposer.py`'s `propose`, `acceptance_llm.py`'s `derive` — re-read live files first).

- [ ] **Step 2: Write failing tests** for each, proving the resolved model reaches the `.complete(` call.

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `delivery_gate.py`** — in `_generate_localized_apology`'s per-tier loop, change `provider = registry.get_with_cascade(tier)` to `provider, model = registry.get_with_cascade_and_model(tier)`. Change `result = await provider.complete(messages, model="", max_tokens=_APOLOGY_MAX_TOKENS, disable_thinking=True)` to use `model=model`.

- [ ] **Step 3b: `planner/proposer.py`** — change `provider = self._providers.get_with_cascade("fast")` + `result = await provider.complete(messages, model="")` (same two-line block) to `provider, model = self._providers.get_with_cascade_and_model("fast")` + `result = await provider.complete(messages, model=model)`.

- [ ] **Step 3c: `acceptance_llm.py`** — change `provider = self._provider_registry.get_with_cascade(self._tier)` to `provider, model = self._provider_registry.get_with_cascade_and_model(self._tier)`. Change the nearby `provider.complete(messages, model="", max_tokens=_DERIVE_MAX_TOKENS, temperature=_DERIVE_TEMPERATURE)` to use `model=model`.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/delivery_gate.py src/stackowl/pipeline/planner/proposer.py src/stackowl/pipeline/acceptance_llm.py <test files>
git commit -m "feat(pipeline): thread the resolved model through delivery_gate/proposer/acceptance_llm"
```

---

## Task 15: `objectives/` package — `decomposer.py`, `driver.py`

**Files:**
- Modify: `src/stackowl/objectives/decomposer.py`, `src/stackowl/objectives/driver.py`
- Test: `tests/objectives/` — find existing coverage, extend.

**Interfaces:**
- Consumes: `get_with_cascade_and_model`, `resolve_capable_or_degrade_and_model` (Task 5).

- [ ] **Step 1: Read both files' relevant methods in full** (prior research: `decomposer.py`'s `decompose_specs`/`decompose_epic_specs`, `driver.py`'s `_synthesize_completion` — re-read live files first).

- [ ] **Step 2: Write failing tests.**

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `decomposer.py`** — apply the same `get_with_cascade` → `get_with_cascade_and_model` + `model=""` → `model=model` transformation at BOTH call sites (`decompose_specs` and `decompose_epic_specs`, both `provider = self._provider_registry.get_with_cascade(_DECOMP_TIER)` followed by `provider.complete(messages, model="", max_tokens=_DECOMP_MAX_TOKENS, temperature=_DECOMP_TEMPERATURE)`).

- [ ] **Step 3b: `driver.py`** — change `provider, degraded_from = self._provider_registry.resolve_capable_or_degrade(_RECOMBINATION_TIER)` to `provider, model, degraded_from = self._provider_registry.resolve_capable_or_degrade_and_model(_RECOMBINATION_TIER)`. Change the nearby `provider.complete(messages, model="", max_tokens=_RECOMBINATION_MAX_TOKENS, temperature=_RECOMBINATION_TEMPERATURE)` to use `model=model`.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/objectives/decomposer.py src/stackowl/objectives/driver.py <test files>
git commit -m "feat(objectives): thread the resolved model through decomposer/driver provider calls"
```

---

## Task 16: `memory/` package part A — `assembly.py` + `FactExtractor`, `entity_extractor.py`

**Files:**
- Modify: `src/stackowl/memory/assembly.py`, `src/stackowl/memory/fact_extractor.py`, `src/stackowl/memory/entity_extractor.py`
- Test: `tests/memory/` — find existing coverage for `FactExtractor`, `EntityExtractor`, extend.

**Interfaces:**
- Consumes: `get_with_cascade_and_model` (Task 5).
- Produces: `FactExtractor.__init__` gains a `model: str = ""` constructor parameter, stored as `self._model`, used wherever it calls `provider.complete(...)`. `EntityExtractor._resolve_provider()` return type widens.

- [ ] **Step 1: Read `src/stackowl/memory/fact_extractor.py`'s `FactExtractor` class in full (constructor confirmed at line ~60 per prior research — find where `self._provider.complete(` or `.stream(` is called inside the class and how `model=` is passed there today). Read `src/stackowl/memory/entity_extractor.py`'s `EntityExtractor` class in full (confirmed: `_resolve_provider(self) -> ModelProvider | None` at line ~156, `extract()` at line ~93).**

- [ ] **Step 2: Write failing tests** for `FactExtractor` (construct it with an explicit `model=` value, verify its internal completion call receives that model) and `EntityExtractor` (a provider with 2 models in different tiers, `extract()` on the tier the SECOND model serves must use that model).

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `FactExtractor`** — add `model: str = ""` to `__init__`'s signature, store as `self._model`. Change wherever `self._provider.complete(...)` (or `.stream(...)`) is called inside the class to pass `model=self._model` instead of whatever it currently passes (likely `model=""`, per this codebase's uniform convention — but verify from your Step 1 read; if it currently passes nothing at all, add `model=self._model` as a new explicit kwarg).

- [ ] **Step 3b: `memory/assembly.py`** — change:
```python
        extraction_provider: ModelProvider = provider_registry.get_with_cascade("standard")
        fact_extractor = FactExtractor(
            provider=extraction_provider,
            embedding_registry=embedding_registry,
            sensitive_categories=mem.sensitive_categories,
            identity_resolver=resolver,
        )
```
to:
```python
        extraction_provider, extraction_model = provider_registry.get_with_cascade_and_model("standard")
        fact_extractor = FactExtractor(
            provider=extraction_provider,
            model=extraction_model,
            embedding_registry=embedding_registry,
            sensitive_categories=mem.sensitive_categories,
            identity_resolver=resolver,
        )
```

- [ ] **Step 3c: `entity_extractor.py`** — change `_resolve_provider(self) -> ModelProvider | None` to return `tuple[ModelProvider, str] | None`:
```python
    def _resolve_provider(self) -> tuple[ModelProvider, str] | None:
        try:
            return self._registry.get_with_cascade_and_model(self._preferred_tier)
        except Exception as exc:
            log.memory.warning(
                "[memory] entity_extractor: provider lookup failed",
                exc_info=exc,
                extra={"_fields": {"preferred_tier": self._preferred_tier}},
            )
            return None
```
Update `extract()`'s caller:
```python
        resolved = self._resolve_provider()
        if resolved is None:
            log.memory.warning("[memory] entity_extractor.extract: no provider available", extra={"_fields": {"fact_id": fact_id}})
            return []
        provider, model = resolved
        ...
        result = await provider.complete(messages, model=model)
```

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/memory/assembly.py src/stackowl/memory/fact_extractor.py src/stackowl/memory/entity_extractor.py <test files>
git commit -m "feat(memory): thread the resolved model through FactExtractor and EntityExtractor"
```

---

## Task 17: `memory/` package part B — `reflection_writer_handler.py`, `critic_scorer_handler.py`

**Files:**
- Modify: `src/stackowl/memory/reflection_writer_handler.py`, `src/stackowl/memory/critic_scorer_handler.py`
- Test: `tests/memory/` — find existing coverage, extend.

**Interfaces:**
- Consumes: `get_with_cascade_and_model` (Task 5).

- [ ] **Step 1: Read both files' `execute()` methods and their private per-row helpers (`_compute_reflection`, `_score_one`) in full** (both confirmed in prior research — re-read live files first).

- [ ] **Step 2: Write failing tests** — a provider resolved once per `execute()` call, reused across a batch loop; prove the model reaches EVERY row's `.complete(` call, not just the first.

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `reflection_writer_handler.py`** — change `provider: ModelProvider = self._providers.get_with_cascade(self._critic_tier)` to `provider, model = self._providers.get_with_cascade_and_model(self._critic_tier)`. Add `model: str` to `_compute_reflection`'s signature (`self, outcome, provider: ModelProvider, model: str`), thread into its `provider.complete(messages, model="")` → `model=model`. Update the call site `p = await self._compute_reflection(outcome, provider)` → `p = await self._compute_reflection(outcome, provider, model)`.

- [ ] **Step 3b: `critic_scorer_handler.py`** — identical pattern: `provider: ModelProvider = self._providers.get_with_cascade(self._critic_tier)` → `provider, model = self._providers.get_with_cascade_and_model(self._critic_tier)`; `_score_one(self, outcome, provider: ModelProvider)` → `_score_one(self, outcome, provider: ModelProvider, model: str)`, threading `model=model` into its `provider.complete(messages, model="", disable_thinking=True)`; update `score = await self._score_one(outcome, provider)` → `score = await self._score_one(outcome, provider, model)`.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/memory/reflection_writer_handler.py src/stackowl/memory/critic_scorer_handler.py <test files>
git commit -m "feat(memory): thread the resolved model through reflection_writer/critic_scorer handlers"
```

---

## Task 18: `skills/` package — `assembly.py`, `synthesizer_handler.py` + `SkillSynthesizer`

**Files:**
- Modify: `src/stackowl/skills/assembly.py`, `src/stackowl/skills/synthesizer_handler.py`, `src/stackowl/skills/synthesizer.py`
- Test: `tests/skills/` — find existing coverage, extend.

**Interfaces:**
- Consumes: `get_with_cascade_and_model` (Task 5).
- Produces: `SkillSynthesizer.__init__` gains a `model: str = ""` parameter (mirroring Task 16's `FactExtractor` pattern).

- [ ] **Step 1: Read `src/stackowl/skills/synthesizer.py`'s `SkillSynthesizer` class in full** (its constructor location was located in prior research — `class SkillSynthesizer` at line ~336; find where it calls `provider.complete(`/`.stream(` internally, likely inside `run_all()` or a phase method). Read `src/stackowl/skills/assembly.py`'s `_summarize_missing` (confirmed ~line 361-434) and `src/stackowl/skills/synthesizer_handler.py`'s `execute` (confirmed ~line 78-138) in full.

- [ ] **Step 2: Write failing tests** for `SkillSynthesizer` (construct with explicit `model=`, verify it reaches the internal completion call) and for `_summarize_missing` (resolved model reaches its per-iteration `.complete(` call).

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `SkillSynthesizer`** — add `model: str = ""` to `__init__`, store as `self._model`, thread into its internal `.complete(`/`.stream(` call(s) found in Step 1.

- [ ] **Step 3b: `synthesizer_handler.py`** — change `provider = self._providers.get_with_cascade(self._synth_tier)` to `provider, model = self._providers.get_with_cascade_and_model(self._synth_tier)`; add `model=model` to the `SkillSynthesizer(provider=provider, model=model, outcome_store=..., ...)` constructor call.

- [ ] **Step 3c: `skills/assembly.py`** — in `_summarize_missing`'s per-skill loop, change `provider = provider_registry.get_with_cascade("fast")` to `provider, model = provider_registry.get_with_cascade_and_model("fast")`; change the nearby `result = await provider.complete(messages, model="")` to `model=model`.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/skills/assembly.py src/stackowl/skills/synthesizer_handler.py src/stackowl/skills/synthesizer.py <test files>
git commit -m "feat(skills): thread the resolved model through SkillSynthesizer and _summarize_missing"
```

---

## Task 19: `tools/browser/browse.py` + `tools/meta/owl_build_infer.py`

**Files:**
- Modify: `src/stackowl/tools/browser/browse.py`, `src/stackowl/tools/meta/owl_build_infer.py`
- Test: `tests/tools/browser/`, `tests/tools/meta/` — find existing coverage, extend.

**Interfaces:**
- Consumes: `get_by_tier_and_model` (Task 5).

- [ ] **Step 1: Read both files' relevant functions in full** (prior research: `browse.py`'s `execute` method around lines 194-408, `owl_build_infer.py`'s `_complete` at lines ~40-53 — re-read live files first).

- [ ] **Step 2: Write failing tests.**

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `browse.py`** — change `inner_provider = providers.get_by_tier(s.inner_browse_model_tier)` to `inner_provider, inner_model = providers.get_by_tier_and_model(s.inner_browse_model_tier)`. Change the per-step-loop `result = await inner_provider.complete(messages, model="")` to `model=inner_model`.

- [ ] **Step 3b: `owl_build_infer.py`** — change `provider = reg.get_by_tier(_FAST_TIER)` + `result = await provider.complete([Message(role="user", content=prompt)], model="")` (same try-block, next line) to `provider, model = reg.get_by_tier_and_model(_FAST_TIER)` + `model=model`.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/tools/browser/browse.py src/stackowl/tools/meta/owl_build_infer.py <test files>
git commit -m "feat(tools): thread the resolved model through browse.py and owl_build_infer.py"
```

---

## Task 20: `interaction/` classifiers part A — `schedule_commit_classifier.py`, `schedule_commit_fulfiller.py`, `retry_intent_classifier.py`

**Files:**
- Modify: `src/stackowl/interaction/schedule_commit_classifier.py`, `src/stackowl/interaction/schedule_commit_fulfiller.py`, `src/stackowl/interaction/retry_intent_classifier.py`
- Test: `tests/interaction/` — find existing coverage for each, extend.

**Interfaces:**
- Consumes: `get_by_tier_and_model` (Task 5).

All three files share the IDENTICAL `_resolve_provider(self) -> ModelProvider | None` shape (confirmed in prior research). Apply the SAME transformation to each:

- [ ] **Step 1: Read all three files' `_resolve_provider` helper and its ONE caller in each file, in full** (confirmed locations: `schedule_commit_classifier.py`'s `commits_to_future_schedule`, `schedule_commit_fulfiller.py`'s `_extract`, `retry_intent_classifier.py`'s `classify` — re-read live files first).

- [ ] **Step 2: Write a failing test per file.**

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3: For EACH of the three files**, change `_resolve_provider(self) -> ModelProvider | None` to `_resolve_provider(self) -> tuple[ModelProvider, str] | None`:

```python
    def _resolve_provider(self) -> tuple[ModelProvider, str] | None:
        """Resolve the fast-tier (provider, model), or None on any registry error."""
        try:
            return self._registry.get_by_tier_and_model("fast")
        except Exception as exc:  # self-healing — missing provider must not raise
            log.engine.warning("<classname>._resolve_provider: get_by_tier failed", exc_info=exc)
            return None
```

(Keep each file's own existing log message text/logger namespace unchanged — only the return type and body change.)

Update each file's ONE caller: unpack the tuple right after the `if provider is None:` guard, and change the caller's `provider.complete(..., model="", ...)` to `model=model`. For example, `schedule_commit_classifier.py`'s `commits_to_future_schedule`:

```python
        resolved = self._resolve_provider()
        if resolved is None:
            log.engine.warning("schedule_commit_classifier.commits_to_future_schedule: no fast provider — fail-safe to none", extra={"_fields": {"commits": False}})
            return False
        provider, model = resolved
        try:
            user_text = self._build_user_text(response)
            async with traced_span(log.engine, "schedule_commit_classifier.commits_to_future_schedule.provider_call"):
                result = await asyncio.wait_for(
                    provider.complete(
                        [
                            Message(role="system", content=_SYSTEM_PROMPT),
                            Message(role="user", content=user_text),
                        ],
                        model=model,
                        max_tokens=_MAX_TOKENS,
                        disable_thinking=True,
                    ),
                    timeout=self._timeout_s,
                )
```

Apply the analogous change to `schedule_commit_fulfiller.py`'s `_extract` and `retry_intent_classifier.py`'s `classify`, using each file's own exact variable names and kwargs (from your Step 1 read — do not copy `schedule_commit_classifier.py`'s exact kwargs onto the other two files, each has its own `max_tokens`/`temperature`/prompt values).

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/interaction/schedule_commit_classifier.py src/stackowl/interaction/schedule_commit_fulfiller.py src/stackowl/interaction/retry_intent_classifier.py <test files>
git commit -m "feat(interaction): thread the resolved model through schedule-commit and retry-intent classifiers"
```

---

## Task 21: `interaction/` classifiers part B — `feedback_classifier.py`, `retrieval_intent_classifier.py`, `intent_classifier.py`

**Files:**
- Modify: `src/stackowl/interaction/feedback_classifier.py`, `src/stackowl/interaction/retrieval_intent_classifier.py`, `src/stackowl/interaction/intent_classifier.py`
- Test: `tests/interaction/` — find existing coverage for each, extend.

**Interfaces:**
- Consumes: `get_by_tier_and_model` (Task 5).

Same pattern as Task 20, applied to the remaining three classifier files.

- [ ] **Step 1: Read all three files' `_resolve_provider` helper and EVERY caller in each, in full.** **Important:** `intent_classifier.py`'s `_resolve_provider` is shared by MULTIPLE methods in the same class (`is_answer`, `is_steer`, `is_steer_incoherent` — confirmed in prior research; only `is_steer_incoherent` was directly read). Grep `_resolve_provider()` within this ONE file to find every call site before editing — do not assume only one caller.

- [ ] **Step 2: Write a failing test per file** — for `intent_classifier.py`, cover at least `is_steer_incoherent`; if `is_answer`/`is_steer` also call `_resolve_provider()`, add a test for at least one more of them to prove the fix isn't accidentally scoped to only one caller.

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3: Apply the identical `_resolve_provider(self) -> tuple[ModelProvider, str] | None` transformation to all three files**, updating EVERY caller found in Step 1 (not just the one shown below for `intent_classifier.py`).

For `feedback_classifier.py`, change:
```python
    def _resolve_provider(self) -> ModelProvider | None:
        """Resolve the fast-tier provider, or ``None`` on any registry error."""
        try:
            return self._registry.get_by_tier("fast")
        except Exception as exc:
            log.gateway.warning("feedback_classifier._resolve_provider: get_by_tier failed", exc_info=exc)
            return None
```
to:
```python
    def _resolve_provider(self) -> tuple[ModelProvider, str] | None:
        """Resolve the fast-tier (provider, model), or ``None`` on any registry error."""
        try:
            return self._registry.get_by_tier_and_model("fast")
        except Exception as exc:
            log.gateway.warning("feedback_classifier._resolve_provider: get_by_tier failed", exc_info=exc)
            return None
```
and its ONE caller (`classify`), from:
```python
        provider = self._resolve_provider()
        if provider is None:
            log.gateway.warning("feedback_classifier.classify: no fast provider — abstain", extra={"_fields": {"abstain": True}})
            return self._abstain("no_provider")

        try:
            user_text = self._build_user_text(user_message, last_agent_message, recent_context)
            async with traced_span(log.gateway, "feedback_classifier.classify.provider_call"):
                result = await asyncio.wait_for(
                    provider.complete(
                        [
                            Message(role="system", content=_SYSTEM_PROMPT),
                            Message(role="user", content=user_text),
                        ],
                        model="",
                        max_tokens=_MAX_TOKENS,
                        temperature=0.0,
                        disable_thinking=True,
                    ),
                    timeout=self._timeout_s,
                )
            raw = result.content or ""
```
to:
```python
        resolved = self._resolve_provider()
        if resolved is None:
            log.gateway.warning("feedback_classifier.classify: no fast provider — abstain", extra={"_fields": {"abstain": True}})
            return self._abstain("no_provider")
        provider, model = resolved

        try:
            user_text = self._build_user_text(user_message, last_agent_message, recent_context)
            async with traced_span(log.gateway, "feedback_classifier.classify.provider_call"):
                result = await asyncio.wait_for(
                    provider.complete(
                        [
                            Message(role="system", content=_SYSTEM_PROMPT),
                            Message(role="user", content=user_text),
                        ],
                        model=model,
                        max_tokens=_MAX_TOKENS,
                        temperature=0.0,
                        disable_thinking=True,
                    ),
                    timeout=self._timeout_s,
                )
            raw = result.content or ""
```

For `retrieval_intent_classifier.py`, apply the identical transformation: `_resolve_provider` → `get_by_tier_and_model`, unpack `provider, model = resolved` in `requires_lookup` right after the `if resolved is None:` guard (was `if provider is None:`), and change its `provider.complete([...], model="", max_tokens=_MAX_TOKENS, disable_thinking=True)` call to `model=model`.

For `intent_classifier.py`'s `is_steer_incoherent` (and every OTHER caller of `_resolve_provider()` you found in Step 1 within this same class — `is_answer`/`is_steer` per the docstring cross-reference), apply the identical transformation. `is_steer_incoherent` specifically, from:
```python
        provider = self._resolve_provider()
        if provider is None:
            log.gateway.warning("intent_classifier.is_steer_incoherent: no fast provider — fail-safe to veto", extra={"_fields": {"veto": True}})
            return True

        try:
            user_text = self._build_coherence_user_text(running_ask, message)
            async with traced_span(log.gateway, "intent_classifier.is_steer_incoherent.provider_call"):
                result = await asyncio.wait_for(
                    provider.complete(
                        [
                            Message(role="system", content=_COHERENCE_SYSTEM_PROMPT),
                            Message(role="user", content=user_text),
                        ],
                        model="",
                        max_tokens=4,
                        disable_thinking=True,
                    ),
                    timeout=self._timeout_s,
                )
            verdict = (result.content or "").strip()
```
to:
```python
        resolved = self._resolve_provider()
        if resolved is None:
            log.gateway.warning("intent_classifier.is_steer_incoherent: no fast provider — fail-safe to veto", extra={"_fields": {"veto": True}})
            return True
        provider, model = resolved

        try:
            user_text = self._build_coherence_user_text(running_ask, message)
            async with traced_span(log.gateway, "intent_classifier.is_steer_incoherent.provider_call"):
                result = await asyncio.wait_for(
                    provider.complete(
                        [
                            Message(role="system", content=_COHERENCE_SYSTEM_PROMPT),
                            Message(role="user", content=user_text),
                        ],
                        model=model,
                        max_tokens=4,
                        disable_thinking=True,
                    ),
                    timeout=self._timeout_s,
                )
            verdict = (result.content or "").strip()
```
Apply the same `resolved = self._resolve_provider()` / `if resolved is None:` / `provider, model = resolved` / `model=model` shape to `is_answer` and `is_steer` (and any other caller found in Step 1) — you have not been shown their exact bodies here; read them directly and apply this exact, already-demonstrated shape.

- [ ] **Step 4: Run to verify pass; run each file's full existing test suite; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/interaction/feedback_classifier.py src/stackowl/interaction/retrieval_intent_classifier.py src/stackowl/interaction/intent_classifier.py <test files>
git commit -m "feat(interaction): thread the resolved model through feedback/retrieval/intent classifiers"
```

---

## Task 22: `providers/llm_gateway.py`

**Files:**
- Modify: `src/stackowl/providers/llm_gateway.py`
- Test: `tests/providers/` — find existing coverage for `LLMGateway`, extend.

**Interfaces:**
- Consumes: `resolve_tier_with_fallback_and_model` (Task 5).

- [ ] **Step 1: Read `LLMGateway.complete` and `LLMGateway.complete_with_tools` in full** (confirmed in prior research, lines ~141-202 and ~227-310+ respectively — re-read the live file first, especially past line 310 which prior research did not capture in full).

- [ ] **Step 2: Write failing tests** — `complete()`: model reaches BOTH the main attempt and the same-tier retry `partial(provider.complete, ...)`. `complete_with_tools()`: **this is the one call site in the whole codebase that never passed `model=` at all** — the test must prove `provider.complete_with_tools(...)` now RECEIVES the resolved model, a genuinely new wire, not a rewire.

- [ ] **Step 2b: Run to verify they fail.**

- [ ] **Step 3a: `complete()`** — change `provider, degraded = self._registry.resolve_tier_with_fallback(tier)` to `provider, model, degraded = self._registry.resolve_tier_with_fallback_and_model(tier)`. Change both `result = await provider.complete(msgs, model="", **kwargs)` and `partial(provider.complete, msgs, model="", **kwargs)` to use `model=model`.

- [ ] **Step 3b: `complete_with_tools()`** — change `provider, degraded = self._registry.resolve_tier_with_fallback(tier)` to `provider, model, degraded = self._registry.resolve_tier_with_fallback_and_model(tier)`. Read `ModelProvider.complete_with_tools`'s actual signature in `src/stackowl/providers/base.py` before editing — confirm whether it accepts a `model` parameter at all (prior research flagged this as unconfirmed). If it does not, this is a NEW parameter to add to the ABC and every concrete implementation (`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`) — if that is the case, STOP and report NEEDS_CONTEXT rather than guessing at a wider ABC change; this task's scope is `llm_gateway.py` only. If `complete_with_tools` DOES already accept `model` (e.g. via `**kwargs`), add `model=model` to both the main call (`await provider.complete_with_tools(user_text=..., system_text=sys, tool_schemas=schemas, tool_dispatcher=tool_dispatcher, can_escalate=can_escalate, model=model, **attempt_kwargs)`) and its retry `partial(...)`.

- [ ] **Step 4: Run to verify pass; run the full existing test suite for this file; lint + types.**

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/llm_gateway.py <test files>
git commit -m "feat(providers): LLMGateway threads the resolved model through complete/complete_with_tools"
```

---

## Task 23: Final cleanup — remove temporary wrappers, rename `*_and_model` back to plain names

**Files:**
- Modify: `src/stackowl/providers/registry.py` and EVERY file touched by Tasks 8-22 (a purely mechanical identifier rename — no logic changes)
- Test: full regression (see Step 4)

**Interfaces:**
- Produces: `get_by_tier`, `get_with_cascade`, `resolve_tier_with_fallback`, `resolve_capable_or_degrade` now natively return the richer (provider, model[, degraded]) shape — the temporary `*_and_model` names and the temporary back-compat wrappers are gone.

- [ ] **Step 1: Confirm every call site has been migrated**

Run:
```bash
grep -rn "\.get_by_tier(\|\.get_with_cascade(\|\.resolve_tier_with_fallback(\|\.resolve_capable_or_degrade(" src/stackowl/ | grep -v __pycache__
```
Expected: the ONLY matches remaining are inside `src/stackowl/providers/registry.py` itself (the temporary wrapper method bodies defined in Task 5 calling their own `*_and_model` siblings). If ANY other file still shows a match, STOP — a call site was missed in Tasks 8-22; go fix that file's task retroactively (re-open that task's file, apply its established migration pattern) before proceeding with this task.

- [ ] **Step 2: Remove the temporary wrapper methods, rename `*_and_model` to the plain names**

In `src/stackowl/providers/registry.py`, delete the four thin wrapper methods added in Task 5 (`get_by_tier`, `get_with_cascade`, `resolve_tier_with_fallback`, `resolve_capable_or_degrade` — the ones with the `"""Back-compat wrapper — TEMPORARY..."""` docstrings). Rename `get_by_tier_and_model` → `get_by_tier`, `get_with_cascade_and_model` → `get_with_cascade`, `resolve_tier_with_fallback_and_model` → `resolve_tier_with_fallback`, `resolve_capable_or_degrade_and_model` → `resolve_capable_or_degrade` (method bodies unchanged, name only).

- [ ] **Step 3: Mechanically rename every call site across the codebase**

Run (review the diff before committing — this is a plain identifier rename, but review it anyway per this repo's minimal-diff discipline):
```bash
grep -rl "_and_model(" src/stackowl/ | grep -v __pycache__ | xargs sed -i \
  -e 's/get_by_tier_and_model(/get_by_tier(/g' \
  -e 's/get_with_cascade_and_model(/get_with_cascade(/g' \
  -e 's/resolve_tier_with_fallback_and_model(/resolve_tier_with_fallback(/g' \
  -e 's/resolve_capable_or_degrade_and_model(/resolve_capable_or_degrade(/g'
```
Do the SAME across `tests/`:
```bash
grep -rl "_and_model(" tests/ | grep -v __pycache__ | xargs sed -i \
  -e 's/get_by_tier_and_model(/get_by_tier(/g' \
  -e 's/get_with_cascade_and_model(/get_with_cascade(/g' \
  -e 's/resolve_tier_with_fallback_and_model(/resolve_tier_with_fallback(/g' \
  -e 's/resolve_capable_or_degrade_and_model(/resolve_capable_or_degrade(/g'
```
Re-run Step 1's grep to confirm zero remaining `_and_model` occurrences anywhere in `src/` or `tests/`:
```bash
grep -rln "_and_model" src/stackowl/ tests/ | grep -v __pycache__
```
Expected: no output.

- [ ] **Step 4: Full scoped regression pass**

Run (this is the widest single test command in the whole plan — every file touched by Tasks 1-22 — but still scoped, never a bare repo-wide `pytest`):
```bash
uv run pytest \
  tests/config/test_provider.py \
  tests/providers/ \
  tests/pipeline/ \
  tests/owls/ \
  tests/memory/ \
  tests/skills/ \
  tests/tools/agents/test_delegate_task*.py \
  tests/tools/browser/ \
  tests/tools/meta/ \
  tests/interaction/ \
  tests/objectives/ \
  tests/parliament/ \
  -q
```
Expected: 100% pass, zero regressions. If this command's total runtime is impractically long (this repo has documented full-suite hang issues per `feedback_test_run_discipline`), split it into 2-3 sequential invocations by package rather than removing coverage.

Run: `uv run ruff check src/stackowl/`
Run: `uv run mypy src/stackowl/` (this is the first mypy run across the WHOLE `src/` tree in this plan — expect it to surface the pre-existing, unrelated findings this session has repeatedly confirmed elsewhere in this codebase; confirm any NEW finding is genuinely introduced by this plan's changes before treating it as this task's responsibility, via `git stash` + re-run comparison, same technique used throughout this session)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(providers): drop temporary *_and_model names — get_by_tier/get_with_cascade/etc. natively return (provider, model)"
```

---

## Task 24: `/provider` model-management commands

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Test: `tests/commands/test_provider_command.py` (existing — extend)

**Interfaces:**
- Consumes: `ModelOverride`, `ProviderConfig.models` (Task 1).
- Produces: `/provider models <name>`, `/provider add-model` (guided), `/provider remove-model <name> <model_name>`, `/provider set-model-tokens <name> <model_name> <value>`, `/provider set-model-context <name> <model_name> <value>`.

- [ ] **Step 1: Read `src/stackowl/commands/provider_command.py` in full** — you have prior research confirming its `_PROVIDER_META`, `handle()` dispatch, `_add_browse`/`_add_execute`/`_add_tier` guided-flow methods, `_menu`, `_set_tier` — re-read the live file (it may have changed since this session's earlier multi-tier work) before writing new code that must match its established style exactly.

- [ ] **Step 2: Write the failing tests**

Add to `tests/commands/test_provider_command.py`, following its own established `tmp_yaml` fixture + `_make_cmd()`/`_load()` helper pattern (confirmed in prior research):

```python
class TestProviderModels:
    @pytest.mark.asyncio
    async def test_models_lists_default_and_extra_models(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [{
            "name": "acme", "protocol": "openai", "default_model": "acme-v1",
            "tiers": ["fast"], "enabled": True,
            "models": [{"name": "acme-v1-mini", "tiers": ["standard"]}],
        }]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("models acme", _state())
        text = out.text if hasattr(out, "text") else out
        assert "acme-v1" in text
        assert "acme-v1-mini" in text

    @pytest.mark.asyncio
    async def test_add_model_browse_shows_provider_buttons_when_no_name_given(
        self, tmp_yaml: Path
    ) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [{
            "name": "acme", "protocol": "openai", "default_model": "acme-v1",
            "tiers": ["fast"], "enabled": True,
        }]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("add-model", _state())
        assert hasattr(out, "actions")
        assert any("acme" in a.command for a in out.actions)

    @pytest.mark.asyncio
    async def test_add_model_execute_writes_new_models_entry(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [{
            "name": "acme", "protocol": "openai", "default_model": "acme-v1",
            "tiers": ["fast"], "enabled": True,
        }]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("add-model-execute acme acme-v1-mini standard", _state())
        text = out.text if hasattr(out, "text") else out
        assert "✓" in text
        persisted = _load(tmp_yaml)
        acme = next(p for p in persisted["providers"] if p["name"] == "acme")
        assert acme["models"] == [{"name": "acme-v1-mini", "tiers": ["standard"]}]

    @pytest.mark.asyncio
    async def test_add_model_rejects_name_colliding_with_default_model(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [{
            "name": "acme", "protocol": "openai", "default_model": "acme-v1",
            "tiers": ["fast"], "enabled": True,
        }]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("add-model-execute acme acme-v1 standard", _state())
        text = out.text if hasattr(out, "text") else out
        assert "✗" in text

    @pytest.mark.asyncio
    async def test_remove_model_deletes_the_entry(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [{
            "name": "acme", "protocol": "openai", "default_model": "acme-v1",
            "tiers": ["fast"], "enabled": True,
            "models": [{"name": "acme-v1-mini", "tiers": ["standard"]}],
        }]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("remove-model acme acme-v1-mini", _state())
        text = out.text if hasattr(out, "text") else out
        assert "✓" in text
        persisted = _load(tmp_yaml)
        acme = next(p for p in persisted["providers"] if p["name"] == "acme")
        assert acme.get("models", []) == []

    @pytest.mark.asyncio
    async def test_set_model_tokens_sets_override(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [{
            "name": "acme", "protocol": "openai", "default_model": "acme-v1",
            "tiers": ["fast"], "enabled": True,
            "models": [{"name": "acme-v1-mini", "tiers": ["standard"]}],
        }]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("set-model-tokens acme acme-v1-mini 50000", _state())
        text = out.text if hasattr(out, "text") else out
        assert "✓" in text
        persisted = _load(tmp_yaml)
        acme = next(p for p in persisted["providers"] if p["name"] == "acme")
        assert acme["models"][0]["max_output_tokens"] == 50000

    @pytest.mark.asyncio
    async def test_set_model_tokens_inherit_clears_override(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [{
            "name": "acme", "protocol": "openai", "default_model": "acme-v1",
            "tiers": ["fast"], "enabled": True,
            "models": [{"name": "acme-v1-mini", "tiers": ["standard"], "max_output_tokens": 50000}],
        }]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("set-model-tokens acme acme-v1-mini inherit", _state())
        text = out.text if hasattr(out, "text") else out
        assert "✓" in text
        persisted = _load(tmp_yaml)
        acme = next(p for p in persisted["providers"] if p["name"] == "acme")
        assert "max_output_tokens" not in acme["models"][0]
```

(This plan does not know this test file's exact `_state()` helper — use whatever this file already defines, per your Step 1 read; do not invent a new one.)

- [ ] **Step 2b: Run to verify they fail** (unknown subcommand → usage text, not the expected behavior).

- [ ] **Step 3: Implement the five subcommands**

Add to `_PROVIDER_META`'s `subcommands` tuple (matching the existing `SubCommand`/`Arg`/`Example` shape used by every other entry in this tuple — read at least 2 existing entries from your Step 1 read before writing these 5):

```python
        SubCommand(
            name="models",
            summary="List a provider's models (default + extras) and their tiers/overrides",
            description="You see every model a provider serves — its own default_model plus any models[] entries — with each one's tiers and any context/output-token overrides (inherited values marked as such).",
            args=(Arg(name="name", summary="provider name"),),
            examples=(Example(invocation="/provider models acme"),),
        ),
        SubCommand(
            name="add-model",
            summary="Add an additional model to an existing provider (guided)",
            description="You add a second (or third...) model under a provider's existing connection — same api_key/base_url, a new model name and starting tier — without duplicating the whole provider block. Guided: pick the provider, then the model name, then the tier.",
            args=(Arg(name="name", summary="provider name", required=False),),
            examples=(Example(invocation="/provider add-model"),),
        ),
        SubCommand(
            name="remove-model",
            summary="Remove an additional model from a provider",
            description="You remove one models[] entry. The provider's own default_model is not removable this way — edit it via /provider edit-menu instead.",
            args=(
                Arg(name="name", summary="provider name"),
                Arg(name="model_name", summary="the model to remove"),
            ),
            examples=(Example(invocation="/provider remove-model acme acme-v1-mini"),),
        ),
        SubCommand(
            name="set-model-tokens",
            summary="Override (or clear) one model's max_output_tokens",
            description="You set a per-model output-token ceiling, or pass 'inherit' to clear the override back to the provider's own value.",
            args=(
                Arg(name="name", summary="provider name"),
                Arg(name="model_name", summary="the model to configure"),
                Arg(name="value", summary="an integer, or 'inherit'"),
            ),
            examples=(Example(invocation="/provider set-model-tokens acme acme-v1-mini 50000"),),
        ),
        SubCommand(
            name="set-model-context",
            summary="Override (or clear) one model's context_chars",
            description="Same as set-model-tokens, for the context_chars field.",
            args=(
                Arg(name="name", summary="provider name"),
                Arg(name="model_name", summary="the model to configure"),
                Arg(name="value", summary="an integer, or 'inherit'"),
            ),
            examples=(Example(invocation="/provider set-model-context acme acme-v1-mini 80000"),),
        ),
```

Add dispatch routing in `handle()`'s subcommand chain (matching the existing `if sub == "...": result = self._X(rest)` chain style):

```python
            elif sub == "models":
                result = self._models(rest)
            elif sub == "add-model":
                result = self._add_model_browse(rest)
            elif sub == "add-model-execute":
                result = self._add_model_execute(rest)
            elif sub == "remove-model":
                result = self._remove_model(rest)
            elif sub == "set-model-tokens":
                result = self._set_model_field(rest, field="max_output_tokens")
            elif sub == "set-model-context":
                result = self._set_model_field(rest, field="context_chars")
```

(`add-model-execute` is the terminal step of the guided flow — the button `/provider add-model-execute <name> <model_name> <tier>` a tier-pick button emits, mirroring `_add_tier`'s existing terminal-step naming convention from your Step 1 read; add it to `_PROVIDER_META` too, `hidden`/undocumented if this codebase's `SubCommand` supports that flag — check your Step 1 read for how `_add_tier`'s own equivalent terminal step is declared, and mirror it exactly.)

Implement the five methods, following `_menu`/`_set_tier`/`_add_browse`/`_add_execute`/`_add_tier`'s established style from your Step 1 read (config_path/load_yaml/save_yaml, `self._emit_reloaded(name)` after a write, `CommandResponse` with `Action` buttons for the guided steps, plain `str` for terminal confirmations):

```python
    def _models(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.models: entry", extra={"_fields": {"raw_len": len(raw)}})
        name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not name:
            return "Usage: /provider models <name>"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        target = next((p for p in self._providers(data) if p.get("name") == name), None)
        if target is None:
            return f"✗ Provider '{name}' not found"
        lines = [f"{name}:"]
        default_model = target.get("default_model", "?")
        lines.append(f"  {default_model} (default) | tiers: {', '.join(target.get('tiers') or [])}")
        for m in target.get("models") or []:
            mt = m.get("max_output_tokens")
            mc = m.get("context_chars")
            override_bits = []
            override_bits.append(f"max_output_tokens={mt}" if mt is not None else "max_output_tokens=(inherited)")
            override_bits.append(f"context_chars={mc}" if mc is not None else "context_chars=(inherited)")
            lines.append(
                f"  {m.get('name', '?')} | tiers: {', '.join(m.get('tiers') or [])} | {' '.join(override_bits)}"
            )
        log.config.debug("[commands] provider.models: exit", extra={"_fields": {"name": name, "n_models": len(target.get('models') or [])}})
        return "\n".join(lines)

    def _add_model_browse(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.add_model_browse: entry", extra={"_fields": {"raw_len": len(raw)}})
        name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        if not name:
            if not providers:
                return "No providers configured — add one first with /provider add."
            actions = tuple(
                Action(label=p.get("name", "?"), command=f"/provider add-model {p.get('name', '?')}", destructive=False)
                for p in providers
            )
            return CommandResponse(text="Add a model to which provider?", actions=actions)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            return f"✗ Provider '{name}' not found"
        # Next: the user types the new model's name as free text, then this
        # same subcommand re-invokes with a second token present — mirror
        # whatever two-step (name-only vs name+model) pattern _add_tier
        # already uses for its own guided flow (see your Step 1 read); if
        # _add_tier instead expects the model name inline with a prompt hint,
        # match that shape exactly instead of inventing this one.
        return f"Provider '{name}' — reply with the new model's name (e.g. `/provider add-model {name} <model_name>`)."

    def _add_model_execute(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.add_model_execute: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split()
        if len(bits) < 2:
            return "Usage: /provider add-model-execute <name> <model_name> [tier]"
        name, model_name = bits[0], bits[1]
        if len(bits) < 3:
            # Guided tier-pick step, mirroring _add_tier's button row.
            actions = tuple(
                Action(label=t, command=f"/provider add-model-execute {name} {model_name} {t}", destructive=False)
                for t in _VALID_TIERS
            )
            return CommandResponse(text=f"Add '{model_name}' to which tier?", actions=actions)
        tier = bits[2]
        if tier not in _VALID_TIERS:
            return f"✗ Invalid tier '{tier}' — valid: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            return f"✗ Provider '{name}' not found"
        if model_name == target.get("default_model"):
            return f"✗ '{model_name}' is already this provider's default_model"
        existing_models = target.get("models") or []
        if any(m.get("name") == model_name for m in existing_models):
            return f"✗ Model '{model_name}' already exists under '{name}'"
        target["models"] = [*existing_models, {"name": model_name, "tiers": [tier]}]
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.add_model_execute: exit",
            extra={"_fields": {"name": name, "model_name": model_name, "tier": tier}},
        )
        return f"✓ Model '{model_name}' added to provider '{name}' in tier '{tier}' — applied immediately"

    def _remove_model(self, raw: str) -> str:
        log.config.debug("[commands] provider.remove_model: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split()
        if len(bits) < 2:
            return "Usage: /provider remove-model <name> <model_name>"
        name, model_name = bits[0], bits[1]
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            return f"✗ Provider '{name}' not found"
        existing_models = target.get("models") or []
        if not any(m.get("name") == model_name for m in existing_models):
            return f"✗ Model '{model_name}' not found under '{name}'"
        target["models"] = [m for m in existing_models if m.get("name") != model_name]
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info("[commands] provider.remove_model: exit", extra={"_fields": {"name": name, "model_name": model_name}})
        return f"✓ Model '{model_name}' removed from provider '{name}' — applied immediately"

    def _set_model_field(self, raw: str, *, field: str) -> str:
        log.config.debug(
            "[commands] provider.set_model_field: entry",
            extra={"_fields": {"raw_len": len(raw), "field": field}},
        )
        bits = raw.split()
        if len(bits) < 3:
            return f"Usage: /provider set-model-{'tokens' if field == 'max_output_tokens' else 'context'} <name> <model_name> <value|inherit>"
        name, model_name, value_raw = bits[0], bits[1], bits[2]
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            return f"✗ Provider '{name}' not found"
        existing_models = target.get("models") or []
        model_entry = next((m for m in existing_models if m.get("name") == model_name), None)
        if model_entry is None:
            return f"✗ Model '{model_name}' not found under '{name}'"
        if value_raw.strip().lower() == "inherit":
            model_entry.pop(field, None)
        else:
            try:
                model_entry[field] = int(value_raw)
            except ValueError:
                return f"✗ '{value_raw}' is not a valid integer (or 'inherit')"
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.set_model_field: exit",
            extra={"_fields": {"name": name, "model_name": model_name, "field": field, "value": value_raw}},
        )
        return f"✓ '{model_name}' {field} set to {value_raw} — applied immediately"
```

- [ ] **Step 4: Run to verify pass; run the full existing test file; lint + types.**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Run: `uv run ruff check src/stackowl/commands/provider_command.py`
Run: `uv run mypy src/stackowl/commands/provider_command.py`

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): /provider model-management subcommands (models/add-model/remove-model/set-model-*)"
```

---

## Task 25: `/tier add`/`/tier remove` — optional `model_name` argument

**Files:**
- Modify: `src/stackowl/commands/tier_command.py`
- Test: `tests/commands/test_tier_admin.py` (existing — extend)

**Interfaces:**
- Consumes: `ProviderConfig.models` (Task 1).
- Produces: `/tier add <tier> <provider>` (2-arg, unchanged) still works byte-identically; `/tier add <tier> <provider> <model_name>` (new 3-arg) operates on a specific `models[]` entry instead of the provider's own `default_model`/`tiers`. Same split for `/tier remove`.

- [ ] **Step 1: Read `src/stackowl/commands/tier_command.py`'s `_add_execute` and `_remove_execute` methods in full** (both already exist and were built earlier this session for the multi-tier-per-provider feature — re-read the live file; do not work from the multi-tier-plan's earlier summary, this file has since been touched).

- [ ] **Step 2: Write the failing tests**

Add to `tests/commands/test_tier_admin.py`, matching its own established `tmp_yaml` fixture/`_make_cmd()` pattern:

```python
class TestTierAddRemoveModelAware:
    @pytest.mark.asyncio
    async def test_add_two_arg_unchanged_operates_on_default_model(self, tmp_yaml: Path) -> None:
        """Byte-compat: the existing 2-arg form still edits the provider's OWN
        tiers, never touching models[]."""
        reply = await _make_cmd().handle("add powerful groq", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        assert "powerful" in groq["tiers"]
        assert not groq.get("models")

    @pytest.mark.asyncio
    async def test_add_three_arg_targets_a_specific_model(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        groq["models"] = [{"name": "groq-mini", "tiers": ["fast"]}]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")

        reply = await _make_cmd().handle("add powerful groq groq-mini", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        persisted = _load(tmp_yaml)
        groq2 = next(p for p in persisted["providers"] if p["name"] == "groq")
        model_entry = next(m for m in groq2["models"] if m["name"] == "groq-mini")
        assert "powerful" in model_entry["tiers"]
        assert "powerful" not in groq2["tiers"]  # provider's own tiers untouched

    @pytest.mark.asyncio
    async def test_remove_three_arg_on_models_only_tier_disables_that_model_entry(
        self, tmp_yaml: Path
    ) -> None:
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        groq["models"] = [{"name": "groq-mini", "tiers": ["standard"]}]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")

        reply = await _make_cmd().handle("remove standard groq groq-mini", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        persisted = _load(tmp_yaml)
        groq2 = next(p for p in persisted["providers"] if p["name"] == "groq")
        model_entry = next(m for m in groq2["models"] if m["name"] == "groq-mini")
        assert model_entry.get("enabled") is False
        assert model_entry["tiers"] == ["standard"]  # preserved, per the provider-level convention

    @pytest.mark.asyncio
    async def test_add_three_arg_model_not_found_rejected(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add powerful groq ghost-model", _state())
        assert isinstance(reply, str)
        assert "✗" in reply
```

- [ ] **Step 2b: Run to verify they fail** (the 3-arg form currently falls into whatever error path `_add_execute`/`_remove_execute` take for "too many arguments" today — read your Step 1 output to know exactly what that is before writing the assertion above, adjust if needed).

- [ ] **Step 3: Extend `_add_execute` and `_remove_execute` for the 3-arg form**

Read the CURRENT exact body of both methods from your Step 1 read (they may not match the exact code shown elsewhere in this plan's other tasks, since this file was last touched independently). Add a branch at the top of each: if `len(bits) >= 3`, look up `bits[2]` inside `target.get("models") or []` instead of operating on `target` directly — apply the EXACT SAME add/remove logic (additive list-append for add, subtract-or-disable for remove) but scoped to the matched `ModelOverride` dict instead of the provider dict itself. If `len(bits) < 3` (today's 2-arg form), fall through to the EXISTING unmodified logic — do not restructure the 2-arg path at all, only ADD a new 3-arg branch above it.

For the "model not found" case, return `f"✗ Model '{model_name}' not found under provider '{name}'"` (mirroring this file's existing "provider not found" message style from your Step 1 read).

For the model-disable-on-last-tier-removal case (Step 2's third test), mirror the EXACT provider-level convention already implemented in `_remove_execute` for the provider case (`enabled = False`, tiers left untouched) — apply it to the model dict instead of the provider dict.

- [ ] **Step 4: Update `_TIER_META`'s `add`/`remove` `SubCommand` descriptions** to mention the optional 3rd `model_name` argument (read the current descriptions from your Step 1 read before editing so the wording stays consistent with the rest of the file).

- [ ] **Step 5: Run to verify pass; run the full existing test file; lint + types.**

Run: `uv run pytest tests/commands/test_tier_admin.py -v`
Run: `uv run ruff check src/stackowl/commands/tier_command.py`
Run: `uv run mypy src/stackowl/commands/tier_command.py`

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/tier_command.py tests/commands/test_tier_admin.py
git commit -m "feat(commands): /tier add|remove accept an optional model_name for per-model tier membership"
```

---

## Task 26: End-to-end integration test

**Files:**
- Modify: `tests/journeys/commands/test_provider_command_journey.py` (add one new test, following this file's existing conventions from earlier this session's multi-tier work)

**Interfaces:**
- Consumes: everything from Tasks 1-25.

- [ ] **Step 1: Read the existing file's fixtures/imports/conventions in full** before writing (established earlier this session — `tmp_yaml`/`register_all_commands`/`CommandRegistry`/`CommandDeps`/`make_state`).

- [ ] **Step 2: Write the new test**

```python
async def test_provider_add_model_then_route_from_both_models_independently(
    tmp_yaml: Path,
) -> None:
    """End-to-end proof of the whole plan's core new capability: ONE provider
    connection serving TWO models, each independently tier-routable, each with
    its own max_output_tokens override — no duplicated provider block."""
    from stackowl.config.settings import Settings
    from stackowl.providers.registry import ProviderRegistry

    deps = CommandDeps(event_bus=MagicMock())
    register_all_commands(deps, registry=CommandRegistry.instance())
    registry = CommandRegistry.instance()

    add_reply = await registry.dispatch(
        "provider", "add multiprov openai gpt-x fast", make_state()
    )
    assert "✓" in add_reply.text

    add_model_reply = await registry.dispatch(
        "provider", "add-model-execute multiprov gpt-x-mini standard", make_state()
    )
    assert "✓" in add_model_reply.text

    set_tokens_reply = await registry.dispatch(
        "provider", "set-model-tokens multiprov gpt-x-mini 40000", make_state()
    )
    assert "✓" in set_tokens_reply.text

    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    persisted = next(p for p in data["providers"] if p["name"] == "multiprov")
    assert persisted["default_model"] == "gpt-x"
    assert persisted["models"] == [
        {"name": "gpt-x-mini", "tiers": ["standard"], "max_output_tokens": 40000}
    ]

    live_registry = ProviderRegistry.from_settings(Settings())
    fast_provider, fast_model = live_registry.get_with_cascade("fast")
    standard_provider, standard_model = live_registry.get_with_cascade("standard")
    assert fast_provider.name == "multiprov"
    assert fast_model == "gpt-x"
    assert standard_provider.name == "multiprov"
    assert standard_model == "gpt-x-mini"
    assert fast_provider is standard_provider  # SAME connection, both models
```

(Adjust the exact `/provider add` invocation shape and `make_state()`/`CommandDeps` construction to match whatever this file's OTHER tests in the same session already use — do not invent a different shape.)

- [ ] **Step 3: Run to verify it fails, then implement any gap it surfaces**

Run: `uv run pytest tests/journeys/commands/test_provider_command_journey.py -v -k test_provider_add_model_then_route`

If it fails for a reason NOT explained by "this exact test doesn't exist yet" — investigate the specific gap (a missed wiring point between two of Tasks 1-25) and fix it with a correctly-scoped follow-up commit in the file that actually owns the gap. Do not loosen the test.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/journeys/commands/test_provider_command_journey.py -v`
Expected: PASS, the whole file.

- [ ] **Step 5: Commit**

```bash
git add tests/journeys/commands/test_provider_command_journey.py
git commit -m "test: end-to-end proof — one provider, two models, independently tier-routed with independent overrides"
```

---

## Task 27: Final full regression pass

**Files:**
- None modified — verification only.

- [ ] **Step 1: Run every test file this plan touched, in scoped groups (never one bare `pytest` invocation)**

```bash
uv run pytest tests/config/ -q
uv run pytest tests/providers/ -q
uv run pytest tests/pipeline/ -q
uv run pytest tests/owls/ -q
uv run pytest tests/memory/ -q
uv run pytest tests/skills/ -q
uv run pytest tests/tools/ -q
uv run pytest tests/interaction/ -q
uv run pytest tests/objectives/ -q
uv run pytest tests/parliament/ -q
uv run pytest tests/commands/test_provider_command.py tests/commands/test_tier_admin.py -q
uv run pytest tests/journeys/commands/ -q
```
Expected: 100% pass across every group. If any group's runtime is impractically long, split it further by subdirectory rather than skipping coverage.

- [ ] **Step 2: Lint + type-check every touched file**

```bash
uv run ruff check src/stackowl/config/provider.py src/stackowl/providers/ src/stackowl/pipeline/ src/stackowl/owls/ src/stackowl/memory/ src/stackowl/skills/ src/stackowl/tools/agents/delegate_task.py src/stackowl/tools/browser/browse.py src/stackowl/tools/meta/owl_build_infer.py src/stackowl/interaction/ src/stackowl/objectives/ src/stackowl/parliament/ src/stackowl/commands/provider_command.py src/stackowl/commands/tier_command.py
uv run mypy src/stackowl/config/provider.py src/stackowl/providers/ src/stackowl/pipeline/ src/stackowl/owls/ src/stackowl/memory/ src/stackowl/skills/ src/stackowl/tools/agents/delegate_task.py src/stackowl/tools/browser/browse.py src/stackowl/tools/meta/owl_build_infer.py src/stackowl/interaction/ src/stackowl/objectives/ src/stackowl/parliament/ src/stackowl/commands/provider_command.py src/stackowl/commands/tier_command.py
```
Expected: no errors NEWLY introduced by this plan (this repo has confirmed, pre-existing, unrelated mypy findings in several of these packages — cross-check any finding against a `git stash` + re-run on the pre-plan commit before treating it as this plan's responsibility, per this session's established technique).

- [ ] **Step 3: Grep sweep — confirm zero remaining `_and_model` names and zero remaining OLD-signature callers**

```bash
grep -rn "_and_model" src/stackowl/ tests/ | grep -v __pycache__
```
Expected: no output (Task 23 already confirmed this — re-confirm here as the plan's final gate, since intervening tasks 24-26 touched files that could theoretically have reintroduced a stale reference).

- [ ] **Step 4: Manual smoke check of the live platform (if running)**

If a StackOwl instance is currently running, restart it and manually exercise, via Telegram or the TUI: `/provider add` (a brand-new provider), `/provider add-model` (guided flow end to end), `/provider models <name>`, `/provider set-model-tokens`, `/tier add <tier> <provider> <model_name>` (3-arg form), `/tier remove <tier> <provider> <model_name>`, and confirm a turn actually routed through the SECOND model (check `~/.stackowl/logs/stackowl.jsonl` for the `[cascade] selected` log line showing the right provider, and the eventual `provider.complete`/`.stream` request actually carrying the second model's name — this is a real schema/routing change touching the tier-selection core, not just internal refactoring).

- [ ] **Step 5: No commit needed for this task** — it's pure verification. If any step surfaced a real gap, fix it as a small follow-up commit in the task that should have covered it, clearly noting what was missed (never amend a prior task's commit).
