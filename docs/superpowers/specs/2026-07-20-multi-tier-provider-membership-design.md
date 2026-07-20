# Multi-Tier Provider Membership — Design

## Motivation

Today one configured provider entry serves exactly one routing tier — `ProviderConfig.tier` is a single `Literal["fast", "standard", "powerful", "local"]`. A user who wants the same provider (one API key, one connection) reachable from more than one tier has no way to express that; the only workaround is duplicating the provider under a second name, which duplicates its circuit-breaker/rate-limiter state instead of sharing it. The goal: let one provider genuinely belong to multiple tiers at once.

## What already exists (confirmed by reading the current code)

The single-tier-per-provider constraint is baked into three layers, not just the YAML schema:

- `ProviderConfig.tier: Literal[...]` (`config/provider.py:24`) — one scalar value.
- `ProviderRegistry._tiers: dict[str, str]` (`providers/registry.py:94`) — one tier per provider name, populated in `_build_into`/`apply_settings` and read by `get_by_tier`, `get_with_cascade`, `resolve_tier_with_fallback`, `resolve_capable_or_degrade`.
- `TierSelector.select()` (`providers/tier_selector.py`) — candidate filter is `t == tier` (exact equality against the single stored value).

The bundled catalog's `ProviderEntry.tier` (`setup/provider_catalog.py`, `setup/providers/*.yaml`) is a *separate* concept — it only suggests a starting tier during the guided `/provider add` flow — and is unaffected by this change.

`/provider set-tier`, `/tier add`, `/tier remove`, `/provider list`/`menu`, and `/tier list`/`menu` (all in `commands/provider_command.py` and `commands/tier_command.py`, shipped in the immediately preceding session) all currently assume one tier per provider and need updating.

## Schema

`ProviderConfig.tier: Literal[...]` becomes `ProviderConfig.tiers: tuple[Literal["fast", "standard", "powerful", "local"], ...]`, constrained to at least one entry and no duplicates (pydantic validator). Only the live `stackowl.yaml` schema changes — `setup/provider_catalog.py`'s `ProviderEntry.tier` stays a singular default-suggestion field, not a membership record.

## Migration

A one-time, idempotent startup step, following this codebase's existing self-healing pattern (the wiring audit that already runs at boot): scan `stackowl.yaml`'s `providers:` list for any entry still holding the legacy scalar `tier:` field, rewrite it to `tiers: [<scalar>]` in place via the existing comment-preserving `ruamel.yaml` I/O (`commands/config_helpers.load_yaml`/`save_yaml`), and log what changed. An entry already on `tiers:` is left untouched, so this step is safe to run on every boot indefinitely — no manual CLI step, no separate migration command. Runs early in the startup sequence, before `ProviderRegistry.from_settings()` first reads the file.

## Architecture & data flow

**Registry internals.** `ProviderRegistry._tiers` changes from `dict[str, str]` to `dict[str, tuple[str, ...]]`. Every read site's filter changes from `t == tier` to `tier in t`:
- `get_by_tier`, `get_with_cascade`, `resolve_tier_with_fallback`, `resolve_capable_or_degrade` (all in `providers/registry.py`)
- `TierSelector.select()`'s candidate list comprehension

`_build_into` and `apply_settings` populate `_tiers[name] = config.tiers` (the tuple) instead of the single scalar; the config-equality check `apply_settings` already uses to decide preserve-vs-rebuild (`self._configs.get(name) == config`) needs no change — pydantic's `BaseModel.__eq__` already compares the new `tiers` tuple field correctly once the schema changes.

**Round-robin behavior is unchanged** within a single tier's selection — `TierSelector` still round-robins across every provider whose tier-set contains the requested tier; a provider in multiple tiers simply appears as a healthy candidate in more than one tier's rotation independently.

## Command semantics

