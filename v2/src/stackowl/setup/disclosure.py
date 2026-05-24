"""AiActDisclosure — EU AI Act disclosure management."""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.setup.localize import localize


class AiActDisclosure:
    """Manages the EU AI Act disclosure text and per-session display tracking.

    The disclosure is shown once per session. Callers should check
    ``was_shown_this_session()`` before rendering and call ``mark_shown()``
    after displaying.
    """

    def __init__(self) -> None:
        # 1. ENTRY
        log.setup.debug("[disclosure] AiActDisclosure.__init__: entry")
        self._shown_sessions: set[str] = set()
        log.setup.debug("[disclosure] AiActDisclosure.__init__: exit")

    def get_text(self, lang: str = "en") -> str:
        """Return the localised AI Act disclosure string.

        Falls back to English if *lang* is not supported. Uses the
        ``localize()`` stub which can be backed by locale files in production.
        """
        # 1. ENTRY
        log.setup.debug(
            "[disclosure] get_text: entry",
            extra={"_fields": {"lang": lang}},
        )
        t0 = time.monotonic()

        # 2. DECISION — delegate to localize() so no string is hardcoded here
        text = localize("ai_act_disclosure", lang)
        if not text or text == "ai_act_disclosure":
            # Fallback: localize returned the key itself — use English
            text = localize("ai_act_disclosure", "en")

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.setup.debug(
            "[disclosure] get_text: exit",
            extra={"_fields": {"lang": lang, "text_len": len(text), "duration_ms": duration_ms}},
        )
        return text

    def was_shown_this_session(self, session_id: str) -> bool:
        """Return True if the disclosure was already shown in *session_id*."""
        # 1. ENTRY
        log.setup.debug(
            "[disclosure] was_shown_this_session: entry",
            extra={"_fields": {"session_id": session_id}},
        )
        result = session_id in self._shown_sessions
        # 4. EXIT
        log.setup.debug(
            "[disclosure] was_shown_this_session: exit",
            extra={"_fields": {"session_id": session_id, "result": result}},
        )
        return result

    def mark_shown(self, session_id: str) -> None:
        """Record that the disclosure was shown in *session_id*."""
        # 1. ENTRY
        log.setup.debug(
            "[disclosure] mark_shown: entry",
            extra={"_fields": {"session_id": session_id}},
        )
        # 2. DECISION — add to in-memory set
        self._shown_sessions.add(session_id)
        # 4. EXIT
        log.setup.debug(
            "[disclosure] mark_shown: exit",
            extra={"_fields": {"session_id": session_id, "total_shown": len(self._shown_sessions)}},
        )
