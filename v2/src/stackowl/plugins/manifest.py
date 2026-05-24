"""PluginManifest — immutable Pydantic model describing a StackOwl plugin."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?(\+[\w.]+)?$")


class PluginManifest(BaseModel):
    """Validated, frozen description of an installable StackOwl plugin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str
    type: Literal["mcp_server", "skill_pack", "local_plugin"]
    entry_point: str
    capabilities: list[str] = Field(default_factory=list)
    config_schema: dict[str, object] | None = None
    description: str
    author: str | None = None
    license: str | None = None

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(
                f"version '{v}' is not valid semver (expected MAJOR.MINOR.PATCH[-pre][+build])"
            )
        return v
