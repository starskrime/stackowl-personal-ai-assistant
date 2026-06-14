# Model-Aware Lean Charter + DNA (Slice 3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Small-window/weak models (window ≤ 8192) get a leaner behavioral charter + a DNA persona with the backfiring directives suppressed; capable models (≥ 16384) are byte-identical to today. The lean/full decision is driven by the resolved model window, shared between `assemble` and `execute`.

**Architecture:** Extract `_select_tool_provider` to a shared `pipeline/provider_select.py` (with a `log_selection` gate) so `assemble` (which runs before `execute`) can resolve the turn's provider quietly + call `resolve_window` (Slice 1, memoized) and stamp `state.model_window`. `assemble` picks `lean = window <= LEAN_WINDOW_THRESHOLD` and passes it to `build_base_prompt(now, lean=)` (lean charter) and `dna_injector.inject(..., lean=)` (suppress backfiring directives). Fail-safe to full on any error; strong-model path unchanged.

**Tech Stack:** Python 3.13, Slice-1 `resolve_window` (`providers/model_window.py`), existing `_select_tool_provider`/charter/dna_injector/assemble, pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/pipeline/provider_select.py` | **Create** | Shared `select_tool_provider(..., log_selection)` (moved from execute) |
| `src/stackowl/pipeline/steps/execute.py` | Modify | Import the moved function; pass `log_selection=True`; use `state.model_window` |
| `src/stackowl/pipeline/state.py` | Modify | Add `model_window: int | None = None` |
| `src/stackowl/owls/base_prompt.py` | Modify | `behavioral_charter_lean()` + `build_base_prompt(now, *, lean=False)` + `LEAN_WINDOW_THRESHOLD` |
| `src/stackowl/owls/dna_injector.py` | Modify | `inject(..., *, lean=False)` + `_LEAN_SUPPRESSED_TRAITS` |
| `src/stackowl/pipeline/steps/assemble.py` | Modify | Resolve window, stamp model_window, pick lean, pass to builders (fail-safe) |
| `tests/pipeline/test_provider_select.py` | **Create** | extraction + log gate |
| `tests/owls/test_lean_charter_dna.py` | **Create** | lean charter + lean DNA units |
| `tests/pipeline/steps/test_assemble_model_aware.py` | **Create** | assemble lean/full/fail-safe |
| `tests/journeys/test_model_aware_charter_journey.py` | **Create** | end-to-end lean vs full |

---

## Task 1: Extract `select_tool_provider` to a shared module

**Files:**
- Create: `src/stackowl/pipeline/provider_select.py`
- Modify: `src/stackowl/pipeline/steps/execute.py` (remove the local `_select_tool_provider`, import the shared one)
- Test: `tests/pipeline/test_provider_select.py`

**Context:** `_select_tool_provider(registry, services, state) -> ModelProvider` lives in `execute.py` (~line 1176-1280). Move it verbatim to `provider_select.py`, adding a keyword-only `log_selection: bool = True` that gates the INFO `"[pipeline] execute: tool provider selected"` log lines (there are 3 — one per precedence branch). `execute.py` imports `select_tool_provider` and calls it with `log_selection=True` (default → unchanged behavior).

- [ ] **Step 0:** Read `execute.py` lines ~1176-1280 (the full `_select_tool_provider`) — note every import it uses (`ProviderRegistry`, `ModelProvider`, `PipelineState`, `get_session_tier`, `ProviderNotFoundError`, `log`) and every `log.engine.info("[pipeline] execute: tool provider selected", ...)` call site. Quote them.

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_provider_select.py`:

```python
import logging

from stackowl.pipeline.provider_select import select_tool_provider


def test_select_returns_tier_provider(provider_registry_with_fast):
    # reuse an existing fixture/idiom for a ProviderRegistry with a registered
    # provider (see how execute/router tests build one). Adjust the fixture name.
    reg, services, state = provider_registry_with_fast
    p = select_tool_provider(reg, services, state)
    assert p is not None


def test_log_selection_false_is_quiet(provider_registry_with_fast, caplog):
    reg, services, state = provider_registry_with_fast
    with caplog.at_level(logging.INFO):
        select_tool_provider(reg, services, state, log_selection=False)
    assert not any("tool provider selected" in r.getMessage() for r in caplog.records)


def test_log_selection_true_emits(provider_registry_with_fast, caplog):
    reg, services, state = provider_registry_with_fast
    with caplog.at_level(logging.INFO):
        select_tool_provider(reg, services, state, log_selection=True)
    assert any("tool provider selected" in r.getMessage() for r in caplog.records)
```

