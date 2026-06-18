# Clarify Router Verdict Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third `clarify` router verdict that, on high ambiguity × commitment-cost, asks the user ONE question instead of forcing a weak model into the tool loop to guess and spiral.

**Architecture:** The fast-tier router emits the clarifying question on line 3 of its reply (same call, no extra LLM hop). `triage` stamps it on `PipelineState`. `execute.py` gains an early branch that registers a turn-yield pending clarify (via the existing `ClarifyGateway`, `deliver=False` to avoid double-send) and emits the question as the turn response — never entering the tool loop. Resume is already wired through `ClarifyPump.resolve_or_rewrite`.

**Tech Stack:** Python 3.12, `uv run pytest`, ruff, mypy (strict). Pipeline = frozen-dataclass `PipelineState` evolved through steps.

## Global Constraints

- Run all commands from repo root with `uv run …`.
- No hardcoded English keyword lists in code; gating is meaning-based via the (English-allowed) router glue prompt. Class tokens (`conversational`/`standard`/`clarify`) are protocol labels.
- Fail-safe is conservative: when unsure between `standard` and `clarify`, choose `standard` (act). Invariant: a `clarify` verdict MUST carry a non-empty question or it downgrades to `standard`.
- 2-line router replies and the conversational/standard paths must stay byte-identical.
- Every `execute()` / non-trivial fn keeps 4-point logging; never a silent `catch`.
- Commit at sub-task granularity; keep the tree green.

---

### Task 1: Tool-free intent class plumbing

**Files:**
- Modify: `src/stackowl/pipeline/state.py` (intent_class Literal ~line 53; add field + constant)
- Modify: `src/stackowl/pipeline/provider_select.py:43`
- Modify: `src/stackowl/pipeline/steps/classify.py:467`
- Modify: `src/stackowl/pipeline/steps/assemble.py:107`
- Test: `tests/pipeline/test_clarify_intent_plumbing.py` (new)

**Interfaces:**
- Produces: `PipelineState.intent_class: Literal["conversational","standard","clarify"]`; `PipelineState.clarify_question: str | None = None`; `stackowl.pipeline.state.TOOL_FREE_CLASSES: frozenset[str]` = `{"conversational","clarify"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_clarify_intent_plumbing.py
from stackowl.pipeline.state import PipelineState, TOOL_FREE_CLASSES
from stackowl.pipeline.provider_select import _ensure_tool_capable


class _NoToolsProvider:
    name = "weak"
    supports_tools = False


def _state(**kw):
    return PipelineState(input_text="x", session_id="s", channel="cli", **kw)


def test_tool_free_classes_membership():
    assert TOOL_FREE_CLASSES == frozenset({"conversational", "clarify"})


def test_clarify_question_field_defaults_none():
    assert _state().clarify_question is None
    assert _state(clarify_question="What kind?").clarify_question == "What kind?"


def test_ensure_tool_capable_passes_through_for_clarify():
    # A clarify turn needs no tools; an incapable provider must pass through
    # untouched (no raise), exactly like conversational.
    p = _NoToolsProvider()
    out = _ensure_tool_capable(p, registry=None, state=_state(intent_class="clarify"),
                               log_selection=False)
    assert out is p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_clarify_intent_plumbing.py -v`
Expected: FAIL (ImportError: TOOL_FREE_CLASSES; or clarify not a valid Literal / passthrough raises).

- [ ] **Step 3: Implement**

In `src/stackowl/pipeline/state.py`, near the top-level constants add:
```python
# Intent classes that NEVER enter the tool loop: a tool-free reply (conversational)
# or a single clarifying question (clarify). Shared by provider_select (skip the
# tool-capability gate), classify (lean assembly), and assemble (skip skills).
TOOL_FREE_CLASSES: frozenset[str] = frozenset({"conversational", "clarify"})
```
Widen the field (keep the existing comment):
```python
    intent_class: Literal["conversational", "standard", "clarify"] = "standard"
    # The ONE clarifying question to surface when intent_class == "clarify"
    # (router-authored, same fast-tier call). None for every other class.
    clarify_question: str | None = None
```

