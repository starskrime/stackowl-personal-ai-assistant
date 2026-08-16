"""ESC-19 — the agent must remember what it SENT you.

BAKIR, 2026-08-16: "Still not remember what agents send to me as goal."

MEASURED end to end before this was written. A scheduled headhunter run gathered
news under session_key ``goal-goal_execution-10da5378`` and delivered it to him on
telegram at 14:03:38. He replied "What?" three times in the next minute and got
the answer to a question he had asked at 13:19 — every time. The news message was
staged under the GOAL lane that produced it; his own lane had no trace of it. The
agent had no record of having spoken to him.

THE FORMAT IS LOAD-BEARING. Rows are stored as "User: X\\n\\nAssistant: Y" and
classify's _parse_turns_to_messages partitions on "\\n\\nAssistant:", skipping a
blank half. Writing an EMPTY user half therefore reads back as a lone assistant
turn — which is exactly what an unprompted message is. Inventing a fake user turn
would put words in the user's mouth and would be read back as something they said.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.notifications.deliverer import ProactiveDeliverer

pytestmark = pytest.mark.asyncio

_TARGET = "72055773"


class _Store:
    def __init__(self, *, boom: bool = False) -> None:
        self.written: list[tuple[str, str]] = []
        self._boom = boom

    async def store(self, content: str, session_key: str) -> None:
        if self._boom:
            raise RuntimeError("store exploded")
        self.written.append((content, session_key))


def _notification(**over: object) -> SimpleNamespace:
    base = dict(
        message="Here is your daily news roundup: ...",
        channel_name="telegram",
        target=_TARGET,
        target_chat_id=_TARGET,
        category="digest",
        urgency="normal",
        file_path=None,
        ephemeral=False,
        job_id="goal-goal_execution-10da5378",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _deliverer(store: object | None) -> ProactiveDeliverer:
    return ProactiveDeliverer(
        router=SimpleNamespace(),  # type: ignore[arg-type]
        registry=SimpleNamespace(),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        conversation_store=store,  # type: ignore[arg-type]
    )


class TestTheMessageJoinsTheConversation:
    async def test_a_delivered_message_is_recorded_for_the_recipient(self) -> None:
        store = _Store()

        await _deliverer(store)._remember_what_we_said(_notification())  # noqa: SLF001

        assert len(store.written) == 1
        content, key = store.written[0]
        assert key == _TARGET, "filed under someone other than the recipient"
        assert "daily news roundup" in content

    async def test_it_reads_back_as_a_LONE_ASSISTANT_turn(self) -> None:
        """The whole point: the next turn must see the agent's message, and must
        NOT see a user turn that never happened."""
        from stackowl.pipeline.steps.classify import _parse_turns_to_messages

        store = _Store()
        await _deliverer(store)._remember_what_we_said(_notification())  # noqa: SLF001

        msgs = _parse_turns_to_messages([store.written[0][0]])

        assert [m.role for m in msgs] == ["assistant"], (
            f"expected one assistant turn, got {[(m.role, m.content[:20]) for m in msgs]}"
        )
        assert "daily news roundup" in msgs[0].content


class TestItDeclinesToGuess:
    async def test_no_recipient_records_nothing(self) -> None:
        """A broadcast or a single-terminal channel has nobody to attribute the
        message to. Filing it under a guess would put it in someone else's
        history — worse than not remembering it."""
        store = _Store()

        await _deliverer(store)._remember_what_we_said(  # noqa: SLF001
            _notification(target=None, target_chat_id=None)
        )

        assert store.written == []

    async def test_an_empty_message_records_nothing(self) -> None:
        store = _Store()

        await _deliverer(store)._remember_what_we_said(_notification(message="   "))  # noqa: SLF001

        assert store.written == []

    async def test_no_store_wired_is_a_no_op(self) -> None:
        """An unwired deliverer behaves exactly as it did before ESC-19."""
        await _deliverer(None)._remember_what_we_said(_notification())  # noqa: SLF001


class TestRememberingNeverCostsTheDelivery:
    async def test_a_raising_store_does_not_propagate(self) -> None:
        """The message HAS been sent by the time this runs. Failing to remember it
        must not turn a delivered message into a failed one."""
        await _deliverer(_Store(boom=True))._remember_what_we_said(  # noqa: SLF001
            _notification()
        )

    async def test_it_falls_back_to_the_chat_id_when_target_is_absent(self) -> None:
        """target is the identity the undelivered outbox uses; the chat id is the
        next best attribution rather than dropping the memory entirely."""
        store = _Store()

        await _deliverer(store)._remember_what_we_said(  # noqa: SLF001
            _notification(target=None)
        )

        assert store.written and store.written[0][1] == _TARGET
