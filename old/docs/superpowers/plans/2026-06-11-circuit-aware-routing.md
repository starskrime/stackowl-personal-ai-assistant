# Circuit-Aware Answer Routing + Provider-Fallback Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the user's answer provider circuit-aware (tier path only) so a provider outage falls back to a healthy model instead of failing, and tell the user a backup completed it (generic, no provider-name leak) — reusing the shipped recovery infra.

**Architecture:** A new `ProviderRegistry.resolve_tier_with_fallback(tier)` composes `get_by_tier` (happy-path choice, byte-identical when healthy) with `get_with_cascade` (fallback walk, only when the chosen provider's circuit is OPEN), returning `(provider, degraded_from)`. `_select_tool_provider` uses it on the tier path (pins untouched) and records a `provider_fallback` recovery on degrade. `execute.run` floors gracefully when all providers are open. `surface_recovery` gains a per-kind template so provider-fallback renders a generic name-free line.

**Tech Stack:** Python 3.13, existing `CircuitBreaker`/`CircuitState`, `infra/recovery_context.py` (kind-agnostic — no change), `surface_recovery`, `localize_format`, pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/providers/registry.py` | Modify (add method) | `resolve_tier_with_fallback(tier) -> (ModelProvider, str \| None)` |
| `src/stackowl/pipeline/steps/execute.py` | Modify (`_select_tool_provider` Step 4 + `run` all-open catch) | Use the new method on the tier path, record recovery on degrade; floor on all-open |
| `src/stackowl/pipeline/recovery_summary.py` | Modify | per-kind template selection (substitution vs provider_fallback) |
| `src/stackowl/setup/localize.py` | Modify | `self_heal_recovery_provider` generic key (en/de/fr/es) |
| `tests/providers/test_resolve_tier_with_fallback.py` | **Create** | registry method unit |
| `tests/pipeline/test_provider_fallback_recovery.py` | **Create** | `_select_tool_provider` records recovery + `run` all-open floors |
| `tests/pipeline/test_recovery_summary_render.py` | Modify | provider_fallback → generic line, no name |
| `tests/journeys/test_circuit_aware_routing_journey.py` | **Create** | gateway FR1/FR3/FR6 + FR2 negative |

---

## Task 1: `resolve_tier_with_fallback` registry method

**Files:**
- Modify: `src/stackowl/providers/registry.py` (add a method near `get_with_cascade`)
- Test: `tests/providers/test_resolve_tier_with_fallback.py`

**Context:** `get_by_tier(tier)` returns the first provider whose tier matches (config order), else a config-degrade to the first registered provider; it is NOT circuit-aware. `get_with_cascade(tier)` skips OPEN breakers, walks `fast→standard→powerful→local` from `tier`, raises `AllProvidersUnavailableError` if all open. `CircuitState` and `AllProvidersUnavailableError` are already imported in this file. Breakers are in `self._breakers: dict[str, CircuitBreaker]`; `breaker.state` returns a `CircuitState` (may transition OPEN→HALF_OPEN on read). Treat ONLY `CircuitState.OPEN` as "fall back" (HALF_OPEN is allowed through, matching cascade).

- [ ] **Step 1: Write the failing test** `tests/providers/test_resolve_tier_with_fallback.py`:

```python
from __future__ import annotations

import pytest

from stackowl.exceptions import AllProvidersUnavailableError
from stackowl.providers.registry import ProviderRegistry
from stackowl.providers.mock_provider import MockProvider


def _open_breaker(registry: ProviderRegistry, name: str) -> None:
    """Trip a provider's breaker to OPEN (threshold is 3 failures)."""
    breaker = registry._breakers[name]
    for _ in range(3):
        breaker._record_failure()
    from stackowl.providers.circuit_breaker import CircuitState
    assert breaker.state is CircuitState.OPEN


def test_healthy_primary_matches_get_by_tier():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    provider, degraded_from = reg.resolve_tier_with_fallback("powerful")
    assert provider is reg.get_by_tier("powerful")   # identical happy-path choice
    assert degraded_from is None


def test_open_primary_falls_back_to_healthy_and_reports_name():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "powerful_a")
    provider, degraded_from = reg.resolve_tier_with_fallback("powerful")
    assert provider.name == "fast_b"       # cascaded to the healthy one
    assert degraded_from == "powerful_a"   # reports who we fell back FROM