> Read `tests/pipeline/steps/` (execute tests) for how a `ProviderRegistry` + `StepServices` + `PipelineState` are constructed in tests; build the `provider_registry_with_fast` fixture from that idiom (a registry with one provider registered on a tier the state routes to). If a shared fixture already exists, reuse it.

- [ ] **Step 2: Run to verify it fails** — `ModuleNotFoundError: provider_select`.

- [ ] **Step 3: Implement.** Create `src/stackowl/pipeline/provider_select.py` with the function moved from execute.py. Signature:
```python
def select_tool_provider(
    registry: ProviderRegistry,
    services: object,
    state: PipelineState,
    *,
    log_selection: bool = True,
) -> ModelProvider:
```
Copy the body verbatim, but wrap EACH of the 3 `log.engine.info("[pipeline] execute: tool provider selected", ...)` calls in `if log_selection:`. Keep the `[pipeline] execute: _select_tool_provider: entry` debug + the warn-on-ProviderNotFoundError as-is (debug/warn are fine to keep). Bring the needed imports (`from stackowl.commands.tier_command import get_session_tier`, `ProviderNotFoundError`, `ProviderRegistry`, `ModelProvider`, `PipelineState`, `log`). Then in `execute.py`: delete the local `_select_tool_provider` def and add `from stackowl.pipeline.provider_select import select_tool_provider`; replace the call site(s) `_select_tool_provider(registry, services, state)` with `select_tool_provider(registry, services, state)` (log_selection defaults True → identical).

- [ ] **Step 4: Verify** — `uv run pytest tests/pipeline/test_provider_select.py -q` (pass) + execute regression `uv run pytest tests/pipeline/steps/ -q -k "execute or provider"` + `uv run pytest tests/journeys/test_self_heal_lying_judge.py -q` (provider selection drives it). `uv run mypy src/stackowl/pipeline/provider_select.py src/stackowl/pipeline/steps/execute.py` (no NEW errors). `uv run ruff check`.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/pipeline/provider_select.py src/stackowl/pipeline/steps/execute.py tests/pipeline/test_provider_select.py
git commit -m "refactor(v2): extract shared select_tool_provider (log_selection gate) for assemble reuse"
```

---

## Task 2: `state.model_window` + lean charter + threshold

**Files:**
- Modify: `src/stackowl/pipeline/state.py` (add field)
- Modify: `src/stackowl/owls/base_prompt.py` (`behavioral_charter_lean`, `build_base_prompt(lean=)`, `LEAN_WINDOW_THRESHOLD`)
- Test: `tests/owls/test_lean_charter_dna.py`

**Context:** `PipelineState` is a frozen dataclass/pydantic model with an `evolve(...)` method (see how `intent_class`/other fields are declared). Add `model_window: int | None = None`. `build_base_prompt(now)` currently returns `behavioral_charter() + "\n\n" + operational_adapter(now)`.

- [ ] **Step 1: Write the failing test** `tests/owls/test_lean_charter_dna.py` (charter portion):

```python
from datetime import datetime

from stackowl.owls.base_prompt import (
    LEAN_WINDOW_THRESHOLD, behavioral_charter, behavioral_charter_lean,
    build_base_prompt,
)

_NOW = datetime(2026, 6, 14, 12, 0, 0)


def test_threshold_value():
    assert LEAN_WINDOW_THRESHOLD == 8192


def test_lean_charter_shorter_but_keeps_principles():
    full = behavioral_charter()
    lean = behavioral_charter_lean()
    assert 0 < len(lean) < len(full)
    low = lean.lower()
    # load-bearing principles retained
    assert "ownership" in low or "own" in low
    assert "deliver" in low and ("hand back" in low or "manual" in low or "link" in low)
    assert "persist" in low
    assert "memory" in low


def test_build_base_prompt_lean_uses_lean_charter():
    assert behavioral_charter_lean() in build_base_prompt(_NOW, lean=True)
    assert behavioral_charter_lean() not in build_base_prompt(_NOW, lean=False)


def test_build_base_prompt_full_byte_identical():
    # default + explicit lean=False both == the pre-change composition
    expected = behavioral_charter() + "\n\n" + build_base_prompt(_NOW).split("\n\n", 1)[1] \
        if False else None  # placeholder removed below
    full = build_base_prompt(_NOW)
    assert behavioral_charter() in full
    assert build_base_prompt(_NOW, lean=False) == full
