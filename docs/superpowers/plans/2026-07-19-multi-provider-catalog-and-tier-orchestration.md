# Multi-Provider Catalog & Tier Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Progress tracking:** a companion file, `docs/superpowers/plans/2026-07-19-multi-provider-catalog-and-tier-orchestration-progress.md`, tracks phase/task status at a glance. Update its checkbox and status line the moment a task's steps are ALL checked off and committed — do not batch updates.

**Goal:** Let a user attach many providers (including free-tier ones) to a single routing tier via a guided, catalog-driven add-flow with full lifecycle UX on Telegram and TUI, with the engine round-robining across healthy providers in a tier and backing off cleanly (quota-aware) when one is unavailable.

**Architecture:** Extend the existing `stackowl.setup.provider_catalog.ProviderCatalog` (already used by `stackowl setup --minimal`) with search/browse; add a small `ModelDiscovery` module that live-queries a provider's real models (doubling as token validation); add a lock-free `TierSelector` that `ProviderRegistry.get_with_cascade` delegates round-robin selection to; extend `CircuitBreaker` with an explicit-duration `open_for()` used for quota-aware cooldown; extend `/provider` with a guided add-flow and richer lifecycle/status subcommands, all via the existing `CommandResponse`/`Action` button convention (no new session/wizard state).

**Tech Stack:** Python 3.13, pydantic, ruamel.yaml, pytest/pytest-asyncio, openai/anthropic/google-genai SDKs (already vendored), python-telegram-bot.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-multi-provider-catalog-and-tier-orchestration-design.md` — every requirement here traces to a section there.
- No behavior change for a single-provider-per-tier config (existing tests for `get_with_cascade`/`get_by_tier` must keep passing unmodified).
- No behavior change for a provider that sets neither `cooldown_hours` nor triggers a parseable reset signal.
- Never log a raw token/secret or a full command string that might carry one — only lengths, refs, or provider/catalog names.
- 4-point logging (entry/decision/step/exit) on every new method that does real work, matching this codebase's existing convention (see any method already read in `providers/registry.py`/`circuit_breaker.py`).
- `ruff check src/` and `mypy src/` (strict) must stay green after every task.
- Existing positional `/provider add <name> <protocol> <model> <tier> ...` must keep working unchanged.

---

## Phase 1 — Catalog extension

### Task 1: `ProviderEntry.category` + `ProviderCatalog.search`/`browse`

**Files:**
- Modify: `src/stackowl/setup/provider_catalog.py`
- Test: `tests/setup/test_provider_catalog.py`

**Interfaces:**
- Produces: `ProviderEntry.category: tuple[str, ...]` (default `()`), `ProviderCatalog.search(query: str) -> list[ProviderEntry]`, `ProviderCatalog.browse(category: str | None = None) -> list[ProviderEntry]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/setup/test_provider_catalog.py`:

```python
def test_provider_entry_category_defaults_empty() -> None:
    entry = ProviderEntry(
        name="x", label="X", protocol="openai",
        base_url="https://x.example.com/v1", default_model="m",
    )
    assert entry.category == ()


