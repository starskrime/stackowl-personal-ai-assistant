# Intent-Gated Lean Context (Conversational Bypass) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop drowning the weak model: classify a turn as `conversational` vs `standard` (reusing the router's cheap call, fail-safe to standard), and give conversational turns a tiny prompt — zero tools, no tool loop, lean assembly — so "hi" replies in seconds instead of a 24k-token 11-minute spiral.

**Architecture:** The `SecretaryRouter` call returns `(owl_name, intent_class)` — owl on line 1 (parsing unchanged), class on an additive line 2 (fail-safe to `standard`). `triage` stamps a new `PipelineState.intent_class` (default `standard` → standard turns byte-identical). `classify` skips the heavy memory/skills/lessons/reflections/graph blocks when conversational; `execute` presents zero tools and takes the plain-stream path (no tool loop) when conversational. Per-block token instrumentation is logged at execute to measure.

**Tech Stack:** Python 3.13, Pydantic-frozen `PipelineState`, the existing `SecretaryRouter`/`triage`/`classify`/`execute` pipeline steps, pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/pipeline/state.py` | Modify | new field `intent_class: Literal["conversational","standard"] = "standard"` |
| `src/stackowl/owls/router.py` | Modify (`_build_prompt`, `_parse_choice`/new class parse, `route`) | return `(owl, intent_class)`; owl parse unchanged, class fail-safe |
| `src/stackowl/pipeline/steps/triage.py` | Modify (`run`) | stamp `intent_class` on state |
| `src/stackowl/pipeline/steps/classify.py` | Modify (`run`) | skip heavy blocks when conversational |
| `src/stackowl/pipeline/steps/execute.py` | Modify (`run`) | conversational → zero tools / plain-stream; per-block token instrumentation |
| `tests/owls/test_router_intent_class.py` | **Create** | router class parse + owl-unchanged |
| `tests/pipeline/test_classify_conversational_lean.py` | **Create** | classify skips blocks |
| `tests/pipeline/test_execute_conversational_notools.py` | **Create** | execute zero-tools + instrumentation |
| `tests/journeys/test_conversational_bypass_journey.py` | **Create** | "hi" tiny prompt + no loop; standard unchanged |

---

## Task 1: `intent_class` signal — router returns it, triage stamps it

**Files:**
- Modify: `src/stackowl/pipeline/state.py`
- Modify: `src/stackowl/owls/router.py`
- Modify: `src/stackowl/pipeline/steps/triage.py`
- Test: `tests/owls/test_router_intent_class.py`

**Context:** `SecretaryRouter.route(state) -> str` returns an owl name; `_parse_choice` takes `splitlines()[0]` (so line 1 = owl name). `_build_prompt` ends with "Reply with exactly one owl name:". `triage.run` does `chosen = await router.route(state); return state.evolve(owl_name=chosen)`. We make `route` return `(owl, intent_class)` with the owl parse UNCHANGED (line 1) and the class additive (line 2), fail-safe to `standard`.

- [ ] **Step 1: Write the failing test** `tests/owls/test_router_intent_class.py`. Study `tests/` for existing router tests first (grep `SecretaryRouter`) to reuse the fake provider/owl-registry fixtures. The test drives `route()` with a scripted fast-tier provider whose `complete()` returns a canned string, asserting the parsed `(owl, intent_class)`:

```python
import pytest
from stackowl.owls.router import SecretaryRouter, RouteResult
# Reuse the fake ProviderRegistry + OwlRegistry pattern from the existing router test.

@pytest.mark.asyncio
async def test_owl_line1_class_line2_conversational(router_env):
    router_env.set_reply("secretary\nconversational")
    res = await router_env.router.route(router_env.state("hi"))
    assert isinstance(res, RouteResult)
    assert res.owl_name == "secretary"
    assert res.intent_class == "conversational"

@pytest.mark.asyncio
async def test_class_defaults_standard_when_absent(router_env):
    router_env.set_reply("secretary")          # owl only, no class line
    res = await router_env.router.route(router_env.state("do a task"))
    assert res.owl_name == "secretary"
    assert res.intent_class == "standard"       # fail-safe

@pytest.mark.asyncio
async def test_garbled_class_falls_back_to_standard(router_env):
    router_env.set_reply("secretary\nbanana")  # unparseable class
    res = await router_env.router.route(router_env.state("x"))
    assert res.owl_name == "secretary"
    assert res.intent_class == "standard"

