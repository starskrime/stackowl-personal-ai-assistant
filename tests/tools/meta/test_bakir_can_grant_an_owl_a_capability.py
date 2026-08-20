"""There must be a way for the user to say yes to a capability.

BAKIR, 2026-08-19: "Why I cannot simple create the agent? Why agent has a limitation
on tool or something?... I'm giving my permission to do that. Agent still failing.
I'm giving everything agent still failing. What's our core issue?"

IT WAS STRUCTURAL, AND HE WAS EXACTLY RIGHT. Consent gated ACTIONS — "may I run this
tool now?" — and he could answer that. NOTHING gated AUTHORITY. An owl's tools are
clamped at mint against ``SAFE_DEFAULT_CEILING``, and ``_edit`` deliberately
re-clamps against the ORIGINAL ceiling ("an edit can never widen authority past what
was approved at mint time"). So ``shell``, ``write_file`` and ``send_message`` were
unreachable for an agent-created owl by EVERY route, permanently — and no permission
he could give would change it, because there was no question the platform knew how
to ask him.

MEASURED THAT DAY on mailbutler: three edits requesting those tools, each returning
``missing: ['send_message', 'shell', 'write_file']``, each then scored as a failed
edit and retried. The owl he needed could never have existed.

``action='grant'`` is that missing question. It is a separate action, not a flag on
``edit``, because the monotone ratchet is a real safety property: widening must be
an explicit, auditable request rather than a side effect of an ordinary edit.

AND IT CAN NEVER BE TAKEN WITHOUT HIM. It is gated on ``authority_widening``, which
is always-ask — so the reversible auto-allow cannot take it, and (since the same
change) neither can the autonomous grant when nobody is attached. If Bakir cannot be
reached, the answer is no.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class TestTheActionExistsAndIsRingfenced:
    async def test_grant_is_a_valid_action(self) -> None:
        from stackowl.tools.meta.owl_build import _VALID_ACTIONS

        assert "grant" in _VALID_ACTIONS

    async def test_the_spec_accepts_it(self) -> None:
        """A Literal that omitted 'grant' would reject the call before dispatch."""
        from stackowl.tools.meta.owl_build_spec import OwlBuildSpec

        spec = OwlBuildSpec(action="grant", name="mailbutler",
                            explicit_tools=["shell"])

        assert spec.action == "grant"
        assert spec.explicit_tools == ["shell"]

    async def test_it_uses_the_always_ask_widening_category(self) -> None:
        from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_CATEGORIES
        from stackowl.tools.meta.owl_build import _WIDENING_CATEGORY

        assert _WIDENING_CATEGORY in _DEFAULT_ALWAYS_ASK_CATEGORIES

    async def test_the_schema_tells_the_model_grant_exists(self) -> None:
        """A capability the model is never told about is one Bakir can never get,
        no matter that the code path exists."""
        from stackowl.tools.meta.owl_build import OwlBuildTool

        desc = str(
            OwlBuildTool().parameters["properties"]["action"]["description"]
        ).lower()

        assert "grant" in desc
        assert "widen" in desc or "authority" in desc


class TestItAsksBeforeItWidens:
    async def test_the_handler_requests_the_widening_category(self) -> None:
        import inspect

        from stackowl.tools.meta.owl_build import OwlBuildTool

        src = inspect.getsource(OwlBuildTool._grant)

        assert "_WIDENING_CATEGORY" in src
        assert "_consent_or_refuse" in src
        # The refusal must come BEFORE anything is persisted.
        assert src.index("_consent_or_refuse") < src.index("persist_owl")

    async def test_it_widens_the_CEILING_not_only_the_bounds(self) -> None:
        """Widening bounds alone would be undone by the next edit, which re-clamps
        against the ceiling. The ratchet point itself has to move."""
        import inspect

        from stackowl.tools.meta.owl_build import OwlBuildTool

        src = inspect.getsource(OwlBuildTool._grant)

        assert "creation_ceiling" in src


class TestTheUserIsNoLongerBlamedForRefusalsTheyNeverSaw:
    async def test_the_refusal_text_does_not_claim_the_user_declined(self) -> None:
        """On 2026-08-19 "declined by user — owl 'mailbutler' was not built" was
        returned five times for prompts Bakir was never shown: the Telegram prompter
        could not parse the session key and failed closed. A refusal the user never
        saw must not be reported as their decision."""
        import inspect

        from stackowl.tools.meta.owl_build import OwlBuildTool

        src = inspect.getsource(OwlBuildTool._consent_or_refuse)

        assert "declined by user" not in src
        assert "not approved" in src
