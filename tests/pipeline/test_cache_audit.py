"""D01.2 — naming the CAUSE of a cache invalidation, not just the symptom.

``prompt_hash`` (D01.6) already tells you the prompt CHANGED. It cannot tell you
WHICH part changed, and it says nothing at all about the tools array — which
Anthropic renders at position 0, before the system prompt, so a tools array that
varies per turn invalidates everything downstream of it on every single turn.

The tools audit exists to turn D01.3's premise into a measured fact. D01.3
asserts that the per-turn context budget varies the tools array; that assertion
has never been measured. It is measurable here on EVERY protocol — execute.py
builds the schemas the same way for all of them — which matters because this
deployment runs an OpenAI-protocol gateway and would observe nothing at all if
the audit lived on the Anthropic path.

D05.2 has since fixed both causes of the variance (a request_text-driven
ordering, and a budget that shrank as history grew), so in production this audit
is now that item's acceptance test. The tests below are unaffected: they drive
``audit_tools_stability`` directly and assert it still REPORTS a change when one
is handed to it. A detector that stopped detecting would look identical to a
fixed pipeline from the logs alone.
"""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.cache_audit import (
    audit_prompt_parts,
    audit_tools_stability,
    reset_audit_state,
)


def _schemas(*names: str) -> list[dict[str, Any]]:
    return [{"name": n, "description": f"the {n} tool"} for n in names]


def setup_function() -> None:
    from stackowl.infra.prompt_invalidation import reset_expected_changes

    reset_audit_state()
    reset_expected_changes()


# ---------------------------------------------------------------------------
# Tools-array stability
# ---------------------------------------------------------------------------

