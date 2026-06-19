from stackowl.pipeline.progress_tracker import (
    NO_PROGRESS_THRESHOLD,
    TurnProgressTracker,
    resolve_no_progress_threshold,
)


def test_threshold_default_is_three() -> None:
    assert NO_PROGRESS_THRESHOLD == 3


def test_resolve_threshold_scales_with_window() -> None:
    assert resolve_no_progress_threshold(8192) == 2      # lean → contain faster
    assert resolve_no_progress_threshold(4096) == 2
    assert resolve_no_progress_threshold(16384) == 3     # normal → default
    assert resolve_no_progress_threshold(None) == 3      # unknown → safe default


def test_no_progress_trips_at_threshold_and_bounces() -> None:
    t = TurnProgressTracker(threshold=3)
    assert t.record_no_progress("shell") is False  # 1
    assert t.record_no_progress("shell") is False  # 2
    assert t.record_no_progress("shell") is True   # 3 → opens NOW
    assert t.is_open("shell") is True
    assert t.opened_tools == ("shell",)


def test_success_resets_streak() -> None:
    t = TurnProgressTracker(threshold=3)
    t.record_no_progress("shell")
    t.record_no_progress("shell")
    t.record_progress("shell")                      # reset
    assert t.record_no_progress("shell") is False   # streak now 1
    assert t.is_open("shell") is False


def test_made_progress_flag() -> None:
    t = TurnProgressTracker(threshold=3)
    assert t.made_progress is False
    t.record_no_progress("shell")
    assert t.made_progress is False
    t.record_progress("http")
    assert t.made_progress is True


def test_scoped_per_tool() -> None:
    t = TurnProgressTracker(threshold=3)
    for _ in range(3):
        t.record_no_progress("shell")
    assert t.is_open("shell") is True
    assert t.is_open("http") is False