def test_all_open_raises():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "powerful_a")
    _open_breaker(reg, "fast_b")
    with pytest.raises(AllProvidersUnavailableError):
        reg.resolve_tier_with_fallback("powerful")


def test_no_tier_match_degrades_like_get_by_tier():
    reg = ProviderRegistry()
    reg.register_mock("only_fast", MockProvider(name="only_fast"), tier="fast")
    provider, degraded_from = reg.resolve_tier_with_fallback("powerful")  # no powerful provider
    assert provider.name == "only_fast"   # existing config-degrade (first registered)
    assert degraded_from is None
```

> If `_record_failure` isn't the right way to open a breaker, check `tests/providers/test_provider_resilience.py` for the established pattern and adapt `_open_breaker`. The threshold default is 3 (`circuit_breaker.py:37`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_resolve_tier_with_fallback.py -q`
Expected: FAIL — `AttributeError: 'ProviderRegistry' object has no attribute 'resolve_tier_with_fallback'`

- [ ] **Step 3: Implement** — add this method to `ProviderRegistry` (place it right after `get_with_cascade`):

```python
    def resolve_tier_with_fallback(
        self, tier: str,
    ) -> tuple[ModelProvider, str | None]:
        """Tier resolution that is circuit-aware ONLY when the chosen provider is OPEN.

        Returns ``(provider, degraded_from)``. ``degraded_from`` is the name of the
        provider we fell back FROM (its circuit was OPEN), or ``None`` when no
        fallback occurred. Happy path (chosen provider healthy) is byte-identical
        to :meth:`get_by_tier`; the cascade is only invoked when the chosen
        provider's circuit is OPEN. Raises :class:`AllProvidersUnavailableError`
        if every provider is OPEN (caller floors).
        """
        log.engine.debug(
            "[registry] resolve_tier_with_fallback: entry",
            extra={"_fields": {"tier": tier}},
        )
        providers = self._providers
        tiers = self._tiers
        breakers = self._breakers
        # 1. The get_by_tier choice: first provider matching this tier (config order).
        primary_name: str | None = None
        for name, ptier in tiers.items():
            if ptier == tier and name in providers:
                primary_name = name
                break
        # 2. No exact tier match → existing config-degrade (not circuit-aware).
        if primary_name is None:
            log.engine.debug(
                "[registry] resolve_tier_with_fallback: no tier match — config degrade",
                extra={"_fields": {"tier": tier}},
            )
            return self.get_by_tier(tier), None
        # 3. Chosen provider healthy (no breaker, or not OPEN incl. HALF_OPEN) → happy path.
        breaker = breakers.get(primary_name)
        if breaker is None or breaker.state is not CircuitState.OPEN:
            return providers[primary_name], None
        # 4. Chosen provider OPEN → circuit-aware fallback via the tested primitive.
        log.engine.info(
            "[registry] resolve_tier_with_fallback: primary circuit OPEN — cascading",
            extra={"_fields": {"tier": tier, "degraded_from": primary_name}},
        )
        healthy = self.get_with_cascade(tier)  # skips OPEN; raises if all open
        return healthy, primary_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_resolve_tier_with_fallback.py -q` (4 passed). Then `uv run mypy src/stackowl/providers/registry.py` (clean) and `uv run ruff check src/stackowl/providers/registry.py tests/providers/test_resolve_tier_with_fallback.py` (clean).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/registry.py tests/providers/test_resolve_tier_with_fallback.py
git commit -m "feat(v2): resolve_tier_with_fallback — circuit-aware tier resolution (happy-path-preserving)"
```

---

## Task 2: `_select_tool_provider` uses it + records provider_fallback recovery

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`_select_tool_provider` Step 4, ~line 1168)
- Test: `tests/pipeline/test_provider_fallback_recovery.py`

**Context:** `_select_tool_provider` (execute.py:1071) resolves the answer provider: Step 0 owl-named pin (`registry.get(owl_name)`), Step 2 manifest pin, Step 4 `registry.get_by_tier(desired)`. We change ONLY Step 4. `recovery_context` is `from stackowl.infra import recovery_context` (already imported in execute.py from the prior slice — confirm; if not, add it).

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_provider_fallback_recovery.py`:

```python
from __future__ import annotations

import pytest

from stackowl.infra import recovery_context as rc
from stackowl.providers.registry import ProviderRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _select_tool_provider


def _open_breaker(reg, name):
    for _ in range(3):
        reg._breakers[name]._record_failure()


def _state(owl="someowl", session="s1"):
    return PipelineState(trace_id="t", session_id=session, input_text="hi",
                         channel="cli", owl_name=owl, pipeline_step="execute")


def test_tier_fallback_records_provider_recovery():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "powerful_a")
    services = StepServices(provider_registry=reg)  # owl_registry None → tier path, desired="powerful"
    token = rc.bind()
    try:
        provider = _select_tool_provider(reg, services, _state())
        assert provider.name == "fast_b"
        evs = rc.get_recovery()
        assert len(evs) == 1
        assert evs[0].kind == "provider_fallback"
        assert evs[0].failed == "powerful_a"
        assert evs[0].recovered_via == "fast_b"
        assert evs[0].user_visible is True
    finally:
        rc.reset(token)


def test_healthy_tier_records_nothing():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    services = StepServices(provider_registry=reg)
    token = rc.bind()
    try:
        provider = _select_tool_provider(reg, services, _state())
        assert provider.name == "powerful_a"
        assert rc.get_recovery() == ()   # FR2: no recovery on the happy path
    finally:
        rc.reset(token)


def test_owl_named_pin_with_open_circuit_is_honored_no_fallback():
    # FR4: an EXPLICIT pin (owl-named provider) is honored even with an OPEN circuit
    # — Step 0 uses registry.get(owl_name), never resolve_tier_with_fallback.
    reg = ProviderRegistry()
    # provider registered under the OWL's name = the per-owl pin (Step 0)
    reg.register_mock("pinned_owl", MockProvider(name="pinned_owl"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "pinned_owl")
    services = StepServices(provider_registry=reg)
    token = rc.bind()
    try:
        provider = _select_tool_provider(reg, services, _state(owl="pinned_owl"))
        assert provider.name == "pinned_owl"   # pin honored despite open circuit
        assert rc.get_recovery() == ()          # no fallback, no recovery claim
    finally:
        rc.reset(token)
```

> Confirm `StepServices(provider_registry=...)` is the right construction (owl_registry defaults None — see `pipeline/services.py`). If `_select_tool_provider`'s default tier isn't "powerful" for a no-manifest owl, adjust the registered tier to match `desired` (read Step 3 of `_select_tool_provider`: session_tier > manifest > "powerful"). A fresh session_id has no session tier.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_provider_fallback_recovery.py -q`
Expected: FAIL — `test_tier_fallback_records_provider_recovery` (no recovery recorded; provider may even be the open one since get_by_tier isn't circuit-aware).

- [ ] **Step 3: Implement.** In `_select_tool_provider`, Step 4 (currently `provider = registry.get_by_tier(desired)` followed by a "tool provider selected" log), replace the `get_by_tier` call with:

```python
    # --- Step 4: Resolve by tier — circuit-aware (falls back if the tier provider's
    # circuit is OPEN; pins above are honored as-is). ---
    provider, degraded_from = registry.resolve_tier_with_fallback(desired)
    if degraded_from is not None:
        recovery_context.record_recovery(
            kind="provider_fallback", failed=degraded_from,
            recovered_via=provider.name, user_visible=True,
        )
```

Keep the existing "tool provider selected" log that follows (it already logs `chosen_provider_name`/`desired_tier`). Ensure `from stackowl.infra import recovery_context` is imported at the top of execute.py (it was added in the recovery slice — verify; add if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_provider_fallback_recovery.py -q` (3 passed — tier-fallback, healthy-no-record, owl-pin-honored). Then `uv run mypy src/stackowl/pipeline/steps/execute.py` (clean) and `uv run ruff check` the 2 files. Run the existing routing test to confirm no regression: `uv run pytest tests/providers/test_routing_model_selection.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_provider_fallback_recovery.py
git commit -m "feat(v2): tier answer routing is circuit-aware + records provider_fallback recovery"
```

---

## Task 3: `execute.run` floors gracefully when all providers are open

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`run`, ~line 1187 where `_select_tool_provider` is called)
- Test: add to `tests/pipeline/test_provider_fallback_recovery.py`

**Context:** With circuit-aware resolution, `_select_tool_provider` can now raise `AllProvidersUnavailableError` (all circuits open). `run` must catch it and return state carrying an error so the existing critical-failure/floor path produces an honest message — NOT a recovery claim. Today `run` calls `provider = _select_tool_provider(registry, services, state)` (line 1187) with no guard.