@pytest.mark.asyncio
async def test_owl_selection_unchanged_with_class_line(router_env):
    # owl parse is line-1 only — a class line must not change owl selection
    router_env.set_reply("research_owl\nstandard")
    res = await router_env.router.route(router_env.state("research X"))
    assert res.owl_name == "research_owl"   # assuming research_owl is a known owl in the fixture
```

> Build `router_env` mirroring the existing router test's fakes (a provider whose `complete()` returns the set reply; an owl registry with secretary + one specialist). If no router test exists, construct minimal fakes: a provider with `async def complete(...) -> CompletionResult` returning the canned content, and an owl registry `.list()` returning manifests with `.name`/`.role`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/owls/test_router_intent_class.py -q`
Expected: FAIL — `ImportError: RouteResult` / `route()` returns a str not RouteResult.

- [ ] **Step 3: Implement.**

(a) `src/stackowl/pipeline/state.py` — add the field (with the other scalar fields; import `Literal` if not present):
```python
    # Coarse intent class from the router (fail-safe default "standard"). A
    # "conversational" turn (greeting/small-talk, no task) gets a lean prompt:
    # zero tools + no tool loop + skipped memory/skills/lessons. "standard" is
    # byte-identical to prior behavior — every unclassified path rides this default.
    intent_class: Literal["conversational", "standard"] = "standard"
```

(b) `src/stackowl/owls/router.py` — add a `RouteResult` dataclass + a class parser + change `route` to return it. Near the top:
```python
from dataclasses import dataclass

_VALID_CLASSES = {"conversational", "standard"}


@dataclass(frozen=True)
class RouteResult:
    owl_name: str
    intent_class: str  # "conversational" | "standard"
```
Add a fail-safe class parser method:
```python
    def _parse_intent_class(self, raw: str) -> str:
        """Read the OPTIONAL 2nd line as the intent class. Fail-safe → 'standard'."""
        lines = (raw or "").strip().splitlines()
        if len(lines) < 2:
            return "standard"
        token = lines[1].strip().strip("\"'`.,:;()[]{}<>").lower()
        return token if token in _VALID_CLASSES else "standard"
```
Extend `_build_prompt` to ask for the class on line 2 (append after the owl-name instruction):
```python
        return (
            "You are a router. Reply with the name of the best owl for the "
            "request on the FIRST line.\n"
            "Available owls:\n"
            f"{owls_block}\n\n"
            "Then on a SECOND line write exactly 'conversational' if the request "
            "is only a greeting or small talk with no task to do, otherwise write "
            "'standard'.\n"
            f"Request: {user_text}\n"
            "First line: owl name. Second line: conversational or standard."
        )
```
(Match the existing `_build_prompt` variable names — it builds an owls list block; keep that. The key change is adding the 2nd-line instruction.)

In `route`, change the return paths: every `return _DEFAULT_FALLBACK` becomes `return RouteResult(_DEFAULT_FALLBACK, "standard")`, and the success path:
```python
        owl = self._parse_choice(result.content, known_names)   # UNCHANGED owl parse (line 1)
        intent_class = self._parse_intent_class(result.content)  # NEW, fail-safe
        return RouteResult(owl, intent_class)
```
Update the `route` return type annotation to `-> RouteResult`.

(c) `src/stackowl/pipeline/steps/triage.py` — update the caller:
```python
    result = await router.route(state)
    log.engine.info(
        "[pipeline] triage: routed",
        extra={"_fields": {"trace_id": state.trace_id, "owl": result.owl_name,
                           "intent_class": result.intent_class}},
    )
    return state.evolve(owl_name=result.owl_name, intent_class=result.intent_class)
```
(The other early-return paths in triage — direct-address, registries-missing — return `state.evolve(owl_name=...)` WITHOUT a class → they keep the `standard` default. Leave them; that's the intended fail-safe.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/owls/test_router_intent_class.py -q` (4 passed). Then existing router + triage tests: `uv run pytest tests/ -q -k "router or triage"` (no regression — owl selection unchanged). `uv run mypy src/stackowl/owls/router.py src/stackowl/pipeline/steps/triage.py src/stackowl/pipeline/state.py` (clean); `uv run ruff check` the changed files.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/state.py src/stackowl/owls/router.py src/stackowl/pipeline/steps/triage.py tests/owls/test_router_intent_class.py
git commit -m "feat(v2): router emits intent_class (owl line1 unchanged, class line2 fail-safe to standard)"
```

> **Contingency (per spec):** if `tests/ -k router` shows the 2-line prompt degrading OWL selection on the existing cases, STOP and report — the spec's fallback is a separate isolated binary classification call. Do not ship degraded routing.

---

## Task 2: `classify` — lean assembly for conversational turns

**Files:**
- Modify: `src/stackowl/pipeline/steps/classify.py` (`run`)
- Test: `tests/pipeline/test_classify_conversational_lean.py`

**Context:** `classify.run` gathers `skills_block`, `lessons_block`, `reflections_block`, `actions_block`, `context`, `graph_context` and joins them into `memory_context`. For a conversational turn these are pure overhead. Gate the heavy gathers on `state.intent_class`.

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_classify_conversational_lean.py`. Drive `classify.run` with a conversational-class state and a `StepServices` whose memory/skill components would otherwise return blocks; assert the heavy blocks are skipped. The cleanest assertion: monkeypatch/spy the `_gather_lessons`/`_gather_relevant_skills` module functions to record calls, and assert they are NOT called when `intent_class == "conversational"` (and ARE called when `standard`).

