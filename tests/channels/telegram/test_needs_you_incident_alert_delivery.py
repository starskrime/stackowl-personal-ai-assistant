"""Story 3.6 — the two new gateway-side needs_you Telegram hooks:

* A newly-opened `incident`/`alert` item (no live waiter of its own) is
  pushed to Telegram as plain narrated text and registered
  (`GatewayLink._deliver_needs_you_opened` -> `TelegramIncidentAlertNotifier`).
* A `needs_you.resolved` event, from ANY surface, edits that same message to
  show the outcome and drops its keyboard — proven here for a normal
  resolve (✅) and an expiry (⏳), and proven to be a no-op when nothing is
  registered for that item id (e.g. a resolving surface already edited it
  itself and popped the registry first — `TelegramConsentPrompter.
  handle_callback`'s own precedent).

Rides the same real journal fan-out path `tests/runtime/test_gateway_journal_
fanout.py` already exercises (`GatewayLink`'s Hello-catch-up, backed by a
real tmp DB) rather than hand-building `JournalEventFrame`s, so the real
`journal.record()` recursive NEEDS_YOU wiring opens/resolves the item exactly
as production code does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stackowl.channels.telegram.needs_you_notifier import TelegramIncidentAlertNotifier
from stackowl.channels.telegram.needs_you_registry import (
    NeedsYouMessageRegistry,
    set_registry_for_tests,
)
from stackowl.db.pool import DbPool
from stackowl.ipc.frames import HelloFrame
from stackowl.journal import consent_events, needs_you
from stackowl.journal.narrator import reset_name_resolvers_for_tests
from stackowl.runtime.gateway_link import GatewayLink

pytestmark = pytest.mark.asyncio

_LINK_SECRET = "test-link-secret"
_OWNER_CHAT_ID = 42


class _FakeMessage:
    def __init__(self, message_id: int, chat_id: int) -> None:
        self.message_id = message_id
        self.chat_id = chat_id


class _FakeTelegramAdapter:
    channel_name = "telegram"

    def __init__(self) -> None:
        self.sent: list[tuple[str, int | str | None]] = []
        self.edits: list[tuple[int, int, str]] = []
        self._next_message_id = 100

    async def send(self, reader) -> None:  # noqa: ANN001
        return None

    async def send_text(
        self, text: str, *, chat_id: int | str | None = None,
    ) -> _FakeMessage:
        self._next_message_id += 1
        self.sent.append((text, chat_id))
        return _FakeMessage(self._next_message_id, int(chat_id) if chat_id is not None else -1)

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, *, reply_markup: object = None,
    ) -> bool:
        self.edits.append((chat_id, message_id, text))
        return True


class _FakeConn:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, frame: object) -> None:
        self.sent.append(frame)


def _hello(pid: int = 1) -> HelloFrame:
    return HelloFrame(
        sender_pid=pid, highest_migration=1, registry_digest="x", link_secret=_LINK_SECRET,
    )


@pytest.fixture(autouse=True)
def _isolated_registry():  # noqa: ANN201 — pytest fixture
    """The registry is a process-global singleton — isolate it per test."""
    set_registry_for_tests()
    yield
    set_registry_for_tests()


@pytest.fixture(autouse=True)
def _reset_name_resolvers():  # noqa: ANN201 — pytest fixture
    reset_name_resolvers_for_tests()
    yield
    reset_name_resolvers_for_tests()


@pytest.fixture(autouse=True)
def _resolve_owner_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake the durable owner-address lookup `_deliver_needs_you_opened`
    calls, rather than mutating real global Settings — the lazy
    `from stackowl.notifications.recipient import resolve_owner_addresses`
    inside the method picks up this patched attribute on every call."""
    import stackowl.notifications.recipient as recipient_module

    monkeypatch.setattr(
        recipient_module, "resolve_owner_addresses",
        lambda settings, channels: (  # noqa: ARG005 — settings unused by the fake
            {"telegram": _OWNER_CHAT_ID} if "telegram" in channels else {}
        ),
    )


def _make_link(adapter: _FakeTelegramAdapter, tmp_db: DbPool) -> GatewayLink:
    link = GatewayLink(
        {"telegram": adapter}, link_secret=_LINK_SECRET, journal_fetcher=tmp_db,
    )
    link.set_needs_you_notifier(TelegramIncidentAlertNotifier(adapter))
    return link


async def _catch_up(link: GatewayLink) -> None:
    conn = _FakeConn()
    link.set_connection(conn, local_hello=_hello())
    await link._route(_hello())