- [ ] **Step 1: Write the failing test** (append to `tests/pipeline/test_provider_fallback_recovery.py`):

```python
import asyncio
from stackowl.pipeline.steps import execute as exe


def test_run_floors_when_all_providers_open():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    _open_breaker(reg, "powerful_a")
    # NOTE: tool_registry None → run takes the plain-stream path through _select_tool_provider.
    services = StepServices(provider_registry=reg)
    from stackowl.pipeline.services import set_services, reset_services
    stoken = set_services(services)
    token = rc.bind()
    try:
        out = asyncio.get_event_loop().run_until_complete(exe.run(_state()))
        # No usable response; an error is recorded → the floor/critical-failure path owns it.
        assert any("AllProvidersUnavailableError" in e for e in out.errors)
        # And NO provider_fallback recovery was recorded (selection raised before any fallback).
        assert rc.get_recovery() == ()
    finally:
        rc.reset(token); reset_services(stoken)
```

> Adapt the async invocation to the test file's style (use `@pytest.mark.asyncio` + `async def` if the file/conftest uses asyncio_mode=auto — check sibling tests; the cleaner form is an async test). Confirm `exe.run` reaches `_select_tool_provider` when `tool_registry is None` (it does — line 1187 is before the tool-loop branch). If `run` returns early on `provider_registry is None`, this test uses a real registry so it proceeds.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_provider_fallback_recovery.py -q -k all_providers_open`
Expected: FAIL — the `AllProvidersUnavailableError` propagates out of `run` (uncaught) rather than being recorded as an error.

- [ ] **Step 3: Implement.** Add the import at the top of execute.py (with other exception imports):
```python
from stackowl.exceptions import AllProvidersUnavailableError
```
Wrap the `_select_tool_provider` call in `run` (line ~1187):
```python
    try:
        provider = _select_tool_provider(registry, services, state)
    except AllProvidersUnavailableError as exc:
        log.engine.error(
            "[pipeline] execute: all providers unavailable — flooring",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            errors=(*state.errors, f"execute: AllProvidersUnavailableError: {exc}"),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_provider_fallback_recovery.py -q` (3 passed). `uv run mypy src/stackowl/pipeline/steps/execute.py` (clean); `uv run ruff check` the 2 files.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_provider_fallback_recovery.py
git commit -m "fix(v2): execute.run floors honestly when all provider circuits are open"
```

---

## Task 4: `surface_recovery` per-kind template + generic provider line

**Files:**
- Modify: `src/stackowl/setup/localize.py` (`_STRINGS`)
- Modify: `src/stackowl/pipeline/recovery_summary.py`
- Test: `tests/pipeline/test_recovery_summary_render.py` (add provider_fallback cases)

**Context:** `surface_recovery` currently renders every user-visible event with `localize_format("self_heal_recovery_note", _LANG, failed=..., recovered_via=...)`. provider_fallback must render a GENERIC line with NO provider names. Make the template selection per-`kind`.

- [ ] **Step 1: Add the localize key.** In `_STRINGS` (next to `self_heal_recovery_note`) add the generic, slot-free key:

```python
    # Provider fallback recovery (pillar ④) — GENERIC, no provider/tier names leaked
    # to the user (names live in the [recovery] turn summary log only). No slots.
    ("self_heal_recovery_provider", "en"): "ℹ️ The usual model was unavailable, so a backup completed this.",
    ("self_heal_recovery_provider", "de"): "ℹ️ Das übliche Modell war nicht verfügbar, daher hat ein Ersatzmodell dies erledigt.",
    ("self_heal_recovery_provider", "fr"): "ℹ️ Le modèle habituel était indisponible, un modèle de secours a donc traité cette demande.",
    ("self_heal_recovery_provider", "es"): "ℹ️ El modelo habitual no estaba disponible, así que un modelo de respaldo completó esto.",
```

- [ ] **Step 2: Write the failing test** (add to `tests/pipeline/test_recovery_summary_render.py`):

```python
@pytest.mark.asyncio
async def test_provider_fallback_renders_generic_line_without_names():
    token = rc.bind()
    try:
        rc.record_recovery(kind="provider_fallback", failed="gpt-secret-name",
                           recovered_via="other-secret-name", user_visible=True)
        out = await surface_recovery(_state(responses=(_answer(),)))
        assert len(out.responses) == 2
        line = out.responses[-1].content
        assert "backup" in line.lower()            # the generic phrasing
        assert "gpt-secret-name" not in line        # NO provider name leaked
        assert "other-secret-name" not in line
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_unknown_kind_is_not_surfaced():
    token = rc.bind()
    try:
        rc.record_recovery(kind="some_future_kind", failed="a",
                           recovered_via="b", user_visible=True)
        s = _state(responses=(_answer(),))
        out = await surface_recovery(s)
        assert out.responses == s.responses   # defensive: unmapped kind → not surfaced
    finally:
        rc.reset(token)
