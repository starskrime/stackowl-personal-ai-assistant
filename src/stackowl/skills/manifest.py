"""SkillManifest — frozen Pydantic shape of one ``SKILL.md`` frontmatter block.

Each skill on disk lives at ``~/.stackowl/skills/<source>/<name>/SKILL.md`` (or
``<source>/<category>/<name>/``) and its YAML frontmatter validates against this
model. This docstring said ``skill.yaml`` until 2026-09-04, by which point there
were 40 ``SKILL.md`` files and ZERO ``skill.yaml`` — the format changed and the
description of it did not. The model mirrors :class:`stackowl.plugins.manifest.PluginManifest`
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
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stackowl.infra.observability import log

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
    # Category: when a skill lives at <source>/<category>/<name>/, the loader
    # derives this from the directory segment so it round-trips into the index
    # (skills_list reads it). Additive/defaulted → existing SKILL.md still validate
    # under extra="forbid".
    category: str | None = None
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
    # ``summary`` REMOVED in D09.3 slice 5 (migration 0110). It was an author
    # override for the injected one-liner, back-filled by an LLM when absent.
    # D10.2 replaced it with fields that already exist and cannot drift from one
    # another: a <=60-char ``description`` plus a required rich ``when_to_use``.
    # Legacy files that still carry the key are handled by ``_drop_retired_keys``
    # below, NOT by extra="forbid" — see the comment there for why.

    #: Frontmatter keys this model used to accept and no longer does. Dropped on
    #: read instead of rejected.
    #:
    #: extra="forbid" is right for a TYPO — it turns `descrption:` into a named
    #: error instead of a silently ignored key. It is wrong for a field we
    #: ourselves retired: 142 of the 169 SKILL.md files on disk when `summary`
    #: was removed still carried it, so forbidding would have failed 84% of the
    #: catalog to load on the next boot. Disabling most of the agent's skills as
    #: a side effect of a schema cleanup is not a loud failure, it is an outage.
    #:
    #: Dropping is safe precisely because the data is going anyway: the column is
    #: gone (migration 0110) and nothing reads the value. The key disappears from
    #: the files themselves when the migration pass rewrites their bodies.
    _RETIRED_KEYS: ClassVar[frozenset[str]] = frozenset({"summary"})

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_keys(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        present = cls._RETIRED_KEYS.intersection(data)
        if not present:
            return data
        # DEBUG, not warning: this fires once per legacy skill on every boot, and
        # 142 warnings a boot trains an operator to stop reading warnings.
        log.skills.debug(
            "[skills] manifest: dropping retired frontmatter key(s)",
            extra={"_fields": {"keys": sorted(present), "name": data.get("name")}},
        )
        return {k: v for k, v in data.items() if k not in cls._RETIRED_KEYS}

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
