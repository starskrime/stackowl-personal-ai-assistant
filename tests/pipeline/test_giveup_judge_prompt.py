"""Test that the give-up judge prompt explicitly flags the hand-back / built-it-for-you shape."""

from stackowl.pipeline.persistence import _build_messages


def test_judge_prompt_flags_handback_shape() -> None:
    msgs = _build_messages(
        user_request="send an email",
        draft_answer="Here are the steps to send the email yourself.",
        tools_tried=[],
    )
    prompt = " ".join(m.content for m in msgs).lower()
    # the hand-back shape must be described in the give-up criteria
    assert "manual steps" in prompt or "do it themselves" in prompt or "instead of performing" in prompt
