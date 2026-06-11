"""TerminalResponseGuarantee — pure never-empty floor, two entry points (self-heal W2.T8)."""
from stackowl.pipeline.supervisor import synthesize_floor, synthesize_from_calls


def test_synthesize_from_calls_non_empty_and_honest():
    calls = [{"name": "browser_browse", "failed": True, "result": "NS_ERROR_UNKNOWN_HOST"}]
    out = synthesize_from_calls(goal="open example.com", all_calls=calls, partial="")
    assert out and "browser_browse" in out


def test_synthesize_from_calls_picks_first_failed_capability():
    calls = [
        {"name": "shell", "failed": False, "result": "ok"},
        {"name": "browser_browse", "failed": True, "result": "DNS fail"},
    ]
    out = synthesize_from_calls(goal="g", all_calls=calls, partial="")
    assert "browser_browse" in out  # the FAILED tool is named, not the ok one


def test_synthesize_floor_degraded_when_no_calls():
    # Hard-exception path: tool records lost — still non-empty, uses goal+error+partial.
    out = synthesize_floor(goal="open example.com", error="boom", attempts=[], partial="prior text")
    assert out and "boom" in out


def test_synthesize_floor_includes_partial_when_present():
    out = synthesize_floor(goal="g", error="e", attempts=["browser_browse"], partial="HALF DONE")
    assert "HALF DONE" in out


def test_floor_never_raises_on_garbage():
    out = synthesize_floor(goal=None, error=None, attempts=None, partial=None)  # type: ignore[arg-type]
    assert out  # minimal localized non-empty string, no exception


def test_synthesize_from_calls_empty_calls_still_non_empty():
    out = synthesize_from_calls(goal="g", all_calls=[], partial="")
    assert out  # no tool records -> still non-empty honest message