def test_a_stable_tools_array_reports_nothing(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_tools_stability(
            "owl:secretary:telegram:dm:1", _schemas("shell", "memory"), session_id="s1"
        )
        audit_tools_stability(
            "owl:secretary:telegram:dm:1", _schemas("shell", "memory"), session_id="s1"
        )
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_a_changed_tools_array_is_reported_once_it_changes(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_tools_stability(
            "owl:secretary:telegram:dm:1", _schemas("shell", "memory"), session_id="s1"
        )
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell"), session_id="s1")
    changed = [r for r in caplog.records if "tools array CHANGED" in r.message]
    assert len(changed) == 1


def test_the_first_turn_of_a_lane_is_not_a_change(caplog: Any) -> None:
    """There is nothing to have changed FROM. Reporting one would make every new
    conversation look like an invalidation."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell"), session_id="s1")
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_two_owls_on_one_lane_do_not_look_like_a_change(caplog: Any) -> None:
    """FOUND IN VALIDATE, on real traffic, and it is DEBT-21's mistake again.

    An incident lane runs the staged RCA: three owls (rca_gatherer, hypothesis,
    verifier) against ONE session_key. Their tool sets and personas differ BY
    DESIGN — that is invariant I6, and the whole reason D01.1's prompt cache is
    keyed (session_key, owl_name).

    Keyed on the lane alone, this audit reported three correct prompts as three
    violations, which is precisely what DEBT-21 says about grouping by session_id:
    "three correct prompts read as three violations, so the item would be judged
    failed for behaving exactly as designed." An audit that cries wolf on every
    multi-owl lane trains its reader to ignore it.
    """
    with caplog.at_level("WARNING"):
        audit_tools_stability(
            "incident-1", _schemas("shell", "memory"), owl="rca_gatherer", session_id="s1"
        )
        audit_tools_stability("incident-1", _schemas("shell"), owl="hypothesis", session_id="s1")
        audit_tools_stability("incident-1", _schemas("memory"), owl="verifier", session_id="s1")
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_one_owl_changing_its_own_tools_is_still_reported(caplog: Any) -> None:
    """The other jaw of the vice: owl-scoping must not silence the real signal."""
    with caplog.at_level("WARNING"):
        audit_tools_stability(
            "incident-1", _schemas("shell", "memory"), owl="rca_gatherer", session_id="s1"
        )
        audit_tools_stability(
            "incident-1", _schemas("shell"), owl="rca_gatherer", session_id="s1"
        )
    changed = [r for r in caplog.records if "tools array CHANGED" in r.message]
    assert len(changed) == 1
    assert changed[0]._fields["owl"] == "rca_gatherer"


def test_two_owls_on_one_lane_do_not_look_like_a_prompt_part_change(caplog: Any) -> None:
    """Same defect on the prompt-part side — persona and the owls block differ
    per owl BY DESIGN, so a lane-keyed audit flags every owl switch."""
    with caplog.at_level("WARNING"):
        audit_prompt_parts("incident-1", {"persona": "A", "owls": "x"}, owl="rca_gatherer")
        audit_prompt_parts("incident-1", {"persona": "B", "owls": "y"}, owl="hypothesis")
    assert [r for r in caplog.records if "prompt part CHANGED" in r.message] == []


def test_two_lanes_do_not_contaminate_each_other(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_tools_stability("lane-a", _schemas("shell"), session_id="sa")
        audit_tools_stability("lane-b", _schemas("memory"), session_id="sb")
        audit_tools_stability("lane-a", _schemas("shell"), session_id="sa")
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_reordering_alone_counts_as_a_change() -> None:
    """Order is part of the cached bytes. A reordered array with identical
    members still invalidates position 0, so it must not be normalised away."""
    audit_tools_stability("lane", _schemas("shell", "memory"), session_id="s1")
    from stackowl.pipeline.cache_audit import tools_digest

    assert tools_digest(_schemas("shell", "memory")) != tools_digest(
        _schemas("memory", "shell")
    )


def test_an_unhashable_schema_never_raises(caplog: Any) -> None:
    """Measurement must never become an outage."""
    class _Unserialisable:
        pass

    with caplog.at_level("ERROR"):
        audit_tools_stability(
            "lane", [{"name": "x", "fn": _Unserialisable()}], session_id="s1"
        )  # must not raise


def test_an_empty_session_key_is_not_tracked(caplog: Any) -> None:
    """Background and utility calls legitimately have no lane. Bucketing them all
    under "" would report a change on every single one."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("", _schemas("shell"), session_id="s1")
        audit_tools_stability("", _schemas("memory"), session_id="s1")
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] != []


def test_the_tracker_is_bounded() -> None:
    """A long-lived process sees unbounded conversations; the map must not grow forever."""
    from stackowl.pipeline.cache_audit import _LANE_CACHE_MAX, _tools_hashes

    for i in range(_LANE_CACHE_MAX + 50):
        audit_tools_stability("lane", _schemas("shell"), session_id=f"s-{i}")
    assert len(_tools_hashes) <= _LANE_CACHE_MAX


# ---------------------------------------------------------------------------
# D05.4 — the audit measures the CONVERSATION, and says what moved
# ---------------------------------------------------------------------------

def test_a_run_with_no_conversation_is_never_reported(caplog: Any) -> None:
    """THE DEFECT THIS ITEM WAS RE-SCOPED AROUND, and it made two root-cause
    analyses wrong before it was noticed.

    A retry-queue run, a self-heal `-fix` turn, goal execution and a delegated
    child all carry `session_id == ""` — they have a lane but no conversation, and
    each builds its own message list from scratch. There was never a previous turn
    of theirs to share a cached prefix with, so "position 0 invalidated this turn"
    is not merely noisy, it is FALSE.

    Measured over six days of production logs before this changed: 122 warnings,
    112 of them exactly this case.
    """
    with caplog.at_level("WARNING"):
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell", "memory"))
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell"))
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("web_fetch"))
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_a_new_conversation_on_one_lane_is_not_a_change(caplog: Any) -> None:
    """A reset mints a new session_id (D01.7 I2) and the new conversation starts
    with no cached prefix. Comparing its first array against the PREVIOUS
    conversation's reports an invalidation of a cache that was already gone.

    9 of the 122 measured warnings were this, and every one named a session_id
    minted seconds earlier."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("lane-1", _schemas("shell", "memory"), session_id="first")
        audit_tools_stability("lane-1", _schemas("web_fetch"), session_id="second")
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_a_change_names_what_entered_and_what_left(caplog: Any) -> None:
    """The one genuine change in six days of logs could not be explained
    afterwards: the presented membership was recorded nowhere, only a count and
    two opaque hashes. A diagnostic that cannot answer its own question is a
    note."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("lane-1", _schemas("shell", "memory"), session_id="s1")
        audit_tools_stability("lane-1", _schemas("shell", "web_fetch"), session_id="s1")
    changed = [r for r in caplog.records if "tools array CHANGED" in r.message]
    assert len(changed) == 1
    assert changed[0]._fields["added"] == ["web_fetch"]
    assert changed[0]._fields["removed"] == ["memory"]
    assert changed[0]._fields["session_id"] == "s1"
    assert changed[0]._fields["session_key"] == "lane-1"


