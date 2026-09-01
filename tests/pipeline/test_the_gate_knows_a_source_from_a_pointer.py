"""A page you browsed IS a source. A search hit you never opened is NOT.

BAKIR SENT THE PLATFORM'S OWN VERDICT on fabricated citations: "links were cited
in the reply without being retrieved in this run... the unverified state was only
acknowledged after the fact." The grounding gate that should have prevented that
had TWO faults, opposite in direction and identical in cause.

MEASURED 2026-09-01 over 206 turns that used a retrieval tool in seven days::

    145 (70.4%)  web_fetch present          the gate worked
     53 (25.7%)  browser retrieval only     INVISIBLE to the gate
      8 ( 3.9%)  web_search only, no fetch  cited without being retrieved

THE FALSE POSITIVE, AND IT IS OBSERVED, NOT INFERRED. ``_RETRIEVAL_TOOLS`` was
``{"web_search", "web_fetch"}``. ``browser_navigate`` genuinely retrieves a page
and was not in it, so a quarter of all retrieval was unknown to the gate. FOUR
turns are in the log with ``browser_navigate`` + ``browser_extract`` in their
tool sequence and BOTH ``grounding.fabricated_citations`` and
``grounding.stripped`` against them — including a ``recover-task`` trace running
the whole browser stack. The platform browsed to those pages, extracted their
content, cited them, and the gate deleted the citations.

THE FALSE NEGATIVE, AND IT IS THE ONE HE REPORTED. Every URL in a ``web_search``
envelope was added to the fetched set, so citing a search hit passed the gate.
But a search result is the ENGINE'S CLAIM ABOUT a page, not evidence that anyone
opened it — which is exactly "cited without being retrieved in this run".

ONE CAUSE: the gate's notion of "retrieved" was a hardcoded tool-name list and a
per-tool shape guess, rather than a measured fact about what content came back.
Its own comment admitted the drift ("ponytail: extend this set if a new
first-class retrieval tool lands") and it had already drifted.

A HYPOTHESIS THIS TEST DELIBERATELY RECORDS AS REFUTED. The floor text —
"I couldn't verify sources for this — I didn't actually retrieve it, so I can't
stand behind those links. Want me to look it up properly?" — is word for word
what the RCA quoted as the model's own admission, so it looked like the floor
was firing wrongly on browser turns. It was not: all 18 ``floored_no_sources``
traces that could be matched to an outcome row used NO retrieval tool at all.
Those floors were correct. The harm is the STRIPPING, not the flooring.

MARKED, NOT STRIPPED, for the pointer case. That is the platform's own
recommended fix — "label the claim inline where it appears, rather than
disclosing unverified-ness after the links are already in the reply" — and it
costs a good answer nothing.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from stackowl.pipeline.delivery_gate import (
    UNVERIFIED_MARK,
    _fetched_source_set,
    _mark_unopened_urls,
    _pointer_source_set,
    _retrieved_source_set,
)

_PAGE = "https://careers.moove.io/jobs"
_HIT = "https://example.com/listing/1"


def _call(name: str, *, url: str = "", result: str = "", error: str | None = None):  # noqa: ANN202
    return SimpleNamespace(
        tool_name=name, args={"url": url} if url else {}, result=result, error=error,
    )


def _state(*calls: object):  # noqa: ANN202
    return SimpleNamespace(tool_calls=list(calls), trace_id="t-1")


# --------------------------------------------------------------------------- #
# A browsed page is a retrieved source                                         #
# --------------------------------------------------------------------------- #


def test_a_navigated_page_counts_as_retrieved() -> None:
    """The observed defect: four turns browsed to pages and had the citations
    stripped because browser_navigate was not in the vocabulary."""
    assert _retrieved_source_set(_state(_call("browser_navigate", url=_PAGE))) == {_PAGE}


def test_a_failed_navigation_is_not_a_source() -> None:
    """The expensive direction. A navigation that errored retrieved nothing, and
    counting it would let the gate bless a page nobody ever saw."""
    assert _retrieved_source_set(
        _state(_call("browser_navigate", url=_PAGE, error="net::ERR_ABORTED"))
    ) == set()


def test_web_fetch_still_counts() -> None:
    assert _retrieved_source_set(_state(_call("web_fetch", url=_PAGE))) == {_PAGE}


# --------------------------------------------------------------------------- #
# A search hit is a pointer, not a source                                      #
# --------------------------------------------------------------------------- #


def _search(*urls: str):  # noqa: ANN202
    return _call("web_search", result=json.dumps({"data": {"web": [{"url": u} for u in urls]}}))


def test_a_search_hit_is_a_pointer_not_a_retrieval() -> None:
    """The fault Bakir reported: citing this passed the gate as though the page
    had been read."""
    state = _state(_search(_HIT))
    assert _pointer_source_set(state) == {_HIT}
    assert _retrieved_source_set(state) == set()


def test_a_pointer_is_still_citable() -> None:
    """It is a real URL from a real search — not fabricated, so never stripped.
    The fabrication test's denominator must keep including it."""
    assert _HIT in _fetched_source_set(_state(_search(_HIT)))


