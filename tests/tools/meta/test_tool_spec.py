"""Unit tests for LearnedToolSpec — build_argv whole-token safety + validate_spec.

These cover the SAFETY CORE of H4 tool_build: an argv template is a LIST (never a
shell string), argv[0] is a fixed literal, every placeholder is a WHOLE token bound
to a declared param, and a substituted value lands as ONE argv element so shell
metacharacters in it are inert. validate_spec is structured and NEVER raises.
"""

from __future__ import annotations

import pytest

from stackowl.tools.meta.tool_spec import (
    LearnedToolSpec,
    ToolParam,
    ToolSpecError,
    build_argv,
    validate_spec,
)


def _spec(**over: object) -> LearnedToolSpec:
    base: dict[str, object] = {
        "name": "shout",
        "description": "uppercase a string",
        "params": [ToolParam(name="text", type="string", description="the text", required=True)],
        "argv_template": ["tr", "a-z", "A-Z"],
    }
    base.update(over)
    return LearnedToolSpec.model_validate(base)


# --- build_argv -------------------------------------------------------------


def test_build_argv_substitutes_whole_token() -> None:
    spec = _spec(argv_template=["printf", "%s", "{text}"])
    assert build_argv(spec, {"text": "hello"}) == ["printf", "%s", "hello"]


def test_build_argv_value_is_one_element_metachars_inert() -> None:
    """A value carrying shell metacharacters becomes ONE argv element — inert."""
    spec = _spec(argv_template=["printf", "%s", "{text}"])
    argv = build_argv(spec, {"text": "a; rm -rf / && echo $HOME | cat"})
    assert argv == ["printf", "%s", "a; rm -rf / && echo $HOME | cat"]
    # The dangerous string is a single opaque element, never split into tokens.
    assert len(argv) == 3


def test_build_argv_coerces_typed_values_to_str() -> None:
    spec = _spec(
        params=[
            ToolParam(name="count", type="integer", description="n", required=True),
            ToolParam(name="ratio", type="number", description="r", required=True),
            ToolParam(name="flag", type="boolean", description="f", required=True),
        ],
        argv_template=["echo", "{count}", "{ratio}", "{flag}"],
    )
    assert build_argv(spec, {"count": 7, "ratio": 1.5, "flag": True}) == [
        "echo",
        "7",
        "1.5",
        "True",
    ]


def test_build_argv_missing_required_arg_raises_toolspecerror() -> None:
    spec = _spec(argv_template=["printf", "%s", "{text}"])
    with pytest.raises(ToolSpecError):
        build_argv(spec, {})


def test_build_argv_optional_param_omitted_drops_token() -> None:
    spec = _spec(
        params=[
            ToolParam(name="text", type="string", description="t", required=True),
            ToolParam(name="suffix", type="string", description="s", required=False),
        ],
        argv_template=["printf", "{text}", "{suffix}"],
    )
    # suffix omitted → its token is dropped (not substituted with empty noise).
    assert build_argv(spec, {"text": "hi"}) == ["printf", "hi"]


# --- validate_spec ----------------------------------------------------------


def test_validate_spec_valid_returns_none() -> None:
    assert validate_spec(_spec(argv_template=["printf", "%s", "{text}"])) is None


def test_validate_spec_argv0_placeholder_rejected() -> None:
    err = validate_spec(_spec(argv_template=["{text}", "x"]))
    assert err is not None
    assert "argv[0]" in err or "first" in err.lower()


def test_validate_spec_embedded_placeholder_rejected() -> None:
    # "--x={p}" is NOT a whole token → rejected (must be two tokens).
    err = validate_spec(
        _spec(
            params=[ToolParam(name="p", type="string", description="p", required=True)],
            argv_template=["tool", "--x={p}"],
        )
    )
    assert err is not None
    assert "whole" in err.lower() and "embed" in err.lower()


def test_validate_spec_undeclared_placeholder_rejected() -> None:
    err = validate_spec(_spec(argv_template=["printf", "{nope}"]))
    assert err is not None
    assert "nope" in err


def test_validate_spec_empty_argv_template_rejected() -> None:
    err = validate_spec(_spec(argv_template=[]))
    assert err is not None


def test_validate_spec_bad_name_rejected() -> None:
    # A name violating ^[a-z][a-z0-9_]*$ is rejected.
    err = validate_spec(_spec(name="Bad-Name"))
    assert err is not None


def test_validate_spec_overlong_description_rejected() -> None:
    err = validate_spec(_spec(description="x" * 5000))
    assert err is not None


def test_validate_spec_duplicate_param_rejected() -> None:
    err = validate_spec(
        _spec(
            params=[
                ToolParam(name="text", type="string", description="a", required=True),
                ToolParam(name="text", type="string", description="b", required=True),
            ],
            argv_template=["printf", "{text}"],
        )
    )
    assert err is not None
    assert "duplicate" in err.lower()
