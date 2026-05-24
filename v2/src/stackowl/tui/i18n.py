"""Minimal TUI localizer — returns key as-is when no translation registered.

The platform is multilingual; the TUI never embeds user-facing English
literals directly.  Strings are looked up by stable key (e.g.
``"compose.placeholder"``) so a future translation layer can swap them out
without touching widget code.
"""

from __future__ import annotations

from stackowl.infra.observability import log

_TRANSLATIONS: dict[str, dict[str, str]] = {}


def localize(key: str, lang: str = "auto") -> str:
    """Return the localized string for ``key``, or the key itself if missing.

    Args:
        key: Stable identifier for the user-facing string.
        lang: BCP-47-ish language tag or ``"auto"`` to fall back through
            available locales (currently just ``"en"`` if registered).

    Returns:
        The translation when found, otherwise ``key`` verbatim so the calling
        widget always has *something* to render.
    """
    if lang == "auto" or lang not in _TRANSLATIONS:
        lang = "en"
    return _TRANSLATIONS.get(lang, {}).get(key, key)


def register_translations(lang: str, translations: dict[str, str]) -> None:
    """Install a translation table for ``lang`` (overwrites prior entries)."""
    log.tui.debug(
        "[tui] i18n.register_translations: entry",
        extra={"_fields": {"lang": lang, "count": len(translations)}},
    )
    _TRANSLATIONS[lang] = dict(translations)
    log.tui.debug(
        "[tui] i18n.register_translations: exit",
        extra={"_fields": {"lang": lang, "total_langs": len(_TRANSLATIONS)}},
    )


def clear_translations() -> None:
    """Drop all registered translations — test-only helper."""
    log.tui.debug(
        "[tui] i18n.clear_translations: entry",
        extra={"_fields": {"removed": len(_TRANSLATIONS)}},
    )
    _TRANSLATIONS.clear()
