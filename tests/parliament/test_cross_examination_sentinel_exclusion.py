"""Cross-examination must never feed a timed-out/errored owl's sentinel back
into the debate as a real position.

CrossExaminationPromptBuilder.build() was the one ParliamentRound consumer that
read raw ``.responses`` instead of ``.genuine_responses()`` (convergence.py and
synthesizer.py both already filtered correctly — see PARL-1/F078). A sentinel
like ``"[timed out after 30s]"`` would be embedded verbatim in every other
owl's next-round prompt as if it were a genuine peer argument.
"""

from __future__ import annotations

from stackowl.parliament.cross_examination import CrossExaminationPromptBuilder
from stackowl.parliament.models import ParliamentRound


def test_sentinel_response_excluded_from_cross_examination_prompt() -> None:
    rnd = ParliamentRound(
        round_number=1,
        responses={"scout": "real position on the topic", "owl": "[timed out after 30s]"},
        truncated={"scout": False, "owl": True},
    )
    builder = CrossExaminationPromptBuilder()

    prompt = builder.build(
        topic="topic text", owl_name="sage", prior_rounds=[rnd], interjections=[]
    )

    assert "[timed out after 30s]" not in prompt
    assert "real position on the topic" in prompt


def test_genuine_response_still_included_and_self_excluded() -> None:
    rnd = ParliamentRound(
        round_number=1,
        responses={"scout": "scout's real take", "sage": "sage's real take"},
        truncated={"scout": False, "sage": False},
    )
    builder = CrossExaminationPromptBuilder()

    prompt = builder.build(
        topic="topic text", owl_name="sage", prior_rounds=[rnd], interjections=[]
    )

    assert "scout's real take" in prompt
    assert "sage's real take" not in prompt  # own position omitted, not re-fed
