"""Journey-suite fixtures.

A journey simulates a real turn arriving through the gateway, and in production a
real turn always arrives on a channel the gateway holds an adapter for — measured
2026-08-21: `telegram` with 585 registrations, plus `cli` since ESC-30. A journey
that registers nothing is asserting against a world that does not exist.

That matters because `0f1431e9` made `owl_build` always-ask, so an owl build with
no official origin is refused before anything else happens:

    ✗ /owl create: refused: no consent gate available to approve building owl 'rsr'

`_official_origin` is IMPORTED from the meta-tool conftest, not restated — it is
the same rule, it carries the reasoning at its definition, and it already provides
the `@pytest.mark.no_official_origin` opt-out for a test that is genuinely ABOUT
consent rather than about the thing consent is gating.

FOURTH DIRECTORY TO NEED IT, and that is the finding rather than the fix. The same
regression has now surfaced in tests/tools/meta (15 tests), tests/acceptance (1),
and here (2) — each time because the fixture was scoped to a directory while the
rule it compensates for is global. Scoping a compensation narrower than the thing
it compensates for guarantees this repeats; the honest read is that the next suite
to build an owl will need it too.
"""

from __future__ import annotations

from tests.tools.meta.conftest import _official_origin  # noqa: F401
