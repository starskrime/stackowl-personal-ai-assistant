# Per-Model Provider Configuration — Design

## Motivation

A `ProviderConfig` entry today is one API key/base_url/protocol connection with exactly one `default_model`. Using a second model on the same backend and key (e.g. a gateway that serves several models behind one virtual key) requires duplicating the entire provider block — same `base_url`, same `api_key` reference, same `protocol` — just to change `default_model`. That duplication was raised directly by the operator while investigating a related bug (a flat `max_output_tokens`/context-window ceiling shared across everything under one provider) and is real, confirmed pain: real config drift risk (two blocks that should share a key/URL slowly diverging), and no way to give two models under the same connection independent context/output-token budgets.

The goal: let one provider connection host multiple models, each independently routable by tier and independently able to override its context/output-token budget, without duplicating the connection-level config.

## What already exists (confirmed by reading the current code)

- `ProviderConfig.default_model: str` (`config/provider.py:23`) — exactly one model per provider entry, referenced at ~28 call sites across `providers/openai_provider.py`, `providers/anthropic_provider.py`, `providers/gemini_provider.py`, `setup/minimal.py`, `cli/providers_cli.py`, and the pipeline (`pipeline/steps/execute.py`, `pipeline/steps/assemble.py`).
- `ProviderConfig.tiers: tuple[...]` (multi-tier-per-provider, shipped earlier today) — tier membership lives on the PROVIDER, not per-model.
- `ProviderConfig.max_output_tokens: int = 250000` and `ProviderConfig.context_chars: int | None` — both flat, provider-level. `OpenAIProvider._output_cap(resolved_model)` (`providers/openai_provider.py:959-982`) computes `min(cached_window(provider, model), self._config.max_output_tokens)` — model-aware on the WINDOW side (probed per (provider, model) pair, `providers/model_window.py`'s `_WINDOW_CACHE` is already keyed by `(provider_name, model)`), but the `max_output_tokens` ceiling it's bounded by is NOT model-aware — one flat value for every model a provider might ever serve.
- `providers/model_window.py`'s `resolve_window`/`cached_window` are ALREADY keyed by `(provider_name, model)` tuples, not provider name alone — the per-model plumbing exists at this layer already; the config surface and the routing layer above it don't yet expose or use that granularity.
- `ProviderRegistry` (`providers/registry.py`) resolves a tier to a PROVIDER object; every call site (`pipeline/steps/execute.py`'s `_open_stream`, etc.) then calls `provider.stream(messages, model="")` — always empty, always resolving to `self._config.default_model` inside the provider. There is no existing path for a tier to resolve to a *specific model* on a provider that might serve several.
- `/provider` and `/tier` (`commands/provider_command.py`, `commands/tier_command.py`) — both shipped earlier today with multi-tier-per-provider support, DI-command pattern, guided browse-then-execute button flows (`_add_browse`/`_add_execute`), `config_helpers.load_yaml`/`save_yaml`.

## Schema

`ProviderConfig` keeps `default_model` and its own `tiers` exactly as they work today — zero change, zero migration risk for the ~60+ existing single-model call sites. A new optional field lists *additional* models sharing the same provider connection:

```python
class ModelOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tiers: tuple[Literal["fast", "standard", "powerful", "local"], ...]
    max_output_tokens: int | None = None   # None = inherit provider's value
    context_chars: int | None = None       # None = inherit provider's value

    @field_validator("tiers")
    @classmethod
    def _validate_tiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("a model's tiers must contain at least one entry")
        if len(set(value)) != len(value):
            raise ValueError(f"a model's tiers must not contain duplicates: {value}")
        return value


class ProviderConfig(BaseModel):
    ...
    default_model: str            # unchanged — the provider's "model #0"
    tiers: tuple[...]             # unchanged — default_model's own tier membership
    models: tuple[ModelOverride, ...] = ()   # NEW — additional models, empty by default

    @field_validator("models")
    @classmethod
    def _validate_models(cls, value: tuple[ModelOverride, ...], info: ValidationInfo) -> tuple[ModelOverride, ...]:
        names = [m.name for m in value]
        default = info.data.get("default_model")
        if default is not None and default in names:
            raise ValueError(f"model name '{default}' collides with default_model")
        if len(set(names)) != len(names):
            raise ValueError(f"models must not contain duplicate names: {names}")
        return value
```

A provider with an empty `models` tuple (every existing config, on load) behaves exactly as today. A provider that wants a second model appends one `ModelOverride` entry instead of duplicating the whole provider block.

## Migration

None needed. `models` defaults to `()`; existing YAML with only `default_model`/`tiers` loads unchanged. A `models` list only appears when an operator explicitly runs `/provider add-model` or hand-edits the YAML. This mirrors the additive-default pattern used for `ProviderConfig.tiers` earlier today, but is simpler — there is no legacy scalar shape to normalize, since `models` is wholly new.

## Architecture & routing

Two layers change so a tier lookup can resolve to a specific model, not just a provider connection:

**1. `ProviderRegistry`** currently maps `tier -> provider name(s)`. It enumerates *routable units* instead: for each enabled provider, one unit for `(provider_name, default_model, tiers)`, plus one more per enabled entry in `models`. `get_by_tier`/`get_with_cascade`/`resolve_tier_with_fallback`/`resolve_capable_or_degrade` currently return a bare `ModelProvider`; they return `(ModelProvider, model_name)` instead — the connection to use, and which model string to request on it. `TierSelector`'s round-robin candidate pool becomes a pool of `(provider_name, model_name)` pairs instead of provider names alone, so a provider serving two models in the same tier round-robins between them independently of any OTHER provider in that tier.

**2. Every call site** that calls `provider.stream(messages, model="")` (`pipeline/steps/execute.py`'s `_open_stream` and the mirror site in `assemble.py`) threads the resolved `model_name` through instead of always passing empty — so a request routed to a provider's second model actually requests that model, not silently falling back to `default_model`.

**3. Per-model override resolution inside each provider class** (`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`): a small shared helper, `_resolve_model_config(model_name) -> (max_output_tokens, context_chars)`, checks `self._config.models` for an entry matching `model_name` with a non-`None` override; falls back to `self._config.max_output_tokens`/`context_chars` (today's provider-level values) otherwise. `_output_cap(resolved_model)` and the context-window resolution call this helper instead of reading `self._config.max_output_tokens` directly. `providers/model_window.py`'s `resolve_window`/`cached_window` need NO change — already keyed by `(provider_name, model)`.

Circuit breakers, cost tracking, and secret resolution stay keyed by provider name, unchanged — a model-routing failure still counts against the SAME provider-level circuit breaker (one connection, shared health), not a per-model one. Only tier resolution and per-request `max_tokens` sizing become model-aware.

## Command surface

Extends `/provider` (same DI-command, `load_yaml`/`save_yaml`, button-driven pattern as today's `/provider`/`/tier`) with model management, and makes `/tier add`/`/tier remove` model-aware without breaking existing usage.

**New `/provider` subcommands:**
- `/provider models <name>` — list a provider's models: `default_model` plus every `models[]` entry, each showing its tiers and any output-token/context overrides (inherited values shown as "(inherited)").
- `/provider add-model <name>` — **guided button flow**, matching `/provider add`'s existing pattern: if `<name>` is omitted, browse configured providers via buttons first; then prompt for the new model's name; then a tier-pick button row (mirroring `_add_tier`'s guided step). Ends by writing a new `ModelOverride` entry with that one tier.
- `/provider remove-model <name> <model_name>` — remove a model from `models[]` (the provider's own `default_model` is not removable this way — edited via the existing `/provider edit-menu`).
- `/provider set-model-tokens <name> <model_name> <max_output_tokens>` / `/provider set-model-context <name> <model_name> <context_chars>` — per-model overrides. Passing the literal value `inherit` clears the override back to `None` (falls back to the provider's own value); any positive integer sets an explicit override.

**`/tier add` / `/tier remove` — backward-compatible extension:**
- `/tier add <tier> <provider>` (2-arg, unchanged) — still operates on the provider's own `default_model`, byte-identical to today.
- `/tier add <tier> <provider> <model_name>` (new 3-arg form) — operates on a specific model under that provider.
- Same 2-arg/3-arg split for `/tier remove`. Existing 2-arg usage keeps working unchanged.

`/provider menu <name>` and `/tier list`/`menu` displays get a per-model breakdown for any provider with `models[]` entries; a provider with none renders exactly as it does today.

## Error handling & edge cases

- A `ModelOverride.name` colliding with `default_model` or another `models[]` entry is rejected at config-load (loud `ValueError`, same as the existing duplicate-tiers check).
- Removing the LAST tier from a model entry (via `/tier remove <tier> <provider> <model_name>`) disables that model entry the same way removing a provider's last tier disables the whole provider today (`enabled` flip, tiers left intact) — same "always routable-or-explicitly-off" invariant, at model granularity.
- If every model under a provider (including `default_model`) ends up disabled, the provider is simply unroutable for any tier — same as today's existing "no provider serves this tier" degraded-fallback path in `ProviderRegistry.get_by_tier`, unchanged.
- A provider-level circuit-breaker trip (e.g. rate limit) affects ALL models under that provider equally — models share one connection's health state, by design (see Architecture).

## Testing

- **Unit**: `ModelOverride` validation (duplicate name vs. `default_model`, duplicate name within `models`, empty/duplicate tiers on a model entry); the per-model override-resolution helper (falls back to provider value when a model's override is `None`, uses the model's own value when set); `ProviderRegistry` routing — a provider with 2 models in different tiers resolves each tier to the correct `(provider, model)` pair independently; a provider with 2 models in the SAME tier round-robins between them.
- **Regression**: every existing single-model provider test (registry, tier selector, `/provider`, `/tier`, `_output_cap`) must keep passing byte-for-byte — an empty `models` tuple is a no-op at every layer touched.
- **Integration**: a journey test mirroring today's two-tier-provider test, but across two MODELS of the same provider — `/provider add-model` guided flow end-to-end, then `get_with_cascade` for each tier resolves to the correct model name, then a mocked `stream()` call records the model-specific `max_tokens` it received.

## Non-goals

- Per-model overrides for anything beyond context/output-token budget (temperature, tool support, timeout, etc.) — out of scope per the original ask; can be added later the same additive way if needed.
- A model-selection strategy beyond tier-based round-robin (cost-based, latency-based, capability-based picking among same-tier models) — `TierSelector`'s existing round-robin behavior applies unchanged once it selects `(provider, model)` pairs instead of provider names alone.
- Per-model circuit breakers or cost tracking — both stay provider-scoped, matching the shared-connection reality (one API key/one rate limit covers every model on it).
