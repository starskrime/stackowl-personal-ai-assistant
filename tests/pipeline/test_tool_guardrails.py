"""D05.7 — tool-loop guardrails.

The headline test is test_a_fan_out_with_distinct_args_never_warns. It encodes a
MEASURED case: `headhunter` ran 46 web_searches in one turn and returned a real
synthesized list of live job postings. Any guard keyed on the tool NAME alone —
which is what StackOwl had — would have flagged productive work.
"""

from __future__ import annotations

from stackowl.pipeline.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolCallSignature,
    canonical_tool_args,
)


def _c(**kw):
    return ToolCallGuardrailController(ToolCallGuardrailConfig(**kw))


# --------------------------------------------------------------------------- #
# The measured case this item must not break.
# --------------------------------------------------------------------------- #


def test_a_fan_out_with_distinct_args_never_warns():
    """46 web_searches with 46 DIFFERENT queries — the real headhunter turn."""
    c = _c()
    actions = [
        c.after_call("web_search", {"q": f"query {i}"}, f"result {i}",
                     failed=False, idempotent=True).action
        for i in range(46)
    ]
    assert set(actions) == {"allow"}, "productive fan-out must not be flagged"


def test_the_same_call_repeated_warns():
    c = _c()
    actions = [
        c.after_call("web_search", {"q": "same"}, "same result",
                     failed=False, idempotent=True).action
        for _ in range(5)
    ]
    assert actions[0] == "allow"
    assert actions[1] == "warn", "a second identical call should already warn"


def test_argument_ORDER_does_not_change_identity():
    """Otherwise {'a':1,'b':2} and {'b':2,'a':1} would look like two calls and a
    real repeat would go unnoticed."""
    a = ToolCallSignature.from_call("t", {"a": 1, "b": 2})
    b = ToolCallSignature.from_call("t", {"b": 2, "a": 1})
    assert a == b


def test_arguments_are_hashed_never_stored():
    """A tool call can carry a token or a file's contents, and the signature is
    logged."""
    sig = ToolCallSignature.from_call("shell", {"command": "export SECRET=hunter2"})
    assert "hunter2" not in sig.args_hash
    assert "hunter2" not in repr(sig)


def test_unserialisable_args_do_not_raise():
    """A guardrail must never cost a turn its tools."""
    assert canonical_tool_args({"obj": object()})
    c = _c()
    assert c.after_call("t", {"obj": object()}, "r", failed=False, idempotent=False).action == "allow"


# --------------------------------------------------------------------------- #
# The three detectors.
# --------------------------------------------------------------------------- #


def test_idempotent_no_progress_warns_on_a_repeated_RESULT():
    """The detector our previous tracker was structurally blind to: it reset the
    streak on success, so a SUCCESSFUL repeat looked like progress."""
    c = _c()
    c.after_call("read_file", {"path": "a"}, "same bytes", failed=False, idempotent=True)
    d = c.after_call("read_file", {"path": "a"}, "same bytes", failed=False, idempotent=True)
    assert d.action == "warn"
    assert d.code in {"idempotent_no_progress_warning", "repeated_exact_call_warning"}


def test_a_changed_result_is_progress_not_a_loop():
    c = _c(exact_repeat_warn_after=99)  # isolate the no-progress detector
    c.after_call("read_file", {"path": "a"}, "v1", failed=False, idempotent=True)
    d = c.after_call("read_file", {"path": "a"}, "v2", failed=False, idempotent=True)
    assert d.action == "allow"


def test_a_mutating_tool_is_not_checked_for_no_progress():
    """Two identical writes returning the same 'ok' are not a loop — the effect
    happened twice."""
    c = _c(exact_repeat_warn_after=99)
    c.after_call("write_file", {"p": "a"}, "ok", failed=False, idempotent=False)
    d = c.after_call("write_file", {"p": "a"}, "ok", failed=False, idempotent=False)
    assert d.action == "allow"


def test_repeated_identical_FAILURE_warns():
    c = _c()
    c.after_call("web_fetch", {"url": "u"}, "boom", failed=True, idempotent=True)
    d = c.after_call("web_fetch", {"url": "u"}, "boom", failed=True, idempotent=True)
    assert d.action == "warn" and "identical arguments" in d.message


def test_same_tool_failing_with_DIFFERENT_args_warns_later():
    c = _c()
    actions = [
        c.after_call("web_fetch", {"url": f"u{i}"}, "boom", failed=True, idempotent=True).action
        for i in range(3)
    ]
    assert actions[-1] == "warn"


def test_a_success_with_the_SAME_args_clears_that_signature():
    c = _c()
    c.after_call("t", {"a": 1}, "boom", failed=True, idempotent=False)
    c.after_call("t", {"a": 1}, "fine", failed=False, idempotent=False)
    d = c.after_call("t", {"a": 1}, "boom", failed=True, idempotent=False)
    assert d.action == "allow", "the call demonstrably works; one failure is not a loop"


