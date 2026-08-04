"""D05.6 — cross-tool reference stripping.

The premise is measured, not assumed: 40 of 77 schemas name another tool, and 31
of those references can genuinely dangle (referrer presented, target absent).
This only became a live problem when D05.3 made tools actually disappear.
"""

from __future__ import annotations

from stackowl.tools._infra.cross_refs import strip_dangling_references
from stackowl.tools.registry import ToolRegistry

_CATALOG = frozenset({"edit", "transcripts", "memory", "browser_click", "browser_snapshot"})
_NO_CAPS: dict[str, str | None] = dict.fromkeys(_CATALOG, None)


def _strip(desc, *, tool="undo_write", presented=frozenset(), caps=None):
    return strip_dangling_references(
        desc, tool_name=tool, presented=presented,
        catalog=_CATALOG, capability_of=caps or {**_NO_CAPS, tool: None},
    )


def test_a_sentence_naming_an_absent_tool_is_dropped():
    out = _strip("Undo the last write. Restores what edit changed.")
    assert out == "Undo the last write."


def test_a_sentence_naming_a_PRESENT_tool_is_kept():
    out = _strip("Undo the last write. Restores what edit changed.",
                 presented=frozenset({"edit"}))
    assert "edit" in out


def test_an_unrelated_word_that_is_not_a_registered_tool_is_ignored():
    """Only names in the CATALOG count. Otherwise ordinary English — 'process',
    'wait', 'memory' are all real tool names — would strip sentences that were
    never references."""
    out = strip_dangling_references(
        "Runs a job. It will edit nothing and wait quietly.",
        tool_name="x", presented=frozenset(), catalog=frozenset({"nonexistent"}),
        capability_of={},
    )
    assert out == "Runs a job. It will edit nothing and wait quietly."


def test_a_substring_match_does_not_count():
    """'edited' must not match the tool 'edit' — word boundaries only."""
    out = _strip("Undo the last write. The file was edited earlier.")
    assert "edited earlier" in out


def test_same_capability_references_are_exempt():
    """browser_click and browser_snapshot are both gated on 'browser', so they
    are presented together or not at all — the hint can never dangle."""
    caps = {**_NO_CAPS, "browser_click": "browser", "browser_snapshot": "browser"}
    out = strip_dangling_references(
        "Click an element. Call browser_snapshot first to get a ref.",
        tool_name="browser_click", presented=frozenset(), catalog=_CATALOG,
        capability_of=caps,
    )
    assert "browser_snapshot" in out


def test_a_different_capability_is_NOT_exempt():
    caps = {**_NO_CAPS, "browser_snapshot": "browser"}
    out = strip_dangling_references(
        "Do a thing. Call browser_snapshot first.",
        tool_name="other", presented=frozenset(), catalog=_CATALOG,
        capability_of=caps,
    )
    assert "browser_snapshot" not in out


def test_the_description_is_never_emptied():
    """If EVERY sentence referenced an absent tool, the original is less wrong
    than nothing: the description is what tool_search ranks on and what the model
    chooses by."""
    out = _strip("Restores what edit changed.")
    assert out == "Restores what edit changed."


def test_nothing_dangling_returns_the_string_unchanged():
    """Identity matters — D05.2 memoizes this output, and a needless rewrite
    would be a pointless cache difference."""
    original = "Undo the last write. Nothing else."
    assert _strip(original, presented=_CATALOG) is original


def test_an_empty_description_is_handled():
    assert _strip("") == ""


# --------------------------------------------------------------------------- #
# End-to-end through the real registry — the wiring, not just the function.
# --------------------------------------------------------------------------- #


def test_the_full_catalog_keeps_every_hint():
    schemas = ToolRegistry.with_defaults().to_provider_schema("anthropic")
    by = {s["name"]: s["description"] for s in schemas}
    assert "edit" in by["undo_write"], "nothing should be stripped when all tools are present"
    assert "transcripts" in by["read_logs"]


def test_a_narrow_presented_set_strips_the_dangling_hints():
    """THE WIRING TEST. undo_write is in the never-evicted base set and names
    `edit`, which is NOT — so on a narrow profile the model would be told to rely
    on a tool it cannot see."""
    reg = ToolRegistry.with_defaults()
    schemas = reg.to_provider_schema(
        "anthropic", profile=[], pins=["undo_write", "read_logs"], hydrated=None,
    )
    by = {s["name"]: s["description"] for s in schemas}
    assert "edit" not in set(by), "test premise: edit must be absent here"

    # The ROUTING sentence is gone: "Restores in place; confined to prior
    # snapshots taken by edit/patch tools."
    assert "confined to prior snapshots" not in by["undo_write"]
    assert "transcripts" not in by["read_logs"]

    # ...but the DEFINING first sentence survives, even though it happens to
    # contain "edit" inside the prose "write/edit". This assertion is the point
    # of the first-sentence rule: an earlier version of the stripper deleted this
    # sentence and left undo_write describing only its 'token' argument, with no
    # statement of what the tool does.
    assert "Undo the most recent file write" in by["undo_write"]


def test_the_first_sentence_is_never_dropped_even_when_it_names_an_absent_tool():
    """Discovered by a test, not by design. Losing a routing hint is the accepted
    cost of sentence-level stripping; losing the tool's definition is not."""
    out = _strip("Undo the last write/edit by restoring it. Restores what edit changed.")
    assert out == "Undo the last write/edit by restoring it."
