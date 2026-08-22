"""A scheduled owl's edit LANDED and the platform told the operator it failed.

BAKIR, 2026-08-22: "Platform still failing." / "Check logs again."

MEASURED, twenty minutes after the previous owl_build fix went live:

    14:42:16  owl_build.execute: entry  {"action": "edit", "name": "syshealth"}
    14:42:16  [owls] persist_owl: stored          {"name": "syshealth"}
    14:42:16  owl_build.execute: exit   {"success": true, "op": "edit"}
    14:42:21  overclaim.detected        {"failed_capability": "owl_build"}
    14:42:21  [pipeline] persist_turn: floored turn

`syshealth.updated_at` moved to 2026-08-22T14:42:16.830Z — the write REALLY LANDED
— and the operator was handed a "couldn't finish" floor for completed work.

THE CAUSE. `_edit_landed` verifies only the fields in `_EDIT_CHECKED_FIELDS`:
model_tier, boundaries, specialty, evolution_strategy, display_name, explicit_tools.
An edit touching ONLY `lifecycle` or `schedule` therefore checked ZERO fields and
returned None — no opinion — which the overclaim gate DEFAULT-DENIES. `_edit` does
apply both (it reconciles the scheduler projection for "a changed
lifecycle/trigger"), so the tool was writing fields its own verifier could not
read. For a SCHEDULED owl those are the obvious things to edit.

A verifier blind to what its tool writes reports UNKNOWN forever, and unknown is
treated as failure. The honesty machinery was working exactly as designed; it was
being fed a blind spot.
"""

from __future__ import annotations

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.trigger import CronTrigger
from stackowl.tools.meta.owl_build import _edit_landed


def _scheduled(schedule: str = "every 1h", lifecycle: str = "scheduled"):
    """The live `syshealth` shape.

    An `on_demand` owl must carry NO trigger — the manifest enforces it — so the
    fixture honours that rather than building a manifest the platform would reject.
    """
    return OwlAgentManifest(
        name="syshealth", role="health", system_prompt="s", model_tier="fast",
        lifecycle=lifecycle,
        trigger=(
            CronTrigger(schedule=schedule, prompt="check health")
            if lifecycle == "scheduled" else None
        ),
    )


class TestTheLiveCase:
    def test_a_schedule_edit_that_landed_verifies_TRUE(self) -> None:
        """THE DEFECT: this returned None, and None floors the turn."""
        owl = _scheduled(schedule="every 30m")

        assert _edit_landed(owl, {"action": "edit", "name": "syshealth",
                                  "schedule": "every 30m"}) is True

    def test_a_lifecycle_edit_that_landed_verifies_TRUE(self) -> None:
        owl = _scheduled(lifecycle="on_demand")

        assert _edit_landed(owl, {"action": "edit", "name": "syshealth",
                                  "lifecycle": "on_demand"}) is True

    def test_a_schedule_that_did_NOT_take_is_still_a_real_failure(self) -> None:
        """The teeth stay in. Verifying more fields must not mean trusting the call.

        If the stored schedule is not the one requested, that is a genuine failed
        edit and must report False — otherwise this fix would trade a false alarm
        for a silent one, which is strictly worse.
        """
        owl = _scheduled(schedule="every 1h")

        assert _edit_landed(owl, {"action": "edit", "name": "syshealth",
                                  "schedule": "every 30m"}) is False

    def test_a_lifecycle_that_did_NOT_take_is_still_a_real_failure(self) -> None:
        owl = _scheduled(lifecycle="scheduled")

        assert _edit_landed(owl, {"action": "edit", "name": "syshealth",
                                  "lifecycle": "on_demand"}) is False


class TestTheBoundaries:
    def test_an_edit_requesting_nothing_checkable_still_has_no_opinion(self) -> None:
        """`None` remains correct when there is genuinely nothing to observe.

        Returning a free True here would be the "the owl still exists" bug the
        field-level check was written to kill.
        """
        assert _edit_landed(_scheduled(), {"action": "edit", "name": "syshealth"}) is None

    def test_a_missing_owl_is_a_failure_not_an_unknown(self) -> None:
        assert _edit_landed(None, {"action": "edit", "name": "gone",
                                   "schedule": "every 1h"}) is False

    def test_an_unrelated_field_left_alone_does_not_fail_the_edit(self) -> None:
        """Only what the caller ASKED to change is checked — a scheduled owl edited
        for its tier must not fail because its schedule was not mentioned."""
        owl = _scheduled(schedule="every 1h")

        assert _edit_landed(owl, {"action": "edit", "name": "syshealth",
                                  "model_tier": "fast"}) is True
