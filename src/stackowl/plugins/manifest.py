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
    #: D08.2 slice C added "memory_provider": a plugin that contributes behind the
    #: existing `memory` tool. One value added to the Literal — the loader,
    #: verifier, index and remote-install path all apply unchanged, which is why a
    #: separate memory-provider registry was rejected as duplicated machinery.
    type: Literal["mcp_server", "skill_pack", "local_plugin", "memory_provider"]
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
