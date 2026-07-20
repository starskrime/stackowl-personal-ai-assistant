# Multi-Provider Catalog & Tier Orchestration — Design

## Motivation

StackOwl routes AI calls through tiers (`fast`/`standard`/`powerful`/`local`), each backed by one configured provider today. The goal: let a user attach *many* providers — including free-tier ones from the ~100 AI providers on the market — to a single tier, with the platform choosing among them and backing off cleanly when one is unavailable or quota-exhausted. Adding a provider today requires typing its protocol, base URL, and exact model id by hand via `/provider add`; the goal is a guided, catalog-driven flow with equally good UX on Telegram and the TUI.

## What already exists (confirmed by reading the current code)

This is materially closer to the goal than it first appears:

- `ProviderRegistry._tiers` is a `name → tier` map — **multiple providers can already share a tier**. `get_with_cascade()` already iterates every provider in a tier, skips any with an OPEN `CircuitBreaker`, and only falls through to the next tier when the whole tier is unavailable. The gap is *selection quality* (first config-order match), not tier-sharing capability.
- `ProviderConfig.protocol` is `openai | anthropic | gemini | grok`; any OpenAI-compatible provider (Groq, Together, Mistral, etc.) already just uses `protocol: openai` + a custom `base_url` — no new protocol type is needed per provider.
- Per-provider `CircuitBreaker` (3-state, adaptive half-open backoff capped at 900s) and `RateLimiter` (token bucket, 429-aware penalty) already exist. Config hot-reload (`apply_settings`) is already wired — `/provider` changes apply without a restart.
- `/provider` already supports `list/add/remove/set-tier/edit/enable/disable/set-token/rename`, rendered as buttons via `CommandResponse`/`Action` on both Telegram and TUI.
- `channels/telegram/command_buttons.py` already solves Telegram's 64-byte `callback_data` limit generically: every `Action`'s full command string — however long — is stashed under a random short id, and only `cmd:{short_id}` crosses the wire. **A live-queried model id or search result embedded in a button command needs no new plumbing.**
- **A provider catalog already exists**: `stackowl.setup.provider_catalog.ProviderCatalog`/`ProviderEntry`, used today by `stackowl setup --minimal`. 15 bundled entries live as one YAML file each under `src/stackowl/setup/providers/*.yaml` (name/label/protocol/base_url/default_model/models/tier/needs_api_key/is_local/key_url), merged with user overrides from `~/.stackowl/providers/*.yaml`. This is the same shape this design needs — it gets *extended*, not duplicated.

## Architecture & data flow

Two small new modules, plus an extension of the existing catalog, sit alongside the existing `providers/`/`commands/`/`setup/` code:

- **`ProviderCatalog` (extended, `setup/provider_catalog.py`)** — gains an optional `category: tuple[str, ...] = ()` field on `ProviderEntry` (default empty, so all 15 existing bundled YAML files parse unchanged) and two new methods, `search(query)` and `browse(category=None)`, operating over `cls.load()`. The bundled YAML directory (`setup/providers/*.yaml`, one file per provider) is where the research pass adds more of the ~100-provider market — same file-per-provider convention, not a new consolidated file. `commands/provider_command.py` imports this catalog directly (no new dependency direction: `setup/provider_catalog.py` has no import back on `commands/`).
- **`ModelDiscovery`** (`providers/model_discovery.py`) — `list_models(protocol, base_url, api_key) -> list[str]`, dispatching by protocol the same way `_build_provider` already does. This same call doubles as token validation.
- **`TierSelector`** (`providers/tier_selector.py`) — `select(tier, providers, tiers, breakers) -> str | None`, a round-robin cursor per tier. `ProviderRegistry.get_with_cascade` delegates to this instead of its current inline first-match loop; a single-provider tier's behavior is unchanged.

**Add-flow data flow:** `/provider add` (no args) → catalog search/category browse buttons → user picks an entry → prompt for a token if the entry requires one (skip if keyless) → `ModelDiscovery` fires, validating the key in the same call → live model list rendered as buttons → user picks a model + tier → confirm → `ProviderConfig` is built and saved through the existing `store_secret` + `save_yaml` + `_emit_reloaded` path. Every step is a normal slash-command invocation whose buttons embed the next full command — the same convention as today's `menu`/`edit-menu` — so no new server-side wizard/session state is needed.

**Routing data flow:** externally unchanged — `get_with_cascade(tier)` still returns a provider or raises `AllProvidersUnavailableError`. Internally, "which of N healthy providers in this tier" is now `TierSelector`'s job instead of dict-order first-match.

**Quota data flow:** a provider call fails → existing failure classification runs → if the response carries a parseable reset signal (header/body), `CircuitBreaker` opens for exactly that duration → else it opens for the provider's configured `cooldown_hours` (new optional YAML field) if set → else it falls back to today's generic threshold-based OPEN with capped half-open backoff. No behavior change for a provider that sets neither.

## Full lifecycle UX (Telegram-first, TUI-identical)

