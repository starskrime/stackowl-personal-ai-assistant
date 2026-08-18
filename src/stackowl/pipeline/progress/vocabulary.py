"""Progress vocabulary — the SINGLE source of friendly live-status phrases.

Design rules (per project conventions):
  * Logic keys on a stable semantic ``ProgressKey`` enum, NEVER on user-language
    strings. The English bundle below is just one resource table; other locales
    are sibling bundles registered via :func:`register_bundle`.
  * A missing/unknown key degrades to the localized ``THINK`` ("Working on it…")
    — it must NEVER surface a raw internal tool name to a customer.
  * Phrases are warm and first-person ("Checking what I remember…"), describe an
    ongoing action, and never leak tool / model / provider / tier names.

A glyph (emoji) precedes each phrase as a language-independent status icon.
"""

from __future__ import annotations

from enum import StrEnum

from stackowl.infra.observability import log


class ProgressKey(StrEnum):
    """Stable semantic identifiers for live-progress states."""

    ACK = "ACK"  # turn received, nothing started yet
    THINK = "THINK"  # model is reasoning, no tool — also the safe fallback
    SEARCH_WEB = "SEARCH_WEB"
    READ_WEB = "READ_WEB"
    READ_FILES = "READ_FILES"
    WRITE_FILES = "WRITE_FILES"
    RUN_CMD = "RUN_CMD"
    SEARCH_MEMORY = "SEARCH_MEMORY"
    SAVE_MEMORY = "SAVE_MEMORY"
    BROWSE = "BROWSE"
    CODE = "CODE"
    CONSULT = "CONSULT"
    SKILL_RUN = "SKILL_RUN"
    SYNTH = "SYNTH"  # synthesis / writing the answer
    RECOVER = "RECOVER"  # a step failed, trying another way
    STILL_WORKING = "STILL_WORKING"  # reassurance after a long generic wait


# Language-independent leading glyph per state.
_GLYPHS: dict[ProgressKey, str] = {
    ProgressKey.ACK: "⏳",
    ProgressKey.THINK: "🧠",
    ProgressKey.SEARCH_WEB: "🔎",
    ProgressKey.READ_WEB: "📖",
    ProgressKey.READ_FILES: "📂",
    ProgressKey.WRITE_FILES: "💾",
    ProgressKey.RUN_CMD: "🛠️",
    ProgressKey.SEARCH_MEMORY: "💭",
    ProgressKey.SAVE_MEMORY: "📝",
    ProgressKey.BROWSE: "🌐",
    ProgressKey.CODE: "💻",
    ProgressKey.CONSULT: "🦉",
    ProgressKey.SKILL_RUN: "✨",
    ProgressKey.SYNTH: "✍️",
    ProgressKey.RECOVER: "⚠️",
    ProgressKey.STILL_WORKING: "⏳",
}

# A bundle value is either a plain template, or a (singular, plural) pair chosen
# by ``count``. Templates may reference ``{count}`` and ``{skill}``.
_Template = str | tuple[str, str]

_EN: dict[ProgressKey, _Template] = {
    # The ACK template is unused for English — ack_spell() supplies the word (see
    # _ACK_SPELLS). Kept as the documented fallback for any bundle that has not
    # been given a rotation of its own.
    ProgressKey.ACK: "Accio",
    ProgressKey.THINK: "Thinking…",
    ProgressKey.SEARCH_WEB: "Searching the web…",
    ProgressKey.READ_WEB: ("Reading a page…", "Reading {count} pages…"),
    ProgressKey.READ_FILES: ("Reading a file…", "Reading {count} files…"),
    ProgressKey.WRITE_FILES: "Saving your file…",
    ProgressKey.RUN_CMD: "Running a command…",
    ProgressKey.SEARCH_MEMORY: "Checking what I remember…",
    ProgressKey.SAVE_MEMORY: "Noting that for later…",
    ProgressKey.BROWSE: "Browsing the site…",
    ProgressKey.CODE: "Looking through the code…",
    ProgressKey.CONSULT: "Consulting the parliament…",
    ProgressKey.SKILL_RUN: "Using my {skill} skill…",
    ProgressKey.SYNTH: "Writing your answer…",
    ProgressKey.RECOVER: "That didn't work — trying another way…",
    ProgressKey.STILL_WORKING: "Still working on this…",
}

_BUNDLES: dict[str, dict[ProgressKey, _Template]] = {"en": _EN}

# The instant acknowledgement, rotated. Bakir, 2026-08-18: a single word, a Harry
# Potter spell, and "multiple different words instead of single hardcoded".
#
# CHOSEN FOR MEANING, not just for being spells. ProgressKey.ACK is "turn received,
# nothing started yet", so every word here is about summoning, revealing,
# illuminating or unlocking — the thing the platform is about to do. Spells about
# defence, repair or silence would read as decoration, so they are deliberately
# absent even though they are shorter or better known.
#
# ALL are single words, so the request holds no matter which one is drawn, and all
# are pseudo-Latin — the one line a user sees FIRST therefore reads identically in
# every language, unlike the English prose in the bundles above.
_ACK_SPELLS: tuple[str, ...] = (
    "Accio",       # summoning charm — summoning your answer
    "Lumos",       # light — illuminating the question
    "Revelio",     # reveals what is hidden
    "Alohomora",   # unlocking charm — opening the problem up
    "Aparecium",   # makes invisible ink appear
)


