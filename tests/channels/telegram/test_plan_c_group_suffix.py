from stackowl.channels.telegram.helpers import strip_command_bot_suffix


def test_strips_bot_suffix_from_command():
    assert strip_command_bot_suffix("/help@StackOwlBot", "StackOwlBot") == "/help"
    assert strip_command_bot_suffix("/cost@StackOwlBot 30d", "StackOwlBot") == "/cost 30d"


def test_strips_bot_suffix_case_insensitive():
    assert strip_command_bot_suffix("/help@stackowlbot", "StackOwlBot") == "/help"
    assert strip_command_bot_suffix("/cost@STACKOWLBOT 30d", "StackOwlBot") == "/cost 30d"


def test_leaves_non_command_text_untouched():
    assert strip_command_bot_suffix("email me at a@StackOwlBot", "StackOwlBot") == "email me at a@StackOwlBot"
    assert strip_command_bot_suffix("/help", "StackOwlBot") == "/help"
    assert strip_command_bot_suffix("/help@StackOwlBot", "") == "/help@StackOwlBot"  # no username -> untouched
    assert strip_command_bot_suffix("/help@StackOwlBot", None) == "/help@StackOwlBot"
