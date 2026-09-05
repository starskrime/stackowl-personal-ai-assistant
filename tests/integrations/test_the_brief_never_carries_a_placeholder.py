"""D16.6 — a brief section is real content or it is absent. Never a placeholder.

`gmail.py` states the rule itself, in `execute_action`:

    # list_messages — real call when connected + client available, else honest
    # "unavailable" (F024): NEVER fabricate "ok" for an unperformed action.

And twenty lines above, `get_morning_brief_section` did exactly that. Once
`is_connected()` returned True — which is only `self._oauth.exists()`, a check
that a token FILE is present — it returned:

    BriefSection(key="email", title="Email",
                 items=["[Gmail brief section — live fetch requires active connection]"])

A section titled "Email", in the operator's morning brief, whose only content is a
bracketed note that a live fetch would require a connection. No mail is fetched
anywhere in that method.

SAME RULE, ONE METHOD SHORT — the shape this map keeps turning up, here with the
rule written in the same file.

IT IS NOT REACHABLE TODAY, and that is why this is a safe correction rather than
a behaviour change: nothing in `src/` constructs `GmailAdapter`, so the
`IntegrationSectionAssembler` iterates an empty registry. It becomes reachable the
moment an integration plugin registers one — which is precisely the future
`/connect` invites.
"""

from __future__ import annotations

import pytest


class _Oauth:
    def __init__(self, present: bool) -> None:
        self._present = present

    def exists(self) -> bool:
        return self._present


def _adapter(*, connected: bool):  # noqa: ANN202
    from stackowl.integrations.gmail import GmailAdapter

    return GmailAdapter(
        client_id="cid", client_secret="secret",
        oauth_manager=_Oauth(connected),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_a_connected_mailbox_with_nothing_fetched_yields_no_section() -> None:
    """The fix. A token file on disk is not a fetched inbox."""
    section = await _adapter(connected=True).get_morning_brief_section()

    assert section is None, (
        "a section whose only item announces that no fetch happened is a "
        "fabricated 'ok' for an unperformed action — the rule this file states "
        "for execute_action twenty lines below"
    )


@pytest.mark.asyncio
async def test_an_unconnected_mailbox_still_yields_no_section() -> None:
    """The control. The not-connected path already returned None and must keep to it."""
    assert await _adapter(connected=False).get_morning_brief_section() is None


def test_no_string_LITERAL_carries_the_placeholder() -> None:
    """It would otherwise return by being moved rather than removed.

    AST, not a substring scan, and the distinction is load-bearing: the fix's own
    comment QUOTES the old placeholder so a future reader knows what was removed
    and why. A text scan cannot tell that comment from a live string, and failed
    on it — the same assertion-versus-mention problem that has sunk four static
    screens in this repo. Here it is soundly separable, because a Python comment
    is not an AST node and a string constant is.
    """
    import ast
    import pathlib

    tree = ast.parse(
        (
            pathlib.Path(__file__).resolve().parents[2]
            / "src" / "stackowl" / "integrations" / "gmail.py"
        ).read_text(encoding="utf-8")
    )
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]

    offenders = [s for s in literals if "live fetch requires active connection" in s]
    assert not offenders, f"the placeholder is back as a live string: {offenders}"
