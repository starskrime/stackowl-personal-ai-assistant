from stackowl.owls.tool_presets import PRESETS, ROUTER_TOOLS


def test_known_presets_present():
    assert set(PRESETS) == {"researcher", "coder", "writer", "analyst"}


def test_researcher_is_least_privilege():
    p = PRESETS["researcher"]
    assert "shell" not in p.tools and "write_file" not in p.tools
    assert "read_file" in p.tools and "web_fetch" in p.tools


def test_coder_has_execution_tools():
    assert {"write_file", "shell"} <= PRESETS["coder"].tools


def test_router_tools_are_discovery_and_the_appeal():
    """WIDENED 2026-08-21, NARROWED 2026-08-23. The equality is kept deliberately.

    `owl_build` + `owls_list` joined because the tool by which an owl asks for
    authority was itself gated by the authority it lacked — mailbutler was refused
    `owl_build` six times in three days, so the request could not even be made.

    `delegate_task` LEFT on 2026-08-23 (ESC-34, Bakir). The bounds gate granted it
    so a blocked owl could route around a limit, and the task envelope then refused
    it as off-plan (8c403494 — "the task envelope is a real boundary"). Two gates
    behaving exactly as designed, with contradictory designs; the call was to keep
    the boundary and drop the vector. This assertion is INVERTED rather than
    deleted, so a silent re-add fails here and whoever does it has to say why.

    NOT RETROACTIVE: ROUTER_TOOLS is applied at BUILD time, so the six owls already
    bounded keep `delegate_task` in their stored manifests.

    The assertion stays an EQUALITY rather than a subset check: this set lands in
    every owl's ceiling, so what is in it is a security decision and adding to it
    should require changing a test on purpose. `test_the_router_grants_no_ability_to_ACT`
    in tests/tools/meta/test_an_owl_can_reach_the_ask.py is the standing guard on
    what may never join.
    """
    assert ROUTER_TOOLS == frozenset(
        {"tool_search", "tool_describe", "owl_build", "owls_list"}
    )
    assert "delegate_task" not in ROUTER_TOOLS


def test_each_preset_declares_specialty_and_capability_profile():
    for p in PRESETS.values():
        assert p.specialty and p.capability_profile


def test_knowledge_roles_use_durable_memory_recall():
    # researcher/analyst recall durable cross-session knowledge via `memory`
    # (hybrid vector+FTS), NOT `session_search` (verbatim current-session turns).
    assert "memory" in PRESETS["researcher"].tools
    assert "memory" in PRESETS["analyst"].tools
    assert "session_search" not in PRESETS["researcher"].tools
