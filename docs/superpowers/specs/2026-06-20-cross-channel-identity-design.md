# Cross-Channel Identity Unification — Design

**Date:** 2026-06-20
**Status:** Approved (design); implementation deferred to a fresh session.
**Author:** brainstormed with Boss.

## Problem

StackOwl is a single-user personal assistant, but the same person reaches it from
multiple channels (Telegram, Slack, CLI). Durable knowledge the user expects to
"follow them" does not, because two different scoping keys were conflated:

- **`owner_id`** — the tenancy *principal*. Always `DEFAULT_PRINCIPAL_ID`
  (`"principal-default"`). **Already unified across every channel.**
- **`owner_key` / `session_id`** — a *per-channel* handle
  (`telegram:{chat_id}`, `slack:{hash}`, `local` for CLI). **Splits the user
  per channel.**

Most durable stores scope only by `owner_id`, so they are already cross-channel:
reflections (`owner_id` + `owl_name`), durable tasks (`owner_id`), skills,
DNA checkpoints. **Two stores are wrongly per-channel:**

1. **`PreferenceStore`** (`src/stackowl/memory/preferences.py`) — queries filter by
   the per-channel `owner_key`. Its own module docstring says preferences should
   "propagate across all channels for the same owner" — the code contradicts the
   intent. `classify._gather_preferences(session_id)` passes the per-channel
   `session_id` as the key.
2. **Extracted long-term facts** — `fact_extractor` stages facts with
   `source_ref = session_id` (per-channel) into `staged_facts`. Conversation turns
   live in the same table, distinguished by `source_type` (`'conversation'` vs
   fact types).

## Goal & Non-Goals

**Goal:** durable knowledge (preferences + extracted facts) follows the user across
channels, keyed on a stable per-person identity.

**Non-goals (explicitly out of scope):**
- Multi-*person* isolation / full multi-tenancy. The `owner_id` principal layer is
  untouched. (This was considered and ruled out: the system is single-user by
  design — `principal.py` — and building per-person isolation would be a
  speculative, data-risky epic with no current consumer.)
- Unifying **live conversation** across channels. Conversation history stays
  per-channel (`source_ref = session_id`) by deliberate choice — no interleaving of
  a Telegram thread into Slack.
- Auto-minting identities. Mapping is explicit config (see below).

## Approach

Introduce a stable **`identity_key`** resolved at the gateway from the inbound
channel handle via an **explicit config alias map**. It is the user's personal
assistant; the set of their handles is small and known, so an explicit map is
simpler and safer than auto-mint (no orphan risk, no identity-spoofing surface).

```
# stackowl.config.json
"identity": {
  "aliases": {
    "owner-primary": ["telegram:12345", "slack:U0ABC", "local"]
  }
}
```

Resolution rule: handle → the `identity_key` whose alias list contains it; if no
alias matches, `identity_key` **falls back to the handle itself** — so an
unconfigured deployment is byte-for-byte identical to today's behavior.

### Components (boundaries)

1. **`IdentityResolver`** (new, `src/stackowl/tenancy/identity.py`)
   - `resolve(channel_handle: str) -> str` — pure function over the loaded alias
     map; returns the canonical `identity_key` or the handle unchanged.
   - One responsibility: handle → identity. Independently testable, no I/O.

2. **`PipelineState.identity_key: str`** (new field)
   - Set at the gateway when the turn's state is built, alongside `session_id`.
   - Distinct from `session_id` (which stays per-channel for conversation/delivery).
   - Default = `session_id` value when unresolved, preserving current behavior.

3. **`PreferenceStore`** — accept and filter on `identity_key` instead of the
   per-channel `owner_key`. Call sites: `classify._gather_preferences` (pass
   `state.identity_key`), and the preference get/set tool paths.

4. **Fact store split** — durable facts staged/retrieved with
   `source_ref = identity_key`; conversation rows keep `source_ref = session_id`.
   The `source_type` discriminator already separates them, so retrieval for facts
   queries `source_ref = identity_key AND source_type != 'conversation'` (or the
   explicit fact types), and conversation retrieval is unchanged.

### Data flow

```
inbound (channel, chat_id) → handle "telegram:123"
        │  GatewayScanner / dispatch
        ▼
IdentityResolver.resolve("telegram:123") → "owner-primary"
        ▼
PipelineState(session_id="telegram:123", identity_key="owner-primary", ...)
        ▼
preferences keyed on identity_key   facts.source_ref = identity_key
conversation source_ref = session_id (unchanged, per-channel)
```

### Migration

One forward migration re-keys the **existing single user's** per-channel rows to the
canonical identity:
- `user_preferences`: rows under the primary owner's per-channel `owner_key`(s) →
  the configured `identity_key`. If no identity config exists at migrate time, the
  migration is a no-op (idempotent).
- `staged_facts` (fact rows only, `source_type != 'conversation'`): `source_ref`
  per-channel → `identity_key`.
- `owner_id` is never touched (zero orphan risk). The migration is owner-scoped and
  reversible.

> Open implementation detail for the plan: whether the migration reads the identity
> map from config at migrate-time, or ships as a CLI re-key command run after the
> operator sets `identity.aliases`. The plan should pick the latter if config is not
> reliably available inside the migration runner — a `stackowl identity link` command
> is cleaner and explicit. Decide in the plan.

## Error handling

- Unresolvable / unconfigured handle → `identity_key = handle` (graceful, identical
  to today). No raise.
- Malformed `identity.aliases` config → log a loud `error`, fall back to
  handle-as-identity for all turns (degrade, never crash; no hidden error).

## Testing

Gateway journeys (mock only the provider; assert on assembled context / store state):
1. **Cross-channel preference:** set a preference on a `telegram:*` turn; read it
   back on a `slack:*` turn mapped to the same `identity_key` → present.
2. **Cross-channel fact:** a fact learned on one channel surfaces in the assembled
   context on the other.
3. **Conversation does NOT cross (negative control):** conversation history from the
   Telegram turn is absent from the Slack turn's assembled history.
4. **Unconfigured = byte-identical:** with no `identity.aliases`, `identity_key`
   equals `session_id` and all behavior matches pre-change.
5. **Migration test:** seed per-channel preference + fact rows, run the re-key,
   assert they resolve under the new identity and conversation rows are untouched.
6. **`IdentityResolver` unit tests:** mapped handle, unmapped handle, malformed map.

## Scope summary

New: `tenancy/identity.py`, one `PipelineState` field, one migration (or
`identity link` CLI), ~3 journey tests + unit tests.
Changed call-sites: gateway state construction, `_gather_preferences`, preference
get/set paths, fact stage/retrieve.
Untouched: `owner_id` principal layer, reflections/tasks/skills/DNA stores, live
conversation/delivery.
