"""OwlDNA — per-owl personality traits with clamped mutation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.exceptions import ManifestValidationError
from stackowl.infra.observability import log

_MUTABLE_TRAITS: tuple[str, ...] = (
    "challenge_level",
    "verbosity",
    "curiosity",
    "formality",
    "creativity",
    "precision",
)


class OwlDNA(BaseModel):
    """Personality traits for an owl. All values in ``[0.0, 1.0]``.

    Instances are immutable; mutation returns a fresh copy via :meth:`mutate`
    with the requested trait clamped to the allowed range. ``dominant_traits``
    surfaces the most distinctive characteristics by deviation from neutral.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    challenge_level: float = Field(default=0.5, ge=0.0, le=1.0)
    verbosity: float = Field(default=0.5, ge=0.0, le=1.0)
    curiosity: float = Field(default=0.5, ge=0.0, le=1.0)
    formality: float = Field(default=0.5, ge=0.0, le=1.0)
    creativity: float = Field(default=0.5, ge=0.0, le=1.0)
    precision: float = Field(default=0.5, ge=0.0, le=1.0)
    decay_rate_per_week: float = Field(default=0.05, ge=0.0, le=1.0)

    def mutate(self, trait: str, delta: float) -> OwlDNA:
        """Return a new ``OwlDNA`` with ``trait`` shifted by ``delta`` (clamped to ``[0, 1]``)."""
        log.engine.debug(
            "[owls] dna.mutate: entry",
            extra={"_fields": {"trait": trait, "delta": delta}},
        )
        if trait not in _MUTABLE_TRAITS:
            log.engine.warning(
                "[owls] dna.mutate: unknown trait",
                extra={"_fields": {"trait": trait, "allowed": list(_MUTABLE_TRAITS)}},
            )
            raise ManifestValidationError("dna_trait", f"Unknown trait: {trait!r}")
        try:
            current = float(getattr(self, trait))
        except (AttributeError, TypeError, ValueError) as exc:
            log.engine.error(
                "[owls] dna.mutate: failed reading current trait value",
                exc_info=exc,
                extra={"_fields": {"trait": trait}},
            )
            raise ManifestValidationError("dna_trait", f"Cannot read trait {trait!r}: {exc}") from exc
        clamped = max(0.0, min(1.0, current + delta))
        log.engine.debug(
            "[owls] dna.mutate: exit",
            extra={
                "_fields": {
                    "trait": trait,
                    "previous": current,
                    "new": clamped,
                    "clamped": clamped != current + delta,
                }
            },
        )
        return self.model_copy(update={trait: clamped})

    def dominant_traits(self, n: int = 3) -> list[tuple[str, float]]:
        """Return top-``n`` traits by absolute deviation from neutral (``0.5``)."""
        log.engine.debug(
            "[owls] dna.dominant_traits: entry",
            extra={"_fields": {"n": n}},
        )
        if n < 0:
            log.engine.warning(
                "[owls] dna.dominant_traits: negative n coerced to 0",
                extra={"_fields": {"requested": n}},
            )
            n = 0
        deviations: list[tuple[str, float, float]] = [
            (trait, float(getattr(self, trait)), abs(float(getattr(self, trait)) - 0.5)) for trait in _MUTABLE_TRAITS
        ]
        deviations.sort(key=lambda item: item[2], reverse=True)
        result = [(trait, value) for trait, value, _dev in deviations[:n]]
        log.engine.debug(
            "[owls] dna.dominant_traits: exit",
            extra={"_fields": {"returned": len(result)}},
        )
        return result
