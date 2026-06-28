"""Acceptance-suite fixtures.

Re-export the dir-scoped ``tmp_home`` / ``_live_io`` fixtures from the meta-tool
conftest so the trust acceptance gate can drive the GENUINE ``OwlBuildTool``
against a real home/registry without rebuilding the harness.
"""

from __future__ import annotations

from tests.tools.meta.conftest import _live_io, tmp_home  # noqa: F401
