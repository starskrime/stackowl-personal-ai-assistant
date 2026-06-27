"""Parliament synthesis data models — DisagreementPoint, SynthesisResult."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DisagreementPoint(BaseModel):
    """A single point of disagreement surfaced by ParliamentSynthesizer.

    ``claim`` is the contested proposition; ``positions`` maps each
    participant's owl name to their stated position on that claim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: str
    positions: dict[str, str]


class SynthesisResult(BaseModel):
    """Structured output of ParliamentSynthesizer.synthesize().

    Fields:

    * ``consensus`` — text describing the agreed-upon outcome.
    * ``disagreements`` — list of unresolved disagreement points.
    * ``recommendation`` — the synthesizer's recommended next action.
    * ``confidence`` — epistemic confidence in [0.0, 1.0]; combines mean
      similarity of round responses with a truncation penalty.
    * ``synthesis_text`` — the full formatted synthesis (roll-call header,
      optional low-confidence warning, body, ``◆`` terminator) suitable for
      direct display to the user.
    * ``mean_similarity`` — mean pairwise cosine similarity across the
      final round responses; 0.0 when no embedder is available.
    * ``parse_ok`` — False when the structural markers could NOT be parsed and
      the result is a graceful fallback (raw text dressed as a verdict). A
      ``parse_ok=False`` result is NOT trustworthy: callers must mark the session
      degraded and must NEVER persist its consensus as durable knowledge
      (F-58/F-59). Defaults True so every genuinely-parsed verdict is trusted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    consensus: str
    disagreements: list[DisagreementPoint]
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    synthesis_text: str
    mean_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    parse_ok: bool = True
