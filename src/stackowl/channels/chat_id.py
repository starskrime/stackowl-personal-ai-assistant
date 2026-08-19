"""Reading a chat id out of a session key, in one place.

BAKIR, 2026-08-19, on why he could not create his email assistant::

    [telegram] consent.prompt: session_key is not a chat id — denying (fail closed)
        {'tool': 'owl_build', 'session': 'owl:secretary:telegram:dm:72055773'}

``owl_build`` is consent-gated. The prompt did ``int(session_key)``, raised, and
failed closed — so every attempt to create an owl from Telegram was denied before
he was ever asked. His chat id, 72055773, is the last segment of the string being
rejected.

THE ASSUMPTION THAT WENT STALE is written in the old comments: "a private chat's
session_key IS the chat id (session_key == str(user_id) == chat_id)". True once.
Lanes are now ``owl:<owl>:<channel>:<kind>:<chat_id>``, and THREE places parsed
them independently — ``telegram/consent.py``, ``telegram/adapter.py`` and
``notifications/router_helpers.py`` — so they went stale together. One rule, three
copies; this module is the one copy they now share.

FAILING CLOSED IS KEPT. The original concern is real: never guess a recipient, or a
confused deputy shows one user another user's consent prompt. Reading the tail of a
KNOWN structured format is parsing, not guessing — and anything that is neither a
bare id nor a recognisable lane still returns None, and the caller still denies.

A leaf on purpose: no platform imports, so both a channel adapter and the
notification router can depend on it without a cycle.
"""

from __future__ import annotations

#: Lane segments are colon-separated: ``owl:<owl>:<channel>:<kind>:<chat_id>``.
_SEP = ":"


def chat_id_from_session(session_key: str | None) -> int | None:
    """The numeric chat id named by ``session_key``, or None if it names none.

    Accepts both the bare form ("72055773", still used by proactive sends) and the
    structured lane ("owl:secretary:telegram:dm:72055773"). Negative ids are valid —
    Telegram groups use them, and rejecting the sign would deny consent in every
    group, which is the same bug in a different hat.

    Pure, and never raises. None means "this does not name a chat", which callers
    must treat as unresolved rather than substituting a default.
    """
    text = (session_key or "").strip()
    if not text:
        return None
    candidate = text.rsplit(_SEP, 1)[-1].strip() if _SEP in text else text
    if not candidate:
        # A trailing separator ("...:dm:") leaves an empty tail. That is not the
        # chat id 0 — it is an absent id, and guessing 0 would address a stranger.
        return None
    try:
        return int(candidate)
    except ValueError:
        return None