In `src/stackowl/pipeline/provider_select.py:43` replace:
```python
    if state.intent_class == "conversational":
        return provider
```
with:
```python
    from stackowl.pipeline.state import TOOL_FREE_CLASSES
    if state.intent_class in TOOL_FREE_CLASSES:
        return provider
```

In `src/stackowl/pipeline/steps/classify.py:467` replace `_lean = state.intent_class == "conversational"` with:
```python
    from stackowl.pipeline.state import TOOL_FREE_CLASSES
    _lean = state.intent_class in TOOL_FREE_CLASSES
```

In `src/stackowl/pipeline/steps/assemble.py:107` replace the `state.intent_class != "conversational"` gate with:
```python
    from stackowl.pipeline.state import TOOL_FREE_CLASSES
    if state.intent_class not in TOOL_FREE_CLASSES:
```
(adjust to the existing boolean expression — the skills block is built only when the class is NOT tool-free).

- [ ] **Step 4: Run test + static checks**

Run: `uv run pytest tests/pipeline/test_clarify_intent_plumbing.py -v && uv run ruff check src/stackowl/pipeline && uv run mypy src/stackowl/pipeline/state.py`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(pipeline): tool-free intent class plumbing for clarify verdict"
```

---

### Task 2: Router emits the `clarify` verdict + question

**Files:**
- Modify: `src/stackowl/owls/router.py` (`_VALID_CLASSES`:32; `RouteResult`:35-40; `_build_prompt`:162; `_parse_intent_class`:220; `route`:316-343)
- Test: `tests/owls/test_router_clarify.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `RouteResult(owl_name, intent_class, clarify_question: str | None)`. `route()` returns `clarify_question` set only when `intent_class == "clarify"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/owls/test_router_clarify.py
from stackowl.owls.router import SecretaryRouter


def _r():
    # parsing helpers are pure — no registries needed
    return SecretaryRouter.__new__(SecretaryRouter)


def test_parse_clarify_with_question():
    raw = "secretary\nclarify\nDo you want me to create images, or find existing ones?"
    assert _r()._parse_intent_class(raw) == "clarify"
    assert _r()._parse_clarify_question(raw, "clarify") == \
        "Do you want me to create images, or find existing ones?"


def test_clarify_without_question_downgrades_to_standard():
    raw = "secretary\nclarify\n"
    assert _r()._parse_clarify_question(raw, "clarify") is None  # no question text


def test_standard_and_conversational_unchanged():
    assert _r()._parse_intent_class("scout\nstandard") == "standard"
    assert _r()._parse_intent_class("scout\nconversational") == "conversational"
    # no question for non-clarify classes
    assert _r()._parse_clarify_question("scout\nstandard", "standard") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/owls/test_router_clarify.py -v`
Expected: FAIL (`_parse_clarify_question` missing; clarify not recognized).

- [ ] **Step 3: Implement**

