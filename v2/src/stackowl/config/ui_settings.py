"""UISettings — terminal UI configuration block."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UISettings(BaseModel):
    """Terminal-UI configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str = Field(
        default="auto",
        description="UI language; 'auto' selects from environment locale.",
        json_schema_extra={"hot_reload": True},
    )
    tui_version: str = Field(
        default="v2",
        description="Active TUI implementation; 'v1' for legacy fallback.",
        json_schema_extra={"hot_reload": False},
    )
    reduced_motion: bool = Field(
        default=False,
        description="Suppress motion/animation in TUI for accessibility.",
        json_schema_extra={"hot_reload": True},
    )
