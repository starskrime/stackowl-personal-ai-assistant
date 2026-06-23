"""Install the default ``en`` TUI translation table.

This module owns the consolidated English string table for the Textual TUI.
It is registered via :func:`stackowl.tui.i18n.register_translations` so that
:func:`stackowl.tui.i18n.localize` returns real user-facing text rather than
the bare lookup key.  Later stories extend ``_EN`` with their own keys; this
module is the single place the default-locale table is assembled.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.tui.i18n import register_translations

_EN: dict[str, str] = {
    # Banner
    "banner.tagline_primary": "Personal AI Assistant",
    "banner.tagline_secondary": "Challenge Everything",
    # Transcript
    "transcript.role.you": "you",
    # Compose box
    "compose.placeholder": "Message StackOwl…   ( /  commands  ·  Shift+Enter  newline )",
    "compose.mcp_disabled": "Input paused — an MCP spectator is connected",
    "compose.parliament_active": "Parliament in session…",
    "compose.hints": "Enter ↵ send · Shift+Enter newline · / commands · @ owls · 🎤 / Ctrl+R voice",
    # Voice dictation (push-to-talk)
    "compose.voice.recording": "🎤 Recording… Ctrl+R to stop",
    "compose.voice.transcribing": "🎤 Transcribing…",
    "compose.voice.unavailable": "🎤 Voice input unavailable (no microphone tool found)",
    "compose.voice.empty": "🎤 Heard nothing — try again",
    "compose.voice.error": "🎤 Transcription failed",
    "compose.voice.ready": "🎤 Transcript ready — edit, then Enter ↵ to send",
    # Autocomplete
    "autocomplete.no_matches": "No matches",
    # Parliament
    "parliament.round": "Round",
    "parliament.consensus": "Consensus",
    "parliament.recommendation": "Recommendation",
    "parliament.disagreements": "Disagreements",
    "parliament.tip": "Tip",
    # Evolution inspection
    "evolution.inspection.no_changes": "No trait changes",
    # Consent prompt (Telegram + any channel rendering the consent keyboard)
    "consent.prompt.title": "⚠ Approval needed",
    "consent.btn.approve": "✅ Approve",
    "consent.btn.deny": "🚫 Deny",
}


def install_default_translations() -> None:
    """Register the default ``en`` translation table with the localizer."""
    log.tui.debug(
        "[tui] i18n_strings.install_default_translations: entry",
        extra={"_fields": {"keys": len(_EN)}},
    )
    register_translations("en", _EN)
    log.tui.debug(
        "[tui] i18n_strings.install_default_translations: exit",
        extra={"_fields": {"keys": len(_EN)}},
    )
