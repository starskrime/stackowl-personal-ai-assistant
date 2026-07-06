"""Task 2 — boundaries fold into the rendered system prompt (neutral DNA)."""
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.manifest import OwlAgentManifest


def _owl(**kw: object) -> OwlAgentManifest:
    base = dict(name="x", role="r", system_prompt="base prompt", model_tier="fast")
    base.update(kw)
    return OwlAgentManifest(**base)  # type: ignore[arg-type]


def test_inject_folds_boundaries_when_present() -> None:
    out = DNAPromptInjector().inject(_owl(boundaries="never share raw URLs"), OwlDNA())
    assert "base prompt" in out
    assert "Boundaries: never share raw URLs" in out


def test_inject_without_boundaries_is_byte_identical() -> None:
    # Neutral DNA + no boundaries → the raw system prompt, unchanged.
    assert DNAPromptInjector().inject(_owl(), OwlDNA()) == "base prompt"
