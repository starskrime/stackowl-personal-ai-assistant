# Hybrid Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the user-facing answer's *starting* tier off the triage router's verdict (conversational→`fast`, standard→`standard`, escalate→`powerful`) and move heavy extractor helpers off the 122b, so internal yes/no calls stop paying for a 256K thinking giant.

**Architecture:** The triage router already classifies `state.intent_class ∈ {conversational, standard, clarify}` and fails safe to `standard`. A pure function maps that verdict to the answer's escalation *floor* (clamped ≤ ceiling), surfaced on `ToolProviderChoice` and consumed at the one gateway call site in `execute.py`. A feature flag makes the whole thing default-on but byte-identical-when-off. Helper-lane tiers are already `"fast"` except the two extractors, which move `powerful`→`standard`. Final activation is a local config edit splitting the three tiers across three real models.

**Tech Stack:** Python 3.14, pydantic-settings, pytest, `uv` (run everything via `uv run`).

## Global Constraints

- **No vendor-specific logic.** Code maps `intent_class` strings → tier strings only. Model identity (`qwen3.5:2b` etc.) lives EXCLUSIVELY in `~/.stackowl/stackowl.yaml`. Never branch on a model name or `base_url`. (`feedback_no_vendor_specific_logic`)
- **Never disable thinking.** No `disable_thinking`, no thinking-suppression anywhere. Routing is by call-type only.
- **No artificial limits.** Do not add token/window caps; those stay model-derived.
- **Tier ladder constant:** `LADDER = ("fast", "standard", "powerful")` in `src/stackowl/providers/llm_gateway.py:31`. `local` is a separate axis — NOT part of this ladder.
- **Run tests from repo root** with `uv run pytest ...`. Lint with `uv run ruff check src/`, type-check with `uv run mypy src/`.
- **Commit message footer** (every commit):
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_019Xwxh1QtkqzZAiPn5otzff
  ```

---

### Task 1: `answer_floor_for_intent` pure function

**Files:**
- Modify: `src/stackowl/pipeline/provider_select.py` (add function near the top, after imports; it needs `LADDER` from `llm_gateway`)
- Test: `tests/pipeline/test_provider_select.py` (append)

**Interfaces:**
- Consumes: `LADDER` from `stackowl.providers.llm_gateway`.
- Produces: `answer_floor_for_intent(intent_class: str, *, ceiling: str, enabled: bool) -> str` — the escalation FLOOR tier for the user-facing answer.

- [ ] **Step 1: Write the failing tests**

Append to `tests/pipeline/test_provider_select.py`:

```python
from stackowl.pipeline.provider_select import answer_floor_for_intent


def test_answer_floor_disabled_is_always_fast():
    # Flag off => legacy behaviour: every turn starts at "fast".
    assert answer_floor_for_intent("standard", ceiling="powerful", enabled=False) == "fast"
    assert answer_floor_for_intent("conversational", ceiling="powerful", enabled=False) == "fast"


def test_answer_floor_conversational_is_fast():
    assert answer_floor_for_intent("conversational", ceiling="powerful", enabled=True) == "fast"


def test_answer_floor_standard_is_standard():
    assert answer_floor_for_intent("standard", ceiling="powerful", enabled=True) == "standard"


def test_answer_floor_unknown_intent_falls_back_to_fast():
    # clarify never reaches the tool loop, but the mapping must be total.
    assert answer_floor_for_intent("clarify", ceiling="powerful", enabled=True) == "fast"
    assert answer_floor_for_intent("garbage", ceiling="powerful", enabled=True) == "fast"


def test_answer_floor_clamped_to_ceiling():
    # A "standard" intent under a "fast" ceiling can never start above the ceiling.
    assert answer_floor_for_intent("standard", ceiling="fast", enabled=True) == "fast"


def test_answer_floor_unknown_ceiling_does_not_crash():
    # Unknown ceiling => no clamp lowering; the intent's own floor stands.
    assert answer_floor_for_intent("standard", ceiling="bogus", enabled=True) == "standard"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_provider_select.py -k answer_floor -v`
Expected: FAIL — `ImportError: cannot import name 'answer_floor_for_intent'`.

- [ ] **Step 3: Write minimal implementation**

In `src/stackowl/pipeline/provider_select.py`, add the import and function (place the import with the existing `stackowl.providers` imports, and the function above `ToolProviderChoice`):

```python
from stackowl.providers.llm_gateway import LADDER

