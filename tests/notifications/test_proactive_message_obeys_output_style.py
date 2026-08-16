"""ESC-20 — a proactive message must obey the user's stored OutputStyle.

BAKIR, 2026-08-16, asked the platform to "reply very short and laconic". Setting
``length: terse`` fixed his CONVERSATIONAL replies and did nothing at all to the
messages he actually finds long.

MEASURED before this was written. The style is enforced in
``pipeline/steps/deliver.py`` (``_enforce_output_prefs``), which is the TURN
delivery path. ``notifications/deliverer.py`` — the chokepoint every proactive
send goes through — contained NO reference to ``OutputStyle`` at all. That day his
conversational replies were 26 and 1,422 chars while Brain's scheduled Sunday
Pulse was 4,396 and then 5,077. The longest things he receives were precisely the
ones style could not reach, and his EXISTING ``output_tables: off`` preference had
never applied to them either — that part predates the terse request entirely.

SCOPE, STATED PLAINLY. This wires the DETERMINISTIC half only: markdown, links,
tables and emoji, via the same ``OutputStyle.enforce`` the turn path uses.
``enforce``'s length step is a documented sync no-op, so the terse LLM compression
does NOT happen here — that half costs a fast-tier call per scheduled send and
rewrites a briefing the owl deliberately formatted, so it stays escalated on
ESC-20 rather than being smuggled in under a formatting fix. The test at the
bottom PINS that boundary so a later change cannot cross it silently.

ONE SOURCE FOR THE RULE. The owner/global merge is not reimplemented here; the
deliverer calls ``load_output_style``, the same function the ``/style`` command
reads through. A second copy of that precedence rule is exactly the "two copies of
one rule" shape this codebase keeps paying for.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.router import Notification

pytestmark = pytest.mark.asyncio

_TARGET = "72055773"

_TABLE = (
    "Here is your roundup:\n\n"
    "| Role | Company |\n"
    "| --- | --- |\n"
    "| Staff Eng | Acme |\n"
)


class _Prefs:
    """A PreferenceStore double with the one method load_output_style calls."""

    def __init__(self, *, glob: dict | None = None, owner: dict | None = None,
                 boom: bool = False) -> None:
        self._glob = glob or {}
        self._owner = owner or {}
        self._boom = boom

    async def list_for_owner(self, owner_key: str) -> dict:
        if self._boom:
            raise RuntimeError("preference store exploded")
        from stackowl.memory.preferences import GLOBAL_OWNER_KEY

        return dict(self._glob if owner_key == GLOBAL_OWNER_KEY else self._owner)


class _Adapter:
    channel_name = "telegram"

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str, *, chat_id: str | int | None = None) -> None:
        self.sent.append(text)


class _Registry:
    def __init__(self, adapter: _Adapter) -> None:
        self._adapter = adapter

    def get(self, channel: str) -> _Adapter:
        return self._adapter


class _Router:
    async def deliver(self, notification: Notification) -> str:
        return "delivered"


class _Store:
    def __init__(self) -> None:
        self.written: list[tuple[str, str]] = []

    async def store(self, content: str, session_key: str) -> None:
        self.written.append((content, session_key))


def _notification(**over: object) -> Notification:
    base: dict = dict(
        message=_TABLE,
        channel_name="telegram",
        # `target_chat_id` is a DEPRECATED construction alias for `target`, not a
        # second field — passing both is a pydantic error. Building the REAL
        # frozen model here (rather than a SimpleNamespace) is what surfaced that.
        target=_TARGET,
        category="digest",
        urgency="normal",
    )
    base.update(over)
    return Notification(**base)


def _deliverer(prefs: object | None, adapter: _Adapter,
               conversation_store: object | None = None) -> ProactiveDeliverer:
    return ProactiveDeliverer(
        router=_Router(),  # type: ignore[arg-type]
        registry=_Registry(adapter),  # type: ignore[arg-type]
        settings=SimpleNamespace(
            notifications=SimpleNamespace(default_channel="telegram")
        ),  # type: ignore[arg-type]
        conversation_store=conversation_store,  # type: ignore[arg-type]
        preference_store=prefs,  # type: ignore[arg-type]
    )


class TestTheStoredStyleReachesAScheduledMessage:
    async def test_tables_off_flattens_a_scheduled_table(self) -> None:
        """Bakir's existing global preference, finally applied where he sees it."""
        adapter = _Adapter()
        prefs = _Prefs(glob={"output_tables": "off"})

        status = await _deliverer(prefs, adapter).deliver(_notification())

        assert status == "delivered"
        assert adapter.sent, "nothing was transported"
        sent = adapter.sent[0]
        assert "| --- |" not in sent, f"the GFM table survived the style: {sent!r}"
        assert "Staff Eng" in sent, "flattening must not lose the content"

    async def test_a_global_preference_applies_without_a_per_owner_one(self) -> None:
        """His style lives under __global__; the recipient key has nothing. If the
        deliverer read only the per-owner scope, the preference would silently
        never apply — which is the whole defect in a different disguise."""
        adapter = _Adapter()
        prefs = _Prefs(glob={"output_tables": "off"}, owner={})

        await _deliverer(prefs, adapter).deliver(_notification())

        assert "| --- |" not in adapter.sent[0]

    async def test_no_preferences_sends_byte_identical_text(self) -> None:
        adapter = _Adapter()

        await _deliverer(_Prefs(), adapter).deliver(_notification())

        assert adapter.sent[0] == _TABLE


