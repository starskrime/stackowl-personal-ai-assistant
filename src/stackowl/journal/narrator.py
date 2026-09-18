"""The one narrator (AD-30): renders a registered event type's plain-English
``full`` sentence and a generic ``public`` (kind + count only) rendering, so
every future delivery surface (Bridge, briefing, Telegram, Web Push) reads
the same words instead of each inventing its own.

Name resolution is I/O, and it happens here at DELIVERY time -- never on
``journal.record()``'s write path (AD-5). ``journal/`` still imports nothing
from any subsystem (AD-7): a ``NameResolver`` port is registered INTO this
module by the owning subsystem (``pipeline.durable`` today, via
:func:`register_name_resolver`), inverting the dependency (AD-30).

The tombstone fallback (``"a retired {kind}"``) is generic and owned entirely
here -- never per-subsystem -- so narration degrades gracefully for a
``RecordKind`` with no registered resolver at all, a resolver returning
``None``, or a resolver that raises. Name resolution never raises out of this
module.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from stackowl.exceptions import JournalInvalidAttrsError
from stackowl.infra.observability import log
from stackowl.journal.enums import RecordKind
from stackowl.journal.models import JournalAttrsBase, JournalEvent
from stackowl.journal.registry import EventTypeSpec, get_registry

#: Resolves a target id to its current display name, or ``None`` if it no
#: longer resolves to one. Registered per :class:`RecordKind` by the owning
#: subsystem -- never called from the write path.
NameResolver = Callable[[str], Awaitable[str | None]]

_resolvers: dict[RecordKind, NameResolver] = {}
_lock = threading.RLock()


def register_name_resolver(record_kind: RecordKind, resolver: NameResolver) -> None:
    """Register the one ``NameResolver`` for ``record_kind``. Raises on a
    duplicate registration -- mirrors ``registry.py``'s duplicate-``type``
    refusal: one resolver per kind, never silently replaced.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] narrator.register_name_resolver: entry",
        extra={"_fields": {"record_kind": record_kind.value}},
    )
    with _lock:
        # 2. DECISION -- refuse a duplicate registration for this kind.
        existing = _resolvers.get(record_kind)
        if existing is not None:
            log.journal.error(
                "[journal] narrator.register_name_resolver: refused -- "
                "already registered",
                extra={"_fields": {"record_kind": record_kind.value}},
            )
            raise ValueError(
                f"journal name resolver for {record_kind.value!r} is already "
                "registered -- one resolver per RecordKind"
            )
        # 3. STEP -- register it.
        _resolvers[record_kind] = resolver
    # 4. EXIT
    log.journal.info(
        "[journal] narrator.register_name_resolver: exit -- registered",
        extra={"_fields": {"record_kind": record_kind.value}},
    )


def reset_name_resolvers_for_tests() -> None:
    """Clear every registered resolver. Test-only -- mirrors
    ``journal/health.py``'s ``reset_for_tests()`` per-test reset convention."""
    with _lock:
        _resolvers.clear()


def _tombstone(record_kind: RecordKind) -> str:
    # 1. ENTRY / 4. EXIT -- a one-line pure helper; the callers around every
    # call site already log WHY the tombstone was needed (no resolver / None /
    # raised), so this logs only its own entry+exit, at debug.
    log.journal.debug(
        "[journal] narrator._tombstone: entry",
        extra={"_fields": {"record_kind": record_kind.value}},
    )
    text = f"a retired {record_kind.value}"
    log.journal.debug(
        "[journal] narrator._tombstone: exit",
        extra={"_fields": {"record_kind": record_kind.value}},
    )
    return text


