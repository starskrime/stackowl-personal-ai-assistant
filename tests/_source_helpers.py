"""A read that lands on the wrong function must say so.

``inspect.getsource(fn)`` seeks to ``fn.__code__.co_firstlineno`` — a line number
frozen when the module was imported — and returns the block it finds there in the
file as it is NOW. If the file grew or shrank in between, the read silently
returns a different function's body.

That is not theoretical. On 2026-09-04 a 30-minute detached suite reported three
failures in the canonical-dedup tests, asserting a dedup rung had been dropped;
the text they had read was the body of ``discover``. The tree had gained ~97
lines under the running suite.

139 call sites across 82 test files read source this way, asserting short tokens
against large modules. The false-RED costs a diagnosis. The false-GREEN — a
shifted read landing on text that happens to contain the token — costs a guard
that everyone believes is holding.

Installed over ``inspect.getsource`` for the whole test session in ``conftest``,
so all 139 sites are covered without editing any of them. It FAILS OPEN by
design: it raises only when it can positively identify a name mismatch, because
pytest itself reads source while reporting, and a guard that raises inside the
runner is worse than the bug it prevents.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

_real_getsource = inspect.getsource

#: Decorator lines ARE part of a function's source, so skip them to reach the
#: `def`. Blank lines likewise. Comments are NOT skipped: `inspect.getblock`
#: starts at the declaration it found, so a block that BEGINS with a comment did
#: not start at a declaration — and that is the symptom, not noise. The
#: 2026-09-04 read began with `log.skills.info(`; a comment-skipping guard walked
#: straight past 40 junk lines to a `def` that was never the one asked for.
_SKIP = re.compile(r"^\s*(@|$)")

#: The read did not begin at any declaration at all.
_NOT_A_DECLARATION = ("", "")


def _declared_name(src: str) -> tuple[str, str] | None:
    """Return ``(kind, name)`` for the declaration ``src`` BEGINS with.

    ``_NOT_A_DECLARATION`` when the first meaningful line is not a declaration —
    a read that landed mid-body. ``None`` only when there is nothing to judge.
    """
    for line in src.splitlines():
        if _SKIP.match(line):
            continue
        m = re.match(r"\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)", line)
        return (m.group(1), m.group(2)) if m else _NOT_A_DECLARATION
    return None


def guarded_getsource(obj: Any) -> str:
    """``inspect.getsource``, plus: the block returned must declare ``obj``."""
    src = _real_getsource(obj)

    expected = getattr(obj, "__name__", None)
    if not isinstance(expected, str):
        return src  # a module, a frame, anything unnamed — not ours to judge
    if inspect.ismodule(obj):
        return src

    declared = _declared_name(src)
    if declared is None:
        return src  # fail open: we could not read it, so we do not judge it
    kind, name = declared
    if name == expected:
        return src
    if declared == _NOT_A_DECLARATION:
        kind, name = "text that is not a declaration at all", "<mid-body>"
    # A partial like functools.partial, or a wrapper whose __name__ was copied
    # from the function it wraps, is a legitimate mismatch. Only complain when
    # the object itself is the thing we can locate in a file.
    if not (inspect.isfunction(obj) or inspect.ismethod(obj) or inspect.isclass(obj)):
        return src

    raise AssertionError(
        f"inspect.getsource({expected!r}) returned the source of {kind} {name!r}.\n"
        f"The line numbers are from the module as IMPORTED; the text is from the "
        f"file as it is NOW. Something changed {getattr(obj, '__module__', '?')} "
        f"on disk after this session imported it — most often an edit made while a "
        f"detached suite was running.\n"
        f"The assertion that follows would have been about the wrong function, in "
        f"either direction. Re-run against a still tree before believing any "
        f"source-reading result from this session."
    )


def install() -> None:
    """Route every ``inspect.getsource`` call in the session through the guard."""
    inspect.getsource = guarded_getsource
