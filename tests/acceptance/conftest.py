"""Acceptance-suite fixtures.

Re-export the dir-scoped ``tmp_home`` / ``_live_io`` fixtures from the meta-tool
conftest so the trust acceptance gate can drive the GENUINE ``OwlBuildTool``
against a real home/registry without rebuilding the harness.

``_official_origin`` COMES WITH THEM, and it was missing. Commit `0f1431e9` made
`owl_build` always-ask, which broke every test constructing
``ConsentPolicy(tiers={"owl_build": TrustTier.AUTO})``; the fix — an autouse
fixture registering a `cli` gateway channel, because production has one — was put
in `tests/tools/meta/conftest.py` and therefore covered that DIRECTORY. This file
reaches ACROSS directories for the helper (`test_trust_acceptance` imports
`_proven_real_create` straight out of `tests/tools/meta/test_owl_build_schedule`),
so it inherited the helper and not the world the helper needs.

`test_eval2_creation_truth` was red for three days as a result, and a whole-suite
run in `tests/tools/meta` could never have shown it. The fixture is IMPORTED
rather than restated: one rule, one source, and the reasoning stays where the
regression is documented.
"""

from __future__ import annotations

from tests.tools.meta.conftest import _live_io, _official_origin, tmp_home  # noqa: F401