class TestItNeverCostsTheDelivery:
    async def test_an_unwired_preference_store_is_a_no_op(self) -> None:
        """A deliverer built without a preference store behaves exactly as it did
        before ESC-20 — every existing construction site stays byte-identical."""
        adapter = _Adapter()

        status = await _deliverer(None, adapter).deliver(_notification())

        assert status == "delivered"
        assert adapter.sent[0] == _TABLE

    async def test_a_raising_preference_store_still_delivers(self) -> None:
        """B5. Styling is a nicety; delivery is the point. A store that explodes
        must send the original text, not drop the message."""
        adapter = _Adapter()

        status = await _deliverer(_Prefs(boom=True), adapter).deliver(_notification())

        assert status == "delivered"
        assert adapter.sent[0] == _TABLE


class TestTheProbeIsLeftAlone:
    async def test_an_ephemeral_canary_is_not_restyled(self) -> None:
        """The health canary is a synthetic probe that is sent and then deleted.
        Rewriting it changes what the probe proves, and it is never read by a
        human — so style has no business touching it. Same exclusion ESC-19 makes
        for remembering it."""
        adapter = _Adapter()
        probe = _notification(message="stackowl canary", ephemeral=True,
                              category="canary")

        await _deliverer(_Prefs(glob={"output_tables": "off"}), adapter).deliver(probe)

        assert adapter.sent == ["stackowl canary"]


class TestWhatWeRememberIsWhatWeSent:
    async def test_the_recorded_message_is_the_styled_one(self) -> None:
        """ESC-19 records a delivered proactive message into the recipient's
        conversation. If styling happened after that record, the agent would
        remember a message it never sent — a small lie that compounds, because the
        next turn reasons over the remembered text."""
        adapter = _Adapter()
        store = _Store()

        await _deliverer(_Prefs(glob={"output_tables": "off"}), adapter,
                         conversation_store=store).deliver(_notification())

        assert store.written, "the delivered message was not recorded at all"
        remembered = store.written[0][0]
        assert "| --- |" not in remembered, "the agent remembered the UNstyled text"
        # ESC-19 stores "User:\n\nAssistant: <body>" and strips the body, so the
        # remembered text is the SENT text modulo that wrapper and whitespace.
        assert remembered.endswith(adapter.sent[0].strip())


class TestTheEscalatedHalfStaysEscalated:
    async def test_terse_does_NOT_compress_a_scheduled_briefing(self) -> None:
        """ESC-20's open question. ``length: terse`` is set globally today, and a
        4,000-char briefing must still arrive whole: the LLM compression is a
        separate, un-decided change that costs a fast-tier call per scheduled send.

        This test exists to FAIL if someone later wires the summariser in here
        without Bakir having answered — the boundary is deliberate, not an
        oversight.
        """
        adapter = _Adapter()
        long_body = "Sunday Pulse. " + ("Something happened today. " * 200)
        prefs = _Prefs(glob={"output_style": '{"length": "terse"}'})

        await _deliverer(prefs, adapter).deliver(_notification(message=long_body))

        assert adapter.sent[0] == long_body, (
            "terse compression reached the proactive path — that is ESC-20's open "
            "question, not a formatting fix"
        )
