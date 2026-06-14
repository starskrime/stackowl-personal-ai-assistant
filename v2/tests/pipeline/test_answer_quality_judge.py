from stackowl.pipeline.persistence import _build_messages


def _prompt(req="hello", draft="hi there", tools=None) -> str:
    msgs = _build_messages(req, draft, tools or [])
    return "\n".join(m.content for m in msgs).lower()


def test_prompt_has_needs_external_action_gate():
    p = _prompt()
    assert "external action" in p
    assert "directly" in p or "knowledge" in p


def test_prompt_accepts_tool_free_reply_to_no_action_request():
    p = _prompt()
    assert "no-action" in p or "no tools is correct" in p or "tool-free reply" in p


def test_prompt_preserves_dressed_up_giveup_shape():
    p = _prompt()
    assert "failed" in p and "not delivery" in p
    assert "manual steps" in p or "hands the task back" in p
    assert "no command was run" in p or "never ran a command" in p


def test_prompt_scopes_giveup_to_action_requests():
    p = _prompt()
    assert "requires an external action" in p or "needs an external action" in p


def test_schema_instruction_unchanged():
    p = _prompt()
    assert '{"delivered"' in p or '"delivered": true' in p
