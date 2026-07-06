"""dry_run must return from run() normally — never hit the os._exit(0) guard.

Root cause context: a prior boot logged "shutdown complete" while the OS
process never actually exited (a stuck native/executor thread outlived our
own teardown). The fix forces os._exit(0) right after run()'s normal
completion so the process can't hang past the point we've already cleaned up
everything we care about. That call is gated on `not self._dry_run` — if the
gate is ever removed or inverted, this test (which calls the REAL run() in
dry_run mode, in-process) would kill the pytest worker instead of returning,
so a regression here fails loudly rather than silently.
"""

import pytest

from stackowl.startup.orchestrator import StartupOrchestrator


@pytest.mark.asyncio
async def test_dry_run_returns_normally_without_process_exit():
    orch = StartupOrchestrator(dry_run=True)
    await orch.run()  # would kill the test process if the dry_run guard broke
    assert True