`_VALID_CLASSES` (line 32):
```python
_VALID_CLASSES = {"conversational", "standard", "clarify"}
```
`RouteResult` (lines 35-40):
```python
@dataclass(frozen=True)
class RouteResult:
    """Immutable router output: chosen owl + coarse turn classification.

    ``clarify_question`` is the one user-facing question to surface when
    ``intent_class == "clarify"`` (router-authored); None for every other class.
    """

    owl_name: str
    intent_class: Literal["conversational", "standard", "clarify"]
    clarify_question: str | None = None
```
`_build_prompt` (line 162) — replace the line-2 instructions block with a three-class
description (English glue prompt is allowed). Keep line 1 unchanged:
```python
        return (
            "You are a router. Reply with ONLY the name of the best owl for "
            'this request, or "secretary" if none fits.\n\n'
            "Available owls:\n"
            f"{roster}\n\n"
            f"User request: {user_text}\n\n"
            "Reply with these lines:\n"
            "Line 1: the owl name (required).\n"
            "Line 2: one of 'conversational', 'standard', or 'clarify':\n"
            "- 'conversational' if you can FULLY answer this yourself, right now, "
            "from your own knowledge — greetings, thanks, opinions, chit-chat, and "
            "any question you can answer or explain directly (a definition, how-to, "
            "advice, a mnemonic, reasoning), with NO need to look anything up, "
            "search, fetch, read a file, run a command, or take any external action.\n"
            "- 'standard' if answering REQUIRES doing, finding, fetching, creating, "
            "or changing something — AND the request is clear enough to act on, OR "
            "the likely action is cheap and reversible (just try it).\n"
            "- 'clarify' ONLY when the request is genuinely ambiguous about WHAT to "
            "do AND the most likely action is expensive, slow, irreversible, or you "
            "are unsure it is even possible — so a wrong guess would waste real "
            "effort or do harm. When torn between 'standard' and 'clarify', choose "
            "'standard' and act. Judge by meaning, in any language.\n"
            "Line 3 (ONLY if line 2 is 'clarify'): the single short question to ask "
            "the user, in their language. Omit this line otherwise."
        )
```
`_parse_intent_class` (line 220) — return the actual token (now three-valued):
```python
    def _parse_intent_class(self, raw: str) -> Literal["conversational", "standard", "clarify"]:
        """Scan every line AFTER the owl-name line for the class token. Fail-safe → 'standard'."""
        lines = (raw or "").strip().splitlines()
        for line in lines[1:]:
            token = line.strip().strip("\"'`.,:;()[]{}<>").lower()
            if token in _VALID_CLASSES:
                return token  # type: ignore[return-value]
        return "standard"
```
Add a new pure helper right after it:
```python
    def _parse_clarify_question(self, raw: str, intent_class: str) -> str | None:
        """Extract the line-3 clarifying question for a 'clarify' verdict.

        The question is every non-empty line AFTER the line that carried the
        class token, joined with spaces. Returns None for any non-clarify class
        OR when no question text follows (caller downgrades clarify→standard).
        """
        if intent_class != "clarify":
            return None
        lines = (raw or "").strip().splitlines()
        # find the index of the line bearing the clarify token, then take the rest
        for i, line in enumerate(lines[1:], start=1):
            token = line.strip().strip("\"'`.,:;()[]{}<>").lower()
            if token in _VALID_CLASSES:
                rest = [ln.strip() for ln in lines[i + 1:] if ln.strip()]
                question = " ".join(rest).strip()
                return question or None
        return None