```

> Replace the awkward `test_build_base_prompt_full_byte_identical` body with a clean assertion: capture `build_base_prompt(_NOW)` once, assert `build_base_prompt(_NOW, lean=False) == build_base_prompt(_NOW)` and that `behavioral_charter()` is a substring. (The point: default == lean=False == today.)

- [ ] **Step 2: Run to verify it fails** (no `behavioral_charter_lean`/`LEAN_WINDOW_THRESHOLD`).

- [ ] **Step 3: Implement** in `src/stackowl/owls/base_prompt.py`:
```python
# Window at/below which a model gets the lean charter + lean DNA (small/weak
# local models, and the unknown/probe-fail fallback). Capable models (>= 16384)
# keep the full charter. Lives here so assemble imports one constant.
LEAN_WINDOW_THRESHOLD = 8192


def behavioral_charter_lean() -> str:
    """Tightened charter for small-window models — the load-bearing principles only.

    Same character as :func:`behavioral_charter`, ~40% shorter: keeps ownership,
    act-and-verify, persistence, memory, deliver-don't-hand-back, no-AI-excuses,
    and clear communication; drops the longer elaborations a small context can't
    afford. Global within the lean tier (no per-example tuning)."""
    return (
        "You are an autonomous, capable agent. Take full ownership of every "
        "request and drive it to a real, delivered outcome — don't just answer "
        "from memory.\n\n"
        "Act and verify: do the actual work with the capabilities available, and "
        "ground factual claims in what you actually checked — never present "
        "unverified or stale information as certain.\n\n"
        "Be persistent: exhaust your capabilities before concluding something is "
        "impossible; when one path is blocked, try another or build what you need.\n\n"
        "You have a persistent memory across conversations — recall what you "
        "already know before answering, and when asked to remember something, "
        "durably save it and confirm.\n\n"
        "Deliver the finished result itself — never hand back a link, manual steps, "
        "or instructions for the user to do the thing they asked. Never decline by "
        "appealing to being an AI or a training cutoff; if truly blocked after real "
        "effort, say so plainly — name the blocker and what you tried.\n\n"
        "Communicate naturally, clearly, and honestly, in the user's own language."
    )
```
Change `build_base_prompt`:
```python
def build_base_prompt(now: datetime, *, lean: bool = False) -> str:
    """Compose the charter (lean or full) and the swappable adapter (charter first)."""
    charter = behavioral_charter_lean() if lean else behavioral_charter()
    return charter + "\n\n" + operational_adapter(now)
```
(`build_base_prompt_now` unchanged — defaults `lean=False`.)

In `src/stackowl/pipeline/state.py`: add `model_window: int | None = None` to `PipelineState` (match the existing field-declaration style; ensure `evolve` carries it — if `evolve` uses `replace`/`model_copy` generically it already does).

- [ ] **Step 4: Verify** — `uv run pytest tests/owls/test_lean_charter_dna.py -q` (charter tests pass) + `uv run pytest tests/pipeline/ -q -k "state or pipeline_state"` + `uv run mypy src/stackowl/owls/base_prompt.py src/stackowl/pipeline/state.py` + `uv run ruff check`.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/owls/base_prompt.py src/stackowl/pipeline/state.py tests/owls/test_lean_charter_dna.py
git commit -m "feat(v2): lean behavioral charter + LEAN_WINDOW_THRESHOLD + state.model_window"
```

---

## Task 3: Lean DNA directive suppression

**Files:**
- Modify: `src/stackowl/owls/dna_injector.py` (`inject(..., *, lean=False)` + `_LEAN_SUPPRESSED_TRAITS`)
- Test: extend `tests/owls/test_lean_charter_dna.py`

**Context:** `DNAPromptInjector.inject(self, manifest, dna)` appends HIGH/LOW trait directives. The `precision` HIGH directive ("cite … line numbers") backfires on weak models; challenge/curiosity/creativity are high-token behaviors a weak model follows poorly. When `lean`, suppress those HIGH traits; keep `formality` (HIGH/LOW) + `verbosity` (LOW) — cheap register/length directives.

- [ ] **Step 1: Write the failing test** (append to `tests/owls/test_lean_charter_dna.py`). Use the existing dna-injector test idiom (grep `tests/owls/` for `DNAPromptInjector` / `inject` — see how a manifest + OwlDNA with high traits are built). Sketch:

