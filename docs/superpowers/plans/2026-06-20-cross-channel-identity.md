# Cross-Channel Identity Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make durable knowledge (preferences + extracted facts) follow one user across Telegram/Slack/CLI by keying it on a stable, config-mapped `identity_key` instead of the per-channel `session_id`/`owner_key`.

**Architecture:** A pure `IdentityResolver` maps an inbound channel handle to a canonical `identity_key` via an explicit config alias map (unmapped → handle unchanged, so unconfigured deployments are byte-identical). The gateway stamps `identity_key` onto `PipelineState`. Preference and extracted-fact reads/writes switch from the per-channel key to `identity_key`; the `owner_id` principal layer and live conversation (`source_ref=session_id`) are untouched. A `stackowl identity link` CLI re-keys the existing user's rows.

**Tech Stack:** Python ≥3.12, pydantic frozen models, pytest (`uv run pytest`), mypy strict, ruff, SQLite via `DbPool`, config via `stackowl.config.json` + the config loader/Settings.

## Global Constraints

- Run from repo root: `uv run pytest <path>`, `uv run ruff check src/`, `uv run mypy src/`.
- Subagent-driven TDD: failing test → confirm fail → minimal impl → confirm pass → commit. One logical change per commit; tree stays green/bisectable.
- **Unconfigured = byte-identical**: with no `identity.aliases`, `identity_key` MUST equal the per-channel handle/`session_id`, and every existing test MUST stay green unchanged.
- Gateway journeys assert on assembled context / store state handed to/observed at the provider mock, never on model output text.
- Absence/negative tests arm the gun + positive control (conversation-does-not-cross needs a proof conversation CAN appear on its own channel).
- No hidden errors: every `except` logs via `log.<module>.error(...)`; malformed config degrades to handle-as-identity, never crashes.
- Do NOT touch the `owner_id` principal layer, or the already-unified stores (reflections/tasks/skills/DNA).
- `PipelineState` is `frozen=True`; mutate via `state.evolve(**kwargs)`.
- Check existing before writing new: reuse the config loader, `PrincipalStore`, the scripted-provider journey fixtures, the migration runner.

