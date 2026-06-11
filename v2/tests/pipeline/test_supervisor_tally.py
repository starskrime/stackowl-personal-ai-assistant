from stackowl.pipeline.supervisor import tally_tool_outcomes
from stackowl.pipeline.persistence import is_structural_giveup


def test_tally_reads_failed_bool_not_marker():
    calls = [
        {"name": "browser_browse", "result": "host unknown", "failed": True},
        {"name": "shell", "result": "ok output", "failed": False},
    ]
    failures, successes = tally_tool_outcomes(calls)
    assert failures == 1
    assert successes == 1


def test_tally_ignores_marker_in_result_string():
    calls = [{"name": "x", "result": "\x00TOOL_FAILED\x00 leftover", "failed": False}]
    failures, successes = tally_tool_outcomes(calls)
    assert failures == 0 and successes == 1


def test_structural_giveup_true_on_failed_and_trivial_draft():
    assert is_structural_giveup(tool_failures=1, successful_tool_calls=0, draft="...") is True


def test_structural_giveup_false_on_substantive_draft():
    assert is_structural_giveup(tool_failures=1, successful_tool_calls=0,
                                draft="No, the file does not exist.") is False


def test_structural_giveup_false_when_a_tool_succeeded():
    assert is_structural_giveup(tool_failures=1, successful_tool_calls=1, draft="...") is False