```python
import pytest
from stackowl.pipeline.steps import classify
from stackowl.pipeline.state import PipelineState

def _state(intent):
    return PipelineState(trace_id="t", session_id="s", input_text="hi", channel="cli",
                         owl_name="secretary", pipeline_step="classify", intent_class=intent)

@pytest.mark.asyncio
async def test_conversational_skips_heavy_blocks(monkeypatch):
    called = {"lessons": False, "skills": False}
    async def _no_lessons(*a, **k): called["lessons"] = True; return ""
    async def _no_skills(*a, **k): called["skills"] = True; return ""
    monkeypatch.setattr(classify, "_gather_lessons", _no_lessons)
    monkeypatch.setattr(classify, "_gather_relevant_skills", _no_skills)
    # ... set_services with minimal StepServices so run() proceeds ...
    out = await classify.run(_state("conversational"))
    assert called["lessons"] is False and called["skills"] is False

@pytest.mark.asyncio
async def test_standard_still_gathers(monkeypatch):
    called = {"lessons": False}
    async def _yes_lessons(*a, **k): called["lessons"] = True; return ""
    monkeypatch.setattr(classify, "_gather_lessons", _yes_lessons)
    out = await classify.run(_state("standard"))
    assert called["lessons"] is True
```
> Set up `StepServices` + `set_services`/`reset_services` so `classify.run` runs without a DB (the gathers are monkeypatched, and memory recall degrades gracefully when components are None — confirm by reading `classify.run`'s guards). Adjust the fixture to whatever `classify.run` needs to reach the gather block.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_classify_conversational_lean.py -q`
Expected: FAIL — `_gather_lessons`/`_gather_relevant_skills` ARE called for conversational (no gate yet).

- [ ] **Step 3: Implement.** In `classify.run`, wrap the heavy gathers in a conversational gate. Find where `skills_block`/`lessons_block`/`reflections_block`/`graph_context`/memory recall are computed and guard them:
```python
    _lean = state.intent_class == "conversational"
    if _lean:
        log.engine.info(
            "[pipeline] classify: conversational turn — lean assembly (skipping heavy blocks)",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
    skills_block = "" if _lean else await _gather_relevant_skills(state.input_text, limit=3, owned=owned)
    lessons_block = "" if _lean else await _gather_lessons(state.input_text, limit=3)
    reflections_block = "" if _lean else <existing reflections gather>
    graph_context = "" if _lean else <existing graph gather>
    # memory recall / long-term context block: also gate to "" when _lean
```
Read the actual variable names + gather calls in `classify.run` and apply the `"" if _lean else <existing>` pattern to each heavy block (skills, lessons, reflections, graph, long-term memory context). Leave `prefs_block` (cheap, always-in-view) and the history threading unchanged. The existing `parts = [p for p in (...) if p]` then naturally drops the empty blocks.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/test_classify_conversational_lean.py -q` (2 passed). Existing classify tests: `uv run pytest tests/pipeline/ -q -k classify` (standard path unchanged). `uv run mypy src/stackowl/pipeline/steps/classify.py`; `uv run ruff check`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/classify.py tests/pipeline/test_classify_conversational_lean.py
git commit -m "feat(v2): classify skips heavy memory/skills/lessons blocks on conversational turns"
```

---

## Task 3: `execute` — zero-tools plain-stream for conversational + per-block instrumentation

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`run`)
- Test: `tests/pipeline/test_execute_conversational_notools.py`

**Context:** `execute.run` (def ~line 1188) branches: `if tool_registry is not None and tool_registry.all(): return await _run_with_tools(state, provider, tool_registry)` then falls through to a plain token-stream path. Gate that branch so a conversational turn takes the plain-stream (zero tools, no tool loop). Add a structured per-block instrumentation log at the call.

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_execute_conversational_notools.py`. Assert that for a conversational state, `_run_with_tools` is NOT entered. Spy on `_run_with_tools`:

```python
import pytest
from stackowl.pipeline.steps import execute as exe
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.services import StepServices, set_services, reset_services
# a minimal provider + a tool_registry with >=1 tool, mirroring an existing execute/provider test.

