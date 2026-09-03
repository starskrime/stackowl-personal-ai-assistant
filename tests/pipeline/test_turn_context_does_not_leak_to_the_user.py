"""Our own scaffolding must never be quoted back at the user.

THE LIVE REPORT, 2026-08-15. Bakir asked why an owl had pinged him early and got:

    I couldn't fully complete this: Right now it is Saturday, August 15, 2026 at
    04:10 PM CDT.

    Wait i tought headhunter will ping me tomorrow only why he pinged me again?.
    The capability that failed: . What I tried: delegate_task, tool_search, ...

The wall-clock sentence is ours, not his. D01.1 moved it out of the frozen system
prompt and onto the turn, which it does by prepending it to the user's message —
so a model can read it as part of what the user SAID. qwen 3.8 then copied the
whole composed message into delegate_task's sub-task argument, the child's
input_text carried it, and the give-up message quoted it back.

This is the same shape as the e8 session smokes, where a scripted provider used
the composed message as a spawn label. Any consumer that treats the composed text
as the user's words inherits the prefix.

A SAFETY NET, NOT THE FIX. The durable answer is to stop concatenating the
context onto the user's words — it should ride as its own message, so nothing can
mistake it for user input. That changes every provider call path, so it is
escalated rather than done in a hotfix.
"""

from __future__ import annotations

from stackowl.pipeline.state import PipelineState
from stackowl.owls.base_prompt import strip_turn_context, volatile_turn_context

_PREFIX = "Right now it is Saturday, August 15, 2026 at 04:10 PM CDT."

LEAKED = (
    "Right now it is Saturday, August 15, 2026 at 04:10 PM CDT.\n\n"
    "Wait i tought headhunter will ping me tomorrow only why he pinged me again?"
)
USER_TEXT = "Wait i tought headhunter will ping me tomorrow only why he pinged me again?"


class TestTheReportedMessage:
    def test_the_users_words_survive_and_ours_do_not(self) -> None:
        assert strip_turn_context(LEAKED) == USER_TEXT

    def test_it_is_idempotent(self) -> None:
        """It runs at more than one surface, so applying it twice must be safe."""
        assert strip_turn_context(strip_turn_context(LEAKED)) == USER_TEXT


class TestItOnlyRemovesOurOwnPrefix:
    def test_text_that_never_had_a_prefix_is_untouched(self) -> None:
        assert strip_turn_context("Just a normal question?") == "Just a normal question?"

    def test_a_mid_sentence_mention_is_preserved(self) -> None:
        """The user is allowed to write those words. Only a LEADING block is ours."""
        said = "He said right now it is fine."
        assert strip_turn_context(said) == said

    def test_empty_and_none_are_safe(self) -> None:
        assert strip_turn_context("") == ""
        assert strip_turn_context(None) == ""

    def test_a_multi_line_question_keeps_all_of_its_lines(self) -> None:
        text = "Right now it is Monday, January 05, 2026 at 09:00 AM UTC.\n\nline one\n\nline two"
        assert strip_turn_context(text) == "line one\n\nline two"


class TestItTracksTheRealBuilder:
    def test_it_strips_what_volatile_turn_context_actually_produces(self) -> None:
        """Generated from the same function the pipeline uses, so a change to the
        wording cannot leave this stripping a shape nobody emits any more."""
        import datetime

        context = volatile_turn_context(
            datetime.datetime(2026, 8, 15, 16, 10, tzinfo=datetime.UTC),
            capabilities_offered=True,
        )
        composed = f"{context}\n\n{USER_TEXT}"

        assert strip_turn_context(composed) == USER_TEXT

    def test_the_no_capabilities_variant_is_also_removed(self) -> None:
        """That turn carries a second paragraph, and it is ours too."""
        import datetime

        context = volatile_turn_context(
            datetime.datetime(2026, 8, 15, 16, 10, tzinfo=datetime.UTC),
            capabilities_offered=False,
        )
        composed = f"{context}\n\n{USER_TEXT}"

        cleaned = strip_turn_context(composed)

        assert cleaned == USER_TEXT, cleaned
        assert "capabilities" not in cleaned


