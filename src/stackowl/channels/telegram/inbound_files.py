"""Turning a file someone sends into something the agent can act on.

BAKIR, 2026-08-18: he was asked for a Gmail credentials JSON, sent it, and the
agent never responded. The cause was a one-line omission with a large blast
radius — ``start()`` registered handlers for ``filters.TEXT`` and ``filters.VOICE``
and nothing else, so python-telegram-bot dropped every document before StackOwl saw
it. Nothing was logged, because from the platform's side the message never arrived.
That is why it looked like the agent was ignoring him rather than failing.

WHY A DOCUMENT CANNOT SIMPLY REUSE THE TEXT HANDLER. A document message carries
``text=None``; ``_handle_update`` strips that to ``""`` and returns early at "empty
after strip". Routing documents there would swap a silent drop for a slightly
different silent drop.

WHAT THE AGENT ACTUALLY NEEDS is not the bytes but a PATH it can open. The file is
saved into the canonical downloads directory — already inside the tree ``read_file``
is permitted to read, and already pruned by the downloads janitor, so this adds no
new storage lifecycle — and the turn text names it. The agent's very next action can
be to read it.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Characters that cannot appear in a saved name. Everything outside this set is
#: replaced, so a hostile ``file_name`` cannot steer the write out of the downloads
#: directory or hide the result.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: Used when Telegram supplies no usable name. Telegram does NOT guarantee
#: ``file_name``, and a blank one would otherwise produce a path that is just the
#: directory.
_FALLBACK_NAME = "upload.bin"

#: A saved name is truncated to this, so a pathological 4KB filename cannot break
#: the filesystem call or the turn text that quotes it.
_MAX_NAME = 120


def safe_document_name(raw: str | None) -> str:
    """A filename safe to join onto the downloads directory.

    The name arrives from OUTSIDE, so it is treated as hostile: ``../../.secrets/
    telegram.key`` would otherwise write straight through the downloads directory
    into the secrets folder. Only the basename is kept, separators of both flavours
    are stripped, and a name that reduces to nothing (``""``, ``"."``, ``".."``)
    falls back rather than yielding a path that is just the directory.
    """
    text = (raw or "").strip()
    # Both separators, explicitly: a Windows-style name reaching a POSIX host is
    # not split by Path(), so "..\\..\\evil.json" would survive as one "name".
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = _UNSAFE.sub("_", text).strip("._")
    if not text:
        return _FALLBACK_NAME
    return text[:_MAX_NAME]


def build_document_turn_text(
    *, file_name: str, saved_path: Path | str, caption: str | None,
) -> str:
    """The turn text for an arriving file.

    The agent acts on text, so a turn saying only "a file arrived" would leave it
    with nothing to open. This names the file AND its path.

    A caption is the user's actual instruction — "here is the JSON, now connect
    Gmail" — so it leads. Without one, the text still states plainly that a file
    was received and where it is, which is enough for the agent to ask what to do
    with it rather than fall silent.
    """
    said = (caption or "").strip()
    lead = said if said else f"I've sent you a file: {file_name}"
    return (
        f"{lead}\n\n"
        f"[A file was received and saved. name: {file_name} — path: {saved_path}. "
        f"You can read it with the read_file tool.]"
    )
