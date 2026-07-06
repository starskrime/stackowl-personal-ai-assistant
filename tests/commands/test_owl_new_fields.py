"""Task 1 — boundaries + evolution_strategy schema round-trip, and the
free-text-create name fix (name optional so elicitation can ask for it)."""
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.tools.meta.owl_build_spec import (
    MissingFields,
    OwlBuildSpec,
    validate_owl_build_spec,
)


def test_manifest_defaults_are_additive() -> None:
    m = OwlAgentManifest(name="x", role="r", system_prompt="p", model_tier="fast")
    assert m.boundaries == ""
    assert m.evolution_strategy == "adaptive"


def test_yaml_entry_round_trips_boundaries_and_strategy() -> None:
    m = OwlAgentManifest(
        name="x", role="r", system_prompt="p", model_tier="fast",
        boundaries="never share raw urls", evolution_strategy="experimental",
    )
    entry = manifest_to_yaml_entry(m)
    assert entry["boundaries"] == "never share raw urls"
    assert entry["evolution_strategy"] == "experimental"
    back = OwlAgentManifest.model_validate(entry)
    assert back.boundaries == "never share raw urls"
    assert back.evolution_strategy == "experimental"


def test_yaml_entry_omits_defaults() -> None:
    m = OwlAgentManifest(name="x", role="r", system_prompt="p", model_tier="fast")
    entry = manifest_to_yaml_entry(m)
    assert "boundaries" not in entry
    assert "evolution_strategy" not in entry


def test_spec_accepts_boundaries_and_strategy() -> None:
    s = OwlBuildSpec(
        action="create", name="x", preset="researcher", specialty="z",
        boundaries="no raw urls", evolution_strategy="conservative",
    )
    assert s.boundaries == "no raw urls"
    assert s.evolution_strategy == "conservative"
    assert validate_owl_build_spec(s) is None


def test_spec_name_optional_lets_freetext_create_elicit_name() -> None:
    # Regression: `name: str` (required) made `/owls create <text>` fail spec
    # construction BEFORE elicitation. Now name defaults to "" so the validator
    # reports it as a recoverable MissingField the tool can ASK for.
    s = OwlBuildSpec(action="create", specialty="a research owl")
    assert s.name == ""
    check = validate_owl_build_spec(s)
    assert isinstance(check, MissingFields)
    assert "name" in check.fields