def ack_spell(seed: object = None) -> str:
    """One acknowledgement spell.

    ``seed`` makes the choice STABLE for a turn, and that is not a nicety: the live
    status message is EDITED every few seconds by the ticker, and each edit
    re-renders. Drawing freshly each time would make the word flicker between
    spells while the user waits, which reads as a glitch rather than a flourish.
    Same seed ⇒ same spell; different turns ⇒ different spells.

    ``None`` draws at random, for callers with no per-turn handle. Never raises.
    """
    try:
        if seed is None:
            import secrets

            return secrets.choice(_ACK_SPELLS)
        return _ACK_SPELLS[hash(str(seed)) % len(_ACK_SPELLS)]
    except Exception:  # pragma: no cover — a status line must never break a turn
        return _ACK_SPELLS[0]

# The tiny settle footer the live status collapses into once the answer is sent
# ("✓ done in 34s"). Localizable; English default. ``{seconds}`` is the int
# wall-clock duration of the turn.
_DONE_FOOTER: dict[str, str] = {"en": "✓ done in {seconds}s"}


def done_footer(elapsed_s: int, lang: str = "en") -> str:
    """Render the collapsed 'done in Ns' footer. Never raises."""
    template = _DONE_FOOTER.get(_norm_lang(lang), _DONE_FOOTER["en"])
    try:
        return template.format(seconds=max(0, int(elapsed_s)))
    except (KeyError, IndexError, ValueError):
        return f"✓ done in {max(0, int(elapsed_s))}s"


def register_done_footer(lang: str, template: str) -> None:
    """Install a localized done-footer template (must contain ``{seconds}``)."""
    _DONE_FOOTER[lang] = template


# Honest failure footer — a turn that raised/hung must NOT leave the live
# status stuck on "Still working on this… (1670s)" forever (confirmed
# production incident), and must NOT lie with "✓ done" either.
_ABORT_FOOTER: dict[str, str] = {"en": "✗ stopped after {seconds}s — the turn didn't finish"}


def abort_footer(elapsed_s: int, lang: str = "en") -> str:
    """Render the honest 'stopped after Ns' footer for a failed turn. Never raises."""
    template = _ABORT_FOOTER.get(_norm_lang(lang), _ABORT_FOOTER["en"])
    try:
        return template.format(seconds=max(0, int(elapsed_s)))
    except (KeyError, IndexError, ValueError):
        return f"✗ stopped after {max(0, int(elapsed_s))}s — the turn didn't finish"


def elapsed_suffix(elapsed_s: int, lang: str = "en") -> str:
    """Render the compact elapsed suffix appended during long waits — ``(23s)``.

    Parentheses + ``s`` are near-universal; kept tiny and locale-overridable via
    the bundle-style ``_ELAPSED`` table. Never raises.
    """
    template = _ELAPSED.get(_norm_lang(lang), _ELAPSED["en"])
    try:
        return template.format(seconds=max(0, int(elapsed_s)))
    except (KeyError, IndexError, ValueError):
        return f"({max(0, int(elapsed_s))}s)"


_ELAPSED: dict[str, str] = {"en": "({seconds}s)"}


def register_bundle(lang: str, bundle: dict[ProgressKey, _Template]) -> None:
    """Install/merge a locale bundle. Other-language siblings register here."""
    _BUNDLES.setdefault(lang, {}).update(bundle)
    log.engine.debug(
        "[progress] vocabulary.register_bundle",
        extra={"_fields": {"lang": lang, "count": len(bundle)}},
    )


def coerce_key(raw: object) -> ProgressKey:
    """Map an arbitrary tool-declared key to a ProgressKey, THINK on miss.

    Accepts a ProgressKey, its string value, or anything unknown/None — the
    latter degrade to THINK so a raw tool name never reaches the user.
    """
    if isinstance(raw, ProgressKey):
        return raw
    if isinstance(raw, str):
        try:
            return ProgressKey(raw)
        except ValueError:
            return ProgressKey.THINK
    return ProgressKey.THINK


def _norm_lang(lang: str) -> str:
    if lang == "auto" or lang not in _BUNDLES:
        return "en"
    return lang


def _pick(template: _Template, count: int) -> str:
    if isinstance(template, tuple):
        singular, plural = template
        return singular if count == 1 else plural
    return template


def render(
    key: object,
    lang: str = "en",
    *,
    count: int | None = None,
    skill: str | None = None,
    seed: object = None,
) -> str:
    """Render a progress phrase: glyph + localized text. Never raises.

    Args:
        key: A ProgressKey (or coercible value). Unknown ⇒ localized THINK.
        lang: Locale tag; ``"auto"`` / unknown falls back to English.
        count: Optional count for "{count} files" plural selection.
        skill: Optional skill name for the SKILL_RUN template.
    """
    progress_key = coerce_key(key)
    bundle = _BUNDLES.get(_norm_lang(lang), _EN)
    template = bundle.get(progress_key)
    if template is None:
        # Fall back to this locale's THINK, then English THINK.
        template = bundle.get(ProgressKey.THINK) or _EN[ProgressKey.THINK]
    if progress_key is ProgressKey.ACK:
        # The instant ack is drawn from the spell rotation rather than the bundle.
        # ``seed`` keeps it stable for one turn — the live status message is edited
        # every few seconds and each edit re-renders, so an unseeded draw would
        # make the word flicker between spells while the user waits.
        return f"{_GLYPHS[ProgressKey.ACK]} {ack_spell(seed)}".strip()
    text = _pick(template, count if count is not None else 1)
    try:
        text = text.format(count=count if count is not None else "", skill=skill or "")
    except (KeyError, IndexError):
        # A malformed bundle template must never crash a turn.
        log.engine.warning(
            "[progress] vocabulary.render: bad template",
            extra={"_fields": {"key": progress_key.value, "lang": lang}},
        )
    # Collapse whitespace so an empty {skill}/{count} slot leaves no double space.
    text = " ".join(text.split())
    glyph = _GLYPHS.get(progress_key, _GLYPHS[ProgressKey.THINK])
    return f"{glyph} {text}".strip()