class TestIncidentAlertOpenedPushesAndRegisters:
    async def test_incident_opened_pushes_narration_to_the_owner_chat(
        self, tmp_db: DbPool,
    ) -> None:
        adapter = _FakeTelegramAdapter()
        link = _make_link(adapter, tmp_db)

        await consent_events.record_channel_unreachable(
            tmp_db, channel="slack", tool_name="shell",
        )

        await _catch_up(link)

        assert len(adapter.sent) == 1
        text, chat_id = adapter.sent[0]
        assert chat_id == _OWNER_CHAT_ID
        assert text  # the narrator's full text — non-empty, plain text

    async def test_approval_and_question_items_are_never_double_delivered_here(
        self, tmp_db: DbPool,
    ) -> None:
        """AC2/Boundaries — this handler must react ONLY to incident/alert;
        approval/question already have their own live prompters."""
        adapter = _FakeTelegramAdapter()
        link = _make_link(adapter, tmp_db)

        # An approval item, opened the same way ConsentPolicy.request() does
        # (via consent_events.record_consent_requested), never reaches this
        # pusher.
        await consent_events.record_consent_requested(
            tmp_db, tool_name="shell", channel="telegram", session_key="s1",
            category=None,
            expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        )

        await _catch_up(link)

        assert adapter.sent == []


class TestCrossSurfaceEditOnResolve:
    async def test_a_resolved_item_edits_its_registered_message_with_a_checkmark(
        self, tmp_db: DbPool,
    ) -> None:
        adapter = _FakeTelegramAdapter()
        link = _make_link(adapter, tmp_db)

        await consent_events.record_channel_unreachable(
            tmp_db, channel="slack", tool_name="shell",
        )
        await _catch_up(link)
        assert len(adapter.sent) == 1

        rows = await tmp_db.fetch_all(
            "SELECT id FROM needs_you WHERE kind = 'incident' AND resolved_cursor IS NULL",
        )
        assert len(rows) == 1
        item_id = rows[0]["id"]

        async with tmp_db.transaction() as conn:
            resolution = await needs_you.resolve(
                conn, item_id=item_id, answer="acknowledged",
                resolved_by="system:test_manual",
            )
        assert resolution.outcome == "resolved"

        await _catch_up(link)

        assert len(adapter.edits) == 1
        chat_id, message_id, text = adapter.edits[0]
        assert chat_id == _OWNER_CHAT_ID
        assert text.startswith("✅ ")

    async def test_an_expired_item_edits_with_an_hourglass(self, tmp_db: DbPool) -> None:
        adapter = _FakeTelegramAdapter()
        link = _make_link(adapter, tmp_db)

        await consent_events.record_channel_unreachable(
            tmp_db, channel="slack", tool_name="shell",
        )
        await _catch_up(link)

        rows = await tmp_db.fetch_all(
            "SELECT id FROM needs_you WHERE kind = 'incident' AND resolved_cursor IS NULL",
        )
        item_id = rows[0]["id"]

        async with tmp_db.transaction() as conn:
            resolution = await needs_you._expire_item(conn, item_id=item_id)
        assert resolution.outcome == "expired"

        await _catch_up(link)

        assert len(adapter.edits) == 1
        _chat_id, _message_id, text = adapter.edits[0]
        assert text.startswith("⏳ ")

    async def test_a_resolution_with_nothing_registered_is_a_silent_no_op(
        self, tmp_db: DbPool,
    ) -> None:
        """Mirrors `TelegramConsentPrompter.handle_callback`'s own pop-first
        precedent: a surface that already edited the message itself and
        popped the registry means this generic hook finds nothing and must
        never re-edit (worse, generic text) or raise."""
        adapter = _FakeTelegramAdapter()
        link = _make_link(adapter, tmp_db)

        await consent_events.record_channel_unreachable(
            tmp_db, channel="slack", tool_name="shell",
        )
        await _catch_up(link)
        # Simulate a resolving surface that already handled its own edit and
        # popped the registry BEFORE the resolved event lands here.
        rows = await tmp_db.fetch_all(
            "SELECT id FROM needs_you WHERE kind = 'incident' AND resolved_cursor IS NULL",
        )
        item_id = rows[0]["id"]
        set_registry_for_tests(NeedsYouMessageRegistry())

        async with tmp_db.transaction() as conn:
            await needs_you.resolve(
                conn, item_id=item_id, answer="acknowledged",
                resolved_by="system:test_manual",
            )

        await _catch_up(link)

        assert adapter.edits == []
