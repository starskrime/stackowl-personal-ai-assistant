"""ESC-34 — a refusal must name a remedy the owl can actually reach.

BAKIR'S DECISION 2026-08-23: drop `delegate_task` from ROUTER_TOOLS and leave the
task envelope alone. It is the only option that contradicts no recorded decision
(8c403494 — "the task envelope is a real boundary"), it keeps the escalation
vector shut, and `owl_build` already IS the appeal path.

MEASURING BEFORE BUILDING CHANGED THE SHAPE OF THE FIX, twice:

1. THE REFUSAL'S ONLY NAMED REMEDY WAS `delegate_task`. `bounds_guard.py` says
   "To use 'X' anyway, delegate_task to an owl that holds it" and nothing else.
   Removing delegate_task WITHOUT changing that message would have left "answer
   the user directly" as the sole option — strictly reducing the recovery surface
   while intending to improve it.

2. THREE OF SIX BOUNDED OWLS HAVE NO `owl_build` IN THEIR BOUNDS AT ALL —
   headhunter, english_tutor, mailbutler (measured from the live `owls` table,
   2026-08-23). They predate owl_build joining ROUTER_TOOLS on 2026-08-21, and
   ROUTER_TOOLS is applied at BUILD time, so the change is not retroactive. For
   those owls, naming owl_build would name a tool they are also refused — which is
   precisely the defect this message was written to fix: "the model picked another
   tool it also lacked and was refused again."

So the rule is: name what this owl HOLDS, never what the platform wishes it held.
"""

from __future__ import annotations

from stackowl.authz.bounds_guard import check_effective_bounds
from stackowl.owls.tool_presets import ROUTER_TOOLS


class _Bounds:
    def __init__(self, tools: set[str]) -> None:
        self.tools = frozenset(tools)

    def permits_tool(self, name: str) -> bool:
        return name in self.tools


# ---------------------------------------------------------------------------
# Bakir's decision
# ---------------------------------------------------------------------------

def test_delegate_task_is_no_longer_a_router_tool() -> None:
    assert "delegate_task" not in ROUTER_TOOLS


def test_the_appeal_path_and_the_discovery_tools_remain() -> None:
    """Removing the escalation vector must not strand a narrow owl. owl_build is
    the appeal; tool_search/tool_describe stop an over-narrow allowlist denying
    discovery itself."""
    for name in ("owl_build", "owls_list", "tool_search", "tool_describe"):
        assert name in ROUTER_TOOLS, name


# ---------------------------------------------------------------------------
# The refusal must name a reachable remedy
# ---------------------------------------------------------------------------

def test_it_names_owl_build_when_the_owl_holds_it() -> None:
    msg = check_effective_bounds(_Bounds({"web_search", "owl_build"}), "send_message")
    assert msg is not None
    assert "owl_build" in msg


def test_it_does_NOT_name_owl_build_when_the_owl_lacks_it() -> None:
    """headhunter / english_tutor / mailbutler, measured live. Naming a tool they
    are also refused is the defect, not the fix."""
    msg = check_effective_bounds(_Bounds({"web_search", "web_fetch"}), "send_message")
    assert msg is not None
    assert "owl_build" not in msg


def test_it_still_names_delegate_task_for_a_LEGACY_owl_that_holds_it() -> None:
    """ROUTER_TOOLS applies at BUILD time, so the six already-bounded owls keep
    delegate_task in their stored manifests. The message must keep working for
    them — the removal is not retroactive and pretending otherwise would strand
    every owl built before today."""
    msg = check_effective_bounds(
        _Bounds({"web_search", "delegate_task"}), "send_message"
    )
    assert msg is not None
    assert "delegate_task" in msg


def test_with_neither_remedy_it_says_so_plainly() -> None:
    """The honest floor. An owl holding neither appeal must not be handed a tool
    name to guess at — it should be told to answer with what it has."""
    msg = check_effective_bounds(_Bounds({"web_search"}), "send_message")
    assert msg is not None
    assert "owl_build" not in msg
    assert "delegate_task" not in msg
    assert "answer the user" in msg.lower()


# ---------------------------------------------------------------------------
# Unchanged guarantees
# ---------------------------------------------------------------------------

def test_the_full_permitted_list_is_still_given() -> None:
    """BAKIR 2026-08-19: "Agent should have access to all list to choose." An
    abbreviated list is the same defect the message exists to fix."""
    tools = {f"tool_{i:02d}" for i in range(25)}
    msg = check_effective_bounds(_Bounds(tools), "nope")
    assert msg is not None
    for name in tools:
        assert name in msg, name


def test_an_unbounded_owl_is_never_refused() -> None:
    assert check_effective_bounds(None, "anything") is None


def test_a_permitted_tool_is_never_refused() -> None:
    assert check_effective_bounds(_Bounds({"web_search"}), "web_search") is None
