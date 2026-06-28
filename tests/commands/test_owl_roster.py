"""UniOwl S14 — friendly owl roster rendering."""

from __future__ import annotations

from stackowl.commands.owls_helpers import format_owl_roster
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.trigger import ThresholdTrigger


def _owl(**kw) -> OwlAgentManifest:
    d = dict(name="audra", role="watches tax deadlines", system_prompt="p", model_tier="standard")
    d.update(kw)
    return OwlAgentManifest(**d)


def test_empty_roster_is_inviting() -> None:
    out = format_owl_roster([])
    assert "no assistants" in out.lower()


def test_on_demand_owl_is_resting() -> None:
    out = format_owl_roster([_owl(display_name="Audra")])
    assert "Audra" in out
    assert "watches tax deadlines" in out
    assert "💤 resting" in out


def test_scheduled_owl_is_active() -> None:
    out = format_owl_roster([
        _owl(
            name="marco", display_name="Marco", role="watches a price",
            lifecycle="scheduled",
            trigger=ThresholdTrigger(source="https://x", op="gt", threshold=10.0),
        )
    ])
    assert "Marco" in out
    assert "🟢 active" in out


def test_roster_shows_display_name_not_slug() -> None:
    out = format_owl_roster([_owl(name="tony_acct", display_name="Tony from Accounting")])
    assert "Tony from Accounting" in out
    assert "tony_acct" not in out  # system slug never shown


def test_roster_sorted_by_display_casefold() -> None:
    owls = [_owl(name="zeb", display_name="zeb"), _owl(name="amy", display_name="Amy")]
    out = format_owl_roster(owls)
    assert out.index("Amy") < out.index("zeb")
