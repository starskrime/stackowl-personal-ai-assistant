"""A structured session key still names a chat, and consent must not deny it.

BAKIR, 2026-08-19: "I am struggling to create agent for my email assistance. I did
talk with platform to create mailbutler but it did not able to create." Then, while
it was being diagnosed, twice in three minutes::

    02:30:12 ERROR [telegram] consent.prompt: session_key is not a chat id —
             denying (fail closed)   {'tool': 'owl_build',
             'session': 'owl:secretary:telegram:dm:72055773'}
    02:32:41 ERROR (the same, again)

``owl_build`` is consent-gated. The prompt is handed the session key, does
``int(session_key)``, raises, and FAILS CLOSED — so every attempt to create an owl
from Telegram is denied before Bakir is ever asked. His chat id, 72055773, is the
last segment of the very string being rejected.

WHY IT BROKE. The code states its assumption in a comment: "a private chat's
session_key IS the chat id (session_key == str(user_id) == chat_id)". That was true
once. Lanes are now ``owl:<owl>:<channel>:<kind>:<chat_id>``, and nothing updated
the three places that parse them — ``channels/telegram/consent.py``,
``channels/telegram/adapter.py`` and ``notifications/router_helpers.py`` each carry
their own ``int(session_key)``. One rule, three copies, all stale together: the
second defect shape in CLAUDE.md, where the code's model of a value stopped
resembling the real thing.

FAILING CLOSED IS STILL RIGHT — the original concern (never guess a recipient, or a
confused deputy shows one user another's prompt) is real and is kept. Reading the
chat id out of a KNOWN structured format is parsing, not guessing. Anything that is
neither a bare id nor a recognisable lane still resolves to None and still denies.
"""

from __future__ import annotations

from stackowl.channels.chat_id import chat_id_from_session


class TestTheRealLaneFormatResolves:
    def test_bakirs_actual_session_key(self) -> None:
        """The exact string the log shows being refused."""
        assert chat_id_from_session("owl:secretary:telegram:dm:72055773") == 72055773

    def test_a_bare_chat_id_still_works(self) -> None:
        """The old form must keep working — it is still what proactive sends carry."""
        assert chat_id_from_session("72055773") == 72055773

    def test_a_group_lane_with_a_negative_id(self) -> None:
        """Telegram group ids are negative. Rejecting the sign would deny consent in
        every group, which is the same bug wearing a different hat."""
        assert chat_id_from_session("owl:brain:telegram:group:-1001234567") == -1001234567

    def test_whitespace_is_tolerated(self) -> None:
        assert chat_id_from_session("  owl:secretary:telegram:dm:72055773 ") == 72055773


class TestItStillRefusesToGuess:
    def test_a_lane_with_no_numeric_tail_is_unresolved(self) -> None:
        """The confused-deputy guard. If the tail is not an id, we do not invent one."""
        assert chat_id_from_session("owl:secretary:cli:local:console") is None

    def test_blank_and_missing_are_unresolved(self) -> None:
        assert chat_id_from_session("") is None
        assert chat_id_from_session(None) is None
        assert chat_id_from_session("   ") is None

    def test_a_bare_word_is_unresolved(self) -> None:
        assert chat_id_from_session("secretary") is None

    def test_a_trailing_separator_does_not_read_as_zero(self) -> None:
        """"...:dm:" must not resolve — an empty tail is not the chat id 0."""
        assert chat_id_from_session("owl:secretary:telegram:dm:") is None


class TestTheThreeCallersAskTheOneImplementation:
    def test_consent_no_longer_carries_its_own_int_parse(self) -> None:
        """The bug was one rule in three places going stale together. If a copy
        comes back, this fails."""
        import inspect

        from stackowl.channels.telegram import consent

        src = inspect.getsource(consent)
        assert "chat_id_from_session" in src
        assert "int(req.session_key)" not in src

    def test_the_adapter_asks_it_too(self) -> None:
        import inspect

        from stackowl.channels.telegram import adapter

        assert "chat_id_from_session" in inspect.getsource(adapter)


class TestTheRootCauseIsTheMissingAddressNotTheParser:
    """The parser above is a BRIDGE. The root cause is that a consent prompt — an
    outgoing message like any other — was never given the turn's delivery target.

    ``PipelineState.reply_target`` exists precisely so a turn's output routes to
    ITS OWN chat rather than a shared ``_last_chat_id``. The deliver step gets it;
    the consent gate did not. With no address, the Telegram prompter had only
    ``session_key`` — an IDENTITY, correctly used elsewhere to scope grants
    (``_window_active``, ``_session_batch``) — and was forced to read it as an
    address. When lanes gained structure the two diverged and owl creation died.
    """

    def test_the_request_can_carry_an_address(self) -> None:
        from stackowl.tools.consent import ConsentRequest

        req = ConsentRequest(
            tool_name="owl_build", channel="telegram",
            session_key="owl:secretary:telegram:dm:72055773",
            reply_target=72055773,
        )

        assert req.reply_target == 72055773

    def test_the_address_defaults_to_absent_so_old_callers_still_work(self) -> None:
        """Proactive and recovery paths have no turn target; they must keep
        resolving through the session key rather than breaking."""
        from stackowl.tools.consent import ConsentRequest

        req = ConsentRequest(tool_name="x", channel="telegram", session_key="72055773")

        assert req.reply_target is None

    def test_the_gate_forwards_the_address_to_the_policy(self) -> None:
        import inspect

        from stackowl.tools.registry import ConsequentialActionGate

        src = inspect.getsource(ConsequentialActionGate.check)
        assert "reply_target" in src

    def test_execute_passes_the_turns_own_target(self) -> None:
        """The producer end. If this regresses, the prompter silently falls back to
        parsing identity again and the bug returns with no new symptom."""
        import inspect

        from stackowl.pipeline.steps import execute

        src = inspect.getsource(execute)
        assert "reply_target=state.reply_target" in src

    def test_the_prompter_prefers_the_address_over_the_session_key(self) -> None:
        import inspect

        from stackowl.channels.telegram import consent

        src = inspect.getsource(consent.TelegramConsentPrompter.prompt)
        addr = src.index("req.reply_target")
        fallback = src.index("chat_id_from_session(req.session_key)")
        assert addr < fallback, "the explicit address must win"