- **`/provider add`** (no args) → catalog search/category browse.
- **`/provider add <query>`** → jumps straight to filtered catalog results.
- Existing **positional** `/provider add <name> <protocol> <model> <tier> ...` keeps working unchanged (power users, tests, back-compat).
- **`/provider list`** gains a live status badge per provider (closed / half-open / open+retry-after), read from the registry's `CircuitBreaker`/`RateLimiter` — not just the static YAML fields it shows today.
- **`/provider menu <name>`** gains a "Status" line: live circuit state, quota-cooldown reason/until if applicable, and which tier round-robin slot it's in.
- **New `/provider status [tier]`** — every provider in a tier with live health, so it's clear why the router picked (or skipped) a given one.
- **`/provider edit <name> default_model`** gains a "pick from live models" button, reusing `ModelDiscovery`, instead of requiring a typed model id.
- `remove` / `enable` / `disable` / `set-tier` / `rename` / `set-token` are already full-lifecycle and already button-driven — unchanged.

## Schema changes

`ProviderConfig` gains one new optional field:

```python
cooldown_hours: float | None = None
```

New/changed catalog entry shape (`setup/provider_catalog.py::ProviderEntry`, one bundled YAML file per provider under `setup/providers/`, populated and refreshed via a research pass, not hand-typed):

```yaml
name: groq
label: Groq
protocol: openai          # anthropic | openai | gemini | grok
base_url: https://api.groq.com/openai/v1
default_model: llama-3.3-70b-versatile
needs_api_key: true
category: [free-tier, fast-inference]   # NEW optional field
key_url: https://console.groq.com/keys
```

`CircuitBreaker` gains an `open_for(seconds)` entry point (distinct from the existing failure-counted path) — used when a quota/rate response carries a parseable reset time, or when a provider has no reset signal but has `cooldown_hours` configured. Absent both, behavior is byte-identical to today. The reset-header parsing and the decision to call `open_for()` vs. the default failure path live at the same call site that already classifies a RATE_LIMIT failure and calls `RateLimiter.penalize()` today (the provider round's exception handling) — not inside `CircuitBreaker`/`RateLimiter` themselves, which stay generic.

`cooldown_hours` is editable like any other provider field: `/provider edit <name> cooldown_hours <value>` (added to `_edit`'s existing field whitelist alongside `protocol`/`default_model`/`base_url`).

## Error handling & edge cases

- **Catalog file missing/corrupt at startup** — logged, treated as an empty catalog (browse/search return nothing); the existing positional `/provider add` keeps working regardless. A broken catalog degrades UX, never breaks provider management.
- **`ModelDiscovery` failure during add** (bad token, unreachable host, timeout) — the add-flow reports the specific reason (auth vs. connection vs. timeout) and re-prompts for the token without losing the catalog/protocol/base_url already chosen.
- **Catalog entry drift** (a stale `base_url`) — surfaces as the same live-validation failure above; a wrong catalog entry fails loudly at add-time rather than silently misrouting later.
- **Quota-reset parsing is defensive** — a response that doesn't match the expected header/body shape falls back to the `cooldown_hours` config, then to today's generic breaker. A parse bug can never crash a round.
- **Round-robin cursor concurrency** — `TierSelector`'s per-tier index is guarded the same way `RateLimiter`/`CircuitBreaker` already guard their mutable state (per-instance `asyncio.Lock`, cheap sync reads); benign staleness under a race is acceptable, matching the existing `CircuitBreaker.state` precedent.
- **Multi-step flow outliving the button TTL** (existing 15-minute expiry in `command_buttons.py`): today a tap on an expired button is a silent no-op. Because the guided add-flow chains several taps, this is more likely to bite here than in today's single-tap flows. Targeted fix included in this work: an expired/unknown tap replies "This step expired — run `/provider add` to start again" instead of doing nothing.

## Testing

- **Unit tests per new module**: `ProviderCatalog.search`/`browse` (match, category filter, empty/corrupt file); `ModelDiscovery.list_models` (one test per protocol dispatch, HTTP layer mocked); `TierSelector.select` (round-robin sequencing, skip-on-OPEN, concurrent-call race safety, empty-tier fallthrough); `CircuitBreaker.open_for` + `cooldown_hours` fallback + reset-header parsing (valid/malformed/absent header × with/without configured `cooldown_hours`).
- **Regression**: `ProviderRegistry.get_with_cascade`/`get_by_tier` behavior for a single-provider-per-tier config must stay byte-identical; existing tests pass unmodified.
- **Gateway-driven integration test** (mandatory for this project): drive the full add-flow through the real `CommandRegistry.dispatch` path end-to-end — browse → pick → token → validate → model pick → tier → confirm → provider live in `ProviderRegistry` — mocking only the AI-provider HTTP boundary.
- **Telegram button-chain test**: exercise the same flow through `TelegramCommandButtonResolver`/`command_buttons.py` (mocked Telegram transport), including the new expired-button message.
- **TUI parity**: confirm the identical `CommandResponse`/`Action` sequence renders correctly through the TUI's button widget path.
- **Lint/type-check**: `ruff check src/` and `mypy src/` stay green; new modules ship fully typed (strict mypy).

## Non-goals / explicitly out of scope

- Per-tenant/per-owl provider catalogs — providers remain a single global list in `stackowl.yaml`, as today.
- Cost-aware or priority-ranked selection within a tier — round-robin only (per decision above); either could be added later as an alternate `TierSelector` strategy without touching the registry's call site.
- A general-purpose multi-step wizard/flow engine — the add-flow reuses the existing "button embeds next command" convention; a generic engine is deferred until a second feature actually needs the same shape.
- Fixing the platform-wide silent-expiry behavior for *every* button flow — only the add-flow's expired-tap path gets a user-facing message as part of this work; other existing single-tap flows are unchanged.
