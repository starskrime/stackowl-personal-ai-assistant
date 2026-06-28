"""Agent-facing envelope for owl_build. Deliberately carries NO authority fields
(origin/created_by/creation_ceiling/bounds) — the tool forces those server-side."""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class MissingFields:
    """A create spec that is underspecified but RECOVERABLE by asking the user.

    ``fields`` are the still-missing required field names (validator-decided — the
    state machine, not the LLM); ``partial`` is the spec so far. The tool rides
    ``partial`` through the ClarifyGateway resume, merges each answer, and
    re-validates until the schema is satisfied. Distinct from a hard error string
    (an INVALID value — asking cannot fix it)."""

    fields: tuple[str, ...]
    partial: OwlBuildSpec


def validate_owl_build_spec(spec: OwlBuildSpec) -> str | MissingFields | None:
    """Structured validation. Returns one of three results, never raises:

    * ``str`` — a HARD error (an invalid value; asking the user cannot fix it).
    * :class:`MissingFields` — recoverable: required ``create`` field(s) absent →
      the tool should ASK the user, merge, and re-validate (ADR-A state machine).
    * ``None`` — the spec is complete and valid.

    For ``create`` the irreducible required set is: a ``name``, a capability
    (``preset`` OR ``explicit_tools``), and a ``specialty``.
    """
    if spec.action == "retire":
        if not spec.name or not spec.name.strip():
            return "owl name is required."
        return None
    if spec.action == "edit":
        if not spec.name or not spec.name.strip():
            return "owl name is required."
        if spec.preset and spec.explicit_tools:
            return "provide either 'preset' or 'explicit_tools', not both."
        return None
    # create — preset XOR explicit_tools is an INVALID value (hard error); the
    # remaining gaps are RECOVERABLE missing required fields (ask the user).
    if spec.preset and spec.explicit_tools:
        return "provide either 'preset' or 'explicit_tools', not both."
    missing: list[str] = []
    if not spec.name or not spec.name.strip():
        missing.append("name")
    if not spec.preset and not spec.explicit_tools:
        missing.append("capability")
    if not spec.specialty or not spec.specialty.strip():
        missing.append("specialty")
    if missing:
        return MissingFields(fields=tuple(missing), partial=spec)
    return None
