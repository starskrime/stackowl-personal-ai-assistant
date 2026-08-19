"""An authority clamp is a decision, not a failed edit.

BAKIR, 2026-08-19: "It is continuesly failing." MEASURED on owl_build's own logs —
50 executions, and:

    19  overclaim.detected
    15  [tools] registry.get: prior-failure advisory — tool repeatedly failed
     3  owl_build.verify: requested tools are absent from the live owl
     3  owl_build.verify: claimed but NOT observed in the live registry
     3  tool.__call__: claimed success but verification FAILED

All three verification failures were ``action: edit`` on `mailbutler`, with
``missing: ['send_message', 'shell', 'write_file']``.

THE TOOL WAS RIGHT AND ITS VERIFIER WAS WRONG. Dropping those tools is deliberate:
an unbounded creator is given ``SAFE_DEFAULT_CEILING`` so consequential tools
(shell/exec/write/network) cannot be minted by an agent on its own authority. Both
the create and edit paths already report it honestly —
"Dropped above your authority: shell".

But ``_edit_landed`` compared the live owl against what was REQUESTED rather than
what was GRANTED. So a correct, secure, honestly-reported outcome scored as a failed
edit: verified=False → overclaim.detected → corrective replay → the agent tries the
identical edit again → clamped again → and round it goes. That loop is what Bakir
saw, and the platform could not break out of it because nothing was actually broken.

THE DISTINCTION THIS RESTORES. A requested tool missing because the owl's
``creation_ceiling`` forbids it is a CLAMP — expected, and not a failure. A
requested tool missing that the ceiling PERMITS is a genuine failure and must still
fail, or the verifier stops being worth having.
"""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.tools.meta.owl_build import _edit_landed


class _Owl:
    def __init__(self, tools: list[str], ceiling: list[str] | None) -> None:
        self.tools = tools
        self.creation_ceiling = (
            BoundsSpec(tools=ceiling) if ceiling is not None else None
        )


class TestAToolTheCeilingForbidsIsNotAFailure:
    def test_the_exact_mailbutler_case(self) -> None:
        """shell/write_file/send_message are above the safe default ceiling, so
        their absence is the clamp working — not the edit failing."""
        owl = _Owl(
            tools=["delegate_task", "memory", "read_file"],
            ceiling=["delegate_task", "memory", "read_file", "web_fetch"],
        )

        landed = _edit_landed(
            owl, {"explicit_tools": ["shell", "write_file", "send_message", "memory"]}
        )

        assert landed is not False, "an authority clamp must not read as a failed edit"

    def test_a_clamp_alone_does_not_manufacture_a_verdict(self) -> None:
        """With nothing else checkable requested, a pure clamp leaves no opinion
        rather than inventing a pass."""
        owl = _Owl(tools=["memory"], ceiling=["memory"])

        assert _edit_landed(owl, {"explicit_tools": ["shell"]}) is not False


class TestAGenuineFailureStillFails:
    def test_a_permitted_tool_that_did_not_land_is_a_failure(self) -> None:
        """The verifier has to keep its teeth. `web_fetch` is inside the ceiling, so
        if it is missing the edit really did not take."""
        owl = _Owl(
            tools=["memory"],
            ceiling=["memory", "web_fetch"],
        )

        assert _edit_landed(owl, {"explicit_tools": ["web_fetch"]}) is False

    def test_an_unbounded_owl_holds_every_requested_tool_to_account(self) -> None:
        """No ceiling means nothing was clamped, so every absence is a real miss."""
        owl = _Owl(tools=["memory"], ceiling=None)

        assert _edit_landed(owl, {"explicit_tools": ["shell"]}) is False

    def test_a_changed_field_that_did_not_take_still_fails(self) -> None:
        owl = _Owl(tools=["memory"], ceiling=["memory"])
        owl.boundaries = "old"  # type: ignore[attr-defined]

        assert _edit_landed(owl, {"boundaries": "new"}) is False


class TestTheOrdinaryCasesAreUnchanged:
    def test_a_tool_that_landed_verifies(self) -> None:
        owl = _Owl(tools=["memory", "web_fetch"], ceiling=["memory", "web_fetch"])

        assert _edit_landed(owl, {"explicit_tools": ["web_fetch"]}) is True

    def test_a_missing_owl_is_still_a_failure(self) -> None:
        assert _edit_landed(None, {"explicit_tools": ["memory"]}) is False
