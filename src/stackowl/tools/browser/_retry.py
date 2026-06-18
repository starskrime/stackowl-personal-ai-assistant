"""Browser-specific alias of the generic resilience retry helper.

Kept as a thin module so call sites read naturally
(``await with_browser_retry(_do, runtime, op_name="web_fetch")``) without
having to import the cross-package ``retry_once_on_dead_handle`` name in
every browser tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from stackowl.infra.resilience import retry_once_on_dead_handle

if TYPE_CHECKING:
    from stackowl.tools.browser.runtime import CamoufoxRuntime


async def with_browser_retry[T](
    op: Callable[[], Awaitable[T]],
    runtime: CamoufoxRuntime,
    *,
    op_name: str,
) -> T:
    """Run ``op``; recycle the browser runtime and retry once if it dies mid-flight.

    ``op`` must re-acquire fresh ``BrowserContext`` / ``Page`` handles on each
    call — the first attempt's handles are presumed dead on retry.
    """
    return await retry_once_on_dead_handle(op, runtime, op_name=op_name)
