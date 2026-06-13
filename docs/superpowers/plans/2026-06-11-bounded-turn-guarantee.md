# Bounded-Turn Guarantee Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee every turn terminates with a delivered reply in bounded time/steps even when a weak model spirals — by activating the dormant `BudgetGovernor` with a default backstop and closing the `decide_nudge` escalation-reward hole with an absolute nudge ceiling.

**Architecture:** (1) `decide_nudge` gains an absolute per-turn nudge ceiling that fires regardless of escalation; both providers' enforce loops track `nudges_issued`. (2) `execute.run` substitutes a default backstop `ResourceCaps(max_time_s, max_steps)` when an owl sets no explicit caps, so the existing tested `BudgetGovernor` always runs; the default-backstop callback is built non-interactive (no Raise prompt) and its breach marker is suppressed (best-available/floor only). Reuses `BudgetGovernor`/`make_budget_callback`/floor.

**Tech Stack:** Python 3.13, existing `BudgetGovernor`/`make_budget_callback` (`pipeline/budget/`), `decide_nudge` (`pipeline/supervisor.py`), `ResourceCaps` (`authz/bounds.py`), pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/pipeline/supervisor.py` | Modify (`decide_nudge` + constant) | Absolute nudge ceiling `MAX_TURN_NUDGES` |
| `src/stackowl/providers/openai_provider.py` | Modify (enforce loop) | Track `nudges_issued`, pass to `decide_nudge` |
| `src/stackowl/providers/anthropic_provider.py` | Modify (enforce loop) | Same (parity) |
| `src/stackowl/authz/bounds.py` | Modify (constants) | `DEFAULT_TURN_MAX_TIME_S`, `DEFAULT_TURN_MAX_STEPS` |
| `src/stackowl/pipeline/steps/execute.py` | Modify (caps/governor + breach marker) | Default backstop caps; non-interactive callback; suppress marker on default backstop |
| `tests/pipeline/test_decide_nudge_ceiling.py` | **Create** | ceiling unit |
| `tests/pipeline/test_default_backstop_caps.py` | **Create** | governor-always-on + non-interactive + marker-suppression |
| `tests/journeys/test_bounded_turn_journey.py` | **Create** | tool-spam terminates + happy-path unchanged |

---

## Task 1: `decide_nudge` absolute nudge ceiling

**Files:**
- Modify: `src/stackowl/pipeline/supervisor.py` (`decide_nudge` + a module constant)
- Test: `tests/pipeline/test_decide_nudge_ceiling.py`

**Context:** `decide_nudge` (supervisor.py:48) is keyword-only, returns `(directive, new_budget, calls_at_last_nudge)`. The escalation-reward bug: when `escalated` (model made a new tool call since last nudge), `nudge_budget` is not decremented → never exhausts. Fix: add an absolute ceiling on TOTAL nudges issued, enforced regardless of escalation. Keep the return tuple shape unchanged (the caller tracks the count and passes it in).

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_decide_nudge_ceiling.py`:

```python
from stackowl.pipeline.supervisor import decide_nudge, MAX_TURN_NUDGES
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE


def _giveup_calls(n):
    # n distinct tool calls so each round looks like an escalation
    return [{"name": f"tool{i}", "args": {}} for i in range(n)]


def test_ceiling_fires_despite_continuous_escalation():
    # Simulate the gemma spiral: judge says give-up every round, model escalates
    # (a new tool call) every round, so budget would never decrement. The ceiling
    # must stop it at MAX_TURN_NUDGES regardless.
    nudge_budget = 2
    calls_at_last_nudge = None
    issued = 0
    for round_i in range(MAX_TURN_NUDGES + 3):
        directive, nudge_budget, calls_at_last_nudge = decide_nudge(
            judge_directive=PERSISTENCE_DIRECTIVE,
            all_calls=_giveup_calls(round_i + 1),  # always escalating (+1 each round)
            draft="not done yet",
            nudge_budget=nudge_budget,
            calls_at_last_nudge=calls_at_last_nudge,
            nudges_issued=issued,
        )
        if directive is not None:
            issued += 1
    # Without the ceiling, escalation would keep budget at 2 and nudge forever.
    assert issued == MAX_TURN_NUDGES, f"expected ceiling at {MAX_TURN_NUDGES}, got {issued}"


def test_below_ceiling_escalation_still_waives_budget_cost():
    # Below the ceiling, an escalating round must NOT decrement the budget (reward preserved).
    directive, new_budget, _ = decide_nudge(
        judge_directive=PERSISTENCE_DIRECTIVE,
        all_calls=_giveup_calls(5),
        draft="x",
        nudge_budget=2,
        calls_at_last_nudge=4,   # current(5) > 4 → escalated
        nudges_issued=0,
    )
    assert directive is not None
    assert new_budget == 2   # escalation waived the cost — budget intact


def test_ceiling_param_defaults_preserve_prior_behavior():
    # nudges_issued defaulting to 0 means a single call behaves as before.
    directive, _, _ = decide_nudge(
        judge_directive=PERSISTENCE_DIRECTIVE,
        all_calls=_giveup_calls(1),
        draft="x",
        nudge_budget=2,
        calls_at_last_nudge=None,
    )
    assert directive is not None
```