def test_a_reorder_reports_an_empty_delta_rather_than_a_wrong_one(caplog: Any) -> None:
    """Order is part of the cached bytes, so a reorder IS an invalidation — but
    nothing entered or left. Empty on both sides is the honest answer and is the
    only thing that distinguishes a reorder from a membership change."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("lane-1", _schemas("shell", "memory"), session_id="s1")
        audit_tools_stability("lane-1", _schemas("memory", "shell"), session_id="s1")
    changed = [r for r in caplog.records if "tools array CHANGED" in r.message]
    assert len(changed) == 1
    assert changed[0]._fields["added"] == []
    assert changed[0]._fields["removed"] == []


def test_the_delta_reads_both_wire_dialects() -> None:
    """OpenAI nests the name under `function`; Anthropic puts it at the top level.
    A delta that could only read one would silently report every OpenAI-protocol
    array as empty — and this deployment runs an OpenAI-protocol gateway, so that
    is the path it would have been blind on."""
    from stackowl.pipeline.cache_audit import schema_names

    anthropic = [{"name": "shell", "input_schema": {}}]
    openai = [{"type": "function", "function": {"name": "shell", "parameters": {}}}]
    assert schema_names(anthropic) == frozenset({"shell"})
    assert schema_names(openai) == frozenset({"shell"})
    assert schema_names([{"description": "no name at all"}]) == frozenset()


def test_two_conversations_on_one_lane_do_not_contaminate_each_other(caplog: Any) -> None:
    """Two chats interleaved on one lane must not read as one oscillating array."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("lane-1", _schemas("shell"), session_id="a")
        audit_tools_stability("lane-1", _schemas("memory"), session_id="b")
        audit_tools_stability("lane-1", _schemas("shell"), session_id="a")
        audit_tools_stability("lane-1", _schemas("memory"), session_id="b")
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


# ---------------------------------------------------------------------------
# Prompt-part audit — which part moved
# ---------------------------------------------------------------------------

def test_prompt_parts_report_the_part_that_changed(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "a", "skills": "b", "profile": "c"})
        audit_prompt_parts("lane", {"persona": "a", "skills": "CHANGED", "profile": "c"})
    changed = [r for r in caplog.records if "prompt part CHANGED" in r.message]
    assert len(changed) == 1
    assert changed[0]._fields["parts"] == ["skills"]


def test_prompt_parts_report_every_part_that_changed(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "a", "skills": "b"})
        audit_prompt_parts("lane", {"persona": "X", "skills": "Y"})
    changed = [r for r in caplog.records if "prompt part CHANGED" in r.message]
    assert changed[0]._fields["parts"] == ["persona", "skills"]


def test_identical_prompt_parts_report_nothing(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "a", "skills": "b"})
        audit_prompt_parts("lane", {"persona": "a", "skills": "b"})
    assert [r for r in caplog.records if "prompt part CHANGED" in r.message] == []


# ---------------------------------------------------------------------------
# D01.4 — an EXPLAINED change must not warn.
#
# D01.2's audit warns whenever a prompt part moves between cold builds. Once
# D01.4 lets an edit invalidate immediately, every deliberate edit produces
# exactly that — a warning about a change the user just asked for. That is the
# same cry-wolf failure D01.2's validate stage caught (the audit reporting the
# staged RCA's three owls as three violations), arriving from the other
# direction.
#
# So invalidation records WHY, and the audit consumes it: an explained change is
# INFO, an unexplained one stays a WARNING. The audit keeps doing the one job it
# exists for — catching invalidators nobody asked for.
# ---------------------------------------------------------------------------

def test_an_explained_change_does_not_warn(caplog: Any) -> None:
    from stackowl.infra.prompt_invalidation import note_expected_change

    audit_prompt_parts("lane", {"persona": "a"}, owl="scout")
    note_expected_change("scout", cause="owl_edit")
    with caplog.at_level("INFO"):
        audit_prompt_parts("lane", {"persona": "CHANGED"}, owl="scout")

    assert [r for r in caplog.records if "prompt part CHANGED" in r.message] == [], (
        "a change the user asked for must not be reported as an invalidator"
    )
    assert any("as requested" in r.message for r in caplog.records)


def test_an_unexplained_change_still_warns(caplog: Any) -> None:
    """The other jaw: the fix must not silence the signal the audit exists for."""
    audit_prompt_parts("lane", {"persona": "a"}, owl="scout")
    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "CHANGED"}, owl="scout")

    assert [r for r in caplog.records if "prompt part CHANGED" in r.message] != []


def test_the_explanation_is_consumed_once(caplog: Any) -> None:
    """One invalidation explains ONE rebuild. A second, unexplained change after
    it must warn again, or a single edit would blind the audit indefinitely."""
    from stackowl.infra.prompt_invalidation import note_expected_change

    audit_prompt_parts("lane", {"persona": "a"}, owl="scout")
    note_expected_change("scout", cause="owl_edit")
    audit_prompt_parts("lane", {"persona": "b"}, owl="scout")   # explained, quiet

    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "c"}, owl="scout")  # unexplained
    assert [r for r in caplog.records if "prompt part CHANGED" in r.message] != []


def test_an_explanation_for_one_owl_does_not_cover_another(caplog: Any) -> None:
    from stackowl.infra.prompt_invalidation import note_expected_change

    audit_prompt_parts("lane", {"persona": "a"}, owl="scout")
    audit_prompt_parts("lane", {"persona": "a"}, owl="researcher")
    note_expected_change("scout", cause="owl_edit")

    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "CHANGED"}, owl="researcher")
    assert [r for r in caplog.records if "prompt part CHANGED" in r.message] != []
