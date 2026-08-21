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
    # ALL THREE SHIP ON (2026-08-20). Bakir's standing rule: "make it default,
    # never ask me to enable anything — everything should be enabled at system
    # level." These were built, finished and left dormant, which is the case the
    # rule names: a capability the operator would have had to go and find.
    #
    # They are safe to default on for the reason each description already gives —
    # AI rows are always MARKED (☆ + dim), never auto-selected, and never
    # auto-executed. Turning them on adds suggestions; it cannot fire anything.
    # Set any of them False to get the byte-identical deterministic surface back.
    # Enforced by tests/config/test_capabilities_ship_enabled.py.
    command_suggestions: bool = Field(
        default=True,
        description=(
            "Show a fenced '☆ suggested' lane of learned next-likely commands "
            "in the TUI dropdown's low-commitment window (just '/' typed). "
            "Suggest-only; never reorders the deterministic rows, never fires. "
            "ON by default; set False for a dropdown byte-identical to the "
            "deterministic baseline."
        ),
        json_schema_extra={"hot_reload": False},
    )
    semantic_command_search: bool = Field(
        default=True,
        description=(
            "Gait-read the compose box: a natural-language phrase that matches "
            "no command path switches the panel to resolver-ranked command "
            "candidates (marked ☆), and a forward ghost-text predicts the next "
            "token. Selecting a candidate only POPULATES the box — never fires. "
            "ON by default; set False for the byte-identical deterministic "
            "dropdown."
        ),
        json_schema_extra={"hot_reload": False},
    )
    command_hints: bool = Field(
        default=True,
        description=(
            "When a natural-language turn strongly matches a slash command, "
            "append a marked, non-intrusive command hint to the owl's reply "
            "(all channels). The owl still answers normally; the hint never "
            "auto-runs. ON by default; set False to suppress the hints."
        ),
        json_schema_extra={"hot_reload": True},
    )