# Map the triage router's verdict to the answer's escalation FLOOR tier.
# conversational/clarify start cheap (escalate on judge give-up); standard starts
# mid. Anything unknown reproduces the legacy "fast" floor.
_INTENT_FLOOR: dict[str, str] = {
    "conversational": "fast",
    "clarify": "fast",
    "standard": "standard",
}


def answer_floor_for_intent(intent_class: str, *, ceiling: str, enabled: bool) -> str:
    """Starting tier for the user-facing answer's escalation span.

    When ``enabled`` is False this returns ``"fast"`` for every turn — byte-identical
    to the legacy hardcoded floor. When enabled, the floor is chosen from the router's
    ``intent_class`` and CLAMPED so its ladder rank never exceeds ``ceiling`` (a low
    manifest/pin ceiling is always honoured). Unknown intents fall back to ``"fast"``.
    """
    if not enabled:
        return "fast"
    floor = _INTENT_FLOOR.get(intent_class, "fast")
    try:
        ceiling_rank = LADDER.index(ceiling)
    except ValueError:
        return floor  # unknown ceiling: no clamp, keep the intent's floor
    floor_rank = LADDER.index(floor)  # floor is always a known ladder tier
    if floor_rank > ceiling_rank:
        return ceiling
    return floor
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_provider_select.py -k answer_floor -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/stackowl/pipeline/provider_select.py
git add src/stackowl/pipeline/provider_select.py tests/pipeline/test_provider_select.py
git commit -m "feat(routing): answer_floor_for_intent pure mapping

Maps triage intent_class -> answer escalation floor, clamped to ceiling.
Flag-off path returns 'fast' for all (byte-identical). No call sites yet.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019Xwxh1QtkqzZAiPn5otzff"
```

---

### Task 2: Settings flag + surface `floor_tier` on the choice + wire execute

**Files:**
- Modify: `src/stackowl/config/settings.py` (add bool field on `Settings`, near line 768 `settings_watch: bool = True`)
- Modify: `src/stackowl/pipeline/provider_select.py` (add `floor_tier` to `ToolProviderChoice`; compute it in `select_tool_provider_plan`)
- Modify: `src/stackowl/pipeline/steps/execute.py:1299` (`floor="fast"` → `floor=choice.floor_tier`)
- Test: `tests/pipeline/test_provider_select.py` (append), `tests/pipeline/test_execute_floor_invariant.py` (append)

**Interfaces:**
- Consumes: `answer_floor_for_intent` (Task 1); `Settings.answer_floor_by_intent: bool`.
- Produces: `ToolProviderChoice.floor_tier: str`; execute's gateway call uses it.

- [ ] **Step 1: Add the settings flag**

In `src/stackowl/config/settings.py`, immediately after `settings_watch: bool = True` (line 768) add:

```python
    # Route the user-facing answer's escalation FLOOR off the triage router's
    # intent_class (conversational->fast, standard->standard). False => the answer
    # always starts at "fast" (legacy, byte-identical). Helpers/extractor tiers are
    # config-driven and unaffected by this flag.
    answer_floor_by_intent: bool = True
```

- [ ] **Step 2: Write the failing test for `floor_tier` on the choice**

Append to `tests/pipeline/test_provider_select.py`. Reuse the existing fixtures/builders in that file for `registry`, `services`, and `state` (follow the patterns already there — find how existing tests build a `PipelineState` with an `intent_class` and a `services` whose `.settings` is a `Settings`). Add:

```python
def test_choice_floor_tier_tracks_intent_when_enabled(make_state, make_services, registry_fast_std_pow):
    # registry with distinct fast/standard/powerful providers; services.settings has
    # answer_floor_by_intent=True (the default).
    state = make_state(intent_class="standard")
    choice = select_tool_provider_plan(registry_fast_std_pow, make_services(), state)
    assert choice.floor_tier == "standard"

    state_conv = make_state(intent_class="conversational")
    choice_conv = select_tool_provider_plan(registry_fast_std_pow, make_services(), state_conv)
    assert choice_conv.floor_tier == "fast"


