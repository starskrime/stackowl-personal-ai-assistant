"""An owl must be able to REACH the tool that asks for authority.

BAKIR, 2026-08-21: *"Today owl does not have freedom to do the job and I want to
provide freedom to them if request come from official channels."* And, on what
official means: *"if it come from channel which is connected to gateway."*

MEASURED THE SAME NIGHT, scoped to the last three days (the all-time count misleads —
most of it is a month old and already fixed):

    83 bounds refusals, ~62 of them mailbutler
      29  mailbutler  shell
       7  mailbutler  owls_list
       6  mailbutler  owl_build        <-- THIS ONE
       6  mailbutler  browser_navigate
       6  mailbutler  execute_code
       4  mailbutler  tool_build

    mailbutler bounds.tools = delegate_task, memory, read_file, tool_describe,
                              tool_search, web_fetch, web_search

`owl_build` is the ONLY sanctioned path that widens a ceiling (389e3902). It is not
in mailbutler's ceiling. So the tool by which an owl asks for authority was itself
gated by the authority it lacked — the request could not even be made, six times.

THE CIRCULARITY IS THE BUG. Not the narrowness of the ceiling: a narrow ceiling is a
legitimate choice, and this does not widen one. What is never legitimate is a ceiling
that cannot be APPEALED, because then the operator's answer is unreachable rather
than merely unsought.

WHY ADDING IT GRANTS NOTHING. `owl_build action='grant'` is gated on the
`authority_widening` consent category, which is always-ask and stays always-ask.
Putting the tool in ROUTER_TOOLS lets an owl RAISE the question; it does not answer
it. That is exactly the authority-vs-action split this platform already paid to
learn: consent gates whether to ACT, and nothing gated whether an owl may BE
something — until grant existed, and it was out of reach.

`owls_list` rides along for the same reason at lower stakes: an owl asked to route or
delegate cannot name a target it is forbidden to enumerate, and it was refused 11
times across two owls in the same window.
"""

from __future__ import annotations

from stackowl.owls.tool_presets import ROUTER_TOOLS


class TestTheAppealIsReachable:
    def test_owl_build_is_in_every_owls_ceiling(self) -> None:
        """The measured case: six refusals of the one tool that can raise the
        question."""
        assert "owl_build" in ROUTER_TOOLS, (
            "an owl cannot ask for authority it does not have — the asking tool is "
            "gated by the very ceiling it exists to appeal"
        )

    def test_owls_list_is_reachable(self) -> None:
        """Refused 11 times across mailbutler and Brain in the same window. An owl
        told to delegate cannot name a target it may not enumerate."""
        assert "owls_list" in ROUTER_TOOLS

    def test_the_router_never_strands_a_narrow_owl(self) -> None:
        """The half of the router's original job that SURVIVES.

        It had two: never strand an owl whose allowlist is too narrow to find a
        tool (discovery), and hand off out-of-scope work (delegate_task). ESC-34
        removed the second on 2026-08-23 — the bounds gate granted delegate_task so
        a blocked owl could route around a limit, and the task envelope then
        refused it as off-plan (8c403494). Two gates behaving as designed, with
        contradictory designs; Bakir kept the boundary and dropped the vector.

        Discovery is untouched, which is what keeps a narrow owl from being
        stranded — and the APPEAL replaces the escape hatch, which the two tests
        above pin.
        """
        assert {"tool_search", "tool_describe"} <= ROUTER_TOOLS
        assert "delegate_task" not in ROUTER_TOOLS, (
            "removed by ESC-34; a silent re-add must fail here"
        )

    def test_the_router_grants_no_ability_to_ACT(self) -> None:
        """The line this must not cross. Reaching the ask is not being granted the
        answer: nothing that changes the world belongs in a set every owl gets."""
        forbidden = {
            "shell", "execute_code", "write_file", "process", "claude_code",
            "browser_navigate", "send_message", "computer_use",
        }
        assert not (ROUTER_TOOLS & forbidden), (
            f"the router must not confer the power to act: {ROUTER_TOOLS & forbidden}"
        )


class TestTheSafeCeilingStillMakesSense:
    def test_the_safe_default_inherits_the_appeal(self) -> None:
        """SAFE_DEFAULT_CEILING is built as its own read-only set UNION ROUTER_TOOLS,
        so an owl minted under the conservative clamp can appeal it too. Asserted
        rather than assumed — the union is what carries the property."""
        from stackowl.tools.meta.owl_build_authz import SAFE_DEFAULT_CEILING

        tools = SAFE_DEFAULT_CEILING.tools or frozenset()
        assert "owl_build" in tools
        assert {"read_file", "web_search", "web_fetch", "memory"} <= tools
