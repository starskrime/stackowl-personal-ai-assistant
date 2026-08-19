"""A refused tool must tell the owl what it CAN do, or it just guesses again.

MEASURED 2026-08-19 across every log the platform has written — bounds refusals,
by owl::

    sysfup      141
    headhunter   73
    Brain        53
    sysdesign    39
    mailbutler   10
    secretary     6
    scout         2
    rca_gatherer  1
                ---
                315

BAKIR: "It is continuesly failing and does not have ability to re heal himself."

The refusal handed back to the model said:

    "The action 'X' is not permitted by this owl's bounds and was not run. This owl
     is restricted to a fixed set of tools; choose one of its permitted tools or
     answer the user directly."

It never says WHICH tools are permitted, and never mentions that ``delegate_task``
is the way to reach a capability this owl does not hold. So the model picks another
tool it also does not have, is refused again, and the loop repeats — 315 times, with
nothing anywhere repairing it. The information needed to recover was in the bounds
object the whole time and simply was not passed on.

THIS IS THE RECOVERY, NOT A NEW ENGINE. No queue, no retry path, no second actuator:
the refusal that already exists becomes actionable. An owl that is told "you have
memory, read_file and delegate_task" can act on the next step instead of guessing.
"""

from __future__ import annotations

from stackowl.authz.bounds_guard import check_effective_bounds
from stackowl.owls.manifest import BoundsSpec


def _bounds(*tools: str) -> BoundsSpec:
    return BoundsSpec(tools=list(tools))


class TestTheRefusalNamesWhatIsAvailable:
    def test_it_lists_the_permitted_tools(self) -> None:
        """The missing half. "Choose one of its permitted tools" is unusable advice
        when the permitted tools are never named."""
        msg = check_effective_bounds(_bounds("memory", "read_file"), "shell")

        assert msg is not None
        assert "memory" in msg
        assert "read_file" in msg

    def test_it_still_names_the_refused_tool(self) -> None:
        msg = check_effective_bounds(_bounds("memory"), "shell")

        assert msg is not None
        assert "shell" in msg


class TestDelegationIsOfferedWhenItIsAvailable:
    def test_delegate_task_is_pointed_at_explicitly(self) -> None:
        """The designed escape hatch for a capability this owl does not hold. It was
        never mentioned, so an owl holding delegate_task still dead-ended."""
        msg = check_effective_bounds(
            _bounds("delegate_task", "memory"), "shell")

        assert msg is not None
        assert "delegate_task" in msg
        assert "delegate" in msg.lower()

    def test_delegation_is_NOT_suggested_when_the_owl_lacks_it(self) -> None:
        """Advising a tool it also cannot call would be one more dead end."""
        msg = check_effective_bounds(_bounds("memory"), "shell")

        assert msg is not None
        assert "delegate_task" not in msg


class TestTheUnrestrictedCaseIsUntouched:
    def test_no_bounds_permits_everything(self) -> None:
        assert check_effective_bounds(None, "shell") is None

    def test_a_permitted_tool_is_not_refused(self) -> None:
        assert check_effective_bounds(_bounds("shell"), "shell") is None


class TestTheWholeListIsOffered:
    def test_every_permitted_tool_is_named_even_when_there_are_many(self) -> None:
        """BAKIR, 2026-08-19: "Agent should have access to all list to choose."

        An earlier version capped the list at 12 with a "(+N more)" tail. That is
        the same defect this message exists to fix: a tool the owl cannot SEE is a
        tool it cannot choose, so it goes back to guessing and is refused again.
        The message is longer for an owl with many tools, and that is the right
        trade against another dead-ended turn."""
        names = [f"tool_{i:02d}" for i in range(40)]

        msg = check_effective_bounds(_bounds(*names), "shell")

        assert msg is not None
        for n in names:
            assert n in msg, f"{n} was hidden from the owl"
        assert "more)" not in msg