> First read the EXISTING `decide_nudge` tests (grep `tests/` for `decide_nudge`) to match fixture/import style and confirm `PERSISTENCE_DIRECTIVE`'s import path.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_decide_nudge_ceiling.py -q`
Expected: FAIL — `TypeError: decide_nudge() got an unexpected keyword argument 'nudges_issued'` / `ImportError: MAX_TURN_NUDGES`.

- [ ] **Step 3: Implement.** In `supervisor.py`, add the constant near the top:
```python
# Absolute per-turn nudge ceiling — escalation waives the per-nudge BUDGET cost,
# but it may NEVER suspend this hard ceiling. A weak model that tool-spams every
# round (continuous "escalation") would otherwise nudge forever (see the live
# bounded-turn bug). Once this many nudges have been issued this turn, stop.
MAX_TURN_NUDGES = 6
```
Change `decide_nudge`'s signature to add the keyword-only param (with defaults preserving old behavior):
```python
def decide_nudge(
    *,
    judge_directive: str | None,
    all_calls: list[dict[str, object]],
    draft: str,
    nudge_budget: int,
    calls_at_last_nudge: int | None,
    nudges_issued: int = 0,
    max_nudges: int = MAX_TURN_NUDGES,
) -> tuple[str | None, int, int | None]:
```
Add the ceiling check FIRST in the body (before the existing `nudge_budget <= 0` / veto logic), so the ceiling is authoritative:
```python
    if nudges_issued >= max_nudges:
        log.engine.info(
            "supervisor.decide_nudge: absolute nudge ceiling reached — accepting (floor is the backstop)",
            extra={"_fields": {"nudges_issued": nudges_issued, "max_nudges": max_nudges}},
        )
        return None, nudge_budget, calls_at_last_nudge
```
Leave the rest (the `nudge_budget <= 0` guard, `apply_structural_veto`, escalation-reward) unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_decide_nudge_ceiling.py -q` (3 passed). Then run the existing supervisor tests to confirm no regression: `uv run pytest tests/ -q -k "decide_nudge or supervisor"`. `uv run mypy src/stackowl/pipeline/supervisor.py` (clean); `uv run ruff check` the 2 files.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/supervisor.py tests/pipeline/test_decide_nudge_ceiling.py
git commit -m "feat(v2): decide_nudge absolute nudge ceiling — escalation can't suspend it"
```

---

## Task 2: Wire `nudges_issued` tracking into both provider enforce loops

**Files:**
- Modify: `src/stackowl/providers/openai_provider.py` (the `_enforce`/`decide_nudge` caller, ~line 199-236)
- Modify: `src/stackowl/providers/anthropic_provider.py` (the parallel enforce loop)
- Test: covered by Task 5's journey (this is loop-state wiring); plus a targeted check below.

**Context:** Each provider's `complete_with_tools` holds nudge state as nonlocals (`nudge_budget = 2`, `calls_at_last_nudge`). Add a `nudges_issued` nonlocal, pass it to `decide_nudge`, and increment it whenever a directive is issued. Without this, `decide_nudge`'s ceiling never sees a rising count and never fires.