```

(The existing `test_appends_line_for_user_visible_recovery_on_real_answer` already covers substitution's named line — keep it.)

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_recovery_summary_render.py -q -k "provider_fallback or unknown_kind"`
Expected: FAIL — provider_fallback currently renders via the named substitution template (would interpolate `{failed}`/`{recovered_via}` → contains the names, or KeyErrors-safe to empty); unknown kind currently still renders.

- [ ] **Step 4: Implement.** In `recovery_summary.py`, add a kind→template map and select per event. Replace the body of the per-event loop:

```python
_TEMPLATE_BY_KIND = {
    "substitution": "self_heal_recovery_note",        # slots: failed, recovered_via
    "provider_fallback": "self_heal_recovery_provider",  # generic, no slots
}
```
And in the loop (inside `surface_recovery`), build each line by kind:
```python
        for offset, e in enumerate(events[:_MAX_LINES]):
            key = _TEMPLATE_BY_KIND.get(e.kind)
            if key is None:
                log.engine.debug(
                    "[recovery_summary] skip — unmapped recovery kind",
                    extra={"_fields": {"trace_id": state.trace_id, "kind": e.kind}},
                )
                continue
            if key == "self_heal_recovery_provider":
                text = localize_format(key, _LANG)            # generic, no names
            else:
                text = localize_format(key, _LANG, failed=e.failed,
                                       recovered_via=e.recovered_via)
            new_chunks.append(ResponseChunk(
                content=text, is_final=False, chunk_index=base_index + offset,
                trace_id=state.trace_id, owl_name=state.owl_name,
            ))
```
Keep the surrounding guards (`events = [user_visible...]`, `has_real_answer`, the B5 catch, the `base_index`) unchanged. NOTE: `base_index + offset` is now slightly loose if an unmapped kind is skipped mid-loop (a gap in chunk_index), which is harmless (chunk_index need not be contiguous), but if you prefer, increment a local counter only for appended chunks. Either is acceptable; keep it simple.

- [ ] **Step 5: Run + verify + commit**

Run: `uv run pytest tests/pipeline/test_recovery_summary_render.py -q` (all pass — the new 2 + existing). `uv run mypy src/stackowl/pipeline/recovery_summary.py src/stackowl/setup/localize.py` (clean); `uv run ruff check` the 3 files; `uv run pytest -q -k localize` (no regression).

```bash
git add src/stackowl/setup/localize.py src/stackowl/pipeline/recovery_summary.py tests/pipeline/test_recovery_summary_render.py
git commit -m "feat(v2): surface_recovery per-kind template — generic name-free provider-fallback line"
```

---

## Task 5: Gateway journey (FR1/FR3/FR6) + FR2/FR5 + full regression

**Files:**
- Create: `tests/journeys/test_circuit_aware_routing_journey.py`

**Context:** Drive the REAL backend with a registry where the desired-tier provider's circuit is OPEN and a healthy backup is registered, plus a scripted backup provider that answers. STUDY `tests/journeys/test_self_heal_substitution.py` and `tests/journeys/test_recovery_explainability_journey.py` for the boot harness (real `AsyncioBackend`, `register_mock`, reading delivered text via `"".join(c.content for c in final_state.responses)`, `caplog`). Reuse it.

- [ ] **Step 1: Write the failing happy-path journey (FR1/FR3/FR6).** Register the desired-tier provider with an OPEN breaker and a healthy backup provider (a scripted one that returns a final answer). Run a turn through the real backend. Assert:
  - the answer is delivered (FR1 — the backup produced it),
  - the delivered text contains the GENERIC backup line ("backup" / the `self_heal_recovery_provider` en text) AND does NOT contain either provider's name (FR3),
  - via `caplog` (logger `stackowl.engine`, INFO), a `[recovery] turn summary` record was emitted carrying the real provider names in its `events` (FR6).

Pattern the boot on `test_recovery_explainability_journey.py`. Open the breaker with the `_open_breaker` helper (3× `_record_failure()` on `registry._breakers[name]`).

