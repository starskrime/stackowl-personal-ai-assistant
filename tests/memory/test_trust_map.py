from stackowl.memory.trust import trust_for_source, SAFE_DEFAULT


def test_external_sources_untrusted():
    assert trust_for_source("webpage") == "untrusted"
    assert trust_for_source("screenshot") == "untrusted"


def test_owl_authored_is_self():
    assert trust_for_source("parliament") == "self"
    assert trust_for_source("agent_self") == "self"
    assert trust_for_source("conversation") == "self"
    assert trust_for_source("conversation_fact") == "self"


def test_human_manual_is_trusted():
    assert trust_for_source("manual") == "trusted"


def test_unknown_source_fails_safe_untrusted():
    assert trust_for_source("some_future_source") == SAFE_DEFAULT == "untrusted"
