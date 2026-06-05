"""OwlAgentManifest — the definition of a single owl persona."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.dna import OwlDNA

_NAME_RE = re.compile(r"^\w+$", re.UNICODE)
_MAX_NAME_LEN = 16


class OwlAgentManifest(BaseModel):
    """Defines an owl persona — loaded from stackowl.yaml at startup."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    role: str
    system_prompt: str
    model_tier: Literal["fast", "standard", "powerful", "local"]
    provider_name: str | None = None
    tools: list[str] = []
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
