"""Property / liveness test for TurnProgressTracker.

The invariant: for any sequence of ops (progress|no_progress, tool_name), a tool
becomes is_open at EXACTLY the threshold-th CONSECUTIVE no_progress op with no
intervening progress; and never before. A progress op resets the consecutive streak.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.progress_tracker import TurnProgressTracker


def _reference_open_set(
    ops: list[tuple[str, str]],
    threshold: int,
) -> set[str]:
    """Reference implementation: compute which tools should be open after all ops.

    A tool opens when its CONSECUTIVE no_progress streak reaches threshold.
    A progress op resets the streak to 0 for that tool.
    Once open, a tool stays open (no un-open operation exists).
    """
    streak: dict[str, int] = {}
    open_set: set[str] = set()
    for kind, tool in ops:
        if kind == "progress":
            streak[tool] = 0
        else:  # no_progress
            streak[tool] = streak.get(tool, 0) + 1
            if streak[tool] >= threshold:
                open_set.add(tool)
    return open_set


def _run_tracker(ops: list[tuple[str, str]], threshold: int) -> set[str]:
    t = TurnProgressTracker(threshold=threshold)
    for kind, tool in ops:
        if kind == "progress":
            t.record_progress(tool)
        else:
            t.record_no_progress(tool)
    return set(t.opened_tools)


# ---------------------------------------------------------------------------
# Hand-built deterministic cases
# ---------------------------------------------------------------------------

_HAND_BUILT: list[tuple[str, list[tuple[str, str]], set[str], int]] = [
    # (test_id, ops, expected_open_set, threshold)
    (
        "empty",
        [],
        set(),
        3,
    ),
    (
        "one_no_progress_not_open",
        [("no_progress", "shell")],
        set(),
        3,
    ),
    (
        "two_no_progress_not_open",
        [("no_progress", "shell"), ("no_progress", "shell")],
        set(),
        3,
    ),
    (
        "three_no_progress_opens",
        [("no_progress", "shell"), ("no_progress", "shell"), ("no_progress", "shell")],
        {"shell"},
        3,
    ),
    (
        "four_no_progress_still_open",
        [("no_progress", "s"), ("no_progress", "s"), ("no_progress", "s"), ("no_progress", "s")],
        {"s"},
        3,
    ),
    (
        "progress_resets_two_then_two_not_open",
        [
            ("no_progress", "t"), ("no_progress", "t"),
            ("progress", "t"),
            ("no_progress", "t"), ("no_progress", "t"),
        ],
        set(),
        3,
    ),
    (
        "progress_resets_two_then_three_opens",
        [
            ("no_progress", "t"), ("no_progress", "t"),
            ("progress", "t"),
            ("no_progress", "t"), ("no_progress", "t"), ("no_progress", "t"),
        ],
        {"t"},
        3,
    ),
    (
        "two_tools_only_a_reaches_threshold",
        [
            ("no_progress", "a"), ("no_progress", "a"), ("no_progress", "a"),
            ("no_progress", "b"), ("no_progress", "b"),
        ],
        {"a"},
        3,
    ),
    (
        "two_tools_both_reach_threshold",
        [
            ("no_progress", "a"), ("no_progress", "a"), ("no_progress", "a"),
            ("no_progress", "b"), ("no_progress", "b"), ("no_progress", "b"),
        ],
        {"a", "b"},
        3,
    ),
    (
        "interleaved_a_opens_b_not",
        # a: 3 total no_progress (consec: 1,2,3 → open); b: 2 no_progress (non-consecutive: 1,2 → not open)
        [
            ("no_progress", "a"),
            ("no_progress", "b"),
            ("no_progress", "a"),
            ("no_progress", "b"),
            ("no_progress", "a"),
        ],
        {"a"},
        3,
    ),
    (
        "progress_then_threshold_opens",
        [
            ("progress", "t"),
            ("no_progress", "t"), ("no_progress", "t"), ("no_progress", "t"),
        ],
        {"t"},
        3,
    ),
    (
        "threshold_2_two_no_progress_opens",
        [("no_progress", "x"), ("no_progress", "x")],
        {"x"},
        2,
    ),
    (
        "threshold_2_one_no_progress_not_open",
        [("no_progress", "x")],
        set(),
        2,
    ),
    (
        "threshold_2_progress_resets",
        [
            ("no_progress", "x"),
            ("progress", "x"),
            ("no_progress", "x"),
        ],
        set(),
        2,
    ),
    (
        "open_stays_open_after_progress",
        # once open, a progress op doesn't un-open the tool
        [
            ("no_progress", "t"), ("no_progress", "t"), ("no_progress", "t"),
            ("progress", "t"),  # resets streak but tool stays in opened_tools
        ],
        {"t"},
        3,
    ),
    (
        "multiple_tools_interleaved_mix",
        [
            ("no_progress", "alpha"), ("no_progress", "beta"),
            ("no_progress", "alpha"), ("progress", "beta"),
            ("no_progress", "alpha"), ("no_progress", "beta"),
            ("no_progress", "beta"),
        ],
        {"alpha"},
        3,
    ),
    (
        "exactly_at_threshold_multiple_tools",
        [
            ("no_progress", "x"), ("no_progress", "x"), ("no_progress", "x"),
            ("no_progress", "y"), ("no_progress", "y"), ("no_progress", "y"),
            ("no_progress", "z"), ("no_progress", "z"),  # z stays at 2
        ],
        {"x", "y"},
        3,
    ),
    (
        "progress_between_two_failures_prevents_open",
        [
            ("no_progress", "t"), ("no_progress", "t"),
            ("progress", "t"),
            ("no_progress", "t"),
        ],
        set(),
        3,
    ),
    (
        "many_progress_ops_do_not_open",
        [("progress", "t")] * 10,
        set(),
        3,
    ),
    (
        "threshold_1_single_no_progress_opens",
        [("no_progress", "fast")],
        {"fast"},
        1,
    ),
    (
        "threshold_1_progress_does_not_close",
        [("no_progress", "fast"), ("progress", "fast")],
        {"fast"},
        1,
    ),
]


def _make_random_case(
    seed: int,
    n_ops: int = 15,
    n_tools: int = 3,
    threshold: int = 3,
) -> tuple[str, list[tuple[str, str]], set[str], int]:
    """Generate a deterministic pseudo-random case from a fixed seed."""
    import random
    rng = random.Random(seed)
    tools = [f"tool_{i}" for i in range(n_tools)]
    ops: list[tuple[str, str]] = [
        (rng.choice(["progress", "no_progress"]), rng.choice(tools))
        for _ in range(n_ops)
    ]
    expected = _reference_open_set(ops, threshold)
    return (f"seed_{seed}", ops, expected, threshold)


_GENERATED = [_make_random_case(s) for s in [1, 2, 3, 4, 5, 6]]

_ALL_CASES = _HAND_BUILT + _GENERATED


@pytest.mark.parametrize(
    "test_id,ops,expected_open,threshold",
    [(c[0], c[1], c[2], c[3]) for c in _ALL_CASES],
    ids=[c[0] for c in _ALL_CASES],
)
def test_tracker_liveness_property(
    test_id: str,
    ops: list[tuple[str, str]],
    expected_open: set[str],
    threshold: int,
) -> None:
    """TurnProgressTracker.opened_tools must exactly match the reference model."""
    actual = _run_tracker(ops, threshold)
    assert actual == expected_open, (
        f"[{test_id}] expected open={expected_open!r}, got {actual!r}"
    )