- [ ] **Step 1: Read both enforce loops.** In `openai_provider.py` find `nudge_budget = 2` (~line 199) and the `_enforce` closure that calls `decide_nudge` (~225) and the `if directive:` block (~232). Find the structurally-identical block in `anthropic_provider.py`.

- [ ] **Step 2: Implement (openai_provider.py).** Add the nonlocal init next to `nudge_budget`:
```python
        nudge_budget = 2
        calls_at_last_nudge: int | None = None
        nudges_issued = 0
```
In the `_enforce` closure, declare it nonlocal and pass + increment:
```python
            nonlocal nudge_budget, calls_at_last_nudge, nudges_issued
            ...
            directive, nudge_budget, calls_at_last_nudge = decide_nudge(
                judge_directive=judge_directive,
                all_calls=all_calls,
                draft=content,
                nudge_budget=nudge_budget,
                calls_at_last_nudge=calls_at_last_nudge,
                nudges_issued=nudges_issued,
            )
            if directive:
                nudges_issued += 1
                log.engine.info(
                    "[openai] complete_with_tools: persistence nudge — continuing loop",
                    extra={"_fields": {"provider": self._name, "nudge_budget": nudge_budget,
                                       "nudges_issued": nudges_issued}},
                )
            return directive
```

- [ ] **Step 3: Implement (anthropic_provider.py).** Apply the identical change to its enforce loop (add `nudges_issued` nonlocal init, pass to `decide_nudge`, `nudges_issued += 1` when a directive is issued). Match its existing logging style.