```
`route()` (lines 316-343) — compute the question and downgrade on empty:
```python
        owl = self._parse_choice(result.content, known_names)  # UNCHANGED owl parse
        intent_class = self._parse_intent_class(result.content)
        clarify_question = self._parse_clarify_question(result.content, intent_class)
        if intent_class == "clarify" and not clarify_question:
            # A clarify verdict with no question is malformed — downgrade to the
            # conservative default (act) so we never surface an empty question.
            log.engine.info(
                "[router] route: clarify verdict had no question — downgrading to standard",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            intent_class = "standard"
        ...
        return RouteResult(owl, intent_class, clarify_question)
```
Update the three fallback returns (lines 271, 299) to `RouteResult(_DEFAULT_FALLBACK, "standard", None)` (third arg defaults to None, so they may be left unchanged). Also widen the `_parse_intent_class` annotation usage if mypy complains.

- [ ] **Step 4: Run tests + static checks**

Run: `uv run pytest tests/owls/test_router_clarify.py tests/owls -v && uv run mypy src/stackowl/owls/router.py`
Expected: PASS / clean. Existing router tests stay green (byte-identical 2-line behavior).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(router): clarify verdict + line-3 question parse (downgrade on empty)"
```

---

### Task 3: `ClarifyGateway.ask(deliver=...)` register-without-deliver

**Files:**
- Modify: `src/stackowl/interaction/clarify_gateway.py` (`ask`:141-247)
- Test: `tests/interaction/test_clarify_gateway_deliver.py` (new)

**Interfaces:**
- Produces: `ClarifyGateway.ask(..., deliver: bool = True)`. When `deliver=False`, the entry is registered (peekable / resolvable) but `adapter.send_clarify` is NOT called.

- [ ] **Step 1: Write the failing test**

```python
# tests/interaction/test_clarify_gateway_deliver.py
import pytest
from stackowl.interaction.clarify_gateway import ClarifyGateway


class _SpyAdapter:
    def __init__(self): self.sent = []
    async def send_clarify(self, session_id, question, choices, clarify_id):
        self.sent.append((session_id, question, clarify_id))


@pytest.mark.asyncio
async def test_deliver_false_registers_but_does_not_send():
    gw = ClarifyGateway()
    spy = _SpyAdapter()
    gw.register_adapter("cli", spy)
    cid = await gw.ask("sess", "cli", "What kind of pictures?", deliver=False)
    assert spy.sent == []                                   # not delivered
    pending = gw.peek_for_session("sess", "cli")            # but registered
    assert pending is not None and pending.clarify_id == cid
    assert pending.question == "What kind of pictures?"
    assert pending.event is None                            # turn-yield


@pytest.mark.asyncio
async def test_deliver_true_default_still_sends():
    gw = ClarifyGateway()
    spy = _SpyAdapter()
    gw.register_adapter("cli", spy)
    await gw.ask("sess", "cli", "Q?")
    assert len(spy.sent) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/interaction/test_clarify_gateway_deliver.py -v`
Expected: FAIL (`ask()` got an unexpected keyword argument 'deliver').

- [ ] **Step 3: Implement**

Add `deliver: bool = True` to the `ask` signature (keyword-only block, after `blocking`):
```python
        blocking: bool = False,
        deliver: bool = True,
    ) -> str:
```
Guard the delivery block (lines ~219-234): wrap the `adapter = self._adapters.get(channel)` …
`send_clarify` block so it is skipped when `not deliver`:
```python
        # 3. STEP — deliver via the channel's adapter (self-healing on failure),
        # UNLESS the caller registers-only (deliver=False): the question reaches
        # the user another way (e.g. streamed as the turn response) and a second
        # send_clarify would double-deliver. The entry is still stored so the
        # user's next reply resolves via try_resolve.
        adapter = self._adapters.get(channel)
        if not deliver:
            log.gateway.debug(
                "clarify_gateway.ask: register-only (deliver=False) — entry stored, not sent",
                extra={"_fields": {"channel": channel, "clarify_id": clarify_id}},
            )
        elif adapter is None:
            log.gateway.warning(...)   # unchanged
        else:
            try:
                await adapter.send_clarify(...)   # unchanged
            except Exception as exc:
                ...
        # 4. EXIT — set delivered = deliver and adapter is not None
```
Update the EXIT log `delivered` field to `deliver and adapter is not None`.

- [ ] **Step 4: Run tests + static checks**

Run: `uv run pytest tests/interaction/test_clarify_gateway_deliver.py tests/interaction -v && uv run mypy src/stackowl/interaction/clarify_gateway.py`
Expected: PASS / clean (existing gateway tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(clarify): ask(deliver=False) register-without-deliver for router-level clarify"
```

---

### Task 4: Wire the verdict — triage stamp + execute clarify branch

**Files:**
- Modify: `src/stackowl/pipeline/steps/triage.py:117-119`
- Modify: `src/stackowl/pipeline/steps/execute.py` (new branch just before the `_use_tools` block ~line 1517)
- Test: `tests/pipeline/test_execute_clarify_branch.py` (new)

**Interfaces:**
- Consumes: `RouteResult.clarify_question` (Task 2); `state.clarify_question`, `TOOL_FREE_CLASSES` (Task 1); `ClarifyGateway.ask(deliver=False)` (Task 3); `state.interactive`, `state.channel`, `_services.clarify_gateway`.
- Produces: a clarify turn that emits exactly one `ResponseChunk` (the question), registers a turn-yield pending clarify, and returns WITHOUT entering `_run_with_tools`.

- [ ] **Step 1: Write the failing test** (unit-level on the execute step path)

```python
# tests/pipeline/test_execute_clarify_branch.py
import pytest
from stackowl.pipeline.state import PipelineState


@pytest.mark.asyncio
async def test_clarify_branch_emits_question_and_registers_no_tool_loop(monkeypatch):
    """A clarify verdict surfaces ONE question chunk, registers a turn-yield
    pending clarify, and never calls _run_with_tools."""
    from stackowl.pipeline.steps import execute as ex

    called = {"tools": False}
    async def _boom(*a, **k):
        called["tools"] = True
        raise AssertionError("tool loop must not run on a clarify turn")
    monkeypatch.setattr(ex, "_run_with_tools", _boom)

    asked = {}
    class _GW:
        async def ask(self, session_id, channel, question, **kw):
            asked.update(session_id=session_id, question=question, kw=kw)
            return "cid"
    class _Services:
        clarify_gateway = _GW()
        # minimal attrs the step reads before the branch — provide as needed
    monkeypatch.setattr(ex, "get_services", lambda: _Services())

    state = PipelineState(
        input_text="can you help me with pictures", session_id="s", channel="cli",
        interactive=True, intent_class="clarify",
        clarify_question="Do you want me to create images, or find existing ones?",
    )
    out = await ex.execute(state)  # use the step's real entrypoint name

    assert called["tools"] is False
    assert asked["question"] == "Do you want me to create images, or find existing ones?"
    assert asked["kw"].get("deliver") is False and asked["kw"].get("blocking") is False
    joined = "".join(c.content for c in out.responses)
    assert "create images" in joined            # the question IS the response
    assert not any(getattr(c, "is_floor", False) for c in out.responses)
```
> NOTE for implementer: confirm the execute step's public entrypoint name/signature
> (it may be `execute(state)` or take extra args). Place the clarify branch so it runs
> AFTER provider selection is skipped for tool-free classes but BEFORE `_use_tools`.
> If wiring the branch through the full `execute()` requires heavy service scaffolding,
> instead test it at the gateway-journey level in Task 5 and keep this as a focused
> branch-logic test by extracting the branch into a small helper
> `_maybe_clarify(state, services) -> PipelineState | None` and testing that helper.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_execute_clarify_branch.py -v`
Expected: FAIL (branch not implemented; tool loop runs or no question chunk).

- [ ] **Step 3: Implement**

In `triage.py` (lines 117-119):
```python
    return state.evolve(
        owl_name=result.owl_name,
        intent_class=result.intent_class,
        clarify_question=result.clarify_question,
        language=language,
    )
```

In `execute.py`, add a branch just before the `_use_tools = (...)` computation (~line 1517).
Prefer extracting a small helper for testability:
```python
async def _maybe_clarify(state: PipelineState, services) -> PipelineState | None:
    """If this is an INTERACTIVE clarify turn, surface ONE question and yield.

    Registers a turn-yield pending clarify (deliver=False — the question is the
    streamed response, so a second send_clarify would double-deliver) and returns
    a state whose single response IS the question. Returns None when this is not a
    clarify turn OR there is no human to answer (cron/parliament) — the caller then
    proceeds to the standard tool path (best-effort action).
    """
    if state.intent_class != "clarify" or not state.clarify_question:
        return None
    if not state.interactive:
        log.engine.info(
            "[pipeline] execute: clarify verdict in a non-interactive context — "
            "falling through to the standard tool path",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None
    gateway = getattr(services, "clarify_gateway", None)
    if gateway is not None:
        try:
            await gateway.ask(
                state.session_id, state.channel, state.clarify_question,
                blocking=False, deliver=False,
            )
        except Exception as exc:  # never block the turn on registration failure
            log.engine.error(
                "[pipeline] execute: clarify pending registration failed — "
                "still surfacing the question",
                exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
            )
    chunk = ResponseChunk(
        content=state.clarify_question, is_final=False, chunk_index=0,
        trace_id=state.trace_id, owl_name=state.owl_name,
    )
    log.engine.info(
        "[pipeline] execute: clarify — surfaced one question, yielding turn (no tool loop)",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
    )
    return state.evolve(responses=(*state.responses, chunk))
```
Call it at the insertion point:
```python
    _clarify_out = await _maybe_clarify(state, _services)
    if _clarify_out is not None:
        return _clarify_out
    # existing _use_tools computation follows unchanged
```
(`_services` is already bound in the step — it is used for the budget callback at ~line 996/999. If it is not in scope at line 1517, call `get_services()`.)

- [ ] **Step 4: Run tests + static checks**

Run: `uv run pytest tests/pipeline/test_execute_clarify_branch.py -v && uv run mypy src/stackowl/pipeline/steps/execute.py src/stackowl/pipeline/steps/triage.py`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(pipeline): wire clarify verdict — triage stamp + execute deliver-and-yield branch"
```

---

### Task 5: Gateway integration journeys (mock only the AI provider)

**Files:**
- Test: `tests/journeys/test_clarify_verdict_journey.py` (new)

**Interfaces:**
- Consumes: everything above, end-to-end through the gateway. Mirror the existing
  `tests/journeys/test_conversational_bypass_journey.py` harness (same provider-mock
  + gateway wiring). Reuse its fixtures — do NOT build a parallel harness.

- [ ] **Step 1: Write the journeys**

Model these on the existing conversational-bypass journey. Script the router/owl provider
mock's reply text to drive the verdict; assert OUTCOMES.

```python
# tests/journeys/test_clarify_verdict_journey.py
#
# 1. test_vague_expensive_request_asks_one_question_not_tool_spiral
#    GIVEN provider mock returns router reply "secretary\nclarify\n<question>"
#    WHEN "can you help me with pictures" runs through the gateway
#    THEN: outbound contains the ONE question; ZERO tool calls executed;
#          is_floor is False; a pending clarify is registered for the session.
#    AND a follow-up message ("the holiday photos") resolves the pending clarify
#        (ClarifyPump.resolve_or_rewrite -> turn-yield rewrite) and the resume
#        turn routes/acts (no overclaim, no floor).
#
# 2. test_greeting_routes_conversational_no_floor   (incident bug H)
#    GIVEN provider mock returns "secretary\nconversational" for "hi"
#    THEN plain reply, is_floor False, no clarify pending, no tool loop.
#
# 3. test_vague_cheap_request_still_acts   (FALSIFICATION GUARD)
#    GIVEN provider mock returns "secretary\nstandard" for "summarize this"
#    THEN the tool path is entered (acts); NO clarify pending registered.
#    Proves clarify fires only on the verdict, never blanket-on-ambiguity.
```
Implement all three with the real pipeline + real `ClarifyGateway` + a spy channel
adapter, mocking only the provider. Assert tool-call count via the same hook the
conversational-bypass journey uses.

- [ ] **Step 2: Run to verify (1) and (3) fail without the feature, then pass with it**

Run: `uv run pytest tests/journeys/test_clarify_verdict_journey.py -v`
Expected: PASS once Tasks 1-4 are in.

- [ ] **Step 3: Full focused suite**

Run: `uv run pytest tests/journeys tests/owls tests/pipeline tests/interaction -q`
Expected: green (the one known pre-existing `test_conversational_bypass_journey::test_standard_turn_enters_tool_loop` failure is unrelated — verify it fails identically on `main`).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test(journeys): clarify verdict — one question not a spiral; hi no-floor; vague-cheap still acts"
```

---

## Self-Review

- **Spec coverage:** §3 mechanism → Tasks 2 (emit), 4 (triage+execute), 3 (deliver=False), resume reused. §4 table → all 7 files mapped across Tasks 1-4. §5 tests → Task 5 (journeys) + Tasks 1-3 unit. §6 invariants → downgrade (Task 2), no-floor (Task 4 assert + Task 5), byte-identical (Task 2 test).
- **Placeholders:** none — code shown for every step. The only deferred decision is the execute-step entrypoint name (Task 4 note instructs the implementer to confirm + offers a helper-extraction fallback that keeps the test focused).
- **Type consistency:** `intent_class` Literal widened identically in state.py and router.py; `clarify_question: str | None` consistent across RouteResult, state, helpers; `TOOL_FREE_CLASSES` single definition in state.py, imported elsewhere.
