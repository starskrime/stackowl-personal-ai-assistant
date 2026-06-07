"""Agent-facing envelope for owl_build. Deliberately carries NO authority fields
(origin/created_by/creation_ceiling/bounds) — the tool forces those server-side."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class OwlBuildSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["create", "edit", "retire"]
    name: str
    preset: str | None = None
    explicit_tools: list[str] | None = None
    specialty: str | None = None
    model_tier: str | None = None


def validate_owl_build_spec(spec: OwlBuildSpec) -> str | None:
    """Structural validation. Returns an error string, or None when valid. Never raises."""
    if not spec.name or not spec.name.strip():
        return "owl name is required."
    if spec.action == "retire":
        return None
    if spec.action == "create":
        if spec.preset and spec.explicit_tools:
            return "provide either 'preset' or 'explicit_tools', not both."
        if not spec.preset and not spec.explicit_tools:
            return "create requires a 'preset' or 'explicit_tools'."
        if not spec.specialty or not spec.specialty.strip():
            return "create requires a 'specialty' describing the owl's standing role."
        return None
    # edit
    if spec.preset and spec.explicit_tools:
        return "provide either 'preset' or 'explicit_tools', not both."
    return None
