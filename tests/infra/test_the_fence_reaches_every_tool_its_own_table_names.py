"""D17.2 — the fence reached three of the four tools its own evidence names.

`infra/untrusted.py` exists because the marker was wired on only some paths: a
scanned PDF was fenced and a fetched web page was not. Its module docstring
carries the measurement that justified it — 974 turns over 7 days, 66 of which
fetched external content and used a powerful tool in the same turn:

    web_fetch        + write_file  35      web_fetch        + shell  24
    web_search       + write_file  31      browser_navigate + shell  23
    browser_navigate + write_file  27      browser_extract  + shell  13

FOUR TOOLS ARE NAMED THERE. Measured 2026-09-05, two of them fenced:

    web_fetch          untrusted.wrap  ✓
    browser_extract    untrusted.wrap  ✓
    web_search         —               ✗
    browser_navigate   —               ✗

`web_search` serialises the whole result payload — third-party titles, snippets
and URLs — into `output` as JSON. `browser_navigate` returns
`await page.title()`, a string the visited site chooses. Both are text an
attacker can author, arriving unmarked.

SAME RULE, ONE TOOL SHORT — twice, in the module written to fix that exact shape,
with the evidence naming the missing tools in its own docstring.

THIS IS A MARKER, NOT A CONTROL, and that distinction is inherited deliberately
from D12.8: narrowing the toolset after a fetch would break the same 66 turns
that are legitimate work. Which capabilities may survive contact with untrusted
input is the operator's call (ESC-110). What ships here is the visibility every
later policy needs, over all four entry points instead of two.

NO PATTERN LIBRARY. The reference platform answers this with `threat_patterns.py`
— regexes by attack class. That is a keyword list, which is banned here on a
standing rule and would be monolingual besides. The fence is structural: it marks
WHERE text came from and never guesses what the text means.
"""

from __future__ import annotations

import pathlib

import pytest

from stackowl.infra import untrusted

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"

#: The four tools named in `untrusted.py`'s own measurement table, and the module
#: that must fence each. The table is the source: a tool that appears there is by
#: definition a path external content takes into the model.
_MUST_FENCE = {
    "web_fetch": "tools/io/web_fetch.py",
    "web_search": "tools/search/web_search.py",
    "browser_navigate": "tools/browser/tools.py",
    "browser_extract": "tools/browser/tools.py",
}


@pytest.mark.tripwire
@pytest.mark.parametrize(("tool", "module"), sorted(_MUST_FENCE.items()))
def test_every_named_entry_point_fences(tool: str, module: str) -> None:
    src = (_SRC / module).read_text(encoding="utf-8")

    assert "untrusted.wrap(" in src, f"{module} never fences anything"
    assert f"source=f\"{tool}:" in src or f'source="{tool}' in src or f"{tool}:" in src, (
        f"{module} fences, but not with a source naming {tool} — 'untrusted' alone "
        "tells a reader nothing actionable; the source is where to go and look"
    )


@pytest.mark.tripwire
def test_the_table_and_this_list_name_the_same_tools() -> None:
    """One source. If a fifth tool is added to the measurement, it must be fenced;
    if one is removed, this list must shrink. Either drift and the guard is a lie."""
    doc = untrusted.__doc__ or ""

    for tool in _MUST_FENCE:
        assert tool in doc, (
            f"{tool} is guarded here but no longer named in untrusted.py's table — "
            "the guard has outlived its evidence"
        )


def test_the_fence_is_structural_and_names_no_patterns() -> None:
    """The divergence from the reference platform, pinned.

    Theirs is a regex library by attack class. Ours must never grow one: a keyword
    list is banned on a standing rule, and it is monolingual by construction. The
    fence marks WHERE text came from and never guesses what it means.
    """
    src = (_SRC / "infra" / "untrusted.py").read_text(encoding="utf-8")

    assert "re.compile" not in src, "the fence must not acquire a pattern library"
    assert "ignore previous" not in src.lower()
    assert "jailbreak" not in src.lower()


def test_wrapping_is_idempotent_across_the_new_call_sites() -> None:
    """Two fences around one body would show the model two openings for one
    payload. Already-fenced text is returned unchanged — asserted here because
    two more callers now share that guarantee."""
    once = untrusted.wrap("hello", source="web_search:q")
    twice = untrusted.wrap(once, source="web_search:q")

    assert once == twice
    assert once.count(untrusted.OPEN_MARK) == 1