class TestTheSurfacesAreWired:
    def test_the_giveup_floor_strips_before_quoting_the_user(self) -> None:
        """The guarantee, asserted on BEHAVIOUR rather than on source text.

        This used to assert the literal string
        ``goal=strip_turn_context(state.input_text)`` appeared in delivery_gate.
        That pinned one spelling of the fix rather than the fix: on 2026-09-03 the
        strip moved inside ``user_goal`` — which also refuses to quote a prompt the
        USER never wrote — and this went red while the guarantee it protects was
        strictly stronger than before. A test that fails on a correct refactor is
        reporting on the code's shape, not on its promise.
        """
        import datetime

        from stackowl.pipeline.state import user_goal

        context = volatile_turn_context(
            datetime.datetime(2026, 8, 15, 16, 10, tzinfo=datetime.UTC),
        )
        state = PipelineState(
            trace_id="t", session_key="tg-1", input_text=f"{context}\n\n{USER_TEXT}",
            channel="telegram", owl_name="secretary", pipeline_step="start",
        )

        goal = user_goal(state)

        assert goal == USER_TEXT, f"our own scaffolding reached the floor's goal: {goal!r}"
        assert "Right now it is" not in (goal or "")

    def test_a_delegated_child_receives_clean_input(self) -> None:
        import inspect

        from stackowl.tools.agents import delegate_task

        source = inspect.getsource(delegate_task)
        assert "input_text=strip_turn_context(sub_task)" in source, (
            "the child's input_text carries whatever the model wrote, prefix included"
        )


class TestTheProviderPathThatActuallyReachedTheUser:
    """The first strip missed this one, which is the path Bakir's message took.

    delivery_gate and delegate_task were patched on the reasonable theory that the
    child's input_text carried the prefix. It does — but this turn never went
    through either: the provider hit "persistent tool-call leak, no escalation
    available" and built the floor itself from the COMPOSED user_text it was
    called with. Reproducing the reported string is what found it.
    """

    def _calls(self) -> list[dict[str, object]]:
        names = ["delegate_task", "tool_search", "tool_describe", "memory",
                 "tool_describe", "tool_search", "tool_search", "delegate_task",
                 "tool_describe", "tool_describe"]
        return [{"name": n, "args": {}, "result": "ok", "failed": False} for n in names]

    def test_the_goal_no_longer_carries_the_prefix(self) -> None:
        from stackowl.pipeline.supervisor import synthesize_from_calls

        out = synthesize_from_calls(f"{_PREFIX}\n\n{USER_TEXT}", self._calls(), "")

        assert "Right now it is" not in out
        assert USER_TEXT in out

    def test_the_empty_slots_are_OMITTED_not_filled_with_a_guess(self) -> None:
        """Every tool in that turn SUCCEEDED — it failed for want of an answer,
        not a broken tool — so no capability could be attributed and the message
        rendered "The capability that failed: ." and a dangling "Technical
        detail: ".

        My first fix derived the name from attempts[0]. That was WRONG and a
        sibling test caught it: it would report a capability that ultimately
        SUCCEEDED as the one that failed (owl_build create->create->edit). In a
        message whose only job is honesty that is a worse bug than a blank slot,
        so the sentences are omitted instead."""
        from stackowl.pipeline.supervisor import synthesize_from_calls

        out = synthesize_from_calls(USER_TEXT, self._calls(), "")

        assert "The capability that failed:" not in out
        assert "Technical detail:" not in out
        assert "No single step reported a failure" in out
        assert "delegate_task" in out, "what was tried is still reported"

    def test_a_genuinely_failed_tool_is_still_named_precisely(self) -> None:
        """The fallback must not shadow a real answer: when a call IS marked
        failed, that name wins over attempts[0]."""
        from stackowl.pipeline.supervisor import synthesize_from_calls

        calls = self._calls()
        calls[4] = {"name": "cronjob", "args": {}, "result": "boom", "failed": True}

        out = synthesize_from_calls(USER_TEXT, calls, "")

        assert "The capability that failed: cronjob" in out
        assert "boom" in out
