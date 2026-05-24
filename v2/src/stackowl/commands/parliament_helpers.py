"""Formatting helpers for ``/parliament`` subcommand output.

Extracted from :mod:`stackowl.commands.parliament_command` to keep the
command module under the B2 300-line cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stackowl.parliament.models import ParliamentSession


_SESSION_ID_WIDTH = 10
_TOPIC_WIDTH = 60


def format_session_table(sessions: list[ParliamentSession]) -> str:
    """Render a recent-sessions summary table (ASCII, language-neutral)."""
    if not sessions:
        return "No Parliament sessions recorded yet."
    header = (
        f"{'session':<{_SESSION_ID_WIDTH}}  "
        f"{'topic':<{_TOPIC_WIDTH}}  "
        f"{'owls':>4}  "
        f"{'rounds':>6}  "
        f"{'status':<10}"
    )
    rule = "-" * len(header)
    lines = [header, rule]
    for session in sessions:
        topic = session.topic[:_TOPIC_WIDTH]
        sid = session.session_id[:8]
        lines.append(
            f"{sid:<{_SESSION_ID_WIDTH}}  "
            f"{topic:<{_TOPIC_WIDTH}}  "
            f"{len(session.owl_names):>4}  "
            f"{len(session.rounds):>6}  "
            f"{session.status:<10}"
        )
    return "\n".join(lines)


def format_session_transcript(session: ParliamentSession) -> str:
    """Render a full session transcript with round headers and synthesis."""
    lines: list[str] = [
        f"Session: {session.session_id}",
        f"Topic:   {session.topic}",
        f"Status:  {session.status}",
        f"Owls:    {', '.join(session.owl_names)}",
        "",
    ]
    for rnd in session.rounds:
        lines.append(f"=== Round {rnd.round_number} ===")
        for owl_name, response in rnd.responses.items():
            marker = " (truncated)" if rnd.truncated.get(owl_name) else ""
            lines.append(f"[{owl_name}]{marker}:")
            lines.append(response)
            lines.append("")
    if session.interjections:
        lines.append("=== Interjections ===")
        for interjection in session.interjections:
            lines.append(f"- {interjection}")
        lines.append("")
    if session.synthesis:
        lines.append("=== Synthesis ===")
        lines.append(session.synthesis)
    return "\n".join(lines)


def format_rollcall(owl_names: list[str]) -> str:
    """Return a short roll-call header for the start of a /parliament reply."""
    return f"Parliament: {' · '.join(owl_names)}"