def test_opening_a_pointer_promotes_it() -> None:
    """Search then fetch is the honest path and must read as fully retrieved."""
    state = _state(_search(_HIT), _call("web_fetch", url=_HIT))
    assert _retrieved_source_set(state) == {_HIT}


# --------------------------------------------------------------------------- #
# Marked where it appears, not disclosed afterwards                            #
# --------------------------------------------------------------------------- #


def test_an_unopened_link_is_marked_in_place() -> None:
    text = f"Two roles: [Backend]({_HIT}) looks closest."
    marked = _mark_unopened_urls(text, {_HIT})
    assert marked == f"Two roles: [Backend]({_HIT}){UNVERIFIED_MARK} looks closest."


def test_a_bare_unopened_url_is_marked() -> None:
    marked = _mark_unopened_urls(f"See {_HIT} for details.", {_HIT})
    assert UNVERIFIED_MARK in marked and _HIT in marked


def test_a_retrieved_link_is_left_alone() -> None:
    """Marking a page the platform actually read would be a lie in the other
    direction, and would train the reader to ignore the mark."""
    text = f"[Careers]({_PAGE}) has three openings."
    assert _mark_unopened_urls(text, {_HIT}) == text


def test_marking_is_idempotent() -> None:
    """A second gate pass, or a retry that re-renders the draft, must not stack
    the label."""
    once = _mark_unopened_urls(f"See {_HIT}.", {_HIT})
    assert _mark_unopened_urls(once, {_HIT}) == once
    assert once.count(UNVERIFIED_MARK) == 1


def test_nothing_unopened_is_byte_identical() -> None:
    text = f"[Careers]({_PAGE}) has three openings."
    assert _mark_unopened_urls(text, set()) == text


def test_the_mark_carries_no_markdown() -> None:
    """It has to survive every channel converter intact — a marker that renders
    as literal asterisks on Telegram is the bug this platform spent a night on."""
    assert not set(UNVERIFIED_MARK) & set("*_`#[]")


def test_the_vocabulary_is_split_not_merged() -> None:
    """Structural: collapsing these back into one set is the defect returning,
    in whichever direction the collapse happens."""
    from stackowl.pipeline.delivery_gate import (
        _CONTENT_RETRIEVAL_TOOLS,
        _POINTER_TOOLS,
        _RETRIEVAL_TOOLS,
    )

    assert "browser_navigate" in _CONTENT_RETRIEVAL_TOOLS
    assert "web_search" in _POINTER_TOOLS
    assert "web_search" not in _CONTENT_RETRIEVAL_TOOLS, (
        "a search hit is being treated as a retrieved page again — this is the "
        "'cited without being retrieved' defect"
    )
    assert _RETRIEVAL_TOOLS == _CONTENT_RETRIEVAL_TOOLS | _POINTER_TOOLS