```python
from stackowl.owls.dna_injector import DNAPromptInjector


def test_lean_suppresses_citation_directive(high_precision_manifest_and_dna):
    manifest, dna = high_precision_manifest_and_dna  # precision >= 0.7
    full = DNAPromptInjector().inject(manifest, dna, lean=False)
    lean = DNAPromptInjector().inject(manifest, dna, lean=True)
    assert "line numbers" in full          # full keeps it (unchanged)
    assert "line numbers" not in lean      # lean drops the backfiring directive


def test_lean_keeps_formality_and_verbosity(low_verbosity_manifest_and_dna):
    manifest, dna = low_verbosity_manifest_and_dna  # verbosity <= 0.3
    lean = DNAPromptInjector().inject(manifest, dna, lean=True)
    assert "concise" in lean.lower()       # verbosity LOW directive still applied


def test_full_inject_byte_identical(high_precision_manifest_and_dna):
    manifest, dna = high_precision_manifest_and_dna
    # default == lean=False == today's behaviour
    assert DNAPromptInjector().inject(manifest, dna) == DNAPromptInjector().inject(manifest, dna, lean=False)
```

> Build the manifest/DNA fixtures from the existing dna-injector tests' idiom (they already construct an `OwlAgentManifest` + `OwlDNA` with specific trait values and may interact with `DIRECTIVE_LATCH` — note the latch may require the trait to cross the threshold; if the latch needs warm-up, follow how existing tests prime it).

- [ ] **Step 2: Run to verify it fails** — `inject()` has no `lean` kwarg.

- [ ] **Step 3: Implement** in `src/stackowl/owls/dna_injector.py`:
```python
# Traits whose HIGH directive backfires on / overloads a weak (small-window)
# model: the precision directive makes a weak model fabricate citations; the
# challenge/curiosity/creativity directives push behaviours it follows poorly.
# Suppressed only on the lean path; capable models keep the full set.
_LEAN_SUPPRESSED_TRAITS = frozenset({"precision", "challenge_level", "curiosity", "creativity"})
```
Change `inject`:
```python
    def inject(self, manifest: OwlAgentManifest, dna: OwlDNA, *, lean: bool = False) -> str:
        """Return ``manifest.system_prompt`` with DNA-driven directives appended.

        When ``lean`` (small-window model), the directives that backfire on / overload
        a weak model (``_LEAN_SUPPRESSED_TRAITS``) are skipped; the cheap register/
        length directives (formality, verbosity) still apply. ``lean=False`` is
        byte-identical to the prior behaviour."""
        from stackowl.owls.directive_latch import DIRECTIVE_LATCH
        ...
        directives: list[str] = []
        for trait, directive in _HIGH_DIRECTIVES:
            if lean and trait in _LEAN_SUPPRESSED_TRAITS:
                continue
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.high_state(manifest.name, trait, value):
                directives.append(directive)
        for trait, directive in _LOW_DIRECTIVES:
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.low_state(manifest.name, trait, value):
                directives.append(directive)
        ...  # rest unchanged
```
(Keep the 4-point logging; add `lean` to the entry/exit log fields.)

