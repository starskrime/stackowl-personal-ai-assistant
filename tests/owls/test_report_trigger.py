"""ReportTrigger pins a scheduled owl to a REAL existing handler (morning_brief/
check_in) instead of the generic goal_execution cron path — these two handlers
take no goal/prompt, they self-assemble from deterministic sources."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.trigger import ReportTrigger


def test_report_trigger_accepts_morning_brief() -> None:
    trig = ReportTrigger(report="morning_brief", schedule="daily@08:00")
    assert trig.kind == "report"
    assert trig.report == "morning_brief"


def test_report_trigger_accepts_check_in() -> None:
    trig = ReportTrigger(report="check_in", schedule="daily@18:00")
    assert trig.report == "check_in"


def test_report_trigger_rejects_unknown_report_name() -> None:
    with pytest.raises(ValidationError):
        ReportTrigger(report="goal_execution", schedule="daily@08:00")  # not a report kind


def test_manifest_accepts_report_trigger() -> None:
    m = OwlAgentManifest(
        name="briefowl", role="r", system_prompt="p", model_tier="fast",
        lifecycle="scheduled",
        trigger=ReportTrigger(report="morning_brief", schedule="daily@08:00"),
    )
    assert m.trigger is not None and m.trigger.kind == "report"