def _state(intent):
    return PipelineState(trace_id="t", session_id="s", input_text="hi", channel="cli",
                         owl_name="secretary", pipeline_step="execute", intent_class=intent,
                         system_prompt="You are a helper.")

@pytest.mark.asyncio
async def test_conversational_does_not_enter_tool_loop(monkeypatch):
    entered = {"tools": False}
    async def _spy_run_with_tools(*a, **k):
        entered["tools"] = True
        return a[0]  # return state unchanged
    monkeypatch.setattr(exe, "_run_with_tools", _spy_run_with_tools)
    # services: a registry returning a mock provider whose stream() yields a token,
    # and a tool_registry with >=1 tool (so the branch WOULD trigger for standard).
    services = <StepServices with provider_registry + a non-empty tool_registry>
    stoken = set_services(services)
    try:
        await exe.run(_state("conversational"))
        assert entered["tools"] is False   # conversational bypassed the tool loop
    finally:
        reset_services(stoken)

@pytest.mark.asyncio
async def test_standard_enters_tool_loop(monkeypatch):
    entered = {"tools": False}
    async def _spy_run_with_tools(*a, **k):
        entered["tools"] = True
        return a[0]
    monkeypatch.setattr(exe, "_run_with_tools", _spy_run_with_tools)
    services = <same StepServices, non-empty tool_registry>
    stoken = set_services(services)
    try:
        await exe.run(_state("standard"))
        assert entered["tools"] is True    # standard still uses the tool loop
    finally:
        reset_services(stoken)
```
> Mirror an existing execute test for the `StepServices` + mock provider + tool_registry construction (grep `tests/pipeline` for execute.run usage, or reuse `tests/journeys/test_budget_cap.py`'s harness shrunk down). The mock provider needs a `stream()` that yields at least one chunk for the plain-stream path.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_execute_conversational_notools.py -q`
Expected: FAIL — `test_conversational_does_not_enter_tool_loop` (it currently DOES enter `_run_with_tools`).

