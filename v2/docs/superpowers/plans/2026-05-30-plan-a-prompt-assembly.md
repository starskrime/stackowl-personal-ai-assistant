# Plan A — Prompt Assembly Re-Architecture (persona/DNA + real multi-turn history) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the model receive the owl's persona+DNA as its system prompt and the prior conversation as real user/assistant message turns, so follow-up requests keep context.

**Architecture:** Add a dedicated `assemble` pipeline step (after `classify`, before `execute`) that builds the final system prompt via `owls/dna_injector.py`. Carry prior turns as real `Message` objects in a new `PipelineState.history` field (populated by `classify`, parsed from the existing staged "User: …/Assistant: …" rows). Add a backward-compatible `history` parameter to the providers' `complete_with_tools`. Fixes RC-B (no persona) and RC-C (history was flat system-text).

**Tech Stack:** Python 3, Pydantic (frozen models), pytest, asyncio. Providers: Anthropic + OpenAI SDKs.

**BMad boundaries honored:** `owls/` owns persona (assemble *calls* the injector, doesn't reimplement it); `pipeline/` orchestrates; B5 (every catch logs). No new external deps (B8 N/A).

---

## File Structure

- Create: `src/stackowl/pipeline/steps/assemble.py` — builds `system_prompt` from persona/DNA + memory_context. One responsibility.
- Modify: `src/stackowl/pipeline/state.py` — add `history: tuple[Message, ...]` and `system_prompt: str | None`.
- Modify: `src/stackowl/pipeline/steps/classify.py` — populate `state.history` from recent turns; stop folding recent turns into `memory_context` (avoids duplication).
- Modify: `src/stackowl/pipeline/registry.py` — insert the `assemble` step between `classify` and `execute`.
- Modify: `src/stackowl/pipeline/steps/execute.py` — pass `system_text=state.system_prompt` and `history=state.history`.
- Modify: `src/stackowl/providers/base.py`, `anthropic_provider.py`, `openai_provider.py` — add `history` param to `complete_with_tools`.
- Test: `tests/pipeline/test_plan_a_assemble.py`, `tests/pipeline/test_plan_a_history_threading.py`, `tests/providers/test_plan_a_history_param.py`.

---

### Task 1: PipelineState gains `history` + `system_prompt`

**Files:**
- Modify: `src/stackowl/pipeline/state.py`
- Test: `tests/pipeline/test_plan_a_assemble.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_plan_a_assemble.py
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import Message


def _state(**kw):
    base = dict(trace_id="t", session_id="s", input_text="hi",
                channel="cli", owl_name="default", pipeline_step="start")
    base.update(kw)
    return PipelineState(**base)


def test_state_defaults_history_and_system_prompt():
    s = _state()
    assert s.history == ()
    assert s.system_prompt is None


def test_state_evolve_carries_history():
    s = _state().evolve(history=(Message(role="user", content="prev"),))
    assert s.history[0].content == "prev"
    assert s.evolve(system_prompt="SYS").system_prompt == "SYS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_plan_a_assemble.py -k state -v`
Expected: FAIL — `PipelineState` has no `history` / `system_prompt`.

- [ ] **Step 3: Add the fields**

In `src/stackowl/pipeline/state.py`, add the import near the top:

```python
from stackowl.providers.base import Message
```

Inside `class PipelineState`, add after the `memory_context` field (line ~56):

```python
    # Real prior conversation turns (user/assistant), oldest-first. Populated by
    # the classify step from staged conversation rows and threaded into the
    # provider messages array by execute. Empty for the first turn / non-chat
    # pipelines. RC-C fix.
    history: tuple[Message, ...] = ()
    # Final assembled system prompt (owl persona + DNA directives + memory
    # blocks). Built by the assemble step; consumed by execute. None until
    # assemble runs. RC-B fix.
    system_prompt: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_plan_a_assemble.py -k state -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/state.py tests/pipeline/test_plan_a_assemble.py
git commit -m "feat(v2): PipelineState carries real history + assembled system_prompt (RC-B/RC-C)"
```

---

### Task 2: `assemble` step builds persona+DNA system prompt

**Files:**
- Create: `src/stackowl/pipeline/steps/assemble.py`
- Test: `tests/pipeline/test_plan_a_assemble.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pipeline/test_plan_a_assemble.py
import pytest
from stackowl.pipeline.steps import assemble
from stackowl.pipeline.services import StepServices, set_services
from stackowl.owls.registry import OwlRegistry


@pytest.mark.asyncio
async def test_assemble_prepends_persona_to_memory(monkeypatch):
    reg = OwlRegistry.from_settings_default()  # builtin personas registered
    set_services(StepServices(owl_registry=reg))
    s = _state(owl_name="default", memory_context="## Learned Preferences\n- likes tea")
    out = await assemble.run(s)
    # persona text comes first, memory block still present
    assert out.system_prompt is not None
    assert "likes tea" in out.system_prompt
    manifest = reg.get("default")
    assert manifest.system_prompt.split("\n")[0] in out.system_prompt


@pytest.mark.asyncio
async def test_assemble_handles_no_memory(monkeypatch):
    reg = OwlRegistry.from_settings_default()
    set_services(StepServices(owl_registry=reg))
    out = await assemble.run(_state(owl_name="default", memory_context=None))
    assert out.system_prompt  # persona alone, never None/empty
```

> Note for implementer: if `OwlRegistry.from_settings_default()` does not exist, use the project's standard builtin-registry constructor (grep `register_builtin_personas`) — the assertion only needs a registry with the `default` owl. Adjust the helper, not the behavior.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_plan_a_assemble.py -k assemble -v`
Expected: FAIL — `stackowl.pipeline.steps.assemble` does not exist.

- [ ] **Step 3: Create the step**

```python
# src/stackowl/pipeline/steps/assemble.py
"""Pipeline step: assemble — build the final system prompt (persona + DNA + memory).

RC-B fix: the pipeline previously sent only `memory_context` as the system
prompt, so the owl persona/DNA never reached the model. This step composes the
owl's persona + DNA-modulated directives (via owls/dna_injector) with the
recalled memory blocks classify produced.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

_injector = DNAPromptInjector()


async def run(state: PipelineState) -> PipelineState:
    log.engine.debug(
        "[pipeline] assemble: entry", extra={"_fields": {"trace_id": state.trace_id}}
    )
    services = get_services()
    registry = services.owl_registry
    persona = ""
    if registry is not None:
        try:
            manifest = registry.get(state.owl_name)
            persona = _injector.inject(manifest, manifest.dna)
        except Exception as exc:  # B5 — unknown owl must not blank the prompt
            log.engine.warning(
                "[pipeline] assemble: persona lookup failed — memory-only prompt",
                exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
            )
    parts = [p for p in (persona, state.memory_context) if p]
    system_prompt = "\n\n".join(parts) or None
    log.engine.debug(
        "[pipeline] assemble: exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "persona_len": len(persona),
            "system_len": len(system_prompt or ""),
        }},
    )
    return state.evolve(system_prompt=system_prompt)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_plan_a_assemble.py -k assemble -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/assemble.py tests/pipeline/test_plan_a_assemble.py
git commit -m "feat(v2): assemble step injects owl persona+DNA into system prompt (RC-B)"
```

---

### Task 3: Register `assemble` between classify and execute

**Files:**
- Modify: `src/stackowl/pipeline/registry.py`
- Test: `tests/pipeline/test_plan_a_assemble.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pipeline/test_plan_a_assemble.py
def test_assemble_registered_between_classify_and_execute():
    from stackowl.pipeline.registry import PIPELINE_STEPS
    names = [n for n, _ in PIPELINE_STEPS]
    assert "assemble" in names
    assert names.index("classify") < names.index("assemble") < names.index("execute")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_plan_a_assemble.py -k registered -v`
Expected: FAIL — `"assemble"` not in step names.

- [ ] **Step 3: Insert the step**

In `src/stackowl/pipeline/registry.py`, add `assemble` to the `from stackowl.pipeline.steps import (...)` block, then update `PIPELINE_STEPS`:

```python
PIPELINE_STEPS: list[tuple[str, StepFn]] = [
    ("triage", triage.run),
    ("dispatch", dispatch.run),
    ("classify", classify.run),
    ("assemble", assemble.run),
    ("execute", execute.run),
    ("parliament_step", parliament_step.run),
    ("consolidate", consolidate.run),
    ("synthesize", synthesize.run),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_plan_a_assemble.py -k registered -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/registry.py tests/pipeline/test_plan_a_assemble.py
git commit -m "feat(v2): register assemble step (classify -> assemble -> execute)"
```

---

### Task 4: classify populates real `history`; stops duplicating turns in memory_context

**Files:**
- Modify: `src/stackowl/pipeline/steps/classify.py`
- Test: `tests/pipeline/test_plan_a_history_threading.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_plan_a_history_threading.py
import pytest
from stackowl.pipeline.steps.classify import _parse_turns_to_messages


def test_parse_turns_splits_user_and_assistant():
    rows = ["User: hello\n\nAssistant: hi there",
            "User: find aws practice\n\nAssistant: here are some"]
    msgs = _parse_turns_to_messages(rows)
    # oldest-first, alternating user/assistant
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi there"


def test_parse_turns_tolerates_missing_assistant():
    msgs = _parse_turns_to_messages(["User: just a question"])
    assert msgs[0].role == "user" and msgs[0].content == "just a question"
    assert all(m.content for m in msgs)  # never emits empty-content turns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_plan_a_history_threading.py -v`
Expected: FAIL — `_parse_turns_to_messages` not defined.

- [ ] **Step 3: Add the parser + populate history; drop recent_block from memory_context**

In `src/stackowl/pipeline/steps/classify.py`, add near the top imports:

```python
from stackowl.providers.base import Message
```

Add this helper (next to `_gather_recent_session_turns`):

```python
def _parse_turns_to_messages(contents: list[str]) -> list[Message]:
    """Parse stored "User: X\n\nAssistant: Y" rows into real Message turns.

    The store format is fixed by consolidate.py: f"User: {input}\n\nAssistant: {reply}".
    Returns oldest-first user/assistant pairs; skips empty halves so we never
    emit a blank-content turn (providers reject empty content).
    """
    msgs: list[Message] = []
    for content in contents:
        user_part, _, assistant_part = content.partition("\n\nAssistant:")
        user_text = user_part.removeprefix("User:").strip()
        assistant_text = assistant_part.strip()
        if user_text:
            msgs.append(Message(role="user", content=user_text))
        if assistant_text:
            msgs.append(Message(role="assistant", content=assistant_text))
    return msgs
```

Add a history-builder that returns ordered Messages (oldest-first) using the existing read:

```python
async def _gather_history(session_id: str, limit: int) -> list[Message]:
    services = get_services()
    bridge = services.memory_bridge
    if bridge is None or limit <= 0:
        return []
    try:
        turns = await bridge.recent_conversation_turns(session_id=session_id, limit=limit)
    except Exception as exc:
        log.engine.warning(
            "[pipeline] classify: history fetch failed — skipping",
            exc_info=exc, extra={"_fields": {"session_id": session_id}},
        )
        return []
    return _parse_turns_to_messages([t.content for t in turns])
```

In `run()`, **replace** the `recent_block = await _gather_recent_session_turns(...)` line and its inclusion in `parts`. Specifically:
- Build history instead: `history = await _gather_history(state.session_id, short_term_window)`
- Remove `recent_block` from the `parts` tuple (lines ~370-375) so prior turns are no longer duplicated as flat text.
- Change the final return to carry history:

```python
    return state.evolve(memory_context=combined or None, history=tuple(history))
```

Keep the existing `_gather_recent_session_turns` function in place (it is now unused by `run` but referenced by its own tests; remove only if no test imports it — verify with `grep -rn _gather_recent_session_turns tests/`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_plan_a_history_threading.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/classify.py tests/pipeline/test_plan_a_history_threading.py
git commit -m "feat(v2): classify emits real history turns, stops flat-text duplication (RC-C)"
```

---

### Task 5: Providers accept `history` in `complete_with_tools`

**Files:**
- Modify: `src/stackowl/providers/base.py`
- Modify: `src/stackowl/providers/anthropic_provider.py`
- Modify: `src/stackowl/providers/openai_provider.py`
- Test: `tests/providers/test_plan_a_history_param.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_plan_a_history_param.py
import inspect
from stackowl.providers import base, anthropic_provider, openai_provider


def test_all_providers_expose_history_param():
    for mod in (base.ModelProvider, anthropic_provider, openai_provider):
        # locate the class in each module
        cls = mod if isinstance(mod, type) else next(
            v for v in vars(mod).values()
            if isinstance(v, type) and hasattr(v, "complete_with_tools")
            and v.__module__ == mod.__name__
        )
        sig = inspect.signature(cls.complete_with_tools)
        assert "history" in sig.parameters, f"{cls} missing history param"
        assert sig.parameters["history"].default is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_plan_a_history_param.py -v`
Expected: FAIL — `history` not in signature.

- [ ] **Step 3: Add the param (backward-compatible, default None)**

In `src/stackowl/providers/base.py` `complete_with_tools` (line ~105), add the param and prepend history before the user turn:

```python
    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_iterations: int = 8,
        history: list[Message] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        msgs: list[Message] = []
        if system_text:
            msgs.append(Message(role="system", content=system_text))
        msgs.extend(history or [])
        msgs.append(Message(role="user", content=user_text))
        result = await self.complete(msgs, model="")
        return result.content, []
```

In `src/stackowl/providers/anthropic_provider.py` `complete_with_tools` (line ~73): add `history: list[Message] | None = None,` to the signature, then change the initial messages construction (line ~88) to:

```python
        history_dicts = [{"role": m.role, "content": m.content} for m in (history or [])]
        messages: list[dict[str, Any]] = [
            *history_dicts,
            {"role": "user", "content": user_text},
        ]
```

In `src/stackowl/providers/openai_provider.py` `complete_with_tools` (line ~80): add the same `history` param and, where it builds its initial messages list, prepend the history dicts in the same `{"role", "content"}` shape (mirror the existing user-message construction — read lines 80-130 and insert `*history_dicts` ahead of the user message exactly as Anthropic does).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_plan_a_history_param.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/base.py src/stackowl/providers/anthropic_provider.py src/stackowl/providers/openai_provider.py tests/providers/test_plan_a_history_param.py
git commit -m "feat(v2): providers thread real conversation history into messages array (RC-C)"
```

---

### Task 6: execute uses `system_prompt` + `history`

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py`
- Test: `tests/pipeline/test_plan_a_history_threading.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pipeline/test_plan_a_history_threading.py
import pytest
from stackowl.providers.base import Message


@pytest.mark.asyncio
async def test_execute_passes_history_and_system_prompt(monkeypatch):
    captured = {}

    class FakeProvider:
        protocol = "anthropic"
        async def complete_with_tools(self, user_text, system_text, tool_schemas,
                                      tool_dispatcher, max_iterations=8, history=None):
            captured["system_text"] = system_text
            captured["history"] = history
            return "ok", []

    from stackowl.pipeline.steps import execute
    # Minimal harness: monkeypatch provider resolution used inside execute.run.
    # Implementer: patch the same accessor execute.run already uses to obtain a
    # provider (grep `provider =` in execute.py) to return FakeProvider().
    # Then build a state with system_prompt + history and assert they arrive.
    ...
```

> Implementer note: execute.run resolves a provider and a tool_registry from services. Wire the FakeProvider through the same accessor and assert `captured["system_text"] == state.system_prompt` and `captured["history"] == list(state.history)`. If a full harness is heavy, instead assert at the call-site via a thin unit extraction (see Step 3).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_plan_a_history_threading.py -k execute -v`
Expected: FAIL — execute still passes `system_text=state.memory_context` and no history.

- [ ] **Step 3: Update the call site**

In `src/stackowl/pipeline/steps/execute.py` (line ~186), change the tool-loop call:

```python
        final_text, raw_calls = await provider.complete_with_tools(
            user_text=state.input_text,
            system_text=state.system_prompt,   # was: state.memory_context
            tool_schemas=tool_schemas,
            tool_dispatcher=_dispatch,
            history=list(state.history),
        )
```

And in the no-tool streaming branch (line ~309-311), build the messages from history + system_prompt:

```python
        messages: list[Message] = [*state.history, Message(role="user", content=state.input_text)]
        if state.system_prompt:
            messages = [Message(role="system", content=state.system_prompt), *messages]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_plan_a_history_threading.py -k execute -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_plan_a_history_threading.py
git commit -m "feat(v2): execute sends assembled system_prompt + real history (RC-B/RC-C)"
```

---

### Task 7: Full-pipeline regression + smoke

**Files:**
- Test: `tests/pipeline/test_plan_a_history_threading.py`

- [ ] **Step 1: Add an end-to-end test** that runs two turns through the backend with a stub provider that echoes whether it saw the prior turn, asserting turn 2's `history` contains turn 1's text. Use the existing pipeline test harness (grep `AsyncioBackend` in tests/ for the established pattern).

- [ ] **Step 2: Run the targeted suites (bounded, per Jetson rule)**

Run: `uv run pytest tests/pipeline/test_plan_a_assemble.py tests/pipeline/test_plan_a_history_threading.py tests/providers/test_plan_a_history_param.py -v --timeout=120`
Expected: all PASS.

- [ ] **Step 3: Lint + type-check the touched files**

Run: `uv run ruff check src/stackowl/pipeline src/stackowl/providers && uv run mypy src/stackowl/pipeline/steps/assemble.py src/stackowl/pipeline/state.py`
Expected: clean.

- [ ] **Step 4: Manual smoke (real multi-turn)** — start serve, send "Hi" then "what did I just say?" on the same channel; confirm the second reply references the first. (Per project rule: implement → QA agent → party-mode → smoke.)

- [ ] **Step 5: Commit**

```bash
git add tests/pipeline/test_plan_a_history_threading.py
git commit -m "test(v2): end-to-end multi-turn context regression (RC-B/RC-C)"
```

---

## Self-Review

- **Spec coverage:** RC-B → Tasks 2,3,6 (persona injected via assemble, reaches model). RC-C → Tasks 1,4,5,6 (real history field, parser, provider param, execute wiring). ✓
- **Type consistency:** `Message(role, content)` used identically in state.py, classify.py, providers, execute.py. `history` is `tuple[Message,...]` in state, `list[Message] | None` at the provider boundary (converted with `list(...)`). `system_prompt: str | None`. ✓
- **Placeholders:** Task 6's test has an implementer note because the provider-resolution accessor must be read at execution time; the behavior + assertions are fully specified. No "TODO/TBD" in implementation steps. ✓
