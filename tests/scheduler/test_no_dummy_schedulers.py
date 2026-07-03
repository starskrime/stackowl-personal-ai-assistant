"""Guardrail: every persistent while-True/asyncio.sleep poll loop outside
scheduler/ must be an explicitly reasoned infra-liveness exception, never an
undeclared business-domain timer bypassing JobScheduler."""

from __future__ import annotations

import re
from pathlib import Path

from tests.scheduler.scheduler_timer_allowlist import INFRA_TIMER_ALLOWLIST

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "stackowl"
_SLEEP_RE = re.compile(r"asyncio\.sleep\(")
_WHILE_TRUE_RE = re.compile(r"while\s+True\s*:")


def _files_with_persistent_poll_loop() -> set[str]:
    """A file counts as a persistent poll loop only if it contains BOTH
    ``while True:`` AND ``asyncio.sleep(`` — the actual signature of a
    long-lived background timer, not any bounded wait or algorithmic loop."""
    hits: set[str] = set()
    for path in _SRC_ROOT.rglob("*.py"):
        if "scheduler" in path.relative_to(_SRC_ROOT).parts:
            continue  # the one true authority is exempt from its own check
        text = path.read_text(encoding="utf-8")
        if _WHILE_TRUE_RE.search(text) and _SLEEP_RE.search(text):
            hits.add(str(path.relative_to(_REPO_ROOT)))
    return hits


def test_every_persistent_poll_loop_is_scheduler_or_allowlisted():
    found = _files_with_persistent_poll_loop()
    allowlisted = set(INFRA_TIMER_ALLOWLIST)
    unexplained = found - allowlisted
    assert not unexplained, (
        "New persistent background poll loop(s) found outside "
        "src/stackowl/scheduler/ with no allowlist entry — either route this "
        "through JobScheduler (a proper handler + jobs row) or, if it's a "
        "genuine infra-liveness exception (must survive the scheduler itself "
        "hanging, or a channel-protocol requirement), add it to "
        f"INFRA_TIMER_ALLOWLIST with a one-line reason: {unexplained}"
    )
    stale = allowlisted - found
    assert not stale, (
        f"Allowlist entries no longer match any file — remove stale entries: {stale}"
    )