**Implementer pre-flight lookups (cheap, do before Task 1 — the plan's code assumes these; confirm exact names):**
- Config load path: how `stackowl.config.json` is loaded and surfaced (`src/stackowl/config/loader.py`, `config/settings.py`) and the idiomatic way to add an `identity` section.
- `PreferenceStore` full API (`src/stackowl/memory/preferences.py`): exact `get`/`set`/list signatures and the `owner_key` parameter name.
- `_gather_preferences` body (`src/stackowl/pipeline/steps/classify.py:89`) and how it calls the store.
- Where `PipelineState` is constructed at the gateway for an inbound turn (dispatch/backend entry) — the site that sets `session_id`; that is where `identity_key` gets set.
- `fact_extractor` stage call (`src/stackowl/memory/fact_extractor.py:170` `source_ref=session_id`) and the fact-retrieval path in `sqlite_bridge.py` (the `source_type`/`source_ref` SELECTs), to switch fact rows (not conversation rows) to `identity_key`.
- Latest migration number under `src/stackowl/db/migrations/` (next is likely `0065`).

---

### Task 1: `IdentityResolver` + config surface

**Files:**
- Create: `src/stackowl/tenancy/identity.py`
- Modify: config loader/Settings to expose `identity.aliases` (confirm exact module in pre-flight)
- Test: `tests/tenancy/test_identity.py`

**Interfaces:**
- Produces: `IdentityResolver(aliases: dict[str, list[str]])` with `resolve(handle: str) -> str`; module function `load_identity_resolver() -> IdentityResolver` that reads config (empty map when absent).

- [ ] **Step 1: Write the failing test**
```python
from stackowl.tenancy.identity import IdentityResolver


def test_mapped_handle_resolves_to_identity() -> None:
    r = IdentityResolver({"owner-primary": ["telegram:123", "slack:U0ABC", "local"]})
    assert r.resolve("telegram:123") == "owner-primary"
    assert r.resolve("slack:U0ABC") == "owner-primary"


def test_unmapped_handle_returns_itself() -> None:
    r = IdentityResolver({"owner-primary": ["telegram:123"]})
    assert r.resolve("telegram:999") == "telegram:999"  # unconfigured = identity behavior


def test_empty_map_is_identity() -> None:
    assert IdentityResolver({}).resolve("slack:x") == "slack:x"


def test_malformed_alias_value_degrades_not_crashes() -> None:
    # A non-list alias value must not crash resolution.
    r = IdentityResolver({"bad": "telegram:123"})  # type: ignore[dict-item]
    assert r.resolve("telegram:123") == "telegram:123"
```

- [ ] **Step 2: Run, verify fail**
Run: `uv run pytest tests/tenancy/test_identity.py -v` — FAIL (module missing).

- [ ] **Step 3: Implement**
```python
"""IdentityResolver — map a per-channel handle to a stable cross-channel identity.

Single-user assistant: the same person reaches it from several channels. An
explicit alias map (config, not auto-mint) collapses their handles to one
``identity_key`` so durable knowledge follows them. Unmapped handle → itself,
so an unconfigured deployment is byte-identical to per-channel behavior.
"""
from __future__ import annotations

from stackowl.logger import log


class IdentityResolver:
    def __init__(self, aliases: dict[str, list[str]]) -> None:
        # Invert {identity: [handles]} → {handle: identity}; tolerate malformed
        # values (log + skip) so one bad config row can't break resolution.
        self._handle_to_identity: dict[str, str] = {}
        for identity, handles in (aliases or {}).items():
            if not isinstance(handles, list):
                log.engine.error(
                    "[identity] malformed alias value — skipping",
                    extra={"_fields": {"identity": identity}},
                )
                continue
            for h in handles:
                self._handle_to_identity[str(h)] = identity

    def resolve(self, handle: str) -> str:
        return self._handle_to_identity.get(handle, handle)
```
Add `load_identity_resolver()` reading the config `identity.aliases` (empty dict when the section is absent), per the loader confirmed in pre-flight.

- [ ] **Step 4: Run, verify pass.** `uv run pytest tests/tenancy/test_identity.py -v` → PASS.
- [ ] **Step 5: Commit.** `git add src/stackowl/tenancy/identity.py tests/tenancy/test_identity.py <config files>` → `feat(tenancy): IdentityResolver maps channel handles to a stable identity_key`.

---

### Task 2: `PipelineState.identity_key` + gateway resolution

**Files:**
- Modify: `src/stackowl/pipeline/state.py` (new field)
- Modify: the gateway/dispatch site that builds `PipelineState` for an inbound turn (confirmed in pre-flight)
- Test: `tests/pipeline/test_identity_key_wiring.py`

**Interfaces:**
- Consumes: `IdentityResolver.resolve` (Task 1).
- Produces: `PipelineState.identity_key: str` (defaults to `""`; when empty, consumers fall back to `session_id`).

- [ ] **Step 1: Write the failing test** — assert (a) the field exists defaulting to `""`, and (b) the gateway state-build resolves it. Mirror the nearest existing gateway/dispatch test for the harness; assert `state.identity_key == "owner-primary"` when the resolver maps the inbound handle, and `== state.session_id` (or `""`) when unmapped.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3a:** add to `state.py` near `session_id`:
```python
    #: Stable cross-channel identity for durable-knowledge scoping (preferences,
    #: extracted facts). Resolved at the gateway from the channel handle via the
    #: IdentityResolver. Empty ⇒ consumers fall back to session_id (per-channel),
    #: i.e. unconfigured behavior is byte-identical.
    identity_key: str = ""
```
- [ ] **Step 3b:** at the gateway state-build site, set `identity_key=resolver.resolve(<handle>)` where `<handle>` is the per-channel handle used for `session_id`. Resolver obtained from services/config; on resolver-load failure, log error and leave `identity_key=""` (degrade).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.** `feat(pipeline): resolve and thread identity_key at the gateway`.

---

### Task 3: Preferences keyed on identity

**Files:**
- Modify: `src/stackowl/memory/preferences.py` (queries key on identity instead of per-channel `owner_key`)
- Modify: `src/stackowl/pipeline/steps/classify.py:89` `_gather_preferences` (pass `state.identity_key or state.session_id`)
- Modify: preference get/set tool call-sites (the memory/preference tool paths)
- Test: `tests/memory/test_preferences_identity.py`

**Interfaces:**
- Consumes: `PipelineState.identity_key` (Task 2).
- Produces: preference reads/writes scoped by the resolved identity.

> Decision for the implementer: the cleanest change is to pass the resolved
> identity as the existing `owner_key` argument (keep the store's column/SQL
> unchanged, just feed it identity instead of the per-channel handle). This avoids
> a schema change. Confirm `owner_key`'s exact role; if any caller legitimately
> needs the per-channel key, scope only the durable-preference path to identity.

- [ ] **Step 1: Write the failing test** — set a preference under identity `"owner-primary"` via a `telegram:*` handle; read it back via a `slack:*` handle that also resolves to `"owner-primary"`; assert the value is present. And a control: two DIFFERENT identities do not see each other's preference.
- [ ] **Step 2: Run, verify fail** (today the slack read misses — per-channel key).
- [ ] **Step 3: Implement** — feed the resolved identity to the store key at the durable-preference call-sites (`_gather_preferences` passes `state.identity_key or state.session_id`; get/set paths likewise). Keep store SQL unchanged.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.** `feat(memory): scope preferences on identity_key so they follow the user across channels`.

---

### Task 4: Extracted facts keyed on identity (conversation stays per-channel)

**Files:**
- Modify: `src/stackowl/memory/fact_extractor.py:170` (`source_ref` for fact rows → identity)
- Modify: `src/stackowl/memory/sqlite_bridge.py` fact-retrieval SELECTs (fact rows queried by identity `source_ref`; conversation SELECT at `:397` unchanged)
- Test: `tests/memory/test_facts_identity.py`

**Interfaces:**
- Consumes: `PipelineState.identity_key`.
- Produces: extracted-fact rows staged/retrieved by identity; `source_type='conversation'` rows untouched (still `source_ref=session_id`).

- [ ] **Step 1: Write the failing test** — stage a fact from a `telegram:*` turn (identity `"owner-primary"`); retrieve facts for a `slack:*` turn (same identity) → fact present. Negative control: a `'conversation'` row staged under `telegram:*` is NOT returned for the `slack:*` turn's conversation retrieval (conversation stays per-channel).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — thread identity into the fact stage (`source_ref=identity` for fact `source_type`s) and the fact-retrieval SELECT; leave the `source_type='conversation'` path keyed on `session_id`. Pass identity down from the caller (fact_extractor is invoked with `session_id` today — add an `identity_key` parameter, defaulting to the session_id for back-compat).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.** `feat(memory): scope extracted facts on identity_key; conversation stays per-channel`.

---

### Task 5: `stackowl identity link` migration CLI + cross-channel gateway journey

**Files:**
- Create: `src/stackowl/db/migrations/00NN_identity_rekey.sql` OR (preferred) a `stackowl identity link` CLI command under the CLI module (confirm number/CLI location in pre-flight)
- Test: `tests/journeys/test_cross_channel_identity_journey.py`, `tests/<cli>/test_identity_link.py`

**Interfaces:**
- Consumes: everything above.

> Preferred: a `stackowl identity link` CLI command (run AFTER the operator sets
> `identity.aliases`) that re-keys the existing user's `user_preferences.owner_key`
> and fact-row `staged_facts.source_ref` (where `source_type != 'conversation'`)
> from the per-channel handles to the configured `identity_key`, owner-scoped,
> idempotent, with a dry-run flag. A pure SQL migration can't read the config map,
> so the CLI is the clean home — decide and implement one.

- [ ] **Step 1: Write the failing migration/CLI test** — seed `user_preferences` + fact `staged_facts` rows under `telegram:123` and a `'conversation'` row under the same handle; run `identity link` with config mapping `telegram:123`→`owner-primary`; assert preference + fact rows now key on `owner-primary`, the conversation row is UNCHANGED, and a second run is a no-op (idempotent).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement the CLI re-key** (owner-scoped UPDATEs, dry-run flag, logs counts; never touches `owner_id` or conversation rows).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Write the cross-channel gateway journey** `test_cross_channel_identity_journey.py` (mirror `tests/journeys/test_skill_injection_journey.py` / `test_conversational_bypass_journey.py` fixtures): configure `telegram:*` and `slack:*` to one identity; (a) set a preference on the telegram turn, assert it appears in the slack turn's assembled context; (b) a fact learned on telegram surfaces on slack; (c) NEGATIVE: telegram conversation history is absent from the slack turn's assembled history; (d) with no `identity.aliases`, assert behavior is unchanged (identity_key == session_id).
- [ ] **Step 6: Run the journey + full touched suites** `uv run pytest tests/journeys/test_cross_channel_identity_journey.py tests/memory/ tests/tenancy/ tests/pipeline/ -q` → all green.
- [ ] **Step 7: Commit.** `feat(tenancy): identity-link re-key CLI + cross-channel identity merge-gate journey`.

---

## Self-Review

**Spec coverage:** IdentityResolver+config → Task 1; `identity_key` field + gateway resolution → Task 2; preferences re-key → Task 3; facts re-key (conversation untouched) → Task 4; migration + journeys (incl. unconfigured byte-identical + conversation-does-not-cross negative control) → Task 5. `owner_id` layer and already-unified stores explicitly untouched per Global Constraints. ✔

**Placeholder scan:** Concrete test code in every task; the handful of "confirm exact signature" items are pre-flight lookups (the code/SQL is in the repo), not logic placeholders — same pattern as the boundary-honesty plan. ✔

**Type consistency:** `IdentityResolver.resolve(handle:str)->str` and `identity_key: str` used consistently across Tasks 1-5; the `identity_key or session_id` fallback is uniform. ✔

**Open decisions deliberately left to the implementer (flagged, not hidden):** (1) feed identity via the existing `owner_key` arg vs a new column — recommended: reuse the arg, no schema change; (2) migration as CLI vs SQL — recommended: CLI (config-aware). Both are called out at their tasks with a recommendation.