async def _resolve_name(record_kind: RecordKind, target_id: str) -> str:
    """Resolve ``target_id``'s current display name, or a tombstone.

    Never raises: a missing resolver, a resolver returning ``None``, or a
    resolver that itself throws all fall back to the same generic tombstone
    text -- narration must never fail because a target went away.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] narrator._resolve_name: entry",
        extra={"_fields": {"record_kind": record_kind.value, "target_id": target_id}},
    )
    with _lock:
        resolver = _resolvers.get(record_kind)
    # 2. DECISION -- no resolver registered for this kind at all.
    if resolver is None:
        log.journal.debug(
            "[journal] narrator._resolve_name: no resolver registered -- tombstone",
            extra={"_fields": {"record_kind": record_kind.value}},
        )
        return _tombstone(record_kind)
    # 3. STEP -- ask the resolver; it may raise or return None.
    try:
        name = await resolver(target_id)
    except Exception as exc:
        log.journal.error(
            "[journal] narrator._resolve_name: resolver raised -- tombstone "
            "substituted, never propagated",
            exc_info=exc,
            extra={"_fields": {"record_kind": record_kind.value, "target_id": target_id}},
        )
        return _tombstone(record_kind)
    if name is None:
        log.journal.debug(
            "[journal] narrator._resolve_name: resolver returned None -- tombstone",
            extra={"_fields": {"record_kind": record_kind.value, "target_id": target_id}},
        )
        return _tombstone(record_kind)
    # 4. EXIT
    log.journal.debug(
        "[journal] narrator._resolve_name: exit -- resolved",
        extra={"_fields": {"record_kind": record_kind.value, "target_id": target_id}},
    )
    return name


@dataclass(frozen=True)
class NarrationResult:
    """The two renderings a registered event type produces."""

    #: The full plain-English sentence, naming the target by its current name
    #: (or a tombstone). Owner-facing surfaces (Bridge, briefing) use this.
    full: str
    #: A generic kind+count rendering with no name -- for surfaces (public
    #: activity feeds, aggregate counts) that must never leak a target's name.
    public: str


def narrate_full(spec: EventTypeSpec, attrs: JournalAttrsBase, name: str) -> str:
    """The ``full`` half of narration: the type's own declared ``narrate``
    callable, given the resolved (or tombstoned) name."""
    # 1. ENTRY
    log.journal.debug(
        "[journal] narrator.narrate_full: entry", extra={"_fields": {"type": spec.type}},
    )
    # 2. DECISION / 3. STEP -- no branching of its own; delegates to the
    # type's own declared `narrate` callable.
    text = spec.narrate(attrs, name)
    # 4. EXIT
    log.journal.debug(
        "[journal] narrator.narrate_full: exit", extra={"_fields": {"type": spec.type}},
    )
    return text


def narrate_public(record_kind: RecordKind, count: int = 1) -> str:
    """The ``public`` half of narration: kind + count only, never a name --
    exposed standalone so a future batched surface can render one sentence
    for N events of the same kind without resolving any of their names."""
    # 1. ENTRY
    log.journal.debug(
        "[journal] narrator.narrate_public: entry",
        extra={"_fields": {"record_kind": record_kind.value, "count": count}},
    )
    # 2. DECISION / 3. STEP -- singular vs plural noun, then render.
    noun = f"{record_kind.value} update" if count == 1 else f"{record_kind.value} updates"
    text = f"{count} {noun}"
    # 4. EXIT
    log.journal.debug(
        "[journal] narrator.narrate_public: exit",
        extra={"_fields": {"record_kind": record_kind.value}},
    )
    return text


async def narrate(event: JournalEvent, locale: str = "en") -> NarrationResult:
    """Render one recorded event's ``full`` sentence and ``public`` rendering.

    I/O happens here (name resolution), at delivery time -- never on the
    write path. Only ``"en"`` is supported today; any other value still
    renders (no future locale exists to select) rather than raising, since
    narration must never fail because of what surface is asking. Raises
    :class:`~stackowl.exceptions.JournalInvalidAttrsError` if ``event.attrs``
    does not match the type's registered model -- the same check
    ``recorder.py`` makes on the write path, made here too because a
    ``JournalEvent`` handed to ``narrate()`` need not have gone through
    ``record()`` first, and a mismatched ``attrs`` must fail loudly and
    clearly rather than crash inside the type's own ``narrate`` callable.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] narrator.narrate: entry",
        extra={"_fields": {"type": event.type, "target_id": event.target_id, "locale": locale}},
    )
    # 2. DECISION -- the type must be declared, and attrs must match it (same
    # registry, same check `recorder.py` makes on the write path).
    spec = get_registry().get(event.type)
    if not isinstance(event.attrs, spec.attrs_model):
        raise JournalInvalidAttrsError(
            event.type, type(event.attrs).__name__, spec.attrs_model.__name__,
        )
    # 3. STEP -- resolve the name (never raises), then render both halves.
    name = await _resolve_name(spec.record_kind, event.target_id)
    full = narrate_full(spec, event.attrs, name)
    public = narrate_public(spec.record_kind)
    # 4. EXIT
    log.journal.debug(
        "[journal] narrator.narrate: exit",
        extra={"_fields": {"type": event.type, "target_id": event.target_id}},
    )
    return NarrationResult(full=full, public=public)