- **`/provider set-tier <name> <tier>`** — now **adds** `<tier>` to the provider's `tiers` list (previously: replaced the single value). Idempotent — adding a tier the provider already has is a no-op success, not an error.
- **`/tier add <tier> <name>`** — adds `<tier>` to the list (unchanged from the immediately preceding session's design — already additive).
- **`/tier remove <tier> <name>`** — removes just `<tier>` from the provider's list; the provider keeps routing from any remaining tiers. If `<tier>` is the provider's ONLY tier, the command does NOT remove it from the list (the schema requires at least one entry) — instead it sets `enabled: false` and leaves `tiers` untouched, i.e. disabled-with-its-last-tier-value-intact. This is exactly today's existing convention for a disabled provider (its `tier` field already stays in place when disabled) — unaffected by this change, just re-expressed for a list. Preserves the existing "a provider is always routable somewhere or explicitly off" invariant without ever writing an empty `tiers` list.
- **`/provider add` / the guided catalog add-flow** (`_add_tier` in `provider_command.py`) — UX is unchanged (pick exactly one tier at add time); it now stores that single choice as a one-item `tiers` list instead of a scalar.
- **`/provider list`/`menu`/`status`, `/tier list`/`menu`** — every display site that currently prints one tier prints the joined list instead (e.g. `fast, standard`); `/tier list`'s per-tier membership check (`p.get("tier") == tier`) becomes a containment check (`tier in p.get("tiers", ())`).

## Error handling & edge cases

- **Legacy YAML with a bad/unknown scalar tier value** — the migration step reuses the existing schema validation path; a value that wouldn't have validated under the old `Literal` field still won't validate under the new one, so this surfaces exactly as it does today (loud, at config load), not silently.
- **A provider disabled with an empty-would-be `tiers` list** — never actually stored empty (see `/tier remove` above); the schema's "at least one entry" constraint is a hard backstop even if a future code path tries to write `tiers: []`.
- **Concurrent `/tier add` and `/tier remove` on the same provider** — both go through the existing single `save_yaml`/`_emit_reloaded` write path already used by every other `/provider`/`/tier` mutation; no new concurrency concern beyond what already exists there.
- **`resolve_capable_or_degrade`'s capability-order substitution** — unaffected in logic; a provider that belongs to multiple tiers is now correctly considered a candidate for each of them independently, which is the intended behavior, not an edge case to guard against.

## Testing

- **Unit tests**: `ProviderConfig.tiers` validation (at least one, no duplicates, rejects unknown tier values — mirrors existing `tier` Literal tests); the startup migration step (legacy scalar → list rewrite, idempotent on already-migrated entries, preserves YAML comments); `TierSelector.select()` correctly treats a provider present in two tiers' candidate pools as independently selectable from each.
- **Regression**: every existing single-tier-per-provider test (registry, tier selector, `/provider`, `/tier`) must keep passing — a provider with `tiers: [fast]` (the migrated shape of today's `tier: fast`) must route byte-identically to before.
- **New behavior tests**: `/provider set-tier` twice with different tiers results in a 2-item list, not a replace; `/tier add` on an already-member tier is a no-op success; `/tier remove` on a provider's only tier disables it without emptying `tiers`; `/tier remove` on one of several tiers leaves the provider enabled and routable from the rest.
- **Integration**: extend the existing gateway-driven `/provider`+`/tier` journey tests to cover a provider ending up in two tiers via `/tier add` twice, and confirm `ProviderRegistry.get_with_cascade` for BOTH tiers resolves to it.

## Non-goals / explicitly out of scope

- Per-tier weighting or priority when a provider belongs to multiple tiers — membership is binary (in the tier's pool or not); round-robin fairness within a tier is unaffected and not being redesigned here.
- Changing the bundled catalog's `ProviderEntry.tier` (add-time suggestion) to a list — out of scope, unrelated concept.
- A manual/explicit migration CLI command — the automatic idempotent startup rewrite is the only migration path (per your stated preference for a real one-time file rewrite over an in-memory-only shim).
