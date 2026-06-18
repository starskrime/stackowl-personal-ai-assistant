"""Pydantic models for the morning brief (Story 7.3).

A :class:`MorningBrief` is an ordered list of :class:`BriefSection` records
plus delivery metadata. Sections carry a stable ``key`` (used for identity
and as the rendered header — never an English literal in code) and a
``title`` that may be overridden by a locale lookup at render time.

All models are immutable (``frozen=True``) and reject unknown fields
(``extra="forbid"``) per ARCH-90.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BriefSection(BaseModel):
    """A single rendered section of the morning brief.

    Attributes:
        key: Stable identifier used as the rendered header. Never an
            English literal in code; locale-mapped at render time.
        title: Display title; may be the same as ``key`` or pulled from
            a locale resource.
        items: One rendered line per entry. Empty list is permitted when
            ``omitted`` is ``True``.
        omitted: When ``True``, the renderer skips this section entirely.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    title: str
    items: list[str] = Field(default_factory=list)
    omitted: bool = False


class MorningBrief(BaseModel):
    """A full morning brief — ordered sections plus delivery metadata.

    Attributes:
        sections: Ordered list of :class:`BriefSection` records. Omitted
            sections are kept in the list (the renderer filters them out)
            so callers can introspect what was attempted.
        generated_at: ISO-8601 timestamp of when the brief was assembled.
        delivery_channels: Channel identifiers (``"cli"``, ``"telegram"``,
            etc.) that received the rendered brief.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: list[BriefSection]
    generated_at: str
    delivery_channels: list[str]