def test_a_success_with_DIFFERENT_args_does_NOT_clear_another_signature():
    """Asserted deliberately, because the first version of this test assumed the
    opposite and the code was right.

    If t{a:1} has failed identically three times, that IS a loop worth flagging —
    whether or not t{a:2} happened to work in between. The exact-repeat detector
    is per-signature by design; the same-tool counter is the one that clears on
    any success."""
    c = _c()
    c.after_call("t", {"a": 1}, "boom", failed=True, idempotent=False)
    c.after_call("t", {"a": 1}, "boom", failed=True, idempotent=False)
    c.after_call("t", {"a": 2}, "fine", failed=False, idempotent=False)
    d = c.after_call("t", {"a": 1}, "boom", failed=True, idempotent=False)
    assert d.action == "warn"


# --------------------------------------------------------------------------- #
# Warn-only — the operator decision.
# --------------------------------------------------------------------------- #


def test_there_is_NO_hard_stop_mode_at_all() -> None:
    """REPLACES two tests that exercised hard stops. Removed 2026-08-30 by
    operator decision: hard_stop_enabled had no setter anywhere, so the three
    block/halt branches were unreachable — and two of them lived in before_call,
    which production never called. The old test asserted the flag defaulted False,
    which passes just as well when the feature is decoration.

    This asserts the stronger thing: the capability is GONE, so it cannot rot back
    into unreachable code that reads as protection.
    """
    c = _c()
    assert not hasattr(c, "before_call")
    assert not hasattr(c, "halt_decision")
    for field in ("hard_stop_enabled", "exact_repeat_block_after",
                  "same_tool_failure_halt_after", "no_progress_block_after"):
        assert field not in type(c.config).__dataclass_fields__, field
    assert all(
        d.allows_execution
        for d in (c.after_call("t", {"a": 1}, "x", failed=True, idempotent=False)
                  for _ in range(20))
    ), "no sequence of failures may ever prevent execution"


def test_reset_for_turn_clears_everything():
    c = _c()
    c.after_call("t", {"a": 1}, "boom", failed=True, idempotent=False)
    c.reset_for_turn()
    d = c.after_call("t", {"a": 1}, "boom", failed=True, idempotent=False)
    assert d.action == "allow"


# --------------------------------------------------------------------------- #
# WIRING. D05.2 shipped a suite where the module was correct and unused; this is
# the test that would catch the same mistake here.
# --------------------------------------------------------------------------- #


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_the_warning_reaches_the_MODEL_not_just_the_log():
    """The guidance must ride along with the tool result the model sees.

    Mutation: delete `+ _guard_note` from execute.py's `return tr.output`. Every
    unit test above still passes and this one fails — which is the point.
    """
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import execute
    from stackowl.tools.base import Tool, ToolManifest, ToolResult
    from stackowl.tools.registry import ToolRegistry

    class _Echo(Tool):
        @property
        def name(self): return "echo"
        @property
        def description(self): return "echo back the same value for loop-guard testing"
        @property
        def parameters(self): return {"type": "object", "properties": {"q": {"type": "string"}}}
        @property
        def manifest(self):
            return ToolManifest(name=self.name, description=self.description,
                                parameters=self.parameters, action_severity="read")
        async def execute(self, **kw):
            return ToolResult(success=True, output="identical", duration_ms=1.0, verified=True)

    seen: list[str] = []

    class _Provider:
        protocol = "anthropic"
        async def complete_with_tools(self, user_text, system_text, tool_schemas,
                                      tool_dispatcher, max_iterations=8, history=None, **_kw):
            # Call the SAME tool with the SAME args twice — a real loop.
            for _ in range(2):
                seen.append(await tool_dispatcher("echo", {"q": "same"}))
            return "done", []

    class _Registry:
        def get(self, name): return _Echo() if name == "echo" else None
        def all(self): return [_Echo()]
        def to_provider_schema(self, protocol, **kw):
            return ToolRegistry.with_defaults().to_provider_schema(protocol, **kw)

    class _PR:
        def __init__(self, p): self._p = p
        def get(self, n): return self._p
        def get_by_tier(self, t): return self._p

    token = set_services(StepServices(provider_registry=_PR(_Provider()),
                                      tool_registry=_Registry()))
    try:
        await execute.run(PipelineState(
            trace_id="t", session_key="s", input_text="go", channel="cli",
            owl_name="secretary", pipeline_step="execute", system_prompt="SYS",
        ))
    finally:
        reset_services(token)

    assert len(seen) == 2, f"expected two dispatches, got {len(seen)}"
    assert "[loop-guard]" not in seen[0], "the first call is not a repeat"
    assert "[loop-guard]" in seen[1], (
        f"the repeat warning never reached the model: {seen[1]!r}"
    )
