"""owl_build must verify the EFFECT, not the record it just wrote.

BAKIR, 2026-08-16: the agent "fails or lies by saying updated".

MEASURED. ``_verify_by_action`` read back the same registry row the tool had just
written:

* ``rename``  -> ``current.display_name == wanted``. True the instant the write
  lands, and it stayed true while the model still answered to the OLD name,
  because nothing injected the name into the prompt (fixed separately in
  ``dna_injector``). The record was right and the behaviour was unchanged.
* ``edit``    -> ``current is not None``. That is only "the owl still exists". An
  edit that changed NOTHING AT ALL verified as True.

With ``verified=True`` the overclaim gate is satisfied, so the agent says
"updated" in good faith. That is the whole "lies by saying updated" complaint: the
verification measured the WRITE and called it the EFFECT. It is the programme's
deepest recorded root — success ASSERTED rather than MEASURED — in a new instance.

WHAT VERIFICATION NOW MEANS. For a rename, the effect is that the owl's assembled
PERSONA carries the new name — that is what makes the model answer to it, and it
is exactly what was missing. For an edit, every field the caller actually asked to
change must be observably that value on the live manifest. A field not requested is
not checked; an edit that asked for nothing is not an achievement.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.tools.meta.owl_build import OwlBuildTool

pytestmark = pytest.mark.asyncio


def _owl(**over: object) -> OwlAgentManifest:
    base: dict = dict(
        name="secretary",
        role="primary-assistant",
        system_prompt="You are the Secretary, the user's primary agent.",
        model_tier="powerful",
    )
    base.update(over)
    return OwlAgentManifest(**base)


class _Registry:
    def __init__(self, owl: OwlAgentManifest | None) -> None:
        self._owl = owl

    def get(self, name: str) -> OwlAgentManifest:
        if self._owl is None:
            raise KeyError(name)
        return self._owl


def _with_registry(monkeypatch: pytest.MonkeyPatch, owl: OwlAgentManifest | None) -> None:
    monkeypatch.setattr(
        "stackowl.tools.meta.owl_build.get_services",
        lambda: SimpleNamespace(owl_registry=_Registry(owl)),
    )


class TestRenameIsVerifiedByTheEffect:
    async def test_a_rename_the_model_can_SEE_verifies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_registry(monkeypatch, _owl(display_name="Friday"))

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "rename", "name": "secretary", "display_name": "Friday"}
        )

        assert ok is True

    async def test_a_rename_the_model_CANNOT_see_does_not_verify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The live defect, pinned. The record says Friday but the persona the
        model receives never mentions it, so the owl will keep answering to the old
        name. Claiming success here is precisely the lie Bakir reported.

        A manifest whose display_name is set but whose persona cannot carry it is
        constructed by blanking the system prompt the identity line is prefixed to —
        if the identity line were ever dropped from the persona again, this test
        goes red instead of the user discovering it.
        """
        owl = _owl(display_name="Friday")
        # Simulate the pre-fix world: the persona does not carry the name.
        monkeypatch.setattr(
            "stackowl.tools.meta.owl_build._persona_of",
            lambda m: "You are the Secretary, the user's primary agent.",
        )
        _with_registry(monkeypatch, owl)

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "rename", "name": "secretary", "display_name": "Friday"}
        )

        assert ok is False

    async def test_a_rename_to_a_different_name_does_not_verify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_registry(monkeypatch, _owl(display_name="Mary"))

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "rename", "name": "secretary", "display_name": "Friday"}
        )

        assert ok is False


class TestEditIsVerifiedFieldByField:
    async def test_an_edit_that_landed_verifies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_registry(monkeypatch, _owl(model_tier="fast"))

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "edit", "name": "secretary", "model_tier": "fast"}
        )

        assert ok is True

    async def test_an_edit_that_did_NOT_land_does_not_verify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Before this, `current is not None` made this return True — the owl
        existed, so the edit was 'verified' despite changing nothing."""
        _with_registry(monkeypatch, _owl(model_tier="powerful"))

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "edit", "name": "secretary", "model_tier": "fast"}
        )

        assert ok is False

    async def test_boundaries_are_checked_when_requested(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_registry(monkeypatch, _owl(boundaries="Never spend money."))

        assert await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "edit", "name": "secretary", "boundaries": "Never spend money."}
        ) is True
        assert await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "edit", "name": "secretary", "boundaries": "Spend freely."}
        ) is False

    async def test_a_field_the_caller_did_not_ask_about_is_not_checked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only what was requested is verified — otherwise every edit would fail on
        the fields it deliberately left alone."""
        _with_registry(monkeypatch, _owl(model_tier="powerful", boundaries="x"))

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "edit", "name": "secretary", "model_tier": "powerful"}
        )

        assert ok is True

    async def test_an_edit_requesting_nothing_is_not_an_achievement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An 'edit' with no field to change cannot be confirmed as having changed
        anything. No opinion (None) rather than a free True."""
        _with_registry(monkeypatch, _owl())

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "edit", "name": "secretary"}
        )

        assert ok is None


class TestUnchangedBehaviour:
    async def test_retire_is_still_verified_by_absence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_registry(monkeypatch, None)

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "retire", "name": "secretary"}
        )

        assert ok is True

    async def test_a_retire_that_left_the_owl_present_does_not_verify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_registry(monkeypatch, _owl())

        ok = await OwlBuildTool()._verify_by_action(  # noqa: SLF001
            {"action": "retire", "name": "secretary"}
        )

        assert ok is False
