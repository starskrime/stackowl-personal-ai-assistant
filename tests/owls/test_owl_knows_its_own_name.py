"""The rename must reach the MODEL, not just the record.

BAKIR, 2026-08-16: "I am asking to change behavior of one of agent and he fails
or lies by saying updated."

MEASURED before this was written, on live state. ``owl_build action=rename`` works
perfectly as far as it goes: ``~/.stackowl/stackowl.yaml`` carries
``{name: secretary, display_name: Friday}``, and ``manifest.display`` returns
``'Friday'``. The write is durable and correct.

But ``manifest.display`` — documented as "the name to show/speak" — had exactly TWO
readers in the whole source: ``tools/agents/resolver.py`` (spoken name -> slug, the
INPUT direction) and the ``owls_list`` tool. Neither is the prompt. The persona
actually handed to the model was 291 characters long, opened with "You are the
Secretary", and did not contain the string "Friday" anywhere. The model was never
told its own name, so it kept answering as the old one — and the agent had resorted
to writing itself a curated memory note ("My display name is FRIDAY (renamed from
Mary)... rollover messages saying 'Secretary (Mary)' are STALE artifacts") to
paper over it.

That is CLAUDE.md defect shape #1 exactly: a write with no reader. The rename was
"cosmetic" in the most literal sense — it changed a label two tools read and never
reached the thing whose behaviour it was supposed to change.

WHY THIS IS THE RIGHT SEAM. ``DNAPromptInjector.inject`` has exactly one caller,
``pipeline/steps/assemble.py``, and it is the only place a turn's persona is built.
One fix here covers every owl on the real turn path — there is no second copy to
drift from.

LAW 1 IS SAFE. The identity line lives in the STABLE base prompt, which is frozen
per incarnation. It changes only when the owl's display_name changes, and that
already invalidates the presented prompt — the same event, not a new one.
"""

from __future__ import annotations

from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.manifest import OwlAgentManifest


def _owl(**over: object) -> OwlAgentManifest:
    base: dict = dict(
        name="secretary",
        role="primary-assistant",
        system_prompt="You are the Secretary, the user's primary agent.",
        model_tier="powerful",
    )
    base.update(over)
    return OwlAgentManifest(**base)


def _persona(m: OwlAgentManifest) -> str:
    return DNAPromptInjector().inject(m, m.dna, lean=False)


class TestTheModelIsToldItsName:
    def test_a_renamed_owl_carries_its_display_name(self) -> None:
        """The exact live case: secretary renamed to Friday."""
        persona = _persona(_owl(display_name="Friday"))

        assert "Friday" in persona, (
            "the renamed owl's persona does not mention its own name — the rename "
            "never reaches the model"
        )

    def test_the_routing_slug_is_still_available_to_the_model(self) -> None:
        """The model needs both: the name it answers to, and the id it is addressed
        by internally. Dropping the slug would break self-reference in handoffs."""
        persona = _persona(_owl(display_name="Friday"))

        assert "secretary" in persona.lower()

    def test_the_original_persona_text_is_preserved(self) -> None:
        """The identity line ADDS to the persona; it must not replace what the owl
        was configured to be."""
        persona = _persona(_owl(display_name="Friday"))

        assert "the user's primary agent" in persona

    def test_it_survives_dna_modulation(self) -> None:
        """The DNA path returns early when there are no directives and takes a
        different branch when there are. The name must be present either way —
        a fix that only works on one branch is the 'wired on some paths' shape."""
        m = _owl(display_name="Friday")
        # OwlDNA is frozen — mutate through model_copy, not attribute assignment.
        loud = m.dna.model_copy(update={"verbosity": 0.95, "formality": 0.95})

        assert "Friday" in DNAPromptInjector().inject(m, loud, lean=False)

    def test_boundaries_do_not_displace_the_identity(self) -> None:
        persona = _persona(_owl(display_name="Friday", boundaries="Never spend money."))

        assert "Friday" in persona
        assert "Never spend money." in persona


class TestItChangesNothingWhenThereIsNoRename:
    def test_an_owl_with_no_display_name_is_byte_identical(self) -> None:
        """Most owls have never been renamed. They must get exactly the prompt they
        got before — this fix may not perturb every persona in the tree."""
        m = _owl()
        expected = m.system_prompt

        assert _persona(m) == expected

    def test_a_display_name_equal_to_the_slug_adds_nothing(self) -> None:
        """Naming an owl the same as its id is not a rename; saying "your name is
        secretary, your id is secretary" is noise in a cache-frozen prompt."""
        m = _owl(display_name="secretary")

        assert _persona(m) == m.system_prompt

    def test_a_display_name_differing_only_in_case_adds_nothing(self) -> None:
        """"Secretary" vs "secretary" is not a rename either."""
        m = _owl(display_name="Secretary")

        assert _persona(m) == m.system_prompt