```python
# tests/journeys/test_circuit_aware_routing_journey.py
# Boot mirrors test_recovery_explainability_journey.py. The desired-tier provider's
# breaker is opened; a healthy scripted backup is registered and answers.
# Assertions: answer present; generic backup line present; NO provider name in the
# user text; caplog has "[recovery] turn summary" with the real names.
```

- [ ] **Step 2: Run — confirm PASS** (the feature is wired). If it fails on harness construction, fix until it runs and the assertion is meaningful. If the fallback doesn't fire (wrong tier/desired mismatch), align the registered tier with the owl's resolved `desired` tier (see `_select_tool_provider` Step 3).

Run: `uv run pytest tests/journeys/test_circuit_aware_routing_journey.py -q`

- [ ] **Step 3: Add FR2 + FR5 negatives.**
  - `test_no_recovery_line_when_all_healthy` (FR2): same boot but NO breaker opened → answer present, NO backup line, NO `[recovery]` record for provider_fallback.
  - `test_all_open_floors_without_recovery_line` (FR5): open the breakers of ALL registered providers → assert the turn delivers a floor/honest-failure message (no usable answer) and the backup recovery line is ABSENT. IMPORTANT: if the backup line LEAKS onto this all-open floored turn, that's a real honesty defect — STOP and report BLOCKED; do not weaken. (Behaviorally: `_select_tool_provider` raises → `run` records the error → floor; no recovery recorded.)

- [ ] **Step 4: Full regression (FR7).**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: prior 88 passed + 1 skipped, plus the new journey's tests → report exact counts. ZERO failures/regressions. If any prior journey regresses (especially a routing-sensitive one), STOP and report BLOCKED.

- [ ] **Step 5: Lint + commit.**

Run: `uv run ruff check tests/journeys/test_circuit_aware_routing_journey.py` (clean).

```bash
git add tests/journeys/test_circuit_aware_routing_journey.py
git commit -m "test(v2): circuit-aware routing journey — backup answers (FR1), generic line (FR3), log names (FR6), all-open floors (FR5)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1→Task 1+2+5; FR2→Task 2 (`test_healthy_tier_records_nothing`) + Task 5; FR3→Task 4+5; FR4 (pins honored)→Task 2 changes ONLY Step 4, Steps 0/2 untouched (note: add an explicit pin-honored assertion is optional; the diff scope guarantees it); FR5→Task 3+5; FR6→Task 5 caplog; FR7→Task 5. Honesty invariants: happy-path unchanged (Task 1 `test_healthy_primary_matches_get_by_tier`), generic no-name line (Task 4), all-open floors not claims (Task 3+5).
- **GAP found + fixed:** FR4 (explicit pin with open circuit is honored, no fallback) had no direct test. Mitigation: Task 2 changes only Step 4, and Steps 0/2 use `registry.get(...)` unchanged — so a pinned provider is never routed through `resolve_tier_with_fallback`. This is guaranteed by the diff, not a test. ADD a Task 2 assertion if cheap: register an owl-named provider with an open breaker and assert `_select_tool_provider` returns THAT provider and records no recovery. (Optional but recommended; note for the implementer.)
- **Placeholder scan:** the `<...>` notes in Task 5 are "mirror this named existing journey" instructions (concrete files), not deferred work. No TBD/TODO.
- **Type consistency:** `resolve_tier_with_fallback(tier) -> tuple[ModelProvider, str | None]`, `record_recovery(kind=, failed=, recovered_via=, user_visible=)`, `_TEMPLATE_BY_KIND`, localize key `self_heal_recovery_provider` — consistent across tasks.

## Risk & containment
- **Risk (FR2):** happy-path routing changes. **Contained:** step 3 of `resolve_tier_with_fallback` returns the `get_by_tier` choice directly when healthy (Task 1 asserts `is reg.get_by_tier(...)`); full journeys regression (Task 5) catches routing drift.
- **Risk:** all-open now raises where it didn't. **Contained:** Task 3 catches + floors; Task 5 FR5 asserts the floor + no leak.
- **Risk:** provider name leak in the user line. **Contained:** Task 4 generic template + explicit no-name assertion.
- **Rollback:** pure-additive (see spec).

## Pre-merge note
Add an explicit FR4 pin-honored test in Task 2 (recommended above) before the final holistic review, OR flag it for the holistic reviewer to verify the pin paths are untouched.
