"""Reversibility is the CONSENT test. It was also the clarify test, and they
answer different questions.

HE REPORTED, 2026-09-03: "i did ask him on task he did just responde done not on
jarvis style and WITHOUT ASKING." He had said "Optimize my promt and wish to
create good agent" — an invitation to settle the prompt together — and a
scheduled agent was built without a word.

THE MODEL FOLLOWED ITS INSTRUCTIONS EXACTLY. Consent logged
`decision: allow, reason: reversible_auto` for owl_build: an owl can be retired,
so the effect is undo-able and F-27/ADR-3 auto-allows it. That classification is
deliberate and stays. What was wrong is that the CLARIFY tool's description
reused the same property as its own test — "ONLY when the action it gates is
irreversible or expensive" — so one fact about undo-ability silently answered two
different questions:

    may I run this without permission?        <- reversibility is the right test
    should I confirm the spec with the human? <- it is not

AND THE DESCRIPTION CONTRADICTED ITSELF. Its first sentence says to ask "when the
task is genuinely ambiguous or you are missing information only the user can
provide". His request was exactly that, and reversible. Sentence one said ask;
the LANE clause said do not.

MEASURED: 13 clarify calls in 20,144 turns with a tool sequence — 0.065% — while
the tool is in the guaranteed-presented set. It is offered on every turn and
almost never chosen, which is what an instruction not to use it looks like.

WHAT THIS DOES NOT DECIDE. How much the platform should interrupt him is his
call, and it is escalated rather than set here. This only removes a contradiction
and names DURABLE alongside irreversible and expensive, because an artefact that
keeps acting after the turn ends is expensive to get wrong however easily it can
be deleted afterwards.
"""

from __future__ import annotations

from stackowl.tools.interaction.clarify import ClarifyTool


def test_reversibility_is_no_longer_the_clarify_test() -> None:
    d = ClarifyTool().description
    assert "ONLY when the action it gates is irreversible or expensive" not in d, (
        "clarify still uses the CONSENT test as its own — a reversible action "
        "that is under-specified is exactly the case he complained about"
    )
    assert "REVERSIBILITY IS NOT THE TEST" in d


def test_a_DURABLE_artefact_is_named_as_worth_asking_about() -> None:
    """The specific gap: a scheduled agent is reversible and still worth one
    question, because getting it wrong costs every run until he notices."""
    d = ClarifyTool().description
    assert "DURABLE" in d
    for cue in ("scheduled agent", "recurring job", "standing rule"):
        assert cue in d, f"the guidance never names {cue!r} as a case to ask about"


def test_the_anti_lane_SURVIVES() -> None:
    """The control. Widening when to ask must not turn clarify into a lookup —
    "do not ask the user what you can find yourself" is the rule that keeps this
    from becoming the interrogation he would like even less."""
    d = ClarifyTool().description
    assert "ANTI-LANE" in d
    assert "Do not ask the user what you can find" in d


def test_a_one_off_cheap_action_is_still_ACTED_on_not_asked_about() -> None:
    """The other half of the control: the change must not delete the instruction
    to just act. If every turn starts asking, the fix is worse than the defect."""
    d = ClarifyTool().description
    assert "do NOT clarify: act on the most likely interpretation" in d
    assert "state your assumption" in d
