"""Story 3.4 (AC4) — a `ConsentRequest`'s `channel` comes only from ingress
provenance (the pipeline's own `state.channel` / `TraceContext`), never from
the tool call's own arguments.

Driven directly through `ConsequentialActionGate.check()` (the real chokepoint
`pipeline/steps/execute.py` calls, `channel=state.channel` — see
`execute.py`'s own comment: "The category is derived inside gate.check() from
the TRUSTED manifest, never from LLM-supplied args"). `call_args` carries a
model-controlled dict; a spoofed `"channel"` key inside it must never reach
the `ConsentRequest` the prompter sees, because `check()`'s `channel` keyword
is a caller-supplied, provenance-sourced value and `call_args` is passed
through to `Tool.consent_summary()` only, never read for routing.
"""

from __future__ import annotations

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope
from stackowl.tools.registry import ConsequentialActionGate

pytestmark = pytest.mark.asyncio


class _RecordingPrompter:
    def __init__(self, scope: ConsentScope = ConsentScope.ONCE) -> None:
        self._scope = scope
        self.requests: list[ConsentRequest] = []

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.requests.append(req)
        return self._scope


class _StubConsequentialTool(Tool):
    @property
    def name(self) -> str:
        return "send_file"

    @property
    def description(self) -> str:
        return "A consequential stub."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description,
            parameters=self.parameters, action_severity="consequential",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ran", duration_ms=1.0)


async def test_a_spoofed_channel_in_call_args_never_overrides_the_real_channel() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))

    allowed = await gate.check(
        _StubConsequentialTool(),
        channel="telegram",  # the REAL, provenance-sourced channel
        session_key="s1",
        call_args={"channel": "discord", "path": "/etc/passwd"},  # model-controlled
    )

    assert allowed is True
    assert len(prompter.requests) == 1
    assert prompter.requests[0].channel == "telegram", (
        "the spoofed 'channel' key inside call_args must never reach the "
        "ConsentRequest the prompter decides against"
    )


async def test_no_channel_key_in_call_args_at_all_is_unaffected() -> None:
    """Baseline: an ordinary call with no channel-shaped key in its args still
    carries the real channel through, unchanged."""
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))

    await gate.check(
        _StubConsequentialTool(), channel="slack", session_key="s1",
        call_args={"path": "notes.txt"},
    )

    assert prompter.requests[0].channel == "slack"


async def test_an_empty_provenance_channel_is_not_backfilled_from_call_args() -> None:
    """The absence of a real channel must not be silently 'fixed' by reading
    one out of the model's own arguments either — an empty provenance channel
    stays empty."""
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))

    await gate.check(
        _StubConsequentialTool(), channel=None, session_key="s1",
        call_args={"channel": "telegram"},
    )

    assert prompter.requests[0].channel == ""
