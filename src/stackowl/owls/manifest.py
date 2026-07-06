"""OwlAgentManifest — the definition of a single owl persona."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.dna import OwlDNA
from stackowl.owls.trigger import TriggerSpec

_NAME_RE = re.compile(r"^\w+$", re.UNICODE)
_MAX_NAME_LEN = 16
# Human display name ("Tony from Accounting") — spaces/case allowed, unlike the
# `^\w+$` routing slug. Generous cap; this is what the user sees and speaks.
_MAX_DISPLAY_NAME_LEN = 48
# Non-word runs collapse to one separator when slugifying a display name → slug.
_NON_WORD_RE = re.compile(r"\W+", re.UNICODE)


def slugify_owl_name(display: str) -> str:
    """Derive a routing slug (``^\\w+$``, ≤16, NFC) from a human display name.

    "Tony from Accounting" → "Tony_from_Accou" (trimmed to the length cap). Used
    so a user only ever picks a spoken name; the system name is generated silently.
    Raises ManifestValidationError if nothing usable remains (e.g. all punctuation).
    """
    nfc = unicodedata.normalize("NFC", display).strip()
    slug = _NON_WORD_RE.sub("_", nfc).strip("_")[:_MAX_NAME_LEN].rstrip("_")
    if not slug or not _NAME_RE.match(slug):
        from stackowl.exceptions import ManifestValidationError

        raise ManifestValidationError("display_name", f"cannot derive a name from {display!r}")
    return slug

# The single source of truth for an owl's model tier. Reused by the owl-builder
# (OwlSpec) and command parsing (`_VALID_TIERS = get_args(ModelTier)`) so the
# allowlist can never drift from the field's accepted values.
ModelTier = Literal["fast", "standard", "powerful", "local"]


class OwlAgentManifest(BaseModel):
    """Defines an owl persona — loaded from stackowl.yaml at startup."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    # Human-facing name the user speaks ("Tony") — spaces/case allowed. Empty means
    # "use name". Additive + defaulted so every existing owl loads byte-identical;
    # read via the `display` property which falls back to `name`. (ADR-A/ADR-D)
    display_name: str = ""
    # Lifecycle: an on-demand owl replies only when summoned; a scheduled owl is
    # woken by its `trigger` via the reconcile loop (ADR-B). Defaulted → existing
    # owls are unchanged on-demand personas.
    lifecycle: Literal["on_demand", "scheduled"] = "on_demand"
    # How a scheduled owl is woken. None for on-demand; required for scheduled.
    trigger: TriggerSpec | None = None
    role: str
    system_prompt: str
    model_tier: ModelTier
    provider_name: str | None = None
    tools: list[str] = []
    # Skills this owl owns (records ownership; feeds capability_profile). A tuple
    # for frozen-model hashability. Additive + defaulted: owls predating this
    # field load unchanged. Skill INSTRUCTION-injection is a later story.
    skills: tuple[str, ...] = ()
    # Owl-pinned skills: always FULL-injected regardless of relevance (must be a
    # subset of `skills`; non-owned pins are ignored at injection time). Story B.
    pinned_skills: tuple[str, ...] = ()
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    max_concurrent_requests: int = Field(default=1, ge=1)
    dna: OwlDNA = Field(default_factory=OwlDNA)
    # Toolset-group names this owl is provisioned for — drives DNA-gated
    # presented-set selection (ADR-11). Additive + defaulted: existing manifests
    # without it remain valid. Empty means "no capability gating" (all tools).
    capability_profile: list[str] = []
    # E2-S1 (FR33) — the owl's capability bounds (closed enumeration). Additive +
    # defaulted: ``None`` means UNBOUNDED, so every existing owl is byte-for-byte
    # unchanged. The owl-builder (Epic 5) sets safe-by-construction bounds; the
    # tools axis is enforced at the dispatch seam, the rest by their own layers.
    bounds: BoundsSpec | None = None
    # Provenance + authority (Phase-2 owl_build). Default keeps legacy owls trusted:
    # the security gates key on origin == "agent", which the default is not.
    origin: Literal["human", "builtin", "agent"] = "human"
    created_by: str | None = None  # the owl that minted this owl (agent origin only)
    creation_ceiling: BoundsSpec | None = None  # creator's effective bounds at mint
    # Free-text behavioural guardrail folded into the rendered system prompt
    # (e.g. "has web_fetch but never share raw URLs with the user"). Additive +
    # defaulted → every existing owl loads byte-identical. See DNAPromptInjector.
    boundaries: str = ""
    # Per-owl evolution aggressiveness (design decision 3). Scales the mutation
    # deltas the EvolutionCoordinator applies. Defaulted to "adaptive" (1× — the
    # current behaviour) so existing owls evolve exactly as before.
    evolution_strategy: Literal["conservative", "adaptive", "experimental"] = "adaptive"

    @property
    def display(self) -> str:
        """The name to show/speak — display_name if set, else the routing slug."""
        return self.display_name or self.name

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        nfc = unicodedata.normalize("NFC", v)
        if len(nfc) > _MAX_NAME_LEN:
            from stackowl.exceptions import ManifestValidationError

            raise ManifestValidationError("name", f"exceeds {_MAX_NAME_LEN} characters: {nfc!r}")
        if not _NAME_RE.match(nfc):
            from stackowl.exceptions import ManifestValidationError

            raise ManifestValidationError("name", f"invalid characters in owl name: {nfc!r}")
        return nfc

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        nfc = unicodedata.normalize("NFC", v).strip()
        if len(nfc) > _MAX_DISPLAY_NAME_LEN:
            from stackowl.exceptions import ManifestValidationError

            raise ManifestValidationError(
                "display_name", f"exceeds {_MAX_DISPLAY_NAME_LEN} characters: {nfc!r}"
            )
        return nfc

    @model_validator(mode="after")
    def validate_lifecycle_trigger(self) -> OwlAgentManifest:
        """A scheduled owl MUST carry a trigger; an on-demand owl MUST NOT.

        Cross-field invariant so a contradictory spec (scheduled w/o trigger, or
        on_demand w/ a trigger) can never be minted — caught before persistence.
        """
        from stackowl.exceptions import ManifestValidationError

        if self.lifecycle == "scheduled" and self.trigger is None:
            raise ManifestValidationError("trigger", "scheduled owl requires a trigger")
        if self.lifecycle == "on_demand" and self.trigger is not None:
            raise ManifestValidationError("trigger", "on_demand owl must not have a trigger")
        # Interval floor (S11a): the manifest is the single source of truth, so a
        # scheduled owl can never even be CONSTRUCTED with a trigger that fires
        # faster than the floor — the projected job is thus always within budget.
        # Lazy import keeps the manifest module light (and avoids an import cycle).
        if self.lifecycle == "scheduled" and self.trigger is not None:
            from stackowl.owls.owl_schedule_guards import interval_floor_error

            floor_err = interval_floor_error(self.trigger.schedule)
            if floor_err is not None:
                raise ManifestValidationError("trigger", floor_err)
        return self
