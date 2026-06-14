from stackowl.tools._infra.presentation import ToolPresentation


class _FakeManifest:
    def __init__(self, group): self.toolset_group = group


class _FakeTool:
    def __init__(self, name, group="misc", desc=""):
        self.name = name
        self._g = group
        self.description = desc
        self.manifest = _FakeManifest(group)


def _tools():
    return [
        _FakeTool("read_file", "io", "read a file"),
        _FakeTool("write_file", "io", "write a file"),
        _FakeTool("tool_search", "meta"),
        _FakeTool("send_email", "comms", "send an email message"),
        _FakeTool("web_search", "search", "search the web"),
        _FakeTool("calendar_create", "calendar", "create a calendar event"),
    ]


def test_no_profile_makes_all_non_guaranteed_discretionary():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None, request_text="hello",
    )
    gnames = {t.name for t in guaranteed}
    assert "read_file" in gnames and "tool_search" in gnames
    dnames = {t.name for t in disc}
    assert {"send_email", "web_search", "calendar_create"} <= dnames


def test_relevance_ranks_request_matched_tool_first():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        request_text="please send an email to my boss",
    )
    assert disc[0].name == "send_email"


def test_unmatched_tools_kept_in_deterministic_tail():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        request_text="xyzzy-no-match",
    )
    names = [t.name for t in disc]
    assert names == sorted(names)
    assert {"send_email", "web_search", "calendar_create"} <= set(names)
