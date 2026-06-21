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
    # --- Command-discoverability AI augmentation (WS-D) -------------------
    # Each flag is OFF by default: with all three off, the command surface is
    # byte-identical to the deterministic baseline. AI rows are always marked
    # (☆ + dim), never auto-selected, and never auto-executed.
    command_suggestions: bool = Field(
        default=False,
        description=(
            "Show a fenced '☆ suggested' lane of learned next-likely commands "
            "in the TUI dropdown's low-commitment window (just '/' typed). "
            "Suggest-only; never reorders the deterministic rows, never fires. "
            "OFF by default — the dropdown is byte-identical when off."
        ),
        json_schema_extra={"hot_reload": False},
    )
    semantic_command_search: bool = Field(
        default=False,
        description=(
            "Gait-read the compose box: a natural-language phrase that matches "
            "no command path switches the panel to resolver-ranked command "
            "candidates (marked ☆), and a forward ghost-text predicts the next "
            "token. Selecting a candidate only POPULATES the box — never fires. "
            "OFF by default — byte-identical deterministic dropdown when off."
        ),
        json_schema_extra={"hot_reload": False},
    )
    command_hints: bool = Field(
        default=False,
        description=(
            "When a natural-language turn strongly matches a slash command, "
            "append a marked, non-intrusive command hint to the owl's reply "
            "(all channels). The owl still answers normally; the hint never "
            "auto-runs. OFF by default."
        ),
        json_schema_extra={"hot_reload": True},
    )
