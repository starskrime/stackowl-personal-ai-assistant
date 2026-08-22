"""The secretary is the platform's root administrator.

BAKIR, 2026-08-22: "Secretary should have access to everything. She is root
administrator of platform."

An authority DECISION by the platform's owner, prompted by two measured refusals
that day. She was asked to change `syshealth` and answered:

    "'syshealth' was created by another owl — you may only modify owls you created."

and she was refused `cronjob` and `session_search`, both `denied_by=task`.

WHAT WAS ACTUALLY BLOCKING HER, and the reason this is not a bounds change: her
`bounds` and `creation_ceiling` are BOTH None. She was already unbounded on tools.
The two guards that stopped her never consult bounds at all — `can_modify`'s
ownership rule, and the task envelope. An administrator whose own plan can revoke
her administration is not one.

`can_retire` had ALREADY reasoned this way — "something the human explicitly asking
(via the root/secretary caller) may legitimately want" — and never implemented it in
`can_modify`. One rule, understood in two places and enforced in one.
"""

from __future__ import annotations

from stackowl.owls.tool_presets import ROOT_OWL, is_root_owl
from stackowl.tools.meta.owl_build import can_modify


class _Owl:
    def __init__(self, origin: str = "agent", created_by: str = "someone_else") -> None:
        self.origin = origin
        self.created_by = created_by


class TestRootIsOneDefinition:
    def test_the_secretary_is_root(self) -> None:
        assert is_root_owl(ROOT_OWL) is True

    def test_matching_survives_case_and_whitespace(self) -> None:
        """The name arrives from a manifest, a trace context and an LLM-supplied
        spec. A rule that silently fails on "Secretary" is worse than no rule."""
        for spelling in ("secretary", "Secretary", "  SECRETARY  "):
            assert is_root_owl(spelling) is True

    def test_nobody_else_is_root(self) -> None:
        for name in ("Brain", "syshealth", "mailbutler", "", None, "secretaries"):
            assert is_root_owl(name) is False


class TestRootMayAdministerAnyOwl:
    def test_the_live_case_editing_an_owl_she_did_not_create(self) -> None:
        """THE REFUSAL Bakir hit: `syshealth`, minted by a different owl."""
        assert can_modify(_Owl(created_by="Brain"), caller="secretary",
                          target_name="syshealth") is None

    def test_root_may_modify_a_builtin_or_human_owl(self) -> None:
        """`origin != 'agent'` blocked this too, and root administers all of it."""
        assert can_modify(_Owl(origin="builtin"), caller="secretary",
                          target_name="scout") is None
        assert can_modify(_Owl(origin="human"), caller="secretary",
                          target_name="someone") is None


class TestWhatRootStillCannotDo:
    def test_root_cannot_modify_the_secretary_HERSELF(self) -> None:
        """A registry-level mandatory invariant, NOT an authority limit.

        It protects the platform's own entry point. Being trusted with every other
        owl is not a reason to be able to delete yourself, and the check stays FIRST
        so root never reaches past it.
        """
        refusal = can_modify(_Owl(), caller="secretary", target_name="secretary")

        assert refusal is not None
        assert "cannot be modified" in refusal

    def test_a_NON_root_owl_is_still_held_to_ownership(self) -> None:
        """THE SECURITY LINE. This must be an exemption for root, not a hole.

        If widening root also widened everyone, an agent-minted owl could edit its
        siblings and launder authority through an owl it does not own — which is the
        exact thing `can_modify` exists to prevent.
        """
        refusal = can_modify(_Owl(created_by="Brain"), caller="mailbutler",
                             target_name="syshealth")

        assert refusal is not None
        assert "created by another owl" in refusal

    def test_a_non_root_owl_still_cannot_touch_a_builtin(self) -> None:
        refusal = can_modify(_Owl(origin="builtin"), caller="mailbutler",
                             target_name="scout")

        assert refusal is not None
        assert "cannot be modified by owl_build" in refusal