def test_search_matches_name_label_or_category(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.setup import provider_catalog as mod

    fake = [
        ProviderEntry(
            name="groq", label="Groq", protocol="openai",
            base_url="https://api.groq.com/openai/v1", default_model="llama-3.3-70b-versatile",
            category=("free-tier", "fast-inference"),
        ),
        ProviderEntry(
            name="openai", label="OpenAI", protocol="openai",
            base_url="https://api.openai.com/v1", default_model="gpt-4o",
        ),
    ]
    monkeypatch.setattr(mod.ProviderCatalog, "load", classmethod(lambda cls: fake))

    assert [e.name for e in mod.ProviderCatalog.search("groq")] == ["groq"]
    assert [e.name for e in mod.ProviderCatalog.search("free")] == ["groq"]
    assert [e.name for e in mod.ProviderCatalog.search("GROQ")] == ["groq"]
    assert mod.ProviderCatalog.search("nonexistent-xyz") == []


def test_browse_filters_by_category(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.setup import provider_catalog as mod

    fake = [
        ProviderEntry(
            name="groq", label="Groq", protocol="openai",
            base_url="https://api.groq.com/openai/v1", default_model="llama-3.3-70b-versatile",
            category=("free-tier",),
        ),
        ProviderEntry(
            name="openai", label="OpenAI", protocol="openai",
            base_url="https://api.openai.com/v1", default_model="gpt-4o",
        ),
    ]
    monkeypatch.setattr(mod.ProviderCatalog, "load", classmethod(lambda cls: fake))

    assert [e.name for e in mod.ProviderCatalog.browse("free-tier")] == ["groq"]
    assert len(mod.ProviderCatalog.browse(None)) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/setup/test_provider_catalog.py -v -k "category or search or browse"`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'category'` / `AttributeError: search`/`browse`.

- [ ] **Step 3: Implement**

In `src/stackowl/setup/provider_catalog.py`, add the field to `ProviderEntry` (after `key_url`):

```python
    key_url: str | None = None
    # NEW — optional tags for browse/search filtering (add/tier UX). Empty
    # default means every existing bundled YAML file parses unchanged.
    category: tuple[str, ...] = field(default_factory=tuple)
```

Update `__post_init__` to coerce it like the other tuple fields:

```python
    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ValueError(
                f"ProviderEntry '{self.name}': unknown protocol '{self.protocol}' "
                f"— must be one of {PROTOCOLS}"
            )
        # Coerce list → tuple so the dataclass stays frozen/hashable
        object.__setattr__(self, "models", tuple(self.models))
        object.__setattr__(self, "vision_models", tuple(self.vision_models))
        object.__setattr__(self, "category", tuple(self.category))
```

Add two classmethods to `ProviderCatalog`, right after `load`:

```python
    @classmethod
    def search(cls, query: str) -> list[ProviderEntry]:
        """Case-insensitive substring match against name/label/category."""
        log.setup.debug("[provider_catalog] ProviderCatalog.search: entry", extra={"_fields": {"query_len": len(query)}})
        needle = query.strip().casefold()
        if not needle:
            return cls.load()
        result = [
            e for e in cls.load()
            if needle in e.name.casefold()
            or needle in e.label.casefold()
            or any(needle in c.casefold() for c in e.category)
        ]
        log.setup.debug("[provider_catalog] ProviderCatalog.search: exit", extra={"_fields": {"matches": len(result)}})
        return result

    @classmethod
    def browse(cls, category: str | None = None) -> list[ProviderEntry]:
        """Return the catalog, optionally filtered to one category tag."""
        log.setup.debug("[provider_catalog] ProviderCatalog.browse: entry", extra={"_fields": {"category": category}})
        entries = cls.load()
        if category is None:
            return entries
        needle = category.casefold()
        result = [e for e in entries if any(c.casefold() == needle for c in e.category)]
        log.setup.debug("[provider_catalog] ProviderCatalog.browse: exit", extra={"_fields": {"matches": len(result)}})
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/setup/test_provider_catalog.py -v`
Expected: PASS (all, including the 5 pre-existing tests — unmodified).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/setup/provider_catalog.py && uv run mypy src/stackowl/setup/provider_catalog.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/setup/provider_catalog.py tests/setup/test_provider_catalog.py
git commit -m "feat(catalog): add category field + search/browse to ProviderCatalog"
```

---

### Task 2: Expand bundled catalog with a batch of real, verified providers

**Files:**
- Create: `src/stackowl/setup/providers/{groq,together,openrouter,mistral,cerebras,cohere,perplexity}.yaml` (7 new entries — a real, verified starter batch toward the ~100-provider vision; more can be added the same way later, one file per provider, no code change needed)
- Modify: `tests/setup/test_provider_catalog.py` (bundled-count assertion)

**Interfaces:**
- Consumes: `ProviderEntry` schema from Task 1 (including the new `category` field).
- Produces: 22 total bundled entries (15 existing + 7 new).

- [ ] **Step 1: Write the failing test**

In `tests/setup/test_provider_catalog.py`, update the existing count assertions:

```python
def test_catalog_loads_all_bundled_yaml_files() -> None:
    entries = ProviderCatalog.load()
    assert len(entries) == 22, f"Expected 22 bundled providers, got {len(entries)}: {[e.name for e in entries]}"
```

And in `test_user_override_replaces_bundled_entry_by_name`, change `assert len(entries) == 15` → `assert len(entries) == 22`. In `test_user_can_add_new_provider_beyond_bundled`, change `assert len(entries) == 16` → `assert len(entries) == 23`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/setup/test_provider_catalog.py -v`
Expected: FAIL — counts still 15/16 (new YAML files not added yet).

- [ ] **Step 3: Add the bundled YAML files**

`src/stackowl/setup/providers/groq.yaml`:
```yaml
name: groq
label: Groq
protocol: openai
base_url: https://api.groq.com/openai/v1
default_model: llama-3.3-70b-versatile
models:
  - llama-3.3-70b-versatile
  - llama-3.1-8b-instant
  - mixtral-8x7b-32768
tier: fast
needs_api_key: true
category: [free-tier, fast-inference]
key_url: https://console.groq.com/keys
```

`src/stackowl/setup/providers/together.yaml`:
```yaml
name: together
label: Together AI
protocol: openai
base_url: https://api.together.xyz/v1
default_model: meta-llama/Llama-3.3-70B-Instruct-Turbo-Free
models:
  - meta-llama/Llama-3.3-70B-Instruct-Turbo-Free
  - Qwen/Qwen2.5-72B-Instruct-Turbo
  - deepseek-ai/DeepSeek-R1
tier: standard
needs_api_key: true
category: [free-tier]
key_url: https://api.together.ai/settings/api-keys
```

`src/stackowl/setup/providers/openrouter.yaml`:
```yaml
name: openrouter
label: OpenRouter
protocol: openai
base_url: https://openrouter.ai/api/v1
default_model: meta-llama/llama-3.3-70b-instruct:free
models:
  - meta-llama/llama-3.3-70b-instruct:free
  - google/gemini-2.0-flash-exp:free
  - mistralai/mistral-small-24b-instruct-2501:free
tier: standard
needs_api_key: true
category: [free-tier, aggregator]
key_url: https://openrouter.ai/keys
```

`src/stackowl/setup/providers/mistral.yaml`:
```yaml
name: mistral
label: Mistral AI
protocol: openai
base_url: https://api.mistral.ai/v1
default_model: mistral-large-latest
models:
  - mistral-large-latest
  - mistral-small-latest
  - codestral-latest
tier: standard
needs_api_key: true
category: []
key_url: https://console.mistral.ai/api-keys
```

`src/stackowl/setup/providers/cerebras.yaml`:
```yaml
name: cerebras
label: Cerebras
protocol: openai
base_url: https://api.cerebras.ai/v1
default_model: llama-3.3-70b
models:
  - llama-3.3-70b
  - llama3.1-8b
tier: fast
needs_api_key: true
category: [free-tier, fast-inference]
key_url: https://cloud.cerebras.ai/platform/apikeys
```

`src/stackowl/setup/providers/cohere.yaml`:
```yaml
name: cohere
label: Cohere
protocol: openai
base_url: https://api.cohere.ai/compatibility/v1
default_model: command-r-plus
models:
  - command-r-plus
  - command-r
tier: standard
needs_api_key: true
category: [free-tier]
key_url: https://dashboard.cohere.com/api-keys
```

`src/stackowl/setup/providers/perplexity.yaml`:
```yaml
name: perplexity
label: Perplexity
protocol: openai
base_url: https://api.perplexity.ai
default_model: sonar
models:
  - sonar
  - sonar-pro
tier: standard
needs_api_key: true
category: []
key_url: https://www.perplexity.ai/settings/api
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/setup/test_provider_catalog.py -v`
Expected: PASS (22 total entries; sort-order and override tests unaffected in shape, only counts).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/setup/providers/groq.yaml src/stackowl/setup/providers/together.yaml \
  src/stackowl/setup/providers/openrouter.yaml src/stackowl/setup/providers/mistral.yaml \
  src/stackowl/setup/providers/cerebras.yaml src/stackowl/setup/providers/cohere.yaml \
  src/stackowl/setup/providers/perplexity.yaml tests/setup/test_provider_catalog.py
git commit -m "feat(catalog): add 7 verified providers (groq, together, openrouter, mistral, cerebras, cohere, perplexity)"
```

**Note:** this is a starter batch, not the full ~100. Growing the catalog further is a content task (one YAML file per provider, verified base_url/models), not a code task — no further plan work is needed to add more later.

---

## Phase 2 — Model discovery

### Task 3: `ModelDiscovery.list_models`

**Files:**
- Create: `src/stackowl/providers/model_discovery.py`
- Modify: `src/stackowl/exceptions.py` (new `ModelDiscoveryError`)
- Test: `tests/providers/test_model_discovery.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `async def list_models(protocol: str, base_url: str | None, api_key: str) -> list[str]`, `class ModelDiscoveryError(DomainError)`.

- [ ] **Step 1: Write the failing test**

Create `tests/providers/test_model_discovery.py`:

```python
"""Tests for ModelDiscovery.list_models — protocol dispatch, validation-by-call."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from stackowl.exceptions import ModelDiscoveryError
from stackowl.providers.model_discovery import list_models


@pytest.mark.asyncio
async def test_openai_protocol_lists_model_ids() -> None:
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(
                data=[SimpleNamespace(id="gpt-4o"), SimpleNamespace(id="gpt-4o-mini")]
            ))
        )
    )
    with patch("openai.AsyncOpenAI", return_value=fake_client) as ctor:
        result = await list_models("openai", "https://api.example.com/v1", "sk-test")
    assert result == ["gpt-4o", "gpt-4o-mini"]
    ctor.assert_called_once_with(base_url="https://api.example.com/v1", api_key="sk-test")


@pytest.mark.asyncio
async def test_anthropic_protocol_lists_model_ids() -> None:
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(
                data=[SimpleNamespace(id="claude-sonnet-4-6")]
            ))
        )
    )
    with patch("anthropic.AsyncAnthropic", return_value=fake_client):
        result = await list_models("anthropic", None, "sk-ant-test")
    assert result == ["claude-sonnet-4-6"]


@pytest.mark.asyncio
async def test_gemini_protocol_lists_model_names() -> None:
    fake_models = SimpleNamespace(list=AsyncMock(return_value=[
        SimpleNamespace(name="models/gemini-2.5-pro"),
        SimpleNamespace(name="models/gemini-2.5-flash"),
    ]))
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    with patch("google.genai.Client", return_value=fake_client):
        result = await list_models("gemini", None, "AIza-test")
    assert result == ["gemini-2.5-pro", "gemini-2.5-flash"]


@pytest.mark.asyncio
async def test_grok_protocol_dispatches_via_openai_client() -> None:
    """grok is OpenAI-compatible — mirrors _build_provider's else-branch dispatch."""
    fake_client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(
            data=[SimpleNamespace(id="grok-2")]
        )))
    )
    with patch("openai.AsyncOpenAI", return_value=fake_client):
        result = await list_models("grok", "https://api.x.ai/v1", "xai-test")
    assert result == ["grok-2"]


@pytest.mark.asyncio
async def test_failure_raises_model_discovery_error_with_provider_and_reason() -> None:
    fake_client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(side_effect=ConnectionError("refused")))
    )
    with patch("openai.AsyncOpenAI", return_value=fake_client):
        with pytest.raises(ModelDiscoveryError) as exc_info:
            await list_models("openai", "https://bad.example.com/v1", "sk-test")
    assert "refused" in str(exc_info.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_model_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.providers.model_discovery'`.

- [ ] **Step 3: Add `ModelDiscoveryError` to exceptions.py**

In `src/stackowl/exceptions.py`, add after `ProviderError` (line 197):

```python
class ModelDiscoveryError(DomainError):
    """Raised when live model discovery (or the token validation it doubles
    as) fails during the guided /provider add flow."""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"Model discovery failed for '{provider}': {reason}")
```

- [ ] **Step 4: Implement `model_discovery.py`**

Create `src/stackowl/providers/model_discovery.py`:

```python
"""ModelDiscovery — live model listing per protocol; doubles as token validation.

Dispatches by protocol the same way ``providers.registry._build_provider``
does (anthropic / gemini / else-openai, so ``grok`` — OpenAI-compatible —
shares the openai branch). Used by the guided ``/provider add`` flow: the
SAME call that lists real models also proves the token/base_url are good.
"""

from __future__ import annotations

from stackowl.exceptions import ModelDiscoveryError
from stackowl.infra.observability import log


async def list_models(protocol: str, base_url: str | None, api_key: str) -> list[str]:
    """Return the provider's real, current model ids. Raises ModelDiscoveryError on failure."""
    log.engine.debug(
        "[model_discovery] list_models: entry",
        extra={"_fields": {"protocol": protocol, "has_base_url": base_url is not None}},
    )
    try:
        if protocol == "anthropic":
            models = await _list_anthropic(api_key)
        elif protocol == "gemini":
            models = await _list_gemini(api_key)
        else:
            models = await _list_openai(base_url, api_key)
    except Exception as exc:
        log.engine.warning(
            "[model_discovery] list_models: discovery failed",
            extra={"_fields": {"protocol": protocol, "error": str(exc)}},
        )
        raise ModelDiscoveryError(protocol, str(exc)) from exc
    log.engine.debug(
        "[model_discovery] list_models: exit",
        extra={"_fields": {"protocol": protocol, "model_count": len(models)}},
    )
    return models


async def _list_openai(base_url: str | None, api_key: str) -> list[str]:
    import openai

    client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key or "no-key-needed")
    resp = await client.models.list()
    return [m.id for m in resp.data]


async def _list_anthropic(api_key: str) -> list[str]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    resp = await client.models.list()
    return [m.id for m in resp.data]


async def _list_gemini(api_key: str) -> list[str]:
    from google import genai

    client = genai.Client(api_key=api_key)
    models = await client.aio.models.list()
    return [m.name.removeprefix("models/") for m in models]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_model_discovery.py -v`
Expected: PASS.

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/model_discovery.py src/stackowl/exceptions.py && uv run mypy src/stackowl/providers/model_discovery.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/providers/model_discovery.py src/stackowl/exceptions.py tests/providers/test_model_discovery.py
git commit -m "feat(providers): add ModelDiscovery.list_models (live discovery + token validation)"
```

---

## Phase 3 — Tier selection engine

### Task 4: `TierSelector` (round-robin)

**Files:**
- Create: `src/stackowl/providers/tier_selector.py`
- Test: `tests/providers/test_tier_selector.py`

**Interfaces:**
- Consumes: `CircuitBreaker`, `CircuitState` from `stackowl.providers.circuit_breaker`.
- Produces: `class TierSelector` with `def select(self, tier: str, providers: dict[str, object], tiers: dict[str, str], breakers: dict[str, CircuitBreaker]) -> str | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/providers/test_tier_selector.py`:

```python
"""Tests for TierSelector — round-robin across healthy providers in a tier."""

from __future__ import annotations

from stackowl.infra.clock import WallClock
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.tier_selector import TierSelector


def _providers(*names: str) -> dict[str, object]:
    return {n: object() for n in names}


def test_round_robins_across_healthy_providers_in_tier() -> None:
    selector = TierSelector()
    providers = _providers("a", "b", "c")
    tiers = {"a": "fast", "b": "fast", "c": "fast"}
    breakers: dict[str, CircuitBreaker] = {}

    picks = [selector.select("fast", providers, tiers, breakers) for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]


def test_skips_open_breaker() -> None:
    selector = TierSelector()
    providers = _providers("a", "b")
    tiers = {"a": "fast", "b": "fast"}
    breaker_b = CircuitBreaker(provider_name="b", failure_threshold=1, clock=WallClock())
    breakers = {"b": breaker_b}
    # Force b OPEN.
    breaker_b._state = CircuitState.OPEN  # test-only direct state set
    breaker_b._opened_at = breaker_b._clock.monotonic()

    picks = [selector.select("fast", providers, tiers, breakers) for _ in range(3)]
    assert picks == ["a", "a", "a"]


def test_empty_tier_returns_none() -> None:
    selector = TierSelector()
    assert selector.select("powerful", {}, {}, {}) is None


def test_all_open_returns_none() -> None:
    selector = TierSelector()
    providers = _providers("a")
    tiers = {"a": "fast"}
    breaker_a = CircuitBreaker(provider_name="a", clock=WallClock())
    breaker_a._state = CircuitState.OPEN
    breaker_a._opened_at = breaker_a._clock.monotonic()
    breakers = {"a": breaker_a}

    assert selector.select("fast", providers, tiers, breakers) is None


def test_cursor_is_per_tier_independent() -> None:
    selector = TierSelector()
    providers = _providers("fast-a", "fast-b", "std-a")
    tiers = {"fast-a": "fast", "fast-b": "fast", "std-a": "standard"}
    breakers: dict = {}

    assert selector.select("fast", providers, tiers, breakers) == "fast-a"
    assert selector.select("standard", providers, tiers, breakers) == "std-a"
    assert selector.select("fast", providers, tiers, breakers) == "fast-b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_tier_selector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.providers.tier_selector'`.

- [ ] **Step 3: Implement**

Create `src/stackowl/providers/tier_selector.py`:

```python
"""TierSelector — round-robin selection among healthy providers in one tier.

Deliberately SYNC and lock-free: ``ProviderRegistry.get_with_cascade`` (its
only caller) is a sync method used throughout the pipeline, and every
selection here runs to completion without an ``await`` — so under asyncio's
single-threaded event loop, the cursor read-modify-write is inherently
atomic (no preemption mid-bytecode). No new lock is needed; this mirrors the
existing precedent of ``CircuitBreaker.state`` being a cheap sync property
that "tolerates benign staleness" rather than being lock-guarded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.providers.circuit_breaker import CircuitState

if TYPE_CHECKING:
    from stackowl.providers.circuit_breaker import CircuitBreaker


class TierSelector:
    """Round-robins across every non-OPEN provider registered for a tier."""

    def __init__(self) -> None:
        self._cursor: dict[str, int] = {}

    def select(
        self,
        tier: str,
        providers: dict[str, object],
        tiers: dict[str, str],
        breakers: dict[str, "CircuitBreaker"],
    ) -> str | None:
        """Return the next healthy provider NAME for ``tier``, or None if empty/all-OPEN."""
        log.engine.debug("[tier_selector] select: entry", extra={"_fields": {"tier": tier}})
        candidates = [name for name, t in tiers.items() if t == tier and name in providers]
        healthy = [
            name for name in candidates
            if breakers.get(name) is None or breakers[name].state is not CircuitState.OPEN
        ]
        if not healthy:
            log.engine.debug(
                "[tier_selector] select: exit — no healthy provider",
                extra={"_fields": {"tier": tier, "candidates": len(candidates)}},
            )
            return None
        idx = self._cursor.get(tier, 0) % len(healthy)
        chosen = healthy[idx]
        self._cursor[tier] = (idx + 1) % len(healthy)
        log.engine.debug(
            "[tier_selector] select: exit — chosen",
            extra={"_fields": {"tier": tier, "chosen": chosen, "healthy_count": len(healthy)}},
        )
        return chosen
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_tier_selector.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/tier_selector.py && uv run mypy src/stackowl/providers/tier_selector.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/providers/tier_selector.py tests/providers/test_tier_selector.py
git commit -m "feat(providers): add TierSelector (round-robin across a tier's healthy providers)"
```

---

### Task 5: Wire `TierSelector` into `ProviderRegistry.get_with_cascade`

**Files:**
- Modify: `src/stackowl/providers/registry.py:79-101` (`__init__`), `:414-501` (`get_with_cascade`)
- Test: `tests/providers/test_provider_registry_health.py` (existing file — add new cases)

**Interfaces:**
- Consumes: `TierSelector.select(...)` from Task 4.
- Produces: no change to `get_with_cascade`'s public signature/return type/exceptions.

- [ ] **Step 1: Write the failing test**

Add to `tests/providers/test_provider_registry_health.py` (or create `tests/providers/test_provider_registry_tier_selection.py` if that file doesn't already fit — check the existing file's scope first; if it's about `health_check()` only, create a new file `tests/providers/test_provider_registry_multi_tier.py`):

```python
"""Tests for ProviderRegistry multi-provider-per-tier round-robin (via TierSelector)."""

from __future__ import annotations

from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _registry_with(*names_and_tiers: tuple[str, str]) -> ProviderRegistry:
    registry = ProviderRegistry()
    for name, tier in names_and_tiers:
        registry.register_mock(name, MockProvider(name=name), tier=tier)
    return registry


def test_multiple_providers_same_tier_round_robin() -> None:
    registry = _registry_with(("a", "fast"), ("b", "fast"))
    picks = [registry.get_with_cascade("fast").name for _ in range(4)]
    assert picks == ["a", "b", "a", "b"]


def test_single_provider_per_tier_unchanged() -> None:
    """Regression: existing single-provider-per-tier behavior stays identical."""
    registry = _registry_with(("only", "fast"))
    for _ in range(3):
        assert registry.get_with_cascade("fast").name == "only"
```

(Check `MockProvider`'s constructor signature first — read `src/stackowl/providers/mock_provider.py`'s `__init__` and adjust `MockProvider(name=name)` to match if it differs; also confirm `ModelProvider.name` is the right accessor, per `base.py:182`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier.py -v`
Expected: FAIL — `test_multiple_providers_same_tier_round_robin` fails because today's first-match loop always returns `"a"`.

- [ ] **Step 3: Implement**

In `src/stackowl/providers/registry.py`, add to `ProviderRegistry.__init__` (after line 100, `self._cost_tracker: CostTracker | None = None`):

```python
        # F-multi-tier — round-robin selector for the "which of N healthy
        # providers in this tier" decision (get_with_cascade delegates to it).
        from stackowl.providers.tier_selector import TierSelector
        self._tier_selector = TierSelector()
```

Replace the inner loop in `get_with_cascade` (lines 444-495) — from `details: list[str] = []` through the `raise AllProvidersUnavailableError(details)` — with:

```python
        details: list[str] = []
        for tier in tier_walk:
            chosen = self._tier_selector.select(tier, providers, tiers, breakers)
            if chosen is not None:
                prov = providers[chosen]
                log.engine.info(
                    "[cascade] selected '%s' (tier=%s)",
                    chosen,
                    tier,
                    extra={"_fields": {"provider": chosen, "tier": tier}},
                )
                return prov
            candidates = [name for name, t in tiers.items() if t == tier and name in providers]
            if candidates:
                details.append(f"tier {tier}: no healthy provider ({len(candidates)} candidate(s), all open)")

        log.engine.error(
            "[registry] get_with_cascade: exit — all providers unavailable",
            extra={"_fields": {"details": details}},
        )
        raise AllProvidersUnavailableError(details)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier.py tests/providers/ -v`
Expected: PASS — new tests pass, and every pre-existing test in `tests/providers/` still passes unmodified (single-provider-per-tier regression confirmed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/registry.py && uv run mypy src/stackowl/providers/registry.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/providers/registry.py tests/providers/test_provider_registry_multi_tier.py
git commit -m "feat(providers): wire TierSelector into get_with_cascade for multi-provider tiers"
```

---

## Phase 4 — Quota-aware cooldown

### Task 6: `ProviderConfig.cooldown_hours`

**Files:**
- Modify: `src/stackowl/config/provider.py`
- Test: `tests/config/test_provider_config.py` (create if it doesn't exist — check first with `find tests -iname "test_provider_config*"`)

**Interfaces:**
- Produces: `ProviderConfig.cooldown_hours: float | None = None`.

- [ ] **Step 1: Write the failing test**

If `tests/config/test_provider_config.py` doesn't exist, create it with:

```python
"""Tests for ProviderConfig — new fields."""

from __future__ import annotations

from stackowl.config.provider import ProviderConfig


def test_cooldown_hours_defaults_none() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tier="fast",
    )
    assert cfg.cooldown_hours is None


def test_cooldown_hours_accepts_float() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tier="fast",
        cooldown_hours=24.0,
    )
    assert cfg.cooldown_hours == 24.0
```

If the file already exists, add these two test functions to it instead (do not duplicate an existing similar test — read the file first).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_provider_config.py -v`
Expected: FAIL — `cooldown_hours` unexpected keyword argument (or `AttributeError` on the default test).

- [ ] **Step 3: Implement**

In `src/stackowl/config/provider.py`, add after `supports_native_tools` (the last field, line 54):

```python
    # F-quota — hours to keep this provider's circuit OPEN after a quota/rate
    # failure with NO parseable reset signal from the provider's own response
    # (e.g. "I know this free tier resets daily"). None (default): no change
    # from today's generic failure-threshold breaker behavior. See
    # providers/_resilient_round.py's RATE_LIMIT branch for how this is used.
    cooldown_hours: float | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_provider_config.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/config/provider.py && uv run mypy src/stackowl/config/provider.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/config/provider.py tests/config/test_provider_config.py
git commit -m "feat(config): add ProviderConfig.cooldown_hours (quota-aware breaker cooldown)"
```

---

### Task 7: `CircuitBreaker.open_for` + `ModelProvider` cooldown injection

**Files:**
- Modify: `src/stackowl/providers/circuit_breaker.py`, `src/stackowl/providers/base.py:92-128`
- Test: `tests/providers/test_circuit_breaker_concurrency.py` or a new `tests/providers/test_circuit_breaker_open_for.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `async def CircuitBreaker.open_for(self, seconds: float) -> None`; `ModelProvider._cooldown_hours: float | None = None` (class attr) + `def set_cooldown_hours(self, hours: float | None) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/providers/test_circuit_breaker_open_for.py`:

```python
"""Tests for CircuitBreaker.open_for — explicit-duration quota cooldown."""

from __future__ import annotations

import pytest

from stackowl.infra.clock import Clock
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState


class _FakeClock(Clock):
    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover - unused here
        self._t += seconds

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.mark.asyncio
async def test_open_for_forces_open_with_exact_duration() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)

    await breaker.open_for(3600.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(3600.0)


@pytest.mark.asyncio
async def test_open_for_promotes_to_half_open_after_duration_elapses() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)

    await breaker.open_for(100.0)
    clock.advance(101.0)

    assert breaker.state is CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_open_for_overrides_any_prior_failure_count() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3, clock=clock)
    await breaker.record(ok=False)  # one failure, not yet OPEN

    await breaker.open_for(60.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(60.0)
```

(Check `Clock`'s exact abstract interface first in `src/stackowl/infra/clock.py` — adjust `_FakeClock` to match if `monotonic`/`async_sleep` differ.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_circuit_breaker_open_for.py -v`
Expected: FAIL — `AttributeError: 'CircuitBreaker' object has no attribute 'open_for'`.

- [ ] **Step 3: Implement `open_for` in `circuit_breaker.py`**

Add after `admit_probe` (after line 194, before `clear_probe`):

```python
    async def open_for(self, seconds: float) -> None:
        """Force OPEN for exactly ``seconds`` (quota-aware cooldown).

        Distinct from the failure-counted ``_record_failure`` path: this sets
        an EXACT duration (from a provider's own reset signal, or a
        configured ``cooldown_hours`` fallback) instead of the adaptive
        half-open backoff. Reuses ``_current_half_open_seconds`` — the same
        field ``retry_after_seconds`` already reads — so no new state is
        needed; the NEXT open (after this cooldown clears) resets to the
        base window via the existing HALF_OPEN->CLOSED success path.
        """
        log.engine.debug(
            "[circuit] open_for: entry",
            extra={"_fields": {"provider": self._provider_name, "seconds": seconds}},
        )
        async with self._lock:
            self._current_half_open_seconds = max(seconds, 0.0)
            self._state = CircuitState.OPEN
            self._opened_at = self._clock.monotonic()
            self._failures.clear()
            self._probe_in_flight = False
        log.engine.warning(
            "[circuit] open_for: exit — forced OPEN with explicit cooldown",
            extra={"_fields": {"provider": self._provider_name, "cooldown_seconds": seconds}},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_circuit_breaker_open_for.py -v`
Expected: PASS.

- [ ] **Step 5: Add cooldown injection to `ModelProvider` (base.py)**

In `src/stackowl/providers/base.py`, add a class attribute after `_limiter` (line 93):

```python
    _limiter: RateLimiter | None = None
    # F-quota — this provider's configured cooldown_hours (ProviderConfig),
    # injected by ProviderRegistry alongside breaker/limiter. None (default)
    # → the RATE_LIMIT branch in _resilient_round has no config fallback.
    _cooldown_hours: float | None = None
```

Add a setter method after `set_resilience` (after line 112):

```python
    def set_cooldown_hours(self, hours: float | None) -> None:
        """Inject this provider's configured cooldown_hours (idempotent)."""
        self._cooldown_hours = hours
```

Update `_resilient_round` (lines 114-128) to pass it through:

```python
    async def _resilient_round[T](
        self,
        do_round: Callable[[], Awaitable[T]],
    ) -> T:
        """Run ONE remote round through the shared breaker+limiter site (SP-2).

        Thin instance bracket over :func:`providers._resilient_round.resilient_round`
        binding this provider's injected breaker/limiter/cooldown_hours. Concrete
        providers wrap EVERY remote round (each tool-loop ``create()``, the
        wrap-up round, the ``complete()``/``stream()`` round) in this so
        breaker-record + limiter-acquire + quota-cooldown share ONE audited site.
        Pass-through when nothing is injected.
        """
        from stackowl.providers._resilient_round import resilient_round

        return await resilient_round(
            self._breaker, self._limiter, do_round, cooldown_hours=self._cooldown_hours,
        )
```

- [ ] **Step 6: Run the full providers test suite to catch signature drift**

Run: `uv run pytest tests/providers/ -v`
Expected: PASS (no existing test calls `resilient_round` positionally past `do_round` — Step 8 in Task 8 below adds the actual `cooldown_hours` parameter to `resilient_round`; until then this new kwarg is simply unused/ignored by the current signature, so this step may show a `TypeError: unexpected keyword argument`. Wire Task 7 and Task 8 as one continuous change if your test run shows this — resume at Task 8 Step 3 immediately.)

- [ ] **Step 7: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/circuit_breaker.py src/stackowl/providers/base.py && uv run mypy src/stackowl/providers/circuit_breaker.py src/stackowl/providers/base.py`
Expected: no errors (mypy may flag the forward reference to `resilient_round`'s new kwarg until Task 8 lands — acceptable to land Task 7+8 together in one commit if so; see note above).

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/providers/circuit_breaker.py src/stackowl/providers/base.py tests/providers/test_circuit_breaker_open_for.py
git commit -m "feat(providers): add CircuitBreaker.open_for + cooldown_hours injection on ModelProvider"
```

---

### Task 8: Quota-reset parsing + `cooldown_hours` fallback in `_resilient_round`

**Files:**
- Modify: `src/stackowl/providers/_resilient_round.py`, `src/stackowl/providers/registry.py` (`_build_into`, `apply_settings`)
- Test: `tests/providers/test_provider_resilience.py` (existing file — add cases) or a new `tests/providers/test_quota_cooldown.py`

**Interfaces:**
- Consumes: `CircuitBreaker.open_for` (Task 7), `ProviderConfig.cooldown_hours` (Task 6).
- Produces: `resilient_round(..., *, cooldown_hours: float | None = None, is_provider_fault=...)`; `_parse_retry_after_seconds(exc: BaseException) -> float | None` (private helper).

- [ ] **Step 1: Write the failing tests**

Create `tests/providers/test_quota_cooldown.py`:

```python
"""Tests for quota-aware cooldown: reset-header parsing, cooldown_hours fallback."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stackowl.exceptions import RateLimitError
from stackowl.infra.clock import Clock
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.rate_limiter import RateLimiter
from stackowl.providers._resilient_round import resilient_round


class _FakeClock(Clock):
    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    async def async_sleep(self, seconds: float) -> None:
        self._t += seconds


class _RateLimited429(Exception):
    def __init__(self, retry_after: str | None) -> None:
        super().__init__("429 rate limited")
        self.status_code = 429
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after else {})


@pytest.mark.asyncio
async def test_parseable_reset_header_opens_breaker_for_that_exact_duration() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after="120")

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=None)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_no_reset_header_falls_back_to_configured_cooldown_hours() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after=None)

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=1.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(3600.0)


@pytest.mark.asyncio
async def test_no_header_and_no_cooldown_hours_uses_generic_threshold_path() -> None:
    """Absent both signals: byte-identical to today (penalize only, no open_for)."""
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3, clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after=None)

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=None)

    # One failure recorded via the generic path — NOT forced OPEN (threshold is 3).
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_malformed_reset_header_falls_back_to_cooldown_hours_not_crash() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after="not-a-number")

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=2.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(7200.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_quota_cooldown.py -v`
Expected: FAIL — `TypeError: resilient_round() got an unexpected keyword argument 'cooldown_hours'`.

- [ ] **Step 3: Implement**

In `src/stackowl/providers/_resilient_round.py`, add a private helper after `_is_transport_error` (after line 191):

```python
def _parse_retry_after_seconds(exc: BaseException) -> float | None:
    """Best-effort: read a numeric Retry-After header off exc.response, if present.

    Defensive by construction — ANY failure (missing attrs, non-numeric value,
    HTTP-date form) falls back to None so a parsing bug can never crash a round.
    """
    try:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        raw = headers.get("retry-after") if hasattr(headers, "get") else None
        if raw is None:
            return None
        return float(raw)
    except Exception:  # noqa: BLE001 — parsing is best-effort, never fatal.
        return None
```

Update the `resilient_round` signature (line 194) to accept the new kwarg:

```python
async def resilient_round[T](
    breaker: CircuitBreaker | None,
    limiter: RateLimiter | None,
    do_round: Callable[[], Awaitable[T]],
    *,
    is_provider_fault: Callable[[BaseException], bool] = is_provider_fault,
    cooldown_hours: float | None = None,
) -> T:
```

Update its docstring's summary line to mention the new behavior (append one sentence): `"...; on a RATE_LIMIT fault this also opens the breaker for a quota-aware duration — the response's own reset signal if parseable, else cooldown_hours, else no change (generic threshold path)."`

Update the RATE_LIMIT branch (lines 294-302) to also call `open_for`:

```python
        elif cause is FailureCause.RATE_LIMIT and limiter is not None:
            try:
                limiter.penalize()
            except Exception as pen_exc:  # B5 — must never mask the real error.
                log.engine.error(
                    "[resilient_round] limiter.penalize raised — continuing to re-raise",
                    exc_info=pen_exc,
                    extra={"_fields": {"provider": provider}},
                )
            if breaker is not None:
                reset_seconds = _parse_retry_after_seconds(exc)
                cooldown_seconds = (
                    reset_seconds if reset_seconds is not None
                    else (cooldown_hours * 3600.0 if cooldown_hours is not None else None)
                )
                if cooldown_seconds is not None:
                    try:
                        await breaker.open_for(cooldown_seconds)
                    except Exception as cd_exc:  # B5 — must never mask the real error.
                        log.engine.error(
                            "[resilient_round] breaker.open_for raised — continuing to re-raise",
                            exc_info=cd_exc,
                            extra={"_fields": {"provider": provider}},
                        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_quota_cooldown.py tests/providers/ -v`
Expected: PASS — new tests pass; the entire pre-existing `tests/providers/` suite (including Task 7's tests, now unblocked) still passes.

- [ ] **Step 5: Wire `cooldown_hours` injection into `ProviderRegistry`**

In `src/stackowl/providers/registry.py`, add a tiny injection helper after `_inject_resilience` (after line 53):

```python
def _inject_cooldown_hours(provider: object, cooldown_hours: float | None) -> None:
    """Inject the registry-owned cooldown_hours into one provider, if it accepts it.

    Mirrors ``_inject_resilience`` — duck-typed test fakes without
    ``set_cooldown_hours`` opt out silently.
    """
    setter = getattr(provider, "set_cooldown_hours", None)
    if callable(setter):
        setter(cooldown_hours)
```

Call it in `_build_into` right after `_inject_resilience(provider, breakers[config.name], limiters[config.name])` (line 183):

```python
        _inject_resilience(provider, breakers[config.name], limiters[config.name])
        _inject_cooldown_hours(provider, config.cooldown_hours)
```

And in `apply_settings`'s secret-rotation rebuild branch, right after its own `_inject_resilience(provider, self._breakers[name], self._limiters[name])` call (line 299):

```python
                        _inject_resilience(provider, self._breakers[name], self._limiters[name])
                        _inject_cooldown_hours(provider, config.cooldown_hours)
```

- [ ] **Step 6: Add a regression test confirming hot-reload picks up a changed `cooldown_hours`**

Add to `tests/providers/test_quota_cooldown.py` (the file created in Step 1 of this task — keeping it self-contained avoids coupling to `test_provider_hot_reload.py`'s specific fixtures, which this plan hasn't read; `apply_settings` only ever reads `settings.providers`, so a `SimpleNamespace` stands in for a real `Settings` object here exactly as it does for `ProviderConfig`'s sibling fields elsewhere in this codebase's provider tests):

```python
from types import SimpleNamespace

from stackowl.config.provider import ProviderConfig
from stackowl.providers.registry import ProviderRegistry


def _cfg(**overrides: object) -> ProviderConfig:
    base = dict(
        name="p", protocol="openai", default_model="m", tier="fast",
        api_key=None, base_url=None,
    )
    base.update(overrides)
    return ProviderConfig(**base)


def test_apply_settings_updates_cooldown_hours_on_unchanged_provider() -> None:
    """A config-only cooldown_hours change is picked up on reload, mirroring
    how other config field changes already flow through apply_settings."""
    registry = ProviderRegistry.from_settings(SimpleNamespace(providers=[_cfg(cooldown_hours=None)]))
    assert registry.get("p")._cooldown_hours is None

    registry.apply_settings(SimpleNamespace(providers=[_cfg(cooldown_hours=6.0)]))
    assert registry.get("p")._cooldown_hours == 6.0
```

- [ ] **Step 7: Run the full providers + config test suites**

Run: `uv run pytest tests/providers/ tests/config/ -v`
Expected: PASS, no regressions.

- [ ] **Step 8: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/ && uv run mypy src/stackowl/providers/`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/stackowl/providers/_resilient_round.py src/stackowl/providers/registry.py \
  tests/providers/test_quota_cooldown.py
git commit -m "feat(providers): quota-aware breaker cooldown (reset-header parse + cooldown_hours fallback)"
```

---

## Phase 5 — Guided add-flow (command surface)

### Task 9: `/provider add` browse/search entry point

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Test: `tests/commands/test_provider_command.py`

**Interfaces:**
- Consumes: `ProviderCatalog.search`/`browse` (Task 1).
- Produces: `ProviderCommand._add_browse(self, query: str) -> CommandResponse`; `/provider add` (no positional-looking args) now routes to browse instead of the old 4-arg usage error.

- [ ] **Step 1: Write the failing tests**

Add to `tests/commands/test_provider_command.py`:

```python
@pytest.mark.asyncio
async def test_add_with_no_args_shows_catalog_browse(tmp_yaml: Path) -> None:
    cmd = _make_cmd()
    reply = await cmd.handle("add", _state())
    assert isinstance(reply, CommandResponse)
    assert any("add-pick" in a.command for a in reply.actions)


@pytest.mark.asyncio
async def test_add_with_search_query_filters_catalog(tmp_yaml: Path) -> None:
    cmd = _make_cmd()
    reply = await cmd.handle("add groq", _state())
    assert isinstance(reply, CommandResponse)
    assert any("groq" in a.command for a in reply.actions)


@pytest.mark.asyncio
async def test_add_with_full_positional_args_still_works_directly(tmp_yaml: Path) -> None:
    """Regression: the existing positional add form is untouched."""
    cmd = _make_cmd()
    reply = await cmd.handle("add myprov openai gpt-4o fast", _state())
    assert isinstance(reply, str)
    assert reply.startswith("✓ Provider 'myprov' added")
```

(Add `from stackowl.commands.response import CommandResponse` to the test file's imports if not already present — check first.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k add_with_no_args_or_add_with_search`
Expected: FAIL — today's `_add` with `<4` tokens returns the `_USAGE` string, not a catalog browse `CommandResponse`.

- [ ] **Step 3: Implement**

In `src/stackowl/commands/provider_command.py`, the `handle()` dispatch (line 207-208) currently routes `sub == "add"` straight to `self._add(rest)`. Change the routing so a call with **fewer than 4 whitespace-separated tokens** goes to the new browse/search path instead of `_add`'s usage error, while 4+ tokens (today's positional form) keeps going to `_add` unchanged:

```python
            elif sub == "add":
                add_tokens = rest.split()
                result = self._add(rest) if len(add_tokens) >= 4 else self._add_browse(rest.strip())
```

Add `_add_browse` as a new method, placed right before the existing `_add` method (before line 559):

```python
    # -- add-browse / add-pick (guided catalog flow) ----------------------------

    def _add_browse(self, query: str) -> CommandResponse:
        """Catalog search (query given) or full browse (empty query)."""
        log.config.debug(
            "[commands] provider.add_browse: entry", extra={"_fields": {"query_len": len(query)}}
        )
        from stackowl.setup.provider_catalog import ProviderCatalog

        entries = ProviderCatalog.search(query) if query else ProviderCatalog.browse()
        if not entries:
            return CommandResponse(text=f"No catalog providers match '{query}'." if query else "Catalog is empty.")
        actions = tuple(
            Action(label=entry.label, command=f"/provider add-pick {entry.name}", destructive=False)
            for entry in entries[:30]
        )
        text = (
            f"Found {len(entries)} provider(s) matching '{query}':" if query
            else f"Browse {len(entries)} catalog providers:"
        )
        if len(entries) > 30:
            text += f"\n(showing first 30 — refine with /provider add <search term>)"
        log.config.debug(
            "[commands] provider.add_browse: exit", extra={"_fields": {"shown": min(len(entries), 30)}}
        )
        return CommandResponse(text=text, actions=actions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Expected: PASS — new tests pass; every pre-existing test in the file (list/add/remove/set-tier/edit/enable/disable/set-token/rename) still passes.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py && uv run mypy src/stackowl/commands/provider_command.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): /provider add with no/partial args browses the catalog"
```

---

### Task 10: `add-pick` / `add-token` → live discovery (async)

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Test: `tests/commands/test_provider_command.py`

**Interfaces:**
- Consumes: `ModelDiscovery.list_models` (Task 3), `ProviderCatalog.load`/`search` (Task 1), `store_secret` (existing).
- Produces: `async def ProviderCommand._add_pick(self, raw: str) -> str | CommandResponse`; `async def ProviderCommand._add_token(self, raw: str) -> str | CommandResponse`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/commands/test_provider_command.py`:

```python
@pytest.mark.asyncio
async def test_add_pick_keyless_local_entry_skips_straight_to_discovery(
    tmp_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackowl.setup.provider_catalog import ProviderEntry

    keyless = ProviderEntry(
        name="ollama-local", label="Ollama (local)", protocol="openai",
        base_url="http://localhost:11434/v1", default_model="llama3",
        needs_api_key=False, is_local=True,
    )
    monkeypatch.setattr(
        "stackowl.setup.provider_catalog.ProviderCatalog.load", classmethod(lambda cls: [keyless])
    )
    monkeypatch.setattr(
        "stackowl.providers.model_discovery.list_models",
        AsyncMock(return_value=["llama3", "llama3:70b"]),
    )
    cmd = _make_cmd()
    reply = await cmd.handle("add-pick ollama-local", _state())
    assert isinstance(reply, CommandResponse)
    assert any("add-model" in a.command for a in reply.actions)


@pytest.mark.asyncio
async def test_add_pick_key_required_entry_prompts_for_token(tmp_yaml: Path) -> None:
    cmd = _make_cmd()
    reply = await cmd.handle("add-pick groq", _state())
    assert isinstance(reply, str)
    assert "add-token groq" in reply


@pytest.mark.asyncio
async def test_add_token_valid_stores_secret_and_shows_model_picker(
    tmp_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stackowl.providers.model_discovery.list_models",
        AsyncMock(return_value=["llama-3.3-70b-versatile"]),
    )
    cmd = _make_cmd()
    reply = await cmd.handle(f"add-token groq {RAW_TOKEN}", _state())
    assert isinstance(reply, CommandResponse)
    assert any("add-model groq llama-3.3-70b-versatile" in a.command for a in reply.actions)
    # The raw token must never appear in the reply text or any action command.
    assert RAW_TOKEN not in reply.text
    assert all(RAW_TOKEN not in a.command for a in reply.actions)


@pytest.mark.asyncio
async def test_add_token_invalid_reports_reason_and_offers_retry(
    tmp_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackowl.exceptions import ModelDiscoveryError

    monkeypatch.setattr(
        "stackowl.providers.model_discovery.list_models",
        AsyncMock(side_effect=ModelDiscoveryError("groq", "401 Unauthorized")),
    )
    cmd = _make_cmd()
    reply = await cmd.handle(f"add-token groq {RAW_TOKEN}", _state())
    assert isinstance(reply, str)
    assert "401 Unauthorized" in reply
    assert "add-token groq" in reply
```

(Add `from unittest.mock import AsyncMock` to imports if not present.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "add_pick or add_token"`
Expected: FAIL — `sub == "add-pick"`/`"add-token"` are unrecognized subcommands (fall to `render_usage`).

- [ ] **Step 3: Implement**

In `handle()` (the if/elif ladder starting line 205), add two new branches after the `elif sub == "add":` block, and mark `handle` itself already `async` (it is — no change needed there):

```python
            elif sub == "add-pick":
                result = await self._add_pick(rest)
            elif sub == "add-token":
                result = await self._add_token(rest)
```

Add both methods after `_add_browse` (before the original `_add`):

```python
    async def _add_pick(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.add_pick: entry", extra={"_fields": {"raw_len": len(raw)}})
        catalog_name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        entry = self._catalog_entry(catalog_name)
        if entry is None:
            return f"✗ Unknown catalog provider '{catalog_name}' — run /provider add to browse"
        if not entry.needs_api_key or entry.is_local:
            return await self._add_discover(catalog_name, api_key="")
        key_hint = f"Get a key at: {entry.key_url}\n" if entry.key_url else ""
        log.config.debug("[commands] provider.add_pick: exit — awaiting token", extra={"_fields": {"catalog": catalog_name}})
        return f"{key_hint}Reply with: /provider add-token {catalog_name} <RAW_TOKEN>"

    async def _add_token(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.add_token: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split(maxsplit=1)
        if len(bits) < 2:
            return "Usage: /provider add-token <catalog_name> <RAW_TOKEN>"
        catalog_name, token = bits
        return await self._add_discover(catalog_name, api_key=token)

    async def _add_discover(self, catalog_name: str, *, api_key: str) -> str | CommandResponse:
        """Live-query real models — this call ALSO validates the token (single site)."""
        from stackowl.exceptions import ModelDiscoveryError
        from stackowl.providers.model_discovery import list_models

        entry = self._catalog_entry(catalog_name)
        if entry is None:
            return f"✗ Unknown catalog provider '{catalog_name}' — run /provider add to browse"
        try:
            models = await list_models(entry.protocol, entry.base_url or None, api_key)
        except ModelDiscoveryError as exc:
            log.config.warning(
                "[commands] provider.add_discover: validation failed",
                extra={"_fields": {"catalog": catalog_name, "reason": exc.reason}},
            )
            retry = f"\nReply with: /provider add-token {catalog_name} <NEW_TOKEN>" if api_key else ""
            return f"✗ Could not connect to {entry.label}: {exc.reason}{retry}"

        api_key_ref = "-"
        if api_key:
            _description, api_key_ref = store_secret(f"stackowl-provider-{catalog_name}", api_key)

        if not models:
            return (
                f"✓ Connected to {entry.label}, but it reported no models.\n"
                f"Reply with: /provider add {catalog_name} {entry.protocol} <model_id> <tier>"
            )
        actions = tuple(
            Action(
                label=model,
                command=f"/provider add-model {catalog_name} {model} {api_key_ref}",
                destructive=False,
            )
            for model in models[:30]
        )
        log.config.debug(
            "[commands] provider.add_discover: exit — models found",
            extra={"_fields": {"catalog": catalog_name, "model_count": len(models)}},
        )
        return CommandResponse(text=f"{entry.label} — pick a model:", actions=actions)

    def _catalog_entry(self, catalog_name: str) -> Any:
        from stackowl.setup.provider_catalog import ProviderCatalog

        return next((e for e in ProviderCatalog.load() if e.name == catalog_name), None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py && uv run mypy src/stackowl/commands/provider_command.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): /provider add-pick + add-token (live discovery doubles as validation)"
```

---

### Task 11: `add-model` / `add-tier` → persist (shared with positional `_add`)

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Test: `tests/commands/test_provider_command.py`

**Interfaces:**
- Consumes: `_add_pick`/`_add_discover` (Task 10).
- Produces: `def ProviderCommand._add_model(self, raw: str) -> CommandResponse`; `def ProviderCommand._add_tier(self, raw: str) -> str`; `def ProviderCommand._persist_new_provider(self, entry: dict[str, Any]) -> str` (shared save/validate/persist logic factored out of the existing `_add`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/commands/test_provider_command.py`:

```python
def test_add_model_shows_tier_picker() -> None:
    cmd = _make_cmd()
    reply = cmd._add_model("groq llama-3.3-70b-versatile -")
    assert isinstance(reply, CommandResponse)
    assert any("add-tier groq llama-3.3-70b-versatile - fast" in a.command for a in reply.actions)
    assert any("add-tier groq llama-3.3-70b-versatile - powerful" in a.command for a in reply.actions)


def test_add_tier_persists_provider_with_catalog_name(tmp_yaml: Path) -> None:
    cmd = _make_cmd()
    reply = cmd._add_tier("groq llama-3.3-70b-versatile - fast")
    assert reply.startswith("✓ Provider 'groq' added")
    data = _load(tmp_yaml)
    saved = next(p for p in data["providers"] if p["name"] == "groq")
    assert saved["protocol"] == "openai"
    assert saved["default_model"] == "llama-3.3-70b-versatile"
    assert saved["tier"] == "fast"
    assert saved["api_key"] is None  # "-" sentinel means keyless


def test_add_tier_auto_suffixes_name_on_collision(tmp_yaml: Path) -> None:
    """Adding the SAME catalog provider twice (e.g. two free-tier keys) must
    not collide — this is the actual point of multi-provider-per-tier."""
    cmd = _make_cmd()
    cmd._add_tier("groq llama-3.3-70b-versatile - fast")
    reply2 = cmd._add_tier("groq llama-3.3-70b-versatile - fast")
    assert reply2.startswith("✓ Provider 'groq-2' added")
    data = _load(tmp_yaml)
    names = [p["name"] for p in data["providers"]]
    assert names == ["groq", "groq-2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "add_model or add_tier"`
Expected: FAIL — `AttributeError: 'ProviderCommand' object has no attribute '_add_model'`.

- [ ] **Step 3: Implement**

Add `elif sub == "add-model": result = self._add_model(rest)` and `elif sub == "add-tier": result = self._add_tier(rest)` to the `handle()` ladder, alongside the Task 10 branches (both are sync — no `await`).

Add the two new methods plus the factored-out `_persist_new_provider`, placed right after `_add_discover`/`_catalog_entry`:

```python
    def _add_model(self, raw: str) -> CommandResponse:
        log.config.debug("[commands] provider.add_model: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split(maxsplit=2)
        if len(bits) < 3:
            return CommandResponse(text="Usage: /provider add-model <catalog_name> <model> <api_key_ref_or_dash>")
        catalog_name, model, api_key_ref = bits
        actions = tuple(
            Action(
                label=tier,
                command=f"/provider add-tier {catalog_name} {model} {api_key_ref} {tier}",
                destructive=False,
            )
            for tier in _VALID_TIERS
        )
        log.config.debug("[commands] provider.add_model: exit", extra={"_fields": {"catalog": catalog_name}})
        return CommandResponse(text=f"Pick a tier for {catalog_name} / {model}:", actions=actions)

    def _add_tier(self, raw: str) -> str:
        log.config.debug("[commands] provider.add_tier: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split()
        if len(bits) != 4:
            return "Usage: /provider add-tier <catalog_name> <model> <api_key_ref_or_dash> <tier>"
        catalog_name, model, api_key_ref, tier = bits
        if tier not in _VALID_TIERS:
            return f"✗ Invalid tier '{tier}' — valid: {', '.join(_VALID_TIERS)}"
        entry = self._catalog_entry(catalog_name)
        if entry is None:
            return f"✗ Unknown catalog provider '{catalog_name}' — run /provider add to browse"

        path = config_path()
        data = load_yaml(path)
        existing_names = [p.get("name") for p in self._providers(data)]
        name = self._unique_provider_name(catalog_name, existing_names)

        provider_entry: dict[str, Any] = {
            "name": name,
            "protocol": entry.protocol,
            "enabled": True,
            "api_key": None if api_key_ref == "-" else api_key_ref,
            "base_url": entry.base_url or None,
            "default_model": model,
            "tier": tier,
        }
        result = self._persist_new_provider(provider_entry)
        log.config.debug("[commands] provider.add_tier: exit", extra={"_fields": {"name": name}})
        return result

    @staticmethod
    def _unique_provider_name(base: str, existing: list[Any]) -> str:
        """Auto-suffix (groq, groq-2, groq-3, ...) so adding the SAME catalog
        provider twice — e.g. two free-tier keys for round-robin — never collides."""
        if base not in existing:
            return base
        suffix = 2
        while f"{base}-{suffix}" in existing:
            suffix += 1
        return f"{base}-{suffix}"

    def _persist_new_provider(self, entry: dict[str, Any]) -> str:
        """Validate + save a new provider entry. Shared by the positional
        ``_add`` and the guided add-flow's final ``_add_tier`` step (DRY)."""
        name = entry["name"]
        try:
            ProviderConfig(**entry)
        except Exception as exc:
            log.config.warning(
                "[commands] provider.persist: schema validation failed",
                extra={"_fields": {"name": name, "error": str(exc)}},
            )
            return f"✗ Invalid provider config: {exc}"

        path = config_path()
        data = load_yaml(path)
        providers = self._providers(data)
        providers.append(entry)
        save_yaml(path, data)
        if not self._persisted(path, name):
            log.config.error("[commands] provider.persist: write did not persist", extra={"_fields": {"name": name}})
            return (
                f"✗ Provider '{name}' was not saved — the config file did not "
                "reflect the change (check file permissions/disk). Nothing was added."
            )
        self._emit_reloaded(name)
        log.config.info("[commands] provider.persist: exit — added", extra={"_fields": {"name": name}})
        key_note = f" (api_key ref: {entry['api_key']})" if entry.get("api_key") else ""
        return f"✓ Provider '{name}' added{key_note} — applied immediately"
```

Now refactor the ORIGINAL `_add` (lines 559-655) to call the shared helper instead of duplicating the validate/save/persist/emit block. Replace from `# Validate via the real schema...` (the comment right before `try: ProviderConfig(**entry)`) through the end of the method with:

```python
        # Only after the entry validates: store the secret (if any) and keep
        # only the resolver REF — never the raw token.
        api_key_ref: str | None = None
        if token:
            log.config.debug(
                "[commands] provider.add: storing secret",
                extra={"_fields": {"name": name}},  # token length/value never logged
            )
            _description, api_key_ref = store_secret(f"stackowl-provider-{name}", token)
            entry["api_key"] = api_key_ref

        return self._persist_new_provider(entry)
```

(Delete the old inline `ProviderConfig(**entry)` try/except, `providers.append(entry)`, `save_yaml`, `_persisted` check, `_emit_reloaded`, and final return lines that `_persist_new_provider` now owns — read the current method body first to remove exactly the now-duplicated block, keeping the earlier duplicate-name check, which stays in `_add` since the guided flow's `_add_tier` uses its own `_unique_provider_name` auto-suffix instead of rejecting.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py tests/journeys/commands/test_provider_command_journey.py -v`
Expected: PASS — new tests pass; the original `_add`'s existing tests (duplicate-name rejection, schema validation, secret storage, persisted-check) still pass unmodified through the shared helper.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py && uv run mypy src/stackowl/commands/provider_command.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): /provider add-model + add-tier complete the guided add-flow"
```

---

## Phase 6 — Lifecycle status UX

### Task 12: Live status badges on `list`/`menu` + new `status [tier]`

**Files:**
- Modify: `src/stackowl/commands/provider_command.py` (needs a `ProviderRegistry` reference — check constructor/DI wiring first, see Step 0)
- Test: `tests/commands/test_provider_command.py`

**Interfaces:**
- Consumes: `ProviderRegistry.get_circuit_breaker(name)`, `.get_rate_limiter(name)` (existing, `registry.py:632-638`).
- Produces: `ProviderCommand.__init__` gains an optional `registry: ProviderRegistry | None` parameter; `_status(self, raw: str) -> str` (new subcommand); `_live_status_badge(self, name: str) -> str` (shared by `list`/`menu`/`status`).

**Note (deliberate simplification vs. the spec text):** the spec's UX section mentions the menu status line could show "which tier round-robin slot" a provider is in. `TierSelector` (Task 4) has no introspection accessor for this, and adding one would mean exposing internal cursor state for a cosmetic detail. This task covers live circuit state (closed/half-open/open+retry-after) only — round-robin position is skipped as low-value for the added surface it would need.

- [ ] **Step 0: Check how `ProviderCommand` is constructed in production**

Run: `grep -n "ProviderCommand(" src/stackowl/commands/assembly.py`

Read that call site to see what's already passed (`event_bus=...`) and how `ProviderRegistry` is obtained there (likely already available in the same DI scope — check `CommandDeps` in `src/stackowl/commands/assembly.py`). Wire the registry through the SAME DI path `event_bus` already uses — do not construct a second `ProviderRegistry` instance.

- [ ] **Step 1: Write the failing tests**

Add to `tests/commands/test_provider_command.py`:

```python
@pytest.mark.asyncio
async def test_list_shows_live_circuit_state(tmp_yaml: Path) -> None:
    from stackowl.providers.mock_provider import MockProvider
    from stackowl.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register_mock("myprov", MockProvider(name="myprov"), tier="fast")
    cmd = _make_cmd()
    cmd._registry = registry  # or via constructor, per Step 0's finding

    # add "myprov" to the yaml so _list sees it too
    (Path(tmp_yaml)).write_text(
        yaml.dump({"test_mode": True, "providers": [{
            "name": "myprov", "protocol": "openai", "default_model": "m",
            "tier": "fast", "enabled": True,
        }]}), encoding="utf-8",
    )
    reply = await cmd.handle("list", _state())
    assert "closed" in reply.text.lower() or "🟢" in reply.text


@pytest.mark.asyncio
async def test_status_subcommand_shows_all_providers_in_tier(tmp_yaml: Path) -> None:
    from stackowl.providers.mock_provider import MockProvider
    from stackowl.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register_mock("a", MockProvider(name="a"), tier="fast")
    registry.register_mock("b", MockProvider(name="b"), tier="fast")
    cmd = _make_cmd()
    cmd._registry = registry

    reply = await cmd.handle("status fast", _state())
    assert isinstance(reply, CommandResponse) or isinstance(reply, str)
    assert "a" in reply if isinstance(reply, str) else "a" in reply.text
    assert "b" in reply if isinstance(reply, str) else "b" in reply.text
```

(Adjust the exact attribute name for the registry reference — `cmd._registry` — to match whatever Step 0 determines the real constructor parameter/attribute is named.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "list_shows_live or status_subcommand"`
Expected: FAIL — no live status in `_list`'s output; `status` is an unrecognized subcommand.

- [ ] **Step 3: Implement**

In `ProviderCommand.__init__` (line 181-182), add the optional registry parameter:

```python
    def __init__(self, event_bus: EventBus | None = None, registry: "ProviderRegistry | None" = None) -> None:
        self._bus = event_bus
        self._registry = registry
```

(Add `from typing import TYPE_CHECKING` + `if TYPE_CHECKING: from stackowl.providers.registry import ProviderRegistry` near the top imports if not already present, to type-hint without a runtime import cycle — check first whether `commands/provider_command.py` already imports `providers.registry` anywhere; it currently doesn't, per the file read in this plan's research.)

Add a small status-line helper and the `_status` method, plus a `handle()` branch `elif sub == "status": result = self._status(rest)`:

```python
    def _live_status_badge(self, name: str) -> str:
        if self._registry is None:
            return ""
        breaker = self._registry.get_circuit_breaker(name)
        if breaker is None:
            return " [no breaker]"
        from stackowl.providers.circuit_breaker import CircuitState

        state = breaker.state
        if state is CircuitState.CLOSED:
            return " [closed]"
        if state is CircuitState.HALF_OPEN:
            return " [half-open]"
        return f" [open, retry in {breaker.retry_after_seconds:.0f}s]"

    def _status(self, raw: str) -> str:
        log.config.debug("[commands] provider.status: entry", extra={"_fields": {"raw_len": len(raw)}})
        if self._registry is None:
            return "✗ Provider registry not wired for this command instance."
        tier = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not tier or tier not in _VALID_TIERS:
            return f"Usage: /provider status <tier>\n  tiers: {', '.join(_VALID_TIERS)}"
        path = config_path()
        data = load_yaml(path) if path.exists() else {}
        names = [p.get("name") for p in self._providers(data) if p.get("tier") == tier]
        if not names:
            return f"No providers configured for tier '{tier}'."
        lines = [f"{name}{self._live_status_badge(name)}" for name in names]
        log.config.debug("[commands] provider.status: exit", extra={"_fields": {"tier": tier, "count": len(names)}})
        return f"Tier '{tier}':\n" + "\n".join(lines)
```

Update `_list` (lines 281-314) to append the live badge to each provider's line — change the `lines.append(...)` call (line 306) to:

```python
            lines.append(
                f"{name} | {protocol} | {model} | {tier} | "
                f"enabled={enabled} | api_key={key_disp}{self._live_status_badge(name)}"
            )
```

Update `_menu` (lines 318-363) the same way — change its `text = f"{name} | {protocol} | {model} | {tier} | enabled={enabled}"` line (line 338) to:

```python
        text = f"{name} | {protocol} | {model} | {tier} | enabled={enabled}{self._live_status_badge(name)}"
```

- [ ] **Step 3b: Add the `_menu` status test**

Add to `tests/commands/test_provider_command.py`:

```python
@pytest.mark.asyncio
async def test_menu_shows_live_circuit_state(tmp_yaml: Path) -> None:
    from stackowl.providers.mock_provider import MockProvider
    from stackowl.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register_mock("myprov", MockProvider(name="myprov"), tier="fast")
    cmd = _make_cmd()
    cmd._registry = registry
    Path(tmp_yaml).write_text(
        yaml.dump({"test_mode": True, "providers": [{
            "name": "myprov", "protocol": "openai", "default_model": "m",
            "tier": "fast", "enabled": True,
        }]}), encoding="utf-8",
    )
    reply = await cmd.handle("menu myprov", _state())
    assert "closed" in reply.text.lower() or "🟢" in reply.text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the registry through DI in `commands/assembly.py`**

Based on Step 0's findings, add `registry=<the same ProviderRegistry instance already available in that scope>` to the `ProviderCommand(...)` construction call site. Run the app's existing DI/assembly test (find it: `grep -rln "assembly" tests/commands/`) to confirm nothing else broke.

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py src/stackowl/commands/assembly.py && uv run mypy src/stackowl/commands/provider_command.py src/stackowl/commands/assembly.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/commands/provider_command.py src/stackowl/commands/assembly.py tests/commands/test_provider_command.py
git commit -m "feat(commands): live circuit-state badges on /provider list + new /provider status <tier>"
```

---

### Task 13: `/provider edit default_model` live model picker + `cooldown_hours` in edit whitelist

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Test: `tests/commands/test_provider_command.py`

**Interfaces:**
- Consumes: `ModelDiscovery.list_models` (Task 3).
- Produces: `_EDIT_FIELDS` gains `"cooldown_hours"`; `_edit_field`'s `default_model` branch gains a "pick from live models" action; `_edit`'s field-name check accepts `"cooldown_hours"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/commands/test_provider_command.py`:

```python
def _seed_myprov(tmp_yaml: Path) -> None:
    tmp_yaml.write_text(
        yaml.dump({"test_mode": True, "providers": [{
            "name": "myprov", "protocol": "openai", "default_model": "gpt-4o",
            "tier": "fast", "enabled": True, "base_url": None, "api_key": None,
        }]}),
        encoding="utf-8",
    )


def test_edit_menu_offers_cooldown_hours(tmp_yaml: Path) -> None:
    _seed_myprov(tmp_yaml)
    cmd = _make_cmd()
    reply = cmd._edit_menu("myprov")
    assert isinstance(reply, CommandResponse)
    assert any("edit-field myprov cooldown_hours" in a.command for a in reply.actions)


@pytest.mark.asyncio
async def test_edit_field_default_model_offers_live_model_picker(
    tmp_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_myprov(tmp_yaml)
    monkeypatch.setattr(
        "stackowl.providers.model_discovery.list_models",
        AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"]),
    )
    cmd = _make_cmd()
    reply = await cmd.handle("edit-field myprov default_model", _state())
    assert isinstance(reply, CommandResponse)
    assert any("gpt-4o" in a.command for a in reply.actions)


def test_edit_cooldown_hours_persists(tmp_yaml: Path) -> None:
    _seed_myprov(tmp_yaml)
    cmd = _make_cmd()
    reply = cmd._edit("myprov cooldown_hours 12")
    assert reply.startswith("✓ Provider 'myprov' cooldown_hours set to '12'")
    data = _load(tmp_yaml)
    saved = next(p for p in data["providers"] if p["name"] == "myprov")
    assert float(saved["cooldown_hours"]) == 12.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "cooldown_hours or default_model_offers"`
Expected: FAIL — `_edit` rejects `cooldown_hours` as an unknown field; `edit-field default_model` shows only the "Reply with: /provider edit ..." text hint, no live model actions.

- [ ] **Step 3: Implement**

Add `"cooldown_hours"` to `_EDIT_FIELDS` (line 367) and `_EDIT_FIELD_LABELS` (after line 373):

```python
    _EDIT_FIELDS: typing.ClassVar[tuple[str, ...]] = ("protocol", "default_model", "base_url", "cooldown_hours")
    _EDIT_FIELD_LABELS: typing.ClassVar[dict[str, str]] = {
        "protocol": "Edit protocol",
        "default_model": "Edit default_model",
        "base_url": "Edit base_url",
        "cooldown_hours": "Edit cooldown_hours",
        "api_key": "Set token",
        "name": "Rename",
    }
```

Update `_edit`'s field whitelist check (line 530) to accept it:

```python
        if field not in ("protocol", "default_model", "base_url", "cooldown_hours"):
            return (
                f"✗ Unknown field '{field}' — use protocol, default_model, base_url, "
                "or cooldown_hours (tier: /provider set-tier, enabled: /provider enable|disable)"
            )
```

`cooldown_hours` needs numeric coercion before persisting (it's a `float | None` field, but `_edit` currently writes the raw string value directly for all fields — a string `"12"` in YAML would fail `ProviderConfig` validation on next load if pydantic doesn't coerce it; pydantic DOES coerce a numeric string to float by default, so no special-case is needed here — confirm with a quick check: `python -c "from pydantic import BaseModel; print(BaseModel.__class__)"` is unnecessary; trust pydantic's default str→float coercion, already relied on elsewhere in this codebase's YAML-editing commands per `config_helpers.coerce_scalar`). No further change needed in `_edit`'s body beyond the whitelist above.

Make `_edit_field` async (it currently isn't — check `handle()`'s dispatch line for `edit-field`, which already calls it synchronously: `result = self._edit_field(rest)`, line 222) and update the `handle()` branch to await it, since we're adding a live network call for `default_model`:

```python
            elif sub == "edit-field":
                result = await self._edit_field(rest)
```

Update `_edit_field`'s signature and its `field == "default_model"` handling (currently falls through to the generic `current = target.get(field, "?")` branch at line 423) — add a new branch BEFORE that generic one:

```python
    async def _edit_field(self, raw: str) -> str | CommandResponse:
        ...  # (unchanged entry/lookup lines through `back = (...)`)
        if field == "api_key":
            ...  # unchanged
        if field == "name":
            ...  # unchanged
        if field == "default_model":
            from stackowl.exceptions import ModelDiscoveryError
            from stackowl.providers.model_discovery import list_models

            protocol = target.get("protocol", "openai")
            base_url = target.get("base_url") or None
            api_key_ref = target.get("api_key")
            resolved_key = ""
            if api_key_ref:
                from stackowl.config.secret_resolver import SecretResolver
                resolved_key = SecretResolver.resolve(api_key_ref)
            try:
                models = await list_models(protocol, base_url, resolved_key)
            except ModelDiscoveryError:
                models = []
            if models:
                actions = tuple(
                    Action(label=m, command=f"/provider edit {name} default_model {m}", destructive=False)
                    for m in models[:30]
                ) + back
                return CommandResponse(text=f"Current default_model: {target.get('default_model', '?')}\nPick a live model:", actions=actions)
        current = target.get(field, "?")
        text = (
            f"Current {field}: {current}\n"
            f"Reply with: /provider edit {name} {field} <new value>"
        )
        return CommandResponse(text=text, actions=back)
```

(Mark the whole method `async def _edit_field` and adjust its early-return branches accordingly — they don't need `await` themselves, just the enclosing `async def`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py && uv run mypy src/stackowl/commands/provider_command.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): live model picker on /provider edit default_model + cooldown_hours editable"
```

---

## Phase 7 — Telegram button-chain hardening

### Task 14: Expired-button message (widen `CallbackRouter` handler contract with `chat_id`)

**Files:**
- Modify: `src/stackowl/channels/telegram/callbacks.py`, `src/stackowl/channels/telegram/command_buttons.py`, `src/stackowl/channels/telegram/consent.py`, `src/stackowl/channels/telegram/clarify.py`, `src/stackowl/channels/telegram/voice_confirm.py`, `src/stackowl/channels/telegram/approach_rating.py`
- Test: `tests/channels/telegram/` (find the exact existing test file for `command_buttons.py` first: `find tests -iname "*command_button*"`)

**Interfaces:**
- Produces: `_Handler = Callable[[str, str, int | None], Awaitable[None]]` (was 2-arg); all 5 registered handlers gain a `chat_id: int | None` parameter (4 ignore it, `command_buttons.py` uses it for the expired-tap message).

- [ ] **Step 0: Locate the existing command_buttons test file**

Run: `find tests -iname "*command_button*" -o -iname "*callback*telegram*"`. Read it fully before writing new tests, to match its exact fixture/mocking style for `TelegramChannelAdapter`.

- [ ] **Step 1: Write the failing test**

Add to the file found in Step 0 (create `tests/channels/telegram/test_command_buttons.py` if none exists, matching this project's other Telegram test files' mocking conventions):

```python
@pytest.mark.asyncio
async def test_expired_button_sends_user_facing_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.channels.telegram.command_buttons import TelegramCommandButtonResolver

    sent: list[tuple[str, int]] = []

    class _FakeAdapter:
        async def send_text(self, text: str, *, chat_id: int) -> None:
            sent.append((text, chat_id))

    resolver = TelegramCommandButtonResolver(adapter=_FakeAdapter(), registry=None)
    await resolver.handle_callback("cb-1", "cmd:doesnotexist", chat_id=12345)

    assert len(sent) == 1
    assert "expired" in sent[0][0].lower()
    assert sent[0][1] == 12345
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_command_buttons.py -v -k expired`
Expected: FAIL — `TypeError: handle_callback() got an unexpected keyword argument 'chat_id'`.

- [ ] **Step 3: Widen the shared handler contract in `callbacks.py`**

Change the `_Handler` type alias (line 29):

```python
_Handler = Callable[[str, str, "int | None"], Awaitable[None]]
```

In `CallbackRouter.route` (around lines 204-224), extract a best-effort `chat_id` from the callback query right where `cq` is already in scope, and pass it through the dispatch call:

```python
        chat_id: int | None = None
        from_user = getattr(cq, "from_user", None)
        if from_user is not None:
            chat_id = getattr(from_user, "id", None)
        ...
        if handler is None:
            log.telegram.warning(...)
        else:
            try:
                await handler(callback_id, callback_data, chat_id)
            except Exception as exc:
                log.telegram.error(...)
```

(Insert the `chat_id`/`from_user` extraction right after `callback_data: str = cq.data or ""` near the top of `route`, and change only the single `await handler(callback_id, callback_data)` call site to add `, chat_id`.)

- [ ] **Step 4: Update all 5 registered handlers' signatures**

`command_buttons.py::TelegramCommandButtonResolver.handle_callback` (line 173) — this one USES `chat_id`. Change its signature to:

```python
    async def handle_callback(self, callback_id: str, callback_data: str, chat_id: int | None = None) -> None:
```

And in the `entry is None` branch (currently lines 187-193, just `log.telegram.info(...); return`), send the expired message when `chat_id` is available:

```python
        entry = _pop_valid(short_id)
        if entry is None:
            log.telegram.info(
                "[telegram] command_buttons.handle_callback: expired or unknown button",
                extra={"_fields": {"short_id": short_id}},
            )
            if chat_id is not None:
                await self._adapter.send_text(
                    "This step expired — run /provider add to start again.", chat_id=chat_id,
                )
            return
```

The other 4 handlers just need the parameter added and ignored — `consent.py:169`, `clarify.py:48`, `voice_confirm.py:93`, `approach_rating.py:239`. For each, change:

```python
    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
```
to:
```python
    async def handle_callback(self, callback_id: str, callback_data: str, chat_id: int | None = None) -> None:
```

(`approach_rating.py`'s method is named `handle`, not `handle_callback` — same signature change applies to `async def handle(self, callback_id: str, callback_data: str, chat_id: int | None = None) -> None:`.)

- [ ] **Step 5: Run the full Telegram channel test suite**

Run: `uv run pytest tests/channels/telegram/ -v`
Expected: PASS — the new expired-button test passes; every pre-existing consent/clarify/voice/approach-rating callback test still passes (the added parameter has a default, so old call sites with only 2 positional args still work).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/stackowl/channels/telegram/ && uv run mypy src/stackowl/channels/telegram/`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/channels/telegram/callbacks.py src/stackowl/channels/telegram/command_buttons.py \
  src/stackowl/channels/telegram/consent.py src/stackowl/channels/telegram/clarify.py \
  src/stackowl/channels/telegram/voice_confirm.py src/stackowl/channels/telegram/approach_rating.py \
  tests/channels/telegram/test_command_buttons.py
git commit -m "fix(telegram): expired multi-step button now tells the user, instead of a silent no-op"
```

---

## Phase 8 — End-to-end verification

### Task 15: Gateway-driven integration test (full add-flow)

**Files:**
- Test: `tests/journeys/commands/test_provider_command_journey.py` (existing file — read it first to match its exact `CommandRegistry.dispatch` driving pattern and provider/AI-boundary mocking convention)

**Interfaces:**
- Consumes: everything from Phases 1-6.

- [ ] **Step 1: Read the existing journey test file fully**

It already exercises `/provider` through the real dispatch path with only the AI-provider HTTP boundary mocked (per this project's standing "gateway-driven integration test" rule) — mirror its exact setup instead of building a parallel harness.

- [ ] **Step 2: Write the failing test**

Add one new test function to that file (matching its established `dispatch(...)` calling convention) that drives, in order: `add` (browse) → `add-pick <catalog>` → `add-token <catalog> <token>` (mocking `model_discovery.list_models` at the same boundary the file already mocks provider HTTP calls) → `add-model` → `add-tier`, then asserts the new provider is live in a `ProviderRegistry` built from the resulting `stackowl.yaml`.

- [ ] **Step 3: Run to verify it fails, then implement any gaps it surfaces**

Run: `uv run pytest tests/journeys/commands/test_provider_command_journey.py -v`

If it fails for a reason NOT explained by "feature doesn't exist yet" (e.g. a DI wiring gap between this test's harness and `commands/assembly.py`'s real registration), fix that specific gap — do not loosen the test.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/journeys/commands/test_provider_command_journey.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/journeys/commands/test_provider_command_journey.py
git commit -m "test: gateway-driven end-to-end journey for the guided /provider add-flow"
```

---

### Task 16: Telegram button-chain integration test

**Files:**
- Test: `tests/channels/telegram/test_command_buttons.py` (from Task 14)

- [ ] **Step 1: Write the failing test**

Add a test that drives the SAME add-flow sequence as Task 15, but through `TelegramCommandButtonResolver.handle_callback` taps (using `build_command_keyboard`/`register_command_button` to mint each step's `cmd:{short_id}`, mocking `model_discovery.list_models` and the adapter's send methods), confirming each step's callback round-trips to the right next `CommandResponse`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_command_buttons.py -v`

- [ ] **Step 3: Fix any gap it surfaces, then verify it passes**

Run: `uv run pytest tests/channels/telegram/test_command_buttons.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/channels/telegram/test_command_buttons.py
git commit -m "test: Telegram button-chain coverage for the guided /provider add-flow"
```

---

### Task 17: TUI parity check

**Files:**
- Test: find the TUI's existing `CommandResponse`/`Action` button-rendering test (`grep -rln "CommandResponse" tests/tui/`)

- [ ] **Step 1: Read the existing TUI button test file**

Confirm it already renders an arbitrary `CommandResponse(text=..., actions=(...))` generically (it should — the TUI button widget consumes the same `Action` type, per the spec's architecture section). If it's fully generic, add ONE test case using the add-flow's actual `CommandResponse` shapes (from `_add_browse`/`_add_model`) to lock in that no Telegram-specific assumption leaked into `provider_command.py`.

- [ ] **Step 2: Run to verify it passes**

Run: `uv run pytest tests/tui/ -v -k provider`
Expected: PASS. If it fails because the TUI renderer makes an assumption `provider_command.py` violates (e.g. an action count limit, or a button-label length limit), fix `provider_command.py`'s truncation constants (the `[:30]` caps already in Tasks 9-13) to respect it — do not special-case Telegram vs. TUI in `provider_command.py` itself.

- [ ] **Step 3: Commit**

```bash
git add tests/tui/
git commit -m "test: TUI parity check for the guided /provider add-flow's CommandResponse shapes"
```

---

## Final verification

- [ ] Run the full suite: `uv run pytest`
- [ ] Run lint: `uv run ruff check src/`
- [ ] Run type-check: `uv run mypy src/`
- [ ] Update the progress-tracking file's final status line to "All phases complete".
