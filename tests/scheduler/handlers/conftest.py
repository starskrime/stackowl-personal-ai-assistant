"""Scheduler-handler-suite fixtures.

Re-export the dir-scoped ``tmp_home`` / ``_live_io`` fixtures from the
meta-tool conftest (mirrors tests/acceptance/conftest.py) so
test_rca_verdict_router.py can drive the GENUINE tool_build gate (real
persisted spec file under an isolated STACKOWL_HOME) without rebuilding the
harness.
"""

from __future__ import annotations

from tests.tools.meta.conftest import _live_io, tmp_home  # noqa: F401
