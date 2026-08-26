"""A test double may not invent a parameter name the real tool does not have.

THIS EXACT DRIFT HAS NOW COST THREE SEPARATE BUGS, all from one rename
(``session_id`` -> ``conversation_id``) applied to places it was never meant to
reach:

  * ``_StubOrchestrator.run`` in tests/test_story_5_4.py took ``conversation_id``
    while ParliamentOrchestrator.run takes ``session_id``. Five tests red.
  * The browser smoke doubles passed ``{"conversation_id": ...}`` to
    ``browser_snapshot`` / ``browser_click`` / ``browser_dialog``, all of which
    declare ``session_id``. The dispatcher answered "TOOL_FAILED ... missing
    required parameter(s): session_id", so the provider never received a
    ``[ref=eN]``, never clicked, and the assertion that failed was the CLICK one
    — a full layer downstream of the actual break.

WHAT MADE IT SURVIVE A PREVIOUS FIX was prose. Each double carried a comment
asserting which name the real tool wanted, and each comment was wrong:

    "browser_dialog's schema requires ['conversation_id', 'action', 'dialog_id']"

It requires ``session_id``. A comment cannot be executed, so it was believed, and
the fix went in the wrong direction. This file replaces the comments with a
question put to the registry itself — the codebase's own rule that a fixture
should be generated from the same source the code uses, rather than restating it.

Deliberately asserts only the REQUIRED names. Optional parameters vary by tool
and pinning them would make this fail on every harmless schema addition, which is
how a guard gets deleted instead of fixed.
"""

from __future__ import annotations

import pytest

from stackowl.tools.registry import ToolRegistry

#: Every browser tool the smokes drive, and the argument dicts they build.
_BROWSER_TOOLS = (
    "browser_snapshot",
    "browser_click",
    "browser_dialog",
    "browser_navigate",
)


def _required(name: str) -> set[str]:
    tool = ToolRegistry.with_defaults().get(name)
    params = getattr(tool, "parameters", None) or {}
    return set(params.get("required") or ())


@pytest.mark.parametrize("tool_name", _BROWSER_TOOLS)
def test_no_browser_tool_takes_conversation_id(tool_name: str) -> None:
    """The rename never reached the browser tools, so no double may pretend it did.

    ``conversation_id`` is a real name elsewhere in this codebase — that is
    exactly why it drifted in quietly here.
    """
    tool = ToolRegistry.with_defaults().get(tool_name)
    params = getattr(tool, "parameters", None) or {}
    props = set((params.get("properties") or {}).keys())

    assert "conversation_id" not in props, (
        f"{tool_name} now accepts conversation_id — if that is deliberate, the "
        f"smoke doubles and this guard must move together"
    )
    assert "session_id" in props, (
        f"{tool_name} no longer accepts session_id; every browser double passes it"
    )


def test_the_session_scoped_browser_tools_require_session_id() -> None:
    """browser_navigate is excluded: it OPENS the session, so it requires only a
    url and returns the id the others then pass back."""
    for name in ("browser_snapshot", "browser_click", "browser_dialog"):
        assert "session_id" in _required(name), (
            f"{name} stopped requiring session_id — the smoke doubles build their "
            f"argument dict around it"
        )


def test_browser_dialog_requires_what_the_smoke_double_sends() -> None:
    """The specific claim the s6 comment got wrong, now executable."""
    assert _required("browser_dialog") == {"session_id", "action", "dialog_id"}
