import pytest
from pydantic import ValidationError
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec, validate_owl_build_spec


def test_create_with_preset_is_valid():
    s = OwlBuildSpec(action="create", name="researcher", preset="researcher", specialty="literature review")
    assert validate_owl_build_spec(s) is None


def test_preset_xor_explicit_tools():
    s = OwlBuildSpec(action="create", name="x", preset="researcher",
                     explicit_tools=["read_file"], specialty="z")
    assert validate_owl_build_spec(s) is not None


def test_create_requires_specialty():
    s = OwlBuildSpec(action="create", name="x", preset="researcher")
    assert validate_owl_build_spec(s) is not None


def test_create_requires_preset_or_tools():
    s = OwlBuildSpec(action="create", name="x", specialty="z")
    assert validate_owl_build_spec(s) is not None


def test_no_authority_fields_accepted():
    """extra="ignore" (schema-tolerance): an authority field the agent should
    never control is silently DROPPED, not raised on -- but it is still never
    honored. The security property (authority forced server-side, never from the
    agent-facing envelope) holds either way; only the failure mode changed from
    "reject the whole request" to "ignore the one bad field"."""
    s = OwlBuildSpec(action="create", name="x", preset="researcher",
                      specialty="z", origin="agent")  # type: ignore[call-arg]
    assert "origin" not in s.model_dump()
    s = OwlBuildSpec(action="create", name="x", preset="researcher",
                      specialty="z", creation_ceiling={})  # type: ignore[call-arg]
    assert "creation_ceiling" not in s.model_dump()
    s = OwlBuildSpec(action="create", name="x", preset="researcher",
                      specialty="z", bounds={})  # type: ignore[call-arg]
    assert "bounds" not in s.model_dump()


def test_retire_needs_only_name():
    s = OwlBuildSpec(action="retire", name="researcher")
    assert validate_owl_build_spec(s) is None


def test_empty_name_rejected():
    s = OwlBuildSpec(action="retire", name="  ")
    assert validate_owl_build_spec(s) is not None


# ---------------------------------------------------------------------------
# FR-11/12 Phase B, Item 1 — schema tolerance for weak-model malformed calls.
# ---------------------------------------------------------------------------


def test_extra_fields_dropped_not_rejected():
    """A stray field (e.g. 'prompt'/'priority' bled in from a different tool
    schema) alongside an otherwise-valid create spec is silently dropped -- the
    whole request is not rejected."""
    s = OwlBuildSpec.model_validate({
        "action": "create", "name": "x", "preset": "researcher", "specialty": "z",
        "prompt": "some prompt text", "priority": "medium",
    })
    dumped = s.model_dump()
    assert "prompt" not in dumped
    assert "priority" not in dumped
    assert dumped["name"] == "x"


def test_explicit_tools_json_string_coerced_to_list():
    s = OwlBuildSpec.model_validate({
        "action": "create", "name": "x", "specialty": "z",
        "explicit_tools": '["memory", "owl_build"]',
    })
    assert s.explicit_tools == ["memory", "owl_build"]


def test_explicit_tools_real_list_unchanged():
    """Regression guard: the coercion must not touch the happy path."""
    s = OwlBuildSpec.model_validate({
        "action": "create", "name": "x", "specialty": "z",
        "explicit_tools": ["memory"],
    })
    assert s.explicit_tools == ["memory"]


def test_explicit_tools_non_json_string_still_rejected():
    """The coercion recovers ONE known-bad shape (a JSON-encoded list string) --
    a genuinely bad value (not JSON at all) must still raise the original
    list-type validation error, not be silently swallowed."""
    with pytest.raises(ValidationError):
        OwlBuildSpec.model_validate({
            "action": "create", "name": "x", "specialty": "z",
            "explicit_tools": "not json",
        })


def test_live_trace_payload_1_extra_fields_now_succeeds():
    """Regression fixture: live trace 6f9d6ed79ce7444f84b22f9c7af0d750 payload 1
    (extra 'prompt'/'priority' fields) -- previously raised, now validates."""
    s = OwlBuildSpec.model_validate({
        "action": "create",
        "name": "reminder_owl",
        "schedule": "every 2h",
        "prompt": "Check memory for any \"go ahead\" replies and send the notification.",
        "priority": "medium",
    })
    assert s.action == "create"
    assert s.name == "reminder_owl"


def test_live_trace_payload_2_json_string_explicit_tools_now_succeeds():
    """Regression fixture: live trace 6f9d6ed79ce7444f84b22f9c7af0d750 payload 2
    (explicit_tools sent as a JSON string) -- previously raised, now validates."""
    s = OwlBuildSpec.model_validate({
        "action": "create",
        "name": "reminder_owl",
        "schedule": "every 2h",
        "specialty": "reminds the user about pending approvals",
        "goal": "check memory and notify",
        "explicit_tools": '["memory", "owl_build"]',
    })
    assert s.explicit_tools == ["memory", "owl_build"]


def test_pause_needs_only_name() -> None:
    assert validate_owl_build_spec(OwlBuildSpec(action="pause", name="scout")) is None


def test_resume_needs_only_name() -> None:
    assert validate_owl_build_spec(OwlBuildSpec(action="resume", name="scout")) is None


def test_pause_empty_name_rejected() -> None:
    assert validate_owl_build_spec(OwlBuildSpec(action="pause", name="  ")) is not None
