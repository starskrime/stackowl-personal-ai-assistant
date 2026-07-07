from __future__ import annotations

from stackowl.commands.response import CANCEL_SENTINEL, Action, CommandResponse, make_confirm_response


def test_command_response_defaults_to_no_actions():
    resp = CommandResponse(text="hello")
    assert resp.actions == ()


def test_action_destructive_defaults_false():
    action = Action(label="Remove", command="/provider remove acme")
    assert action.destructive is False


def test_make_confirm_response_builds_yes_cancel():
    action = Action(label="Remove", command="/provider remove acme", destructive=True)
    confirm = make_confirm_response(action)

    assert len(confirm.actions) == 2
    yes, cancel = confirm.actions
    assert yes.command == "/provider remove acme"
    assert yes.destructive is False  # tapping Yes must execute, not re-confirm
    assert cancel.command == CANCEL_SENTINEL
