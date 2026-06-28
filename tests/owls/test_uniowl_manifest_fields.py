"""UniOwl S1 — manifest data model: display_name, lifecycle, trigger, slugify."""

from __future__ import annotations

import pytest

from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.exceptions import ManifestValidationError
from stackowl.owls.manifest import OwlAgentManifest, slugify_owl_name
from stackowl.owls.trigger import CronTrigger, ThresholdTrigger


def _base(**kw):
    d = dict(name="tony", role="advisor", system_prompt="p", model_tier="standard")
    d.update(kw)
    return OwlAgentManifest(**d)


def test_defaults_are_backcompat() -> None:
    m = _base()
    assert m.display_name == ""
    assert m.lifecycle == "on_demand"
    assert m.trigger is None
    assert m.display == "tony"  # falls back to name


def test_display_name_allows_spaces_and_case() -> None:
    m = _base(display_name="Tony from Accounting")
    assert m.display == "Tony from Accounting"


def test_display_name_too_long_rejected() -> None:
    with pytest.raises(ManifestValidationError):
        _base(display_name="x" * 49)


def test_scheduled_requires_trigger() -> None:
    with pytest.raises(ManifestValidationError):
        _base(lifecycle="scheduled")  # no trigger


def test_on_demand_must_not_have_trigger() -> None:
    with pytest.raises(ManifestValidationError):
        _base(trigger=CronTrigger(schedule="every 5m", prompt="check"))


def test_scheduled_with_trigger_ok() -> None:
    m = _base(
        lifecycle="scheduled",
        trigger=ThresholdTrigger(source="price_feed", op="gt", threshold=70000.0),
    )
    assert m.trigger.kind == "threshold"
    assert m.trigger.op == "gt"


def test_slugify_human_name() -> None:
    assert slugify_owl_name("Tony") == "Tony"
    assert slugify_owl_name("Tony from Accounting") == "Tony_from_Accoun"  # capped at 16
    with pytest.raises(ManifestValidationError):
        slugify_owl_name("!!!")  # nothing usable


def test_yaml_roundtrip_persists_new_fields() -> None:
    m = _base(
        display_name="Tony A",
        lifecycle="scheduled",
        trigger=CronTrigger(schedule="every 10m", prompt="summarize inbox"),
    )
    entry = manifest_to_yaml_entry(m)
    assert entry["display_name"] == "Tony A"
    assert entry["lifecycle"] == "scheduled"
    assert entry["trigger"]["kind"] == "cron"
    # Reload reconstructs an equivalent manifest (discriminated union resolves).
    reloaded = OwlAgentManifest(**entry)
    assert reloaded.trigger.kind == "cron"
    assert reloaded.display == "Tony A"


def test_yaml_omits_defaults_for_backcompat() -> None:
    entry = manifest_to_yaml_entry(_base())
    assert "display_name" not in entry
    assert "lifecycle" not in entry
    assert "trigger" not in entry
