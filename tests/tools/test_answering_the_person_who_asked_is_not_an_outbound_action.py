"""The platform was asking permission to answer him.

Bakir, 2026-09-10: *"Platform does not know user communicating with the channel
and if he asked something then it needs to delivered back on channel from where it
asked. also it is aksing approval for everthing which is limting autonomous of
platform. Only dangerous commnds should be approved liked removing os or platform
components, or something dangerous not simple task should be asked for approval."*

BOTH HALVES OF THAT SENTENCE ARE ONE DEFECT. `send_message` and `send_file` are
the tools that carry the turn's answer back to him. Both declare
``action_severity="consequential"``, so the registry gate prompts before execute,
and anything without an explicit tier then falls to
``tiers.get(tool_name, ALWAYS_ASK)`` — so the careful always-ask LIST is not what
binds, the default is.

MEASURED over every retained log: of **26** `send_message`/`send_file` gate
decisions, **17 were denied `not_approved`**. Two thirds of the time the reply
simply never left, which is exactly "it needs to be delivered back on the channel
from where it asked" seen from the inside.

WHY THE EXEMPTION IS SAFE, and it is a property of the code rather than a
judgement anyone has to trust. Neither tool can address a third party: `_deliver`
sets ``target_chat_id=await resolve_recipient(target, session_key, ...)``, which
takes the recipient from the LANE — the session store's recorded target, or the
session key itself. The model's ``target`` argument selects only WHICH CHANNEL to
reach the same requester on. So a send naming the turn's own channel goes to the
person who asked, by construction.

AND IT DOES NOT WEAKEN B2. Authority is still never taken from raw LLM-supplied
args: the comparison is against a TRUSTED value the turn already carries, and a
call arg can only MATCH it, never exceed it. Naming any other channel still goes
to the gate; an always-ask tool is still always-ask.
"""

from __future__ import annotations

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import ConsentPolicy
from stackowl.tools.registry import ConsequentialActionGate


class _ExplodingPolicy(ConsentPolicy):
    """Any consent request at all is the interruption this test exists to end."""

    async def request(self, **kwargs: object) -> bool:  # type: ignore[override]
        raise AssertionError(
            f"the platform asked permission to answer: {kwargs.get('tool_name')!r}"
        )


class _FakeDeliveryTool(Tool):
    def __init__(self, name: str, category: str) -> None:
        self._name = name
        self._category = category

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "delivers this turn's answer"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            consent_category=self._category,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok")


_DELIVERY = (("send_message", "agent_message"), ("send_file", "agent_file"))


@pytest.mark.tripwire
@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "category"), _DELIVERY)
async def test_an_explicit_reply_to_the_same_channel_is_not_gated(
    tool_name: str, category: str
) -> None:
    """THE regression, with the target named."""
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())
    allowed = await gate.check(
        _FakeDeliveryTool(tool_name, category),
        channel="telegram",
        call_args={"text": "here is your answer", "target": "telegram"},
    )
    assert allowed is True


@pytest.mark.tripwire
@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "category"), _DELIVERY)
async def test_an_OMITTED_target_is_the_same_reply_and_is_the_common_case(
    tool_name: str, category: str
) -> None:
    """Omitting `target` IS naming it.

    Both tools document `target` as defaulting to "the channel this turn came
    from", so an absent target is the ordinary way a reply is sent. Had the
    exemption required the argument, it would have covered the rare call and
    missed every real one.
    """
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())
    for args in ({"text": "hi"}, {"text": "hi", "target": ""}, {"text": "hi", "target": "  "}):
        assert await gate.check(
            _FakeDeliveryTool(tool_name, category), channel="telegram", call_args=args
        ) is True


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_send_to_a_DIFFERENT_channel_is_still_gated() -> None:
    """THE CONTROL. Without it "the gate allowed it" is indistinguishable from a
    gate that allows everything — and reaching a lane he is not on is precisely
    the outbound action consent exists for."""
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())
    with pytest.raises(AssertionError, match="asked permission"):
        await gate.check(
            _FakeDeliveryTool("send_message", "agent_message"),
            channel="telegram",
            call_args={"text": "hi", "target": "slack"},
        )


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_turn_with_no_channel_is_still_gated() -> None:
    """An unattended lane has no requester to reply TO, so nothing is exempt.

    This is the cron/MCP/webhook case: there is no "channel it was asked from",
    so the exemption must not fire and the caller must answer to the gate.
    """
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())
    with pytest.raises(AssertionError, match="asked permission"):
        await gate.check(
            _FakeDeliveryTool("send_message", "agent_message"),
            channel="",
            call_args={"text": "hi"},
        )


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_NON_delivery_consequential_tool_is_untouched() -> None:
    """The exemption is keyed on the declared category, not on being a tool.

    `execute_code` is the thing he explicitly wants gated — "only dangerous
    commands should be approved" — and it must not drift into this exemption.
    """
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())
    with pytest.raises(AssertionError, match="asked permission"):
        await gate.check(
            _FakeDeliveryTool("execute_code", "code_execution"),
            channel="telegram",
            call_args={"target": "telegram"},
        )


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_non_string_target_falls_through_to_the_gate() -> None:
    """Fail CLOSED on anything unexpected.

    A structured `target` is not a channel name this exemption can reason about,
    so it must be gated rather than guessed at.
    """
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())
    with pytest.raises(AssertionError, match="asked permission"):
        await gate.check(
            _FakeDeliveryTool("send_message", "agent_message"),
            channel="telegram",
            call_args={"target": {"channel": "telegram"}},
        )


def test_the_two_delivery_tools_declare_the_categories_the_exemption_keys_on() -> None:
    """The property that keeps this true for the REAL tools, not just fakes.

    The sibling browser exemption learned this the hard way: an exemption is only
    as complete as the declaration it keys on, and two of the five browser tools
    lived outside the file everyone was reading.
    """
    from stackowl.tools.consent import REPLY_CATEGORIES
    from stackowl.tools.scheduling import send_file, send_message

    assert send_message._CATEGORY in REPLY_CATEGORIES  # noqa: SLF001
    assert send_file._CATEGORY in REPLY_CATEGORIES  # noqa: SLF001
