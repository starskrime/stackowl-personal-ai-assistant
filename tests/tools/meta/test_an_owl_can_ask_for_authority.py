"""A blocked agent asking for access must not be asked to invent a specialty.

BAKIR, 2026-08-21: *"Today's agent control is very strict and my agents get blocked
on tool usage. I want to give them good and correct access and the ability to ask for
access and update access on themselves."*

MEASURED THE SAME NIGHT, and the agent was already doing the right thing:

    01:25:12  owl_build.execute: entry   {"action": "grant", "name": "mailbutler"}
    01:25:12  owl_build.execute: eliciting missing create fields
                                         {"name": "mailbutler", "missing": ["specialty"]}
    01:28:43  ...identical, second attempt...
    01:32:16  [persistence] judge verdict — "The agent actually attempted the grant
              via owl_build, which failed due to the approval state"

It hit a bounds refusal, reached for the one sanctioned escape hatch — `owl_build
action='grant'`, added by 389e3902 as "the ONLY path that widens a ceiling" — and the
platform asked it for a **specialty**. For an owl that already exists. Twice.

THE CAUSE IS A THREE-PLACE CHANGE MADE IN TWO PLACES. `grant` was added to
`_VALID_ACTIONS` and to the dispatch in `owl_build.execute`, and never to
`validate_owl_build_spec`. That validator branches explicitly on retire/pause/resume,
edit and rename, then FALLS THROUGH to the create requirements — so an unlisted
action inherits create's irreducible set: name, capability, and specialty.

`owl_build.execute` then runs its `MissingFields` elicitation unconditionally, before
the action dispatch, even though the comment directly above it says the recovery is
"recoverable, **create only**". The comment was right; the code did not follow it.

This is the same shape as D16.1's `MemoryProvider` defect — a new member added to two
of three tables that must agree, discovered only when someone tried to use it — and
it is why an agent that behaved exactly as designed still could not widen its own
ceiling.

WHAT A GRANT ACTUALLY NEEDS: the owl's NAME (it exists) and the capability being
requested (`preset` or `explicit_tools`). Not a specialty; not a schedule. The owl
already has a persona — that is precisely why it is running and getting refused.
"""

from __future__ import annotations

from stackowl.tools.meta.owl_build_spec import (
    MissingFields,
    OwlBuildSpec,
    validate_owl_build_spec,
)


def _spec(**over: object) -> OwlBuildSpec:
    base: dict = {"action": "grant", "name": "mailbutler"}
    base.update(over)
    return OwlBuildSpec(**base)  # type: ignore[arg-type]


class TestAGrantIsNotACreate:
    def test_the_live_case_no_longer_asks_for_a_specialty(self) -> None:
        """The exact call from 01:25:12: grant a tool to an owl that exists."""
        result = validate_owl_build_spec(_spec(explicit_tools=["send_message"]))

        assert not isinstance(result, MissingFields), (
            f"a grant was routed into create-field elicitation: "
            f"{getattr(result, 'missing', result)}"
        )
        assert result is None

    def test_a_preset_grant_is_equally_valid(self) -> None:
        """Both capability shapes the tool accepts must work, or the agent has to
        guess which one the platform will take."""
        assert validate_owl_build_spec(_spec(preset="researcher")) is None

    def test_a_grant_never_needs_a_specialty_or_schedule(self) -> None:
        """The owl already exists — it HAS a persona and a cadence. Asking for them
        again is asking the agent to re-create something it is trying to widen."""
        result = validate_owl_build_spec(_spec(explicit_tools=["shell"]))
        missing = getattr(result, "missing", ())
        assert "specialty" not in missing
        assert "schedule" not in missing


class TestAGrantStillHasToMakeSense:
    def test_a_grant_with_no_owl_named_is_refused(self) -> None:
        """Widening 'nothing' is not a request. This must stay a HARD error, not a
        question — there is no useful thing to ask for."""
        result = validate_owl_build_spec(_spec(name=""))
        assert isinstance(result, str)
        assert "name" in result.lower()

    def test_a_grant_with_no_capability_is_refused(self) -> None:
        """A grant that names no capability widens nothing. Refusing plainly beats
        succeeding vacuously — an owl_build that reports success and changes no
        ceiling is the overclaim shape this platform keeps paying for."""
        result = validate_owl_build_spec(_spec())
        assert isinstance(result, str)
        assert "preset" in result.lower() or "tool" in result.lower()

    def test_both_capability_shapes_at_once_is_still_refused(self) -> None:
        """Unchanged from every other action: preset XOR explicit_tools."""
        result = validate_owl_build_spec(
            _spec(preset="researcher", explicit_tools=["shell"])
        )
        assert isinstance(result, str)
        assert "not both" in result.lower()


class TestEveryValidActionIsValidated:
    """THE GUARD FOR THE DEFECT ITSELF, not just this instance of it.

    `grant` fell through to create's requirements because the validator has no branch
    for it and no default. The next action added will do the same unless something
    fails. So: every member of `_VALID_ACTIONS` must be reachable in the validator
    without inheriting create's irreducible set by accident.
    """

    def test_no_action_silently_inherits_create_requirements(self) -> None:
        from stackowl.tools.meta.owl_build import _VALID_ACTIONS

        inherited: list[str] = []
        for action in _VALID_ACTIONS:
            if action == "create":
                continue
            result = validate_owl_build_spec(
                OwlBuildSpec(  # type: ignore[arg-type]
                    action=action, name="existing-owl",
                    display_name="X", explicit_tools=["shell"],
                )
            )
            missing = tuple(getattr(result, "missing", ()) or ())
            if "specialty" in missing:
                inherited.append(action)

        assert not inherited, (
            "these actions fall through to create's requirements and will ask the "
            "caller to invent a specialty for an owl that already exists: "
            f"{inherited}"
        )