- [ ] **Step 4: Verify** — `uv run pytest tests/owls/test_lean_charter_dna.py -q` (pass) + dna regression `uv run pytest tests/owls/ -q -k "dna or inject or injector"` (existing tests call `inject(manifest, dna)` → default lean=False → byte-identical → green). `uv run mypy src/stackowl/owls/dna_injector.py` + `uv run ruff check`.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/owls/dna_injector.py tests/owls/test_lean_charter_dna.py
git commit -m "feat(v2): lean DNA — suppress backfiring directives (citation/challenge/curiosity/creativity) for weak models"
```

---

## Task 4: Wire `assemble` (resolve window → lean) + execute reuse

**Files:**
- Modify: `src/stackowl/pipeline/steps/assemble.py` (resolve window, stamp model_window, pick lean, pass to builders)
- Modify: `src/stackowl/pipeline/steps/execute.py` (use `state.model_window` for the budget when set)
- Test: `tests/pipeline/steps/test_assemble_model_aware.py`

**Context:** `assemble.run` builds `persona = _injector.inject(manifest, manifest.dna)` and `base = build_base_prompt(now_local())`. Add the window resolution + lean decision. Must be FAIL-SAFE (any error → lean=False, no crash) — mirror the existing try/except discipline in assemble.

- [ ] **Step 1: Write the failing test** `tests/pipeline/steps/test_assemble_model_aware.py`. Drive `assemble.run` with `set_services(...)` (the existing test idiom — grep `tests/pipeline/steps/test_assemble*` for how services + a mock provider/registry are wired). Three cases:
  - a provider whose resolved window ≤ 8192 → `result.model_window <= 8192` AND the lean charter (`behavioral_charter_lean()`) is in `result.system_prompt`.
  - a provider whose window ≥ 16384 → `behavioral_charter()` (full) in `result.system_prompt`.
  - provider selection raises → `result.system_prompt` still built with the FULL charter (lean=False), no exception.

```python
from stackowl.owls.base_prompt import behavioral_charter, behavioral_charter_lean
# ... boot assemble.run via set_services with a mock provider on the routed tier,
# controlling resolve_window via the provider _config.context_chars (chars//4):
#   context_chars=8000 → window 2000 (<=8192) → lean
#   context_chars=320000 → window 16384 (>=16384) → full
```

> Clear `model_window._WINDOW_CACHE` between cases (the resolver memoizes). Set the provider `_config.context_chars` to control the window deterministically (no probe needed).

- [ ] **Step 2: Run to verify it fails** (assemble doesn't resolve a window / pass lean yet).

- [ ] **Step 3: Implement** in `src/stackowl/pipeline/steps/assemble.py`. After the manifest/persona section, BEFORE `base = build_base_prompt(...)`, resolve the window + lean (fail-safe), and thread `lean` into both builders:
```python
    # Model-aware lean charter/DNA: resolve THIS turn's window (shared selection +
    # Slice-1 resolve_window, memoized) so a small-window/weak model gets the lean
    # charter + suppressed backfiring DNA. Fail-safe: any error → full prompt.
    from stackowl.owls.base_prompt import LEAN_WINDOW_THRESHOLD
    lean = False
    model_window: int | None = None
    try:
        if services.provider_registry is not None:
            from stackowl.pipeline.provider_select import select_tool_provider
            from stackowl.providers.model_window import resolve_window
            _p = select_tool_provider(
                services.provider_registry, services, state, log_selection=False,
            )
            _pc = getattr(_p, "_config", None)
            model_window = await resolve_window(
                provider_name=getattr(_p, "name", "") or "",
                base_url=_pc.base_url if _pc is not None else None,
                model=(_pc.default_model if _pc is not None else "") or "",
                context_chars=(_pc.context_chars if _pc is not None else None),
                protocol=getattr(_p, "protocol", "") or "",
            )
            lean = model_window <= LEAN_WINDOW_THRESHOLD
    except Exception as exc:  # no-hidden-errors: degrade to the FULL prompt, never crash
        log.engine.warning(
            "[pipeline] assemble: window resolution failed — full charter",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        lean = False
```
Then change `persona = _injector.inject(manifest, manifest.dna)` → `persona = _injector.inject(manifest, manifest.dna, lean=lean)` (note: the persona is built earlier in the function — move the window-resolution block ABOVE the persona injection, OR compute `lean` before the persona section; ensure `lean` is in scope where `inject` is called). And `base = build_base_prompt(now_local())` → `base = build_base_prompt(now_local(), lean=lean)`. Finally stamp the window: change the final `return state.evolve(system_prompt=system_prompt)` → `return state.evolve(system_prompt=system_prompt, model_window=model_window)`.

> IMPORTANT ordering: `lean` must be computed BEFORE `_injector.inject(...)` is called. Restructure `assemble.run` so the window/lean block runs right after `manifest` is resolved and before the `persona = _injector.inject(...)` line. Keep the existing fail-safe try/except around persona/skill/base building.

- [ ] **Step 3b: execute reuses the window.** In `execute._run_with_tools`, where Slice 1 calls `resolve_window` for the budget, prefer the already-resolved `state.model_window` when set: `_window = state.model_window if state.model_window is not None else await resolve_window(...)`. (The values are equal via memoization; this just avoids a redundant call and documents the shared fact.) Keep the rest unchanged.

- [ ] **Step 4: Verify** — `uv run pytest tests/pipeline/steps/test_assemble_model_aware.py -q` (pass) + assemble/execute regression `uv run pytest tests/pipeline/steps/ -q` + `uv run mypy src/stackowl/pipeline/steps/assemble.py src/stackowl/pipeline/steps/execute.py` + `uv run ruff check`.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/pipeline/steps/assemble.py src/stackowl/pipeline/steps/execute.py tests/pipeline/steps/test_assemble_model_aware.py
git commit -m "feat(v2): assemble resolves the model window → lean charter+DNA for weak models (fail-safe to full)"
```

---

## Task 5: Gateway journey + full regression

**Files:**
- Create: `tests/journeys/test_model_aware_charter_journey.py`

**Context:** End-to-end: a small-window owl turn → the assembled system prompt is the lean charter + no citation directive; a large-window owl turn → full charter + directives. STUDY `tests/journeys/test_context_budget_journey.py` (Slice 1 — sets `context_chars` to control the window + boots the real backend) and reuse its harness.

- [ ] **Step 1: Small-window journey.** Boot the real backend; provider `_config.context_chars` small (e.g. 8000 → window 2000 ≤ 8192). Run a turn through to (at least) the assemble step; capture `state.system_prompt` (or run the full turn and inspect the delivered state). Assert the system prompt contains `behavioral_charter_lean()` content (a lean-only phrase) and NOT the full-charter-only phrase, and `state.model_window <= 8192`. (Clear `model_window._WINDOW_CACHE` in the test.)

- [ ] **Step 2: Large-window control.** `context_chars` large (e.g. 320000 → clamps to 16384) → assert the FULL charter is used and `state.model_window >= 16384`.

- [ ] **Step 2b: Run; confirm PASS.** If a small-window turn still gets the full charter, the wiring is wrong — STOP, report BLOCKED. `uv run pytest tests/journeys/test_model_aware_charter_journey.py -q`.

- [ ] **Step 3: Full regression.** `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`. Report EXACT counts. The shared `select_tool_provider` is now used by execute + assemble — watch every journey that drives a tool turn; if a journey's provider double lacks something `select_tool_provider` reads, FIX the double (absorb) and note it. Watch the conversational-bypass + context-budget + self-heal journeys. ZERO failures. Real wiring break → STOP, report BLOCKED.

- [ ] **Step 4: Lint + commit.**
```bash
uv run ruff check tests/journeys/test_model_aware_charter_journey.py
git add tests/journeys/test_model_aware_charter_journey.py <any double fixed>
git commit -m "test(v2): model-aware charter journey — small window → lean, large → full (FR1/FR3)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1 lean charter → T2 + T4 + T5; FR2 lean DNA → T3; FR3 strong byte-identical → T2 (build_base_prompt) + T3 (inject) byte-identical tests; FR4 fail-safe → T4 (selection-raises case); FR5 shared selection → T1; FR6 state carries window → T2 (field) + T4 (stamp) + T3b (execute reuse); FR7 regression → T5. All covered.
- **Placeholder scan:** the awkward `test_build_base_prompt_full_byte_identical` body in T2-Step1 is flagged with an explicit replacement instruction (capture once, assert default==lean=False). T1/T3/T4/T5 instruct READING existing fixtures/harnesses and reusing them (concrete grounding). The lean charter + lean-DNA + assemble code are written out in full. No TBD/TODO.
- **Type consistency:** `select_tool_provider(registry, services, state, *, log_selection=True) -> ModelProvider` (T1) used in T4 + execute. `build_base_prompt(now, *, lean=False)` (T2) used in T4. `inject(manifest, dna, *, lean=False)` (T3) used in T4. `LEAN_WINDOW_THRESHOLD=8192` (T2) used in T4. `state.model_window: int|None` (T2) stamped in T4, read in T3b/T5. Consistent.

## Risk & containment
- **Risk:** assemble calling `select_tool_provider` changes behavior / double-selects with execute. **Contained:** `log_selection=False` (quiet); selection is deterministic + window memoized → assemble's choice == execute's; T1 proves the quiet path; T5 full regression.
- **Risk:** assemble window-resolution raises → broken turn. **Contained:** try/except → lean=False, full prompt; T4 asserts the raise case completes.
- **Risk:** a strong-model prompt drifts. **Contained:** lean=False is byte-identical (T2/T3 assert it); existing charter/dna/assemble tests stay green.
- **Risk:** the directive latch needs warm-up so lean-DNA tests are flaky. **Contained:** T3 follows the existing dna-test latch idiom.
- **Rollback:** see spec — revert the extraction + field + assemble block + lean params; strong path unchanged.
