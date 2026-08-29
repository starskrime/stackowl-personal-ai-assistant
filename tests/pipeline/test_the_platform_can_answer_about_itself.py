"""A question about the platform's OWN state is grounded by the platform.

BAKIR, 2026-08-29: "Platform failing on single ask. It does not have capability to
work with himself." He was right, and the mechanism is exact.

MEASURED, trace 916bb4b77e5f406bb6f258ad045bb57c. He asked **"What agents i have"**
— eighteen characters. The turn:

    18:13:58  owls_list.execute: exit {'success': True, 'count': 11}   <- it HAD the answer
    18:14:07  overclaim.detected {'failed_capability': 'retrieval'}
    18:14:08  corrective re-run ... "it answered from memory when the request
              required a LIVE web lookup, and no retrieval tool was called."
    18:14:20  overclaim.detected again -> floored
    18:16:16  dead-lettered after three attempts; he got a failure

``_should_classify_retrieval`` sends a turn to the "did this need a live lookup?"
classifier whenever ``_retrieval_ran`` is False, and::

    _RETRIEVAL_TOOLS = frozenset({"web_search", "web_fetch"})

So the platform's own registry is not a source. Every self-referential question —
what agents, what skills, what tools, what happened in that session — is answered
correctly from local authority and then judged ungrounded.

WHY NOT SIMPLY WIDEN ``_RETRIEVAL_TOOLS``. It has a SECOND consumer: the URL
provenance check, where "did a WEB tool run?" is the right question and adding
``owls_list`` would be wrong. Two questions, two sets. ``delivery_gate.py:417``
already carries a ``ponytail:`` marker anticipating the extension; this is the
extension, kept to the one consumer that needs it.
"""

from __future__ import annotations

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _turn(*, tools: tuple[str, ...]) -> PipelineState:
    from stackowl.pipeline.state import ToolCall

    return PipelineState(
        trace_id="t-self",
        session_key="owl:secretary:telegram:dm:1",
        conversation_id="c-1",
        input_text="What agents i have",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        intent_class="standard",
        responses=(
            ResponseChunk(
                content="You have 11 agents: secretary, jobmarket, scout, ...",
                is_final=True, chunk_index=0, trace_id="t-self", owl_name="secretary",
            ),
        ),
        tool_calls=tuple(
            ToolCall(tool_name=t, args={}, result="ok", error=None, duration_ms=1.0)
            for t in tools
        ),
    )


def test_owls_list_GROUNDS_a_question_about_the_platforms_own_agents() -> None:
    """Bakir's exact turn. It must not reach the live-lookup classifier."""
    from stackowl.pipeline.delivery_gate import _should_classify_retrieval

    assert _should_classify_retrieval(_turn(tools=("owls_list",))) is False, (
        "the turn answered from the platform's own registry and is still being "
        "asked whether it needed a LIVE WEB LOOKUP — this is why 'What agents i "
        "have' floored three times and dead-lettered"
    )


def test_every_local_authority_tool_grounds_a_turn() -> None:
    """The whole self-knowledge surface, not just the one that bit."""
    from stackowl.pipeline.delivery_gate import _should_classify_retrieval

    for tool in (
        "owls_list", "skills_list", "skill_view", "session_search",
        "transcripts", "read_logs", "memory", "tool_describe", "tool_search",
    ):
        assert _should_classify_retrieval(_turn(tools=(tool,))) is False, tool


def test_a_turn_that_touched_NOTHING_is_still_classified() -> None:
    """The guard must stay narrow.

    A draft answered purely from model memory, with no tool at all, is exactly
    what trigger 3 exists to catch. Grounding everything would delete the gate.
    """
    from stackowl.pipeline.delivery_gate import _should_classify_retrieval

    assert _should_classify_retrieval(_turn(tools=())) is True


def test_an_unrelated_tool_does_NOT_ground_a_turn() -> None:
    """Running *a* tool is not the same as having a source.

    `send_message` is an effect, not evidence. If any tool call counted, a turn
    that messaged someone and then invented an answer would clear the gate.
    """
    from stackowl.pipeline.delivery_gate import _should_classify_retrieval

    assert _should_classify_retrieval(_turn(tools=("send_message",))) is True


def test_the_web_provenance_set_is_UNCHANGED() -> None:
    """The other consumer must not move.

    `_RETRIEVAL_TOOLS` also answers "did a WEB tool run?" for URL provenance. If
    owls_list leaked into it, a locally-sourced answer carrying a URL would be
    treated as web-sourced — a different bug, in the opposite direction.
    """
    from stackowl.pipeline.delivery_gate import _RETRIEVAL_TOOLS

    assert _RETRIEVAL_TOOLS == frozenset({"web_search", "web_fetch"})
