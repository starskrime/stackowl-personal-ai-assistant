"""OwlSettings — how many owls, and what bounds them.

Bakir, 2026-08-18: "Why does the platform have a limitation to create 5 owls only?
Remove that limitation and that should be unlimited."
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OwlSettings(BaseModel):
    """Limits on agent-created owls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_agent_owls: int = Field(
        default=0,
        description=(
            "How many agent-created owls are allowed. 0 (the default) means "
            "UNLIMITED — Bakir's call, 2026-08-18, and the measurement supports "
            "it: the only real cost of another owl is the ground-truth roster in "
            "the system prompt (name + one-line role, every call), which runs 69 "
            "chars per owl on the live registry. Fifty owls would be ~862 tokens, "
            "0.33% of a 262,144 window. The previous cap of five was not "
            "protecting the waist.\n\n"
            "Kept as a setting rather than deleted so a shared deployment can "
            "still bound it without a code change. What actually prevents a mess "
            "is unchanged: owl_build still refuses a near-duplicate, enforces name "
            "quality, and requires consent for the tools a new owl is granted — "
            "guards that catch twenty variants of one persona, which a hard count "
            "never did (a count blocks the sixth GOOD owl as readily as the sixth "
            "junk one)."
        ),
    )
