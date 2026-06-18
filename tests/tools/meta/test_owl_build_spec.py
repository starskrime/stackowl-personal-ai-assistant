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
    with pytest.raises(ValidationError):
        OwlBuildSpec(action="create", name="x", preset="researcher",
                     specialty="z", origin="agent")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        OwlBuildSpec(action="create", name="x", preset="researcher",
                     specialty="z", creation_ceiling={})  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        OwlBuildSpec(action="create", name="x", preset="researcher",
                     specialty="z", bounds={})  # type: ignore[call-arg]


def test_retire_needs_only_name():
    s = OwlBuildSpec(action="retire", name="researcher")
    assert validate_owl_build_spec(s) is None


def test_empty_name_rejected():
    s = OwlBuildSpec(action="retire", name="  ")
    assert validate_owl_build_spec(s) is not None
