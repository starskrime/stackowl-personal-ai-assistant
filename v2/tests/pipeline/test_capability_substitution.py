from stackowl.pipeline.capability_substitution import normalized_input_for, build_args_for


def test_normalized_input_from_failed_browse():
    failed_args = {"task": "find the weather", "seed_url": "https://x.example"}
    ni = normalized_input_for("browser_browse", failed_args)
    assert ni == {"url": "https://x.example", "query": "find the weather"}


def test_normalized_input_from_failed_browse_no_seed():
    ni = normalized_input_for("browser_browse", {"task": "find the weather"})
    assert ni == {"url": "", "query": "find the weather"}  # no url available


def test_build_args_for_web_fetch_uses_url():
    assert build_args_for("web_fetch", {"url": "https://x.example", "query": "q"}) == {"url": "https://x.example"}


def test_build_args_for_web_search_uses_query():
    assert build_args_for("web_search", {"url": "https://x.example", "query": "q"}) == {"query": "q"}


def test_build_args_returns_none_when_unservable():
    # web_fetch needs a url; none available -> cannot serve.
    assert build_args_for("web_fetch", {"query": "q", "url": ""}) is None


def test_build_args_web_search_needs_query():
    # web_search needs a query; none -> cannot serve.
    assert build_args_for("web_search", {"url": "https://x", "query": ""}) is None


def test_unknown_tool_normalized_input_is_none_or_empty():
    # a tool with no declared adapter -> normalized_input_for returns None (no adapter)
    assert normalized_input_for("some_unknown_tool", {"a": 1}) is None
