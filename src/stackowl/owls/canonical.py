"""ONE resolver from whatever the model typed to a canonical owl name.

ESC-38. The model refers to an owl by whatever it has seen — usually the DISPLAY
name, because that is what the prompt shows it. Nothing translated that back, so a
name that looked perfectly reasonable to the model created a brand-new file that
nothing ever reads.

MEASURED 2026-08-23 — 8 of the 13 files in ~/.stackowl/memory/ are orphans, and
the reason is visible once the display names are lined up beside them:

    Collector.md          -> `archivist`   (Collector is archivist's display_name)
    Falcon.md, falcon.md  -> `scout`       (Falcon is scout's display_name)
    agent.md, owl.md      -> no owl at all; the model wrote a generic word
    Brain.md              -> no owl, no display name
    sysdesign.md          -> an owl that no longer exists
    sysfup.md             -> an owl that no longer exists

`CuratedMemory.path_for` cannot catch any of this and should not try: it validates
the SHAPE of a filename so a name with a separator cannot write outside the memory
directory. `Collector` is a perfectly safe filename. Safety and identity are
different questions, and this module answers the second one so that each layer
keeps exactly one rule.

DELIBERATELY NOT FUZZY. It matches an exact name, a case-insensitive name, or a
display name — nothing else. A near-miss resolver would silently route one owl's
notes into another owl's file, which is a worse failure than the orphan it set out
to prevent: an orphan loses a write, a mis-resolution corrupts a reader.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import Iterable


class _NamedOwl(Protocol):
    """The two fields this needs. Structural, so a test double is a plain object
    rather than a whole manifest — and so this module never imports the manifest."""

    name: str
    display_name: str | None


def _fold(value: str | None) -> str:
    return str(value or "").strip().casefold()


def canonical_owl_name(
    target: str | None, owls: Iterable[_NamedOwl],
) -> str | None:
    """Return the canonical owl name for ``target``, or None if it is not an owl.

    Resolution order, most specific first:
      1. an exact canonical name           (`scout`      -> `scout`)
      2. a case-insensitive canonical name (`Scout`      -> `scout`)
      3. a display name, case-insensitive  (`Falcon`     -> `scout`)

    None means "this is not an owl" — which is a real answer, not a failure. The
    caller decides what to do with it, because the right response differs by
    caller: a memory write should fall back to the writer's own file rather than
    mint an orphan, while a routing decision may want to refuse outright.
    """
    wanted = _fold(target)
    if not wanted:
        return None

    candidates = list(owls)
    for owl in candidates:  # 1 — exact wins outright
        if str(getattr(owl, "name", "")) == str(target).strip():
            return str(owl.name)
    for owl in candidates:  # 2 — canonical name, case-insensitively
        if _fold(getattr(owl, "name", None)) == wanted:
            return str(owl.name)
    for owl in candidates:  # 3 — display name
        if _fold(getattr(owl, "display_name", None)) == wanted:
            return str(owl.name)
    return None
