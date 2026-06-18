"""SkillManifest — frozen Pydantic shape of one ``skill.yaml`` file.

Each skill on disk lives at
``~/.stackowl/workspace/skills/<source>/<name>/skill.yaml`` and validates
against this model. The model mirrors :class:`stackowl.plugins.manifest.PluginManifest`
conventions (frozen, extra=forbid, semver-checked version) but adds the
fields needed for the learning loop: ``when_to_use``, ``success_rate``,
``n_executions``, ``parent_traces``, etc.

We don't subclass ``PluginManifest`` because the two live at different
abstraction levels — ``PluginManifest`` describes an installable distribution
(a *bundle* of skills + tools + owls), while ``SkillManifest`` is one
individual learnable artifact.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?(\+[\w.]+)?$")

SkillSource = Literal["builtin", "installed", "user", "learned"]


class SkillManifest(BaseModel):
    """Validated, frozen description of one skill directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str
    when_to_use: str = ""
    version: str = "0.1.0"
    source: SkillSource = "user"
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    # Learning bookkeeping — agent updates these for learned/ skills; humans
    # may leave them at defaults for hand-written user/ skills.
    success_rate: float | None = None
    n_executions: int = 0
    parent_traces: list[str] = Field(default_factory=list)
    # Optional embedding metadata (filled in by SkillIndexStore at index time).
    embedding_model: str | None = None
    # Optional author / license fields for shareable packs.
    author: str | None = None
    license: str | None = None
    # Condensed operational playbook injected into an owning owl's system prompt
    # (Owl Capability arc, Story 2). Author override from SKILL.md frontmatter; when
    # absent the SkillIndexStore back-fill generates + caches one. Additive/defaulted
    # so existing SKILL.md (no `summary:`) still validate under extra="forbid".
    summary: str | None = None

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(
                f"version '{v}' is not valid semver "
                "(expected MAJOR.MINOR.PATCH[-pre][+build])",
            )
        return v

    @field_validator("success_rate")
    @classmethod
    def _clamp_success_rate(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v < 0.0 or v > 1.0:
            raise ValueError(f"success_rate {v} must be in [0.0, 1.0]")
        return v
