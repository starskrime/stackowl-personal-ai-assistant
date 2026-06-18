"""Pure formatting helpers for ParliamentPanel — independent of Textual runtime.

Keeping the string assembly here lets unit tests exercise the rendering logic
without spinning up a Textual ``App`` or composing a ``RichLog`` widget.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stackowl.infra.observability import log

_ROLLCALL_PREFIX = "Parliament:"
_ROLLCALL_SEP = " "  # joined as "{glyph} {name}" segments separated by " · "
_ROLLCALL_GLUE = " · "


def format_rollcall(owl_names: tuple[str, ...], glyph: str) -> str:
    """Render: ``Parliament: {glyph} Owl1 · {glyph} Owl2 · ...``."""
    log.tui.debug(
        "[tui] parliament_panel_helpers.format_rollcall: entry",
        extra={"_fields": {"count": len(owl_names), "glyph": glyph}},
    )
    body = _ROLLCALL_GLUE.join(
        f"{glyph}{_ROLLCALL_SEP}{name}" for name in owl_names
    )
    result = f"{_ROLLCALL_PREFIX} {body}".rstrip()
    log.tui.debug(
        "[tui] parliament_panel_helpers.format_rollcall: exit",
        extra={"_fields": {"len": len(result)}},
    )
    return result


def format_round_header(round_label: str, round_number: int) -> str:
    """Render ``── {round_label} {n} ──`` divider line."""
    return f"── {round_label} {round_number} ──"


@dataclass(frozen=True)
class SynthesisSections:
    """Pre-rendered synthesis lines ready to push into a RichLog."""

    consensus_header: str
    consensus_body: str
    disagreements_header: str | None
    disagreements_lines: tuple[str, ...]
    recommendation_header: str
    recommendation_body: str
    confidence_line: str
    separator: str


def build_synthesis_sections(
    *,
    consensus: str,
    recommendation: str,
    confidence: float,
    disagreements: tuple[str, ...],
    consensus_label: str,
    disagreements_label: str,
    recommendation_label: str,
    separator: str,
) -> SynthesisSections:
    """Assemble the SynthesisArrived display sections in one place."""
    log.tui.debug(
        "[tui] parliament_panel_helpers.build_synthesis_sections: entry",
        extra={
            "_fields": {
                "has_disagreements": bool(disagreements),
                "confidence": confidence,
            }
        },
    )
    disagreement_header: str | None = None
    disagreement_lines: tuple[str, ...] = ()
    if disagreements:
        disagreement_header = f"[{disagreements_label}]"
        disagreement_lines = tuple(f"  · {d}" for d in disagreements)

    pct = max(0.0, min(1.0, confidence)) * 100.0
    sections = SynthesisSections(
        consensus_header=f"[{consensus_label}]",
        consensus_body=consensus,
        disagreements_header=disagreement_header,
        disagreements_lines=disagreement_lines,
        recommendation_header=f"[{recommendation_label}]",
        recommendation_body=recommendation,
        confidence_line=f"[confidence: {pct:.0f}%]",
        separator=separator,
    )
    log.tui.debug(
        "[tui] parliament_panel_helpers.build_synthesis_sections: exit",
        extra={
            "_fields": {
                "disagreement_count": len(disagreement_lines),
                "pct": pct,
            }
        },
    )
    return sections


def synthesis_lines(sections: SynthesisSections) -> tuple[str, ...]:
    """Flatten :class:`SynthesisSections` into the ordered render lines."""
    out: list[str] = [
        sections.separator,
        sections.consensus_header,
        sections.consensus_body,
    ]
    if sections.disagreements_header is not None:
        out.append(sections.disagreements_header)
        out.extend(sections.disagreements_lines)
    out.append(sections.recommendation_header)
    out.append(sections.recommendation_body)
    out.append(sections.confidence_line)
    return tuple(out)


# ---------------------------------------------------------------- onboarding


class OnboardingStore:
    """Thin SQLite wrapper for the `onboarding` table (story 8.4 migration 0021)."""

    def __init__(self, db_path: Path) -> None:
        log.tui.debug(
            "[tui] parliament_panel_helpers.OnboardingStore.__init__: entry",
            extra={"_fields": {"db_path": str(db_path)}},
        )
        self._db_path = db_path

    def was_shown(self, key: str) -> bool:
        """Return True if the tip identified by ``key`` was already shown."""
        log.tui.debug(
            "[tui] parliament_panel_helpers.OnboardingStore.was_shown: entry",
            extra={"_fields": {"key": key}},
        )
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM onboarding WHERE key = ?", (key,)
                ).fetchone()
        except sqlite3.Error as exc:
            log.tui.warning(
                "[tui] parliament_panel_helpers.OnboardingStore.was_shown: query failed",
                exc_info=exc,
                extra={"_fields": {"key": key}},
            )
            return False
        result = row is not None
        log.tui.debug(
            "[tui] parliament_panel_helpers.OnboardingStore.was_shown: exit",
            extra={"_fields": {"key": key, "shown": result}},
        )
        return result

    def mark_shown(self, key: str) -> None:
        """Insert (or ignore) a row recording that ``key`` was shown."""
        log.tui.debug(
            "[tui] parliament_panel_helpers.OnboardingStore.mark_shown: entry",
            extra={"_fields": {"key": key}},
        )
        now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO onboarding (key, shown_at) VALUES (?, ?)",
                    (key, now),
                )
                conn.commit()
        except sqlite3.Error as exc:
            log.tui.warning(
                "[tui] parliament_panel_helpers.OnboardingStore.mark_shown: insert failed",
                exc_info=exc,
                extra={"_fields": {"key": key}},
            )
            return
        log.tui.debug(
            "[tui] parliament_panel_helpers.OnboardingStore.mark_shown: exit",
            extra={"_fields": {"key": key}},
        )
