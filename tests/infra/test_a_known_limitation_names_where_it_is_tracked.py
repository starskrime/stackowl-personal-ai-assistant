"""`ssrf_guard.py` said "Known limitation (tracked)". Nothing tracked it.

MEASURED 2026-09-05 while verifying D17.4. The module docstring read:

    Known limitation (tracked): this validates at call time. A TTL-0 rebind
    between this check and the socket connect, and per-redirect re-validation,
    need a pinned resolver / proxy egress — a fast-follow for the fetch layer.

Two things were wrong with it.

**"(tracked)" was false.** `progress.yml` mentioned rebind, pinned resolver or
proxy egress ZERO times, and so did every document under `docs/reference-mapping`.
The word promised a referent that did not exist, which is worse than admitting the
gap: a reader checks the queue, finds nothing, and concludes it was handled.

**"per-redirect re-validation" was already done.** `web_fetch` routes through
Playwright with `guard_playwright_navigation`, whose own docstring is "re-validate
every navigation/redirect hop", and `tests/infra/
test_the_ssrf_guard_is_callable_as_a_route_handler.py` pins it. Half the stated
limitation had been closed and the comment still claimed it.

So this guard is narrow on purpose: **if that paragraph claims something is
tracked, the identifier it names must exist in `progress.yml`.** It does not
police every comment in the tree — no sound predicate could tell a real tracking
claim from the word "tracked" used in passing — it polices the one paragraph that
was found lying.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GUARD = _ROOT / "src" / "stackowl" / "infra" / "net" / "ssrf_guard.py"
_PROGRESS = _ROOT / "progress.yml"

_ID_RE = re.compile(r"\b(DEBT-\d+|ESC-\d+|ESC_\d+)\b")


def _known_limitation_paragraph() -> str:
    doc = _GUARD.read_text(encoding="utf-8")
    start = doc.index("Known limitation")
    end = doc.index('"""', start)
    return doc[start:end]


@pytest.mark.tripwire
def test_the_limitation_names_a_tracking_id_that_exists() -> None:
    para = _known_limitation_paragraph()

    ids = _ID_RE.findall(para)
    assert ids, (
        "the known-limitation paragraph claims tracking but names no DEBT-/ESC- id. "
        "'(tracked)' with no referent is worse than an admitted gap: a reader checks "
        "the queue, finds nothing, and concludes it was handled."
    )

    progress = _PROGRESS.read_text(encoding="utf-8")
    for ident in ids:
        # AN ENTRY, NOT A SUBSTRING — and the first version of this check made
        # exactly that mistake. It asserted `ident in progress` and PASSED for
        # DEBT-117, which appears in progress.yml only inside another item's
        # sentence "recorded as DEBT-117" — an id that was referenced and never
        # written. Presence of a token is not existence of the thing, which is the
        # same weakness this session has now found three times in other guards.
        if ident.startswith("DEBT"):
            assert f"- id: {ident}\n" in progress, (
                f"{ident} is named in ssrf_guard.py but is not a known_debt entry. "
                "A dangling id reads as tracked and is not."
            )
        else:
            assert ident.replace("-", "_") in progress or ident in progress, (
                f"{ident} is named in ssrf_guard.py but not found in progress.yml"
            )


@pytest.mark.tripwire
def test_the_paragraph_does_not_still_claim_the_closed_half() -> None:
    """Per-redirect re-validation IS implemented, at the Playwright route layer.

    A limitation paragraph that lists a solved problem sends the next reader to
    build something that exists — the same cost as a superseded finding left in
    the present tense.
    """
    para = _known_limitation_paragraph()

    assert "per-redirect re-validation" not in para.lower(), (
        "guard_playwright_navigation re-validates every navigation/redirect hop, "
        "and a test pins it — this half is closed"
    )


def test_the_route_handler_that_closed_it_still_exists() -> None:
    """The claim above is only safe while the thing that closed it is there."""
    from stackowl.infra.net import ssrf_guard

    assert hasattr(ssrf_guard, "guard_playwright_navigation")
    assert "re-validate" in (ssrf_guard.guard_playwright_navigation.__doc__ or "").lower()
