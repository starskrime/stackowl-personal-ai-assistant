"""PB3 — ``job_success_for_rollup`` honest rollup->JobResult.success mapping.

Interim shim (mirrors goal_execution's rollup->retry semantics; superseded by the
PB6a/6b verified/effect_class contract). Pins the full table incl. the ONE
deliberate deviation from goal_execution: an unrecognized rollup is fail-closed
(``False``), not a claimed success.
"""

from __future__ import annotations

import pytest

from stackowl.notifications.proactive_job import job_success_for_rollup


@pytest.mark.parametrize(
    ("rollup", "expected"),
    [
        ("delivered", True),
        ("suppressed", True),
        ("undeliverable", True),
        ("partial", False),
        ("failed", False),
        ("batched", False),  # unrecognized here -> fail-closed, not a claimed win
        ("some_future_rollup", False),  # fail-closed deviation from goal_execution
    ],
)
def test_job_success_for_rollup(rollup: str, expected: bool) -> None:
    assert job_success_for_rollup(rollup) is expected
