"""A file sent to Telegram must reach the agent.

BAKIR, 2026-08-18: he was mid-conversation about connecting Gmail, the agent asked
for a JSON credentials file, he sent it — and nothing happened. No answer, no
acknowledgement.

THE CAUSE, and it is a one-line omission with a large blast radius. ``start()``
registered exactly two handlers::

    MessageHandler(filters.TEXT,  self._handle_update)
    MessageHandler(filters.VOICE, self._voice_handler.handle_voice)   # if enabled

A document matches NEITHER, so python-telegram-bot dropped the update before the
platform saw it. Nothing logged, because from StackOwl's side the message never
arrived — which is exactly why it looked like the agent was ignoring him.

WHY A DOCUMENT CANNOT JUST REUSE THE TEXT HANDLER. A document message has
``text=None``; ``_handle_update`` strips it to "" and returns early on "empty after
strip". So even routing documents there would drop them silently — the fix has to
turn the file into something the agent can actually act on.

WHAT THE AGENT NEEDS is not the bytes but a PATH it can read. The file is saved to
the canonical downloads dir (already pruned by the downloads janitor, already inside
the tree ``read_file`` is allowed to open) and the turn text names it, so the very
next thing the agent does can be to open it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.channels.telegram.inbound_files import (
    build_document_turn_text,
    safe_document_name,
)

pytestmark = pytest.mark.asyncio


class TestTheAgentIsToldWhatArrived:
    async def test_the_turn_text_names_the_file_and_its_path(self) -> None:
        """The agent acts on text. A turn that said only "a file arrived" would
        leave it with nothing to open."""
        text = build_document_turn_text(
            file_name="credentials.json",
            saved_path=Path("/home/boss/.stackowl/workspace/downloads/credentials.json"),
            caption="",
        )

        assert "credentials.json" in text
        assert "/downloads/credentials.json" in text

    async def test_a_caption_is_the_users_actual_request(self) -> None:
        """When someone sends a file WITH a caption, the caption is the
        instruction — "here's the JSON, now connect it". Dropping it would leave
        the agent guessing why the file arrived."""
        text = build_document_turn_text(
            file_name="creds.json", saved_path=Path("/tmp/creds.json"),
            caption="use this to connect gmail",
        )

        assert "use this to connect gmail" in text

    async def test_no_caption_still_produces_a_usable_turn(self) -> None:
        text = build_document_turn_text(
            file_name="creds.json", saved_path=Path("/tmp/creds.json"), caption="",
        )

        assert text.strip()
        assert "creds.json" in text


class TestTheFilenameCannotEscapeTheDownloadsDir:
    async def test_a_traversal_name_is_neutralised(self) -> None:
        """The name comes from OUTSIDE. "../../.secrets/key" would otherwise write
        through the downloads dir into the secrets folder."""
        assert "/" not in safe_document_name("../../.secrets/telegram.key")
        assert ".." not in safe_document_name("../../evil.json")

    async def test_a_windows_style_path_is_neutralised(self) -> None:
        assert "\\" not in safe_document_name(r"..\..\evil.json")

    async def test_an_ordinary_name_survives_intact(self) -> None:
        """The guard must not mangle the normal case — the agent and the user both
        refer to the file by name."""
        assert safe_document_name("credentials.json") == "credentials.json"

    async def test_an_empty_or_missing_name_gets_a_usable_fallback(self) -> None:
        """Telegram does not guarantee a file_name. A blank one must not produce a
        path that is just the directory."""
        for raw in ("", "   ", None):
            got = safe_document_name(raw)  # type: ignore[arg-type]
            assert got and got.strip()
            assert "/" not in got

    async def test_a_dotfile_name_cannot_hide_the_file(self) -> None:
        """".bashrc" would land hidden and be easy to miss; more importantly a bare
        "." or ".." must never survive as a name."""
        assert safe_document_name(".") not in (".", "")
        assert safe_document_name("..") not in ("..", "")