def test_choice_floor_tier_is_fast_when_flag_off(make_state, make_services, registry_fast_std_pow):
    services = make_services(answer_floor_by_intent=False)
    state = make_state(intent_class="standard")
    choice = select_tool_provider_plan(registry_fast_std_pow, services, state)
    assert choice.floor_tier == "fast"
```

> NOTE TO IMPLEMENTER: `test_provider_select.py` already constructs registries/states/services for the existing tests. DO NOT invent new fixtures if equivalents exist — reuse them and only add the `intent_class` / `answer_floor_by_intent` knobs. If the existing helpers are inline (not fixtures), write the two tests inline in the same style instead of using the `make_*` params shown above.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_provider_select.py -k floor_tier -v`
Expected: FAIL — `AttributeError: 'ToolProviderChoice' object has no attribute 'floor_tier'`.

- [ ] **Step 4: Add `floor_tier` to the dataclass + compute it**

In `src/stackowl/pipeline/provider_select.py`, update the `ToolProviderChoice` docstring (remove the "floor is always fast" claim) and add the field:

```python
    provider: ModelProvider
    ceiling_tier: str
    pinned: bool
    floor_tier: str = "fast"
```

Then in `select_tool_provider_plan`, at EVERY `return ToolProviderChoice(...)` site, pass `floor_tier`. For the PINNED returns (owl-name pin, manifest pin, session `/tier`) keep `floor_tier="fast"` (execute's pinned path never uses it — the field is just inert there). For the FINAL non-pinned return, compute it:

```python
    _settings = getattr(services, "settings", None)
    _enabled = bool(getattr(_settings, "answer_floor_by_intent", False))
    floor_tier = answer_floor_for_intent(
        state.intent_class, ceiling=ceiling_tier, enabled=_enabled
    )
    return ToolProviderChoice(
        provider=provider,
        ceiling_tier=ceiling_tier,
        pinned=False,
        floor_tier=floor_tier,
    )
```

> The exact local variable names (`ceiling_tier`, `provider`) must match what that function already binds before its non-pinned return — read the surrounding lines and reuse them. Default `getattr` to `False` so a missing/None settings object reproduces legacy `"fast"`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_provider_select.py -k "floor_tier or answer_floor" -v`
Expected: PASS.

- [ ] **Step 6: Wire execute to use the choice's floor**

In `src/stackowl/pipeline/steps/execute.py`, the non-pinned gateway call (currently line ~1299):

```python
            floor="fast",
            ceiling=choice.ceiling_tier,
```

becomes:

```python
            floor=choice.floor_tier,
            ceiling=choice.ceiling_tier,
```

- [ ] **Step 7: Write the failing execute integration test**

Append to `tests/pipeline/test_execute_floor_invariant.py`, reusing that file's existing harness for driving the execute step with a fake gateway/registry that records the `floor` it was called with. Follow the existing test's setup exactly; add:

```python
async def test_standard_intent_starts_on_standard_floor(execute_harness):
    # answer_floor_by_intent defaults True; intent_class="standard" with a distinct
    # fast/standard/powerful registry must call the gateway with floor="standard".
    h = execute_harness(intent_class="standard")
    await h.run()
    assert h.captured_floor == "standard"
    assert h.captured_ceiling == "powerful"


async def test_conversational_intent_starts_on_fast_floor(execute_harness):
    h = execute_harness(intent_class="conversational")
    await h.run()
    assert h.captured_floor == "fast"


async def test_flag_off_keeps_fast_floor(execute_harness):
    h = execute_harness(intent_class="standard", answer_floor_by_intent=False)
    await h.run()
    assert h.captured_floor == "fast"
```

> NOTE TO IMPLEMENTER: `test_execute_floor_invariant.py` already exercises the execute floor — read it first and extend its existing harness (capture the `floor` kwarg passed to `LLMGateway.complete_with_tools`). If the file's harness can't yet vary `intent_class` / the settings flag, add those knobs to the harness rather than rebuilding it. Mark async tests the way the file already does (it has prior async tests — match `pytest.mark.asyncio` / `anyio` usage).

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_execute_floor_invariant.py -v`
Expected: PASS (existing tests + 3 new).

- [ ] **Step 9: Regression — the existing floor/provider-select/conversational suites stay green**

Run:
```bash
uv run pytest tests/pipeline/test_provider_select.py tests/pipeline/test_execute_floor_invariant.py tests/pipeline/test_execute_conversational_notools.py tests/pipeline/test_execute_clarify_branch.py tests/pipeline/test_execute_skill_pins.py -v
```
Expected: all PASS. If a pre-existing test asserted `floor == "fast"` for a standard turn, it encoded the OLD behaviour — update it to the new mapping (conversational/clarify→fast, standard→standard) and note it in the commit body.

- [ ] **Step 10: Lint, type-check, commit**

```bash
uv run ruff check src/stackowl/config/settings.py src/stackowl/pipeline/provider_select.py src/stackowl/pipeline/steps/execute.py
uv run mypy src/stackowl/pipeline/provider_select.py src/stackowl/pipeline/steps/execute.py
git add src/stackowl/config/settings.py src/stackowl/pipeline/provider_select.py src/stackowl/pipeline/steps/execute.py tests/pipeline/test_provider_select.py tests/pipeline/test_execute_floor_invariant.py
git commit -m "feat(routing): answer floor follows triage intent (flag, default on)

ToolProviderChoice.floor_tier computed from intent_class; execute starts the
escalation span there instead of hardcoded fast. answer_floor_by_intent=False
restores byte-identical legacy behaviour.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019Xwxh1QtkqzZAiPn5otzff"
```

---

### Task 3: Move extractor helpers off the 122b (powerful → standard)

**Files:**
- Modify: `src/stackowl/memory/assembly.py:220` (fact extractor) and `:199` (entity extractor construction)
- Test: `tests/memory/` — add/extend a test asserting the construction tier (find the existing assembly/extractor test; if none asserts tier, add a focused one)

**Interfaces:**
- Consumes: nothing new.
- Produces: extractor providers resolved at `"standard"`.

- [ ] **Step 1: Write the failing test**

Find the existing memory-assembly test (e.g. `tests/memory/test_assembly*.py`). Add a test that builds the memory assembly with a spy `ProviderRegistry` recording each `get_with_cascade(tier)` call, and asserts the fact-extractor and entity-extractor resolve `"standard"`, not `"powerful"`. Skeleton (adapt to the real assembly entry point + fixtures in that test dir):

```python
def test_extractors_use_standard_tier(memory_assembly_with_spy_registry):
    spy = memory_assembly_with_spy_registry  # records tiers requested at construction
    assert "standard" in spy.requested_tiers
    assert "powerful" not in spy.extractor_tiers  # neither extractor asks for powerful
```

> NOTE TO IMPLEMENTER: if there is no existing seam to spy on construction-time tier requests, the lowest-risk assertion is on `EntityExtractor(...).` `_preferred_tier` and on the tier string passed to `get_with_cascade` for the fact extractor. Prefer extending an existing assembly test over a brand-new harness.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/ -k extractor_tier -v` (adjust path/name to where you put it)
Expected: FAIL — currently `"powerful"`.

- [ ] **Step 3: Implement**

`src/stackowl/memory/assembly.py:220`:
```python
        extraction_provider: ModelProvider = provider_registry.get_with_cascade("standard")
```
Update the comment above it (line 209) from "powerful-tier provider for quality extraction" to "standard-tier provider — capable extraction without the 122b cost".

`src/stackowl/memory/assembly.py:199` — pass the tier explicitly:
```python
        entity_extractor = EntityExtractor(
            provider_registry=provider_registry,
            sensitive_categories=mem.sensitive_categories,
            preferred_tier="standard",
        )
```
(Leave the `EntityExtractor` default `preferred_tier="powerful"` untouched so other callers/tests are unaffected; the live wiring overrides it explicitly.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/ -k extractor_tier -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/stackowl/memory/assembly.py
git add src/stackowl/memory/assembly.py tests/memory/
git commit -m "feat(memory): extractors run on standard tier, not powerful

Fact + entity extraction feed long-term memory; standard (mid model) is
capable enough and far cheaper than the 122b. M2M judge/router stay on fast.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019Xwxh1QtkqzZAiPn5otzff"
```

---

### Task 4: Activation — split the three tiers across three models (local config, manual)

> NOT a TDD/git task. `~/.stackowl/stackowl.yaml` is local + gitignored. This is the step that makes the feature do anything: until now every tier still maps to 122b, so all the above is a behavioural no-op.

- [ ] **Step 1: Back up + edit the config**

```bash
cp ~/.stackowl/stackowl.yaml ~/.stackowl/stackowl.yaml.bak-$(date +%s)
```
Edit `~/.stackowl/stackowl.yaml` so the three providers read:
```yaml
- name: ollama            # tier: fast
  default_model: qwen3.5:2b
- name: ollama-standard   # tier: standard
  default_model: qwen3.6:35b
- name: ollama-powerful   # tier: powerful
  default_model: qwen3.5:122b
```
(Keep every other field — `protocol`, `base_url`, `enabled`, `api_key`, `tier` — unchanged.)

- [ ] **Step 2: Full pre-deploy gate**

```bash
uv run pytest -q
uv run ruff check src/
uv run mypy src/
```
Expected: green (modulo the pre-existing `app.py integrations_health` mypy error noted as out-of-scope — confirm it is the ONLY mypy failure and predates this branch).

- [ ] **Step 3: Restart the live service** (two-process gateway+core, tmux session `stackowl`) per the project's normal restart procedure, then confirm boot + Telegram adapter up via logs:

```bash
cat logs/stackowl-$(date +%F).log | jq -c 'select(.level=="error") | {ts, module, msg}' | tail -20
```

- [ ] **Step 4: Live validation (the real risk surface)**

1. Send a **conversational** Telegram message (e.g. a greeting). Verify in logs it routes `intent_class=conversational`, starts on `fast` (2b), and returns a sane reply quickly. Trace:
   ```bash
   cat logs/stackowl-$(date +%F).log | jq -c 'select(.fields.purpose=="execute.tool_loop" or (.msg|test("router|llm_gateway"))) | {ts,msg,fields}' | tail -40
   ```
2. Confirm the **2b reliably emits parseable** router verdicts and judge JSON (look for `judge_delivery: unparseable` or router fail-safe-to-standard warnings — there should be none, or they must self-recover).
3. Send a **standard/tool-using** request. Verify it starts on `standard` (35b) and escalates to `powerful` only on genuine give-up.
4. Confirm end-to-end latency dropped materially vs all-122b.

- [ ] **Step 5: Rollback levers (document, don't execute unless needed)**
   - Quality regression on simple replies but latency good → keep config, but flip `answer_floor_by_intent: false` in YAML to send all answers back to the `fast`-floor escalation (or raise the `fast` model).
   - 2b unreliable for M2M → set the `fast` provider's `default_model` to `qwen3.6:35b` (M2M on 35b; still off 122b).
   - Total revert → restore `~/.stackowl/stackowl.yaml.bak-*`.

---

## Self-Review

**Spec coverage:**
- Answer-floor-by-intent mapping + clamp + flag → Tasks 1, 2. ✓
- Escalation preserved (ceiling unchanged) → Task 2 Step 6 keeps `ceiling=choice.ceiling_tier`. ✓
- Pinned path untouched → Task 2 keeps pinned returns at `floor_tier="fast"` and execute's pinned branch is not modified. ✓
- Extractors → standard; parliament synthesis stays powerful (not touched) → Task 3. ✓
- Config activation (fast=2b, standard=35b, powerful=122b) → Task 4. ✓
- Constraints (no vendor logic, thinking on, no artificial limits) → Global Constraints + code maps strings only. ✓
- Testing: unit (Task 1), integration (Task 2), live validation (Task 4). ✓
- Out-of-scope items (#2/#3/#4/#5/#6) → not present in any task. ✓

**Placeholder scan:** No "TBD/TODO/handle edge cases". The two "NOTE TO IMPLEMENTER" blocks point at REAL existing test files (`test_provider_select.py`, `test_execute_floor_invariant.py`, memory assembly tests) and instruct reuse — they are guidance, not deferred work; the concrete assertions are spelled out.

**Type consistency:** `answer_floor_for_intent(intent_class, *, ceiling, enabled) -> str` defined in Task 1, called identically in Task 2. `ToolProviderChoice.floor_tier: str` defined and consumed consistently (`choice.floor_tier`). `Settings.answer_floor_by_intent: bool` defined in Task 2 Step 1, read via `getattr(services.settings, "answer_floor_by_intent", False)`. ✓
