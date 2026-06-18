from stackowl.owls.tool_presets import PRESETS, ROUTER_TOOLS


def test_known_presets_present():
    assert set(PRESETS) == {"researcher", "coder", "writer", "analyst"}


def test_researcher_is_least_privilege():
    p = PRESETS["researcher"]
    assert "shell" not in p.tools and "write_file" not in p.tools
    assert "read_file" in p.tools and "web_fetch" in p.tools


def test_coder_has_execution_tools():
    assert {"write_file", "shell"} <= PRESETS["coder"].tools


def test_router_tools_are_delegate_and_discovery():
    assert ROUTER_TOOLS == frozenset({"delegate_task", "tool_search", "tool_describe"})


def test_each_preset_declares_specialty_and_capability_profile():
    for p in PRESETS.values():
        assert p.specialty and p.capability_profile


def test_knowledge_roles_use_durable_memory_recall():
    # researcher/analyst recall durable cross-session knowledge via `memory`
    # (hybrid vector+FTS), NOT `session_search` (verbatim current-session turns).
    assert "memory" in PRESETS["researcher"].tools
    assert "memory" in PRESETS["analyst"].tools
    assert "session_search" not in PRESETS["researcher"].tools