- [ ] **Step 4: Verify.** `uv run mypy src/stackowl/providers/openai_provider.py src/stackowl/providers/anthropic_provider.py` (clean); `uv run ruff check` both. Run existing provider enforce/veto tests: `uv run pytest tests/providers/ -q -k "enforce or veto or react"`. Expected: PASS (behavior below the ceiling is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/openai_provider.py src/stackowl/providers/anthropic_provider.py
git commit -m "feat(v2): both provider enforce loops track nudges_issued for the nudge ceiling"
```

---

## Task 3: Default backstop caps — activate the dormant `BudgetGovernor`

**Files:**
- Modify: `src/stackowl/authz/bounds.py` (constants near `ResourceCaps`)
- Modify: `src/stackowl/pipeline/steps/execute.py` (caps/governor block, ~678-700)
- Test: `tests/pipeline/test_default_backstop_caps.py`

**Context:** `execute.run` builds `_caps` (line 678-685), computes `_has_caps` (687), and only builds the `BudgetGovernor` + `_budget_cb` when `_has_caps` (690-700). Default caps are all-None so the governor is dormant. Fix: when no explicit caps, substitute a default backstop and build the governor always; build its callback NON-interactive (no Raise prompt).

- [ ] **Step 1: Add constants** to `src/stackowl/authz/bounds.py` (near `ResourceCaps`):
```python
# Default per-turn safety backstop (applied only when an owl sets NO explicit caps).
# Guarantees every turn terminates with a reply in bounded time/steps even when a
# weak model spirals. Generous for happy-path multi-step work; bounds the pathology.
DEFAULT_TURN_MAX_TIME_S = 120.0
DEFAULT_TURN_MAX_STEPS = 20
```

- [ ] **Step 2: Write the failing test** `tests/pipeline/test_default_backstop_caps.py`. This is a focused test on the caps-resolution behavior. Easiest: a small helper that replicates/▶calls the resolution, OR assert via running `execute.run` with a registry + a mock provider and checking the governor was active. Prefer a direct check: import the constants and assert the wiring. Since the governor construction is inline in `run`, test it through a minimal `execute.run` invocation with a scripted provider that completes in 1 iteration, asserting (a) no crash and (b) — to prove the governor is active — that a turn exceeding the step backstop stops. A cleaner unit: extract is out of scope; instead assert the CONSTANTS exist and write the behavioral proof in the Task 5 journey. For THIS task, write:
```python
from stackowl.authz.bounds import DEFAULT_TURN_MAX_TIME_S, DEFAULT_TURN_MAX_STEPS, ResourceCaps


def test_backstop_constants_present_and_sane():
    assert DEFAULT_TURN_MAX_TIME_S == 120.0
    assert DEFAULT_TURN_MAX_STEPS == 20
    # Default ResourceCaps remain all-None (the backstop is applied in execute, not here)
    c = ResourceCaps()
    assert c.max_time_s is None and c.max_steps is None and c.max_cost_usd is None
```
Plus a behavioral test driving `execute.run` with NO explicit caps + a scripted provider that would loop, asserting the governor stops it. Model this on `tests/journeys/test_budget_cap.py`'s harness (it already drives a capped turn). Specifically assert: with the default backstop, a provider scripted to attempt many iterations is stopped before `tool_max_iterations=30` (i.e. the governor's `max_steps=20` bit). Reuse `test_budget_cap`'s scripted-provider + `_run` helper, but DON'T set explicit caps on the owl — rely on the new default. Assert `len(provider.completed_iterations) <= DEFAULT_TURN_MAX_STEPS`.

> Read `tests/journeys/test_budget_cap.py` fully first and mirror its harness. If wiring the full `execute.run` proves heavy, the Task 5 journey covers the behavioral proof end-to-end; keep this task's behavioral test best-effort but TRY it, and always keep the constants test.

- [ ] **Step 3: Run to verify it fails** (constants don't exist yet).

Run: `uv run pytest tests/pipeline/test_default_backstop_caps.py -q` → FAIL (ImportError).

- [ ] **Step 4: Implement** in `execute.py`, replacing the `_has_caps`/governor block (~687-701) with:
```python
    _has_explicit_caps = any(
        c is not None for c in (_caps.max_steps, _caps.max_time_s, _caps.max_cost_usd)
    )
    # Default safety backstop: when the owl set NO explicit caps, apply a default
    # time/step bound so the (already-tested) BudgetGovernor always runs and every
    # turn terminates in bounded time even when a weak model spirals. The default
    # backstop is NON-interactive (it just stops + delivers — no "Raise?" prompt;
    # that UX is reserved for explicitly-configured owl caps).
    _default_backstop = not _has_explicit_caps
    if _default_backstop:
        _caps = ResourceCaps(
            max_time_s=DEFAULT_TURN_MAX_TIME_S, max_steps=DEFAULT_TURN_MAX_STEPS,
        )
    _governor = BudgetGovernor(
        _caps, cost_tracker=_services.cost_tracker, trace_id=state.trace_id,
        started_monotonic=time.monotonic(), clock=_MonotonicClock(),
    )
    _budget_cb = make_budget_callback(
        _governor,
        interactive=(state.interactive and not _default_backstop),
        clarify=_services.clarify_gateway,
        session_id=state.session_id, channel=state.channel,
    )
```
Add the import: `from stackowl.authz.bounds import DEFAULT_TURN_MAX_TIME_S, DEFAULT_TURN_MAX_STEPS` (alongside the existing `ResourceCaps` import at line 10). The `_default_backstop` local must be in scope where the BudgetBreach is later caught (Task 4 uses it) — it is, since both are in `run`.

- [ ] **Step 5: Run to verify it passes** + regression.

Run: `uv run pytest tests/pipeline/test_default_backstop_caps.py -q`; `uv run pytest tests/journeys/test_budget_cap.py -q` (explicit-cap behavior unchanged — MUST stay green); `uv run mypy src/stackowl/pipeline/steps/execute.py src/stackowl/authz/bounds.py` (clean); `uv run ruff check` the changed files.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/authz/bounds.py src/stackowl/pipeline/steps/execute.py tests/pipeline/test_default_backstop_caps.py
git commit -m "feat(v2): default per-turn backstop activates the BudgetGovernor (non-interactive)"
```

---

## Task 4: Suppress the budget-stop marker for the default backstop

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (the `BudgetBreach` catch / "stopped" chunk finalizer)
- Test: add to `tests/pipeline/test_default_backstop_caps.py`

**Context:** When a `BudgetBreach` is caught in `execute.run`, the finalizer builds a "stopped" chunk that appends a marker note to the partial (see the region ~822-924; there is a stopped-note like `"[stopped: ...]"`). Per choice A, the **default backstop** should deliver clean best-available (no marker); **explicit caps** keep their marker. Gate the marker on `not _default_backstop`.

- [ ] **Step 1: Read the BudgetBreach handler.** In `execute.py`, find the `except BudgetBreach as exc:` block in `run` (around line 822-924). Identify exactly where the marker text is concatenated onto `exc.partial_text` to form the stopped chunk content. (There is a `_stopped_note`/marker string built there.)

- [ ] **Step 2: Write the failing test** (append to `tests/pipeline/test_default_backstop_caps.py`). Drive `execute.run` so a BudgetBreach fires under the DEFAULT backstop (no explicit caps; scripted provider loops past the step backstop) and assert the delivered text is the partial WITHOUT the marker substring. Then a second case with an EXPLICIT cap owl asserts the marker IS present. Mirror `test_budget_cap.py`'s harness for both. Concretely assert:
```python
# default backstop breach → clean partial, NO marker
assert "budget cap" not in delivered.lower() and "stopped:" not in delivered.lower()
assert partial_text_fragment in delivered   # the model's partial still delivered
# explicit-cap breach (separate case) → marker present (unchanged behavior)
assert "budget cap" in delivered_explicit.lower() or "stopped" in delivered_explicit.lower()
```
> If reproducing a default-backstop BudgetBreach in a unit is heavy, the Task 5 journey covers it; keep this test best-effort but TRY it. The exact marker substrings come from Step 1's reading — assert against the REAL marker text you find, not a guess.

- [ ] **Step 3: Run to verify it fails** (marker currently always added).

- [ ] **Step 4: Implement.** In the `BudgetBreach` finalizer, gate the marker concatenation on `not _default_backstop`. Sketch (adapt to the real code found in Step 1):
```python
    except BudgetBreach as exc:
        ...
        if _default_backstop:
            # Default safety backstop: deliver clean best-available; floor handles empty.
            _breach_content = exc.partial_text or ""
        else:
            _breach_marker = "[stopped: budget cap reached — ending this turn here.]"  # existing text
            _breach_content = (
                f"{exc.partial_text}\n\n{_breach_marker}" if exc.partial_text else _breach_marker
            )
        ...
```
IMPORTANT: when `_default_backstop` and `exc.partial_text` is empty, `_breach_content` is `""` — the empty response must then flow to the existing never-empty FLOOR (the self-heal floor / `surface_critical_failure`), NOT deliver an empty chunk. Confirm the empty case routes to the floor (it should — an empty/floor-only response triggers the critical-failure cascade). If an empty `_breach_content` would otherwise emit an empty chunk, do NOT emit a chunk in that case (let the floor own it). Verify against the existing finalizer logic.

- [ ] **Step 5: Run + regression.**

Run: `uv run pytest tests/pipeline/test_default_backstop_caps.py -q`; `uv run pytest tests/journeys/test_budget_cap.py -q` (explicit-cap marker unchanged); `uv run pytest tests/journeys/test_self_heal_invariant.py -q` (floor still works); `uv run mypy src/stackowl/pipeline/steps/execute.py`; `uv run ruff check`.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_default_backstop_caps.py
git commit -m "feat(v2): default backstop delivers clean best-available (no budget marker); floor on empty"
```

---

## Task 5: Gateway journey — tool-spam terminates + happy path unchanged + regression

**Files:**
- Create: `tests/journeys/test_bounded_turn_journey.py`

**Context:** The live-bug regression. Drive the REAL backend with a scripted provider that spams a new tool call every round and never delivers — assert the turn TERMINATES with a delivered reply (does NOT run to the 30-iteration cap). STUDY `tests/journeys/test_budget_cap.py` (capped-turn harness) and `tests/journeys/test_self_heal_invariant.py` (give-up/nudge harness) for the boot + scripted-provider pattern; reuse it.

- [ ] **Step 1: Write the failing/▶passing journey (FR1/FR3).** Scripted provider whose `complete_with_tools` ALWAYS returns a new tool call + a non-answer draft, and a persistence judge double that ALWAYS rules give-up (forcing nudges). With the nudge ceiling (Task 1-2) the turn must stop after ~`MAX_TURN_NUDGES` nudges and deliver. Assert:
  - the turn DELIVERS a non-empty reply (`state.responses` non-empty), and
  - it stopped well under the 30 hard cap — assert the provider's completed-iteration count is `<= MAX_TURN_NUDGES + a small margin` (e.g. ≤ 8), proving the ceiling bit, not the 30-cap.
  - (If the provider tracks iteration count like `test_budget_cap`'s does, assert on it; else assert wall-clock/iteration via the nudge-count log.)

```python
# tests/journeys/test_bounded_turn_journey.py
# Boot mirrors test_self_heal_invariant.py / test_budget_cap.py. Scripted provider
# spams a tool call + non-answer every round; judge double always says give-up.
# Assert: state.responses non-empty AND provider rounds <= 8 (ceiling bit, not the 30-cap).
from stackowl.pipeline.supervisor import MAX_TURN_NUDGES
```

- [ ] **Step 2: Run; confirm it PASSES** (feature wired). If it runs to many iterations / doesn't terminate, the ceiling isn't wired correctly — STOP and report BLOCKED (do not raise the assertion bound to mask it). If it fails on harness construction, fix the harness until the assertion is meaningful.

Run: `uv run pytest tests/journeys/test_bounded_turn_journey.py -q`

- [ ] **Step 3: Add the happy-path-unchanged test (FR5).** A scripted provider that answers in 1 iteration (no give-up) → assert it delivers the answer normally, no nudge, no truncation marker, identical to today. This guards against the backstop/ceiling altering normal turns.

- [ ] **Step 4: Full regression (FR7).**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: prior 91 passed + 1 skipped, plus the new journey's tests → report exact counts. ZERO failures/regressions. Watch especially `test_budget_cap` (explicit caps) and `test_self_heal_*` (nudge/floor) — if any regress, STOP and report BLOCKED.

- [ ] **Step 5: Lint + commit.**

Run: `uv run ruff check tests/journeys/test_bounded_turn_journey.py` (clean).

```bash
git add tests/journeys/test_bounded_turn_journey.py
git commit -m "test(v2): bounded-turn journey — tool-spam terminates with a reply (FR1/FR3), happy path unchanged (FR5)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1 (nudge ceiling)→Task 1+2+5; FR2 (time/step backstop)→Task 3+5; FR3 (best-available/floor)→Task 4+5; FR4 (no Raise/marker on default backstop)→Task 3 (interactive=False) + Task 4 (marker suppressed); FR5 (happy path unchanged)→Task 5 + the `interactive`/explicit-cap gates; FR6 (explicit caps unchanged)→Task 3/4 gate on `_default_backstop`, `test_budget_cap` stays green; FR7→Task 5. All covered.
- **Placeholder scan:** Tasks 3/4 instruct the implementer to READ a specific named handler (the `BudgetBreach` catch ~822-924) and assert against the REAL marker text found — that's "find this concrete existing thing," not deferred work. No TBD/TODO.
- **Type consistency:** `decide_nudge(..., nudges_issued: int = 0, max_nudges: int = MAX_TURN_NUDGES) -> (str|None, int, int|None)` (return shape UNCHANGED); `nudges_issued` nonlocal in both providers; `DEFAULT_TURN_MAX_TIME_S`/`DEFAULT_TURN_MAX_STEPS`/`MAX_TURN_NUDGES` constants; `_default_backstop` flag in execute.run. Consistent across tasks.

## Risk & containment
- **Risk (FR5/FR6):** the default backstop or ceiling alters normal turns / explicit-cap turns. **Contained:** governor never trips for a turn within 120s/20-steps/6-nudges (Task 5 happy-path test); explicit caps bypass the default-backstop branch and keep Raise+marker (`test_budget_cap` stays green — Task 3/4 regression gate).
- **Risk:** empty partial on a default-backstop breach delivers an empty chunk. **Contained:** Task 4 routes empty → existing floor (verified against the finalizer); `test_self_heal_invariant` stays green.
- **Risk:** ceiling not actually wired (loop still spirals). **Contained:** Task 5 asserts termination ≤8 rounds and BLOCKS rather than masking.
- **Rollback:** see spec — additive/config; revert leaves `tool_max_iterations=30` + explicit-cap behavior intact.
