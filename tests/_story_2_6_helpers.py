"""Story 2.6 — shared shadow-gate stubs for evolution-promotion tests.

``EvolutionCoordinator`` now routes every DNA promotion through
``ShadowValidator.validate()`` (the gate), which fails CLOSED on cold start
(fewer than ``sample_size`` eligible held-out ``TaskOutcome`` rows — see
``shadow_validator.py``). Pre-2.6 evolution tests only seed conversation
``messages``, never scored ``task_outcomes``, so the real gate would reject
every mutation in those tests as a cold start. These stubs let tests that are
about evolution *mechanics* (mutation math, strategy scaling, concurrency,
retries) opt out of gate mechanics without duplicating a fake class per file.
Tests about the *gate itself* should use the real ``ShadowValidator``
(``tests/owls/test_shadow_validator.py``) or ``AlwaysFailShadowValidator``.
"""

from __future__ import annotations

from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.shadow_validator import ShadowValidationResult, ShadowValidator


class AlwaysPassShadowValidator(ShadowValidator):
    """Gate always passes — no replay/DB/LLM cost. For tests exercising
    evolution mutation mechanics unrelated to gate logic itself (Story 2.6)."""

    def __init__(self) -> None:  # deliberately skips ShadowValidator.__init__ —
        pass                     # validate() is fully overridden, no db/provider needed.

    async def validate(
        self, owl_name: str, manifest: OwlAgentManifest, proposed_dna: OwlDNA,
    ) -> ShadowValidationResult:
        return ShadowValidationResult(
            passed=True, consecutive_non_regressions=3, n_replayed=3, failures=(),
        )


class AlwaysFailShadowValidator(ShadowValidator):
    """Gate always rejects — for Story 2.6's restore-on-failure tests."""

    def __init__(self) -> None:
        pass

    async def validate(
        self, owl_name: str, manifest: OwlAgentManifest, proposed_dna: OwlDNA,
    ) -> ShadowValidationResult:
        return ShadowValidationResult(
            passed=False, consecutive_non_regressions=0, n_replayed=0, failures=(),
        )