- [ ] **Step 3: Implement.** In `execute.run`, gate the tool-loop branch and add instrumentation. Add a tiny est-tokens helper near the top of the module:
```python
def _est_tokens(text: str | None) -> int:
    """Cheap token estimate (~4 chars/token). Never raises."""
    return (len(text) // 4) if text else 0
```
At the branch (~line 1190), change:
```python
    # Conversational turns take the plain-stream path: ZERO tools, NO tool loop —
    # a small model can't spiral when no tools are in context (intent-gated lean).
    _use_tools = state.intent_class != "conversational" and tool_registry is not None and tool_registry.all()
    # Per-block instrumentation (always) — measures context composition by tier.
    log.engine.info(
        "[pipeline] execute: context budget",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "intent_class": state.intent_class,
            "tools_used": bool(_use_tools),
            "system_prompt_tokens": _est_tokens(state.system_prompt),
            "memory_context_tokens": _est_tokens(state.memory_context),
            "history_tokens": sum(_est_tokens(getattr(m, "content", "")) for m in state.history),
            "total_est_tokens": _est_tokens(state.system_prompt) + _est_tokens(state.memory_context)
                + sum(_est_tokens(getattr(m, "content", "")) for m in state.history),
        }},
    )
    if _use_tools:
        return await _run_with_tools(state, provider, tool_registry)
    # fall through to the existing plain-stream path (unchanged below)
```
(Place the instrumentation log AFTER `provider`/`tool_registry` are resolved and BEFORE the branch. The `total_est_tokens` here excludes the tool schemas because for the conversational path there are none; for standard turns the tool-schema size is logged separately inside `_run_with_tools` if a follow-up needs it — out of scope here. The conversational total is the number FR4 asserts on.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/test_execute_conversational_notools.py -q` (2 passed). Existing execute tests: `uv run pytest tests/pipeline/ -q -k execute` and `uv run pytest tests/journeys/test_budget_cap.py -q`. `uv run mypy src/stackowl/pipeline/steps/execute.py`; `uv run ruff check`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_execute_conversational_notools.py
git commit -m "feat(v2): conversational turns take zero-tools plain-stream path + per-block token instrumentation"
```

---

## Task 4: Gateway journey — "hi" tiny + no spiral; standard unchanged + full regression

**Files:**
- Create: `tests/journeys/test_conversational_bypass_journey.py`

**Context:** The live-bug regression, end to end through the real `AsyncioBackend`. STUDY `tests/journeys/test_self_heal_substitution.py` / `test_recovery_explainability_journey.py` for the boot + scripted-provider harness. Drive a turn where the scripted router classifies the input `conversational`, and assert the execute path was lean (no tool loop, tiny instrumented token total) and a direct reply was delivered.

- [ ] **Step 1: Write the journey (FR4/FR5).** The scripted router/fast-tier provider returns `"secretary\nconversational"` for the routing call; the answer provider returns a short reply. Assert via `caplog` (logger `stackowl.engine`) that the `[pipeline] execute: context budget` record shows `intent_class == "conversational"`, `tools_used == False`, and `total_est_tokens` under the budget (FR4: e.g. `< 4000`). Assert the delivered response is non-empty and the give-up/nudge path was NOT taken (no `persistence nudge` log).

```python
# tests/journeys/test_conversational_bypass_journey.py
# Boot mirrors an existing journey. The router's fast-tier provider is scripted to
# return "secretary\nconversational" for the routing prompt; the secretary provider
# returns a short greeting reply.
# Assertions:
#   assert "<greeting reply>" in delivered
#   budget_rec = <caplog record with msg "[pipeline] execute: context budget">
#   assert budget_rec._fields["intent_class"] == "conversational"
#   assert budget_rec._fields["tools_used"] is False
#   assert budget_rec._fields["total_est_tokens"] < 4000
#   assert not any("persistence nudge" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run; confirm PASS.** If it fails on harness construction, fix until meaningful. If the conversational turn STILL enters the tool loop or the token total is high, the gates from Tasks 2-3 aren't wired — STOP and report BLOCKED (don't raise the threshold to mask it).

Run: `uv run pytest tests/journeys/test_conversational_bypass_journey.py -q`

- [ ] **Step 3: Add the standard-unchanged journey (FR6).** A scripted router returning `"secretary\nstandard"` (or owl-only) for a task input → assert tools ARE presented and `_run_with_tools`/tool-loop IS entered (the `context budget` log shows `tools_used == True`), i.e. standard behavior intact.

- [ ] **Step 4: Full regression (FR8).**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: prior 91 passed + 1 skipped, plus the new journey's tests → report exact counts. ZERO failures/regressions (standard turns default to `standard` and must be unchanged). If any prior journey regresses, STOP and report BLOCKED.

- [ ] **Step 5: Lint + commit.**

Run: `uv run ruff check tests/journeys/test_conversational_bypass_journey.py` (clean).

```bash
git add tests/journeys/test_conversational_bypass_journey.py
git commit -m "test(v2): conversational-bypass journey — hi is lean + no tool loop (FR4/FR5), standard unchanged (FR6)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1 (classify)→Task 1; FR2 (lean assembly)→Task 2; FR3 (zero-tools)→Task 3; FR4 (tiny prompt)→Task 3 instrumentation + Task 4 assertion; FR5 (no spiral/reply)→Task 4; FR6 (standard unchanged)→Tasks 1-3 gates default-standard + Task 4 journey; FR7 (routing intact)→Task 1 `test_owl_selection_unchanged` + the `-k router` regression + the contingency note; FR8→Task 4. All covered.
- **Placeholder scan:** the `<...>` fragments in Task 2/3 tests are explicit "mirror this existing harness / read the real variable names" instructions pointing at named files — not deferred logic. No TBD/TODO.
- **Type consistency:** `RouteResult(owl_name: str, intent_class: str)`; `PipelineState.intent_class: Literal["conversational","standard"]`; `_use_tools` gate; `_est_tokens`; log msg `"[pipeline] execute: context budget"` consistent between Task 3 (emit) and Task 4 (assert). Consistent.

## Risk & containment
- **Risk (FR7):** the 2-line router prompt degrades owl selection on the weak model. **Contained:** owl parse is line-1-only (unchanged); Task 1 asserts owl selection unchanged + the `-k router` regression + the explicit fallback-to-separate-call contingency (STOP if degraded).
- **Risk (FR6):** standard turns altered. **Contained:** `intent_class` defaults `standard`; all gates are `if conversational` (standard takes the existing path); full-journeys regression in Task 4.
- **Risk:** a real task misclassified `conversational` → tool-stripped. **Contained:** fail-safe parser (`standard` on any non-exact match); only an explicit `conversational` line strips tools; spec invariant.
- **Rollback:** additive/gated (see spec) — drop the field default-path, the router class output, the two gates, the instrumentation.
