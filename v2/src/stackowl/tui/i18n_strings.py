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
    "banner.tagline_primary": "Personal AI Assistant",
    "banner.tagline_secondary": "Challenge Everything",
    "transcript.role.you": "you",
    "autocomplete.no_matches": "No matches",
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
