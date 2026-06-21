"""/browser sub-command metadata — THE 2-level case.

`profile` and `watch` carry `children`; `resolve_path` walks the recursive tree
the same way it would for any N-level command (no special-casing).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from stackowl.commands.browser_command import BrowserCommand
from stackowl.commands.metadata import resolve_path


def _state():  # type: ignore[no-untyped-def]
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def test_browser_declares_top_level_subcommands() -> None:
    cmd = BrowserCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == {
        "help",
        "settings",
        "sessions",
        "close",
        "fetch-binary",
        "profile",
        "watch",
    }
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Browser"
    for sub in cmd.meta.subcommands:
        assert sub.summary


def test_profile_and_watch_have_children() -> None:
    cmd = BrowserCommand()
    by_name = {s.name: s for s in cmd.meta.subcommands}

    profile = by_name["profile"]
    assert {c.name for c in profile.children} == {"list", "delete"}
    delete = next(c for c in profile.children if c.name == "delete")
    assert delete.args and delete.args[0].name == "name"

    watch = by_name["watch"]
    assert {c.name for c in watch.children} == {"list"}

    # Leaf top-level subs carry no children.
    assert by_name["sessions"].children == ()


def test_resolve_path_walks_two_levels() -> None:
    subs = BrowserCommand().meta.subcommands
    assert resolve_path(subs, ["profile", "list"]) is not None
    assert resolve_path(subs, ["profile", "list"]).name == "list"
    assert resolve_path(subs, ["profile", "delete"]).name == "delete"
    assert resolve_path(subs, ["watch", "list"]).name == "list"
    # Top-level still resolvable.
    assert resolve_path(subs, ["settings"]).name == "settings"
    # A non-existent path returns None.
    assert resolve_path(subs, ["profile", "nope"]) is None


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage_listing_children_markers() -> None:
    fake_svc = MagicMock()
    fake_svc.browser_runtime = None
    fake_svc.browser_sessions = None
    with patch(
        "stackowl.commands.browser_command.get_services", return_value=fake_svc
    ):
        out = await BrowserCommand().handle("bogus", _state())
    assert "Usage: /browser" in out
    assert "profile" in out and "watch" in out
    # render_usage marks parents that have children with ' ›'.
    assert "›" in out
