"""``EventRegistry`` -- one versioned event-type registry (AD-3).

Dict-based and mirrors ``channels/registry.py`` / ``tools/registry.py``'s
style: ``register()`` raises on a duplicate declaration rather than silently
overwriting one, because two registrations for the same ``type`` is exactly
the "one type meaning two things" AD-3 forbids. An ``RLock`` guards mutation
even though today's only writers are module-import-time registration side
effects (``task_events.py``) -- the same defensive stance
``tools/registry.py`` takes rather than trusting that timing holds forever.

Story 2.10 ("Nothing escapes the journal") adds three things, all additive to
the shape above: ``EventTypeSpec.table`` (so ``journal/coverage.py`` can diff
"every migration-created table" against "what the registry covers" without a
second, hand-maintained type->table dict that could drift from the real
registration call sites -- exactly the drift class
``health/store_cadence.py``'s own docstring warns a parallel list invites);
``tables_covered()`` (that diff's other operand); and the minimal
versioned-model/upcaster API (``VersionEntry``, ``register_upcaster``,
``versions_for``) AD-3's "each version's model and upcaster stay until its
last row is pruned" needs a home for, even though no real type has grown a
second ``schema_version`` yet (Story 2.10 ships only the registration API
plus a tripwire proving no version with live rows lost its model -- wiring an
upcaster into a real read path is explicitly the next story to bump a
``schema_version``'s own job, per AD-3/DW-16).
"""

from __future__ import annotations

import builtins
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from stackowl.infra.observability import log
from stackowl.journal.enums import AttentionClass, Intensity, NeedsYouKind, RecordKind
from stackowl.journal.models import JournalAttrsBase


@dataclass(frozen=True)
class EventTypeSpec:
    """One event type's declared shape -- everything ``record()`` checks
    an event against, and everything a later story's attention policy or
    coverage tripwire reads."""

    type: str
    schema_version: int
    # `builtins.type[...]`, not the bare builtin -- the field named `type`
    # above shadows it within this class body, which mypy resolves literally
    # (`stackowl.pipeline.durable.store` hits the same `import builtins` need
    # for the same reason).
    attrs_model: builtins.type[JournalAttrsBase]
    emitting_process: str
    record_kind: RecordKind
    attention_class: AttentionClass
    #: Renders this type's `full` plain-English sentence (Story 2.2, AD-30).
    #: Required -- no default -- so "no narration" is refused at registration,
    #: the same kind of refusal as "no attention class".
    narrate: Callable[[JournalAttrsBase, str], str]
    #: Required when `attention_class` is `NEEDS_YOU`, forbidden otherwise --
    #: validated in `__post_init__` since dataclasses don't enforce this
    #: cross-field rule at construction time on their own.
    intensity: Intensity | None = None
    #: Story 2.10 (AD-3 coverage tripwire) -- the sqlite table this type's
    #: `record_ref` points into, set at the SAME call site as every other
    #: piece of this type's metadata so it can never drift from the real
    #: registration the way a hand-maintained type->table dict would.
    #: `None` for a type whose record_ref carries no sqlite table at all
    #: (`memory.written` -- `md`-backed, AD-4: "md- and graph-backed targets
    #: never gain mirror tables").
    table: str | None = None
    #: Story 3.1 (AD-28) -- what KIND of Needs-you item this type opens.
    #: Required when `attention_class` is `NEEDS_YOU`, forbidden otherwise --
    #: validated in `__post_init__` alongside `intensity`'s identical
    #: cross-field rule, since the two are only ever meaningful together (a
    #: type that needs the owner's attention always opens a typed item).
    needs_you_kind: NeedsYouKind | None = None
    #: Story 3.1 (AD-28) -- the OTHER registered type names this type closes
    #: when recorded (e.g. a future "job resumed" declaring
    #: `resolves=("job.parked",)`). Never validated against the registry at
    #: registration time -- import order across `*_events.py` modules is not
    #: guaranteed, so `record()` looks each name up LIVE and raises the
    #: existing `JournalEventTypeUnregisteredError` on a bad one, the same
    #: failure mode a bad name in any other live lookup already produces.
    resolves: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # 1. ENTRY
        log.journal.debug(
            "[journal] EventTypeSpec.__post_init__: entry",
            extra={"_fields": {"type": self.type}},
        )
        # 2. DECISION -- narrate must be a declared, callable rendering.
        if not callable(self.narrate):
            log.journal.error(
                "[journal] EventTypeSpec.__post_init__: refused -- no narrate= callable",
                extra={"_fields": {"type": self.type}},
            )
            raise ValueError(
                f"journal event type {self.type!r} must declare a narrate="
                "callable -- every registered type needs a plain-English "
                "rendering (AD-30)"
            )
        # 3. STEP -- attention_class/intensity must agree (AD-5's cross-field rule).
        if self.attention_class is AttentionClass.NEEDS_YOU and self.intensity is None:
            log.journal.error(
                "[journal] EventTypeSpec.__post_init__: refused -- NEEDS_YOU with no intensity",
                extra={"_fields": {"type": self.type}},
            )
            raise ValueError(
                f"journal event type {self.type!r} is attention_class="
                "NEEDS_YOU and must declare an intensity (AD-5)"
            )
        if self.attention_class is AttentionClass.AMBIENT and self.intensity is not None:
            log.journal.error(
                "[journal] EventTypeSpec.__post_init__: refused -- AMBIENT with an intensity",
                extra={"_fields": {"type": self.type}},
            )
            raise ValueError(
                f"journal event type {self.type!r} is attention_class=AMBIENT "
                "and must NOT declare an intensity (AD-5)"
            )
        # 3b. STEP -- needs_you_kind/attention_class must agree, the same
        # cross-field shape as intensity above (AD-28).
        if self.attention_class is AttentionClass.NEEDS_YOU and self.needs_you_kind is None:
            log.journal.error(
                "[journal] EventTypeSpec.__post_init__: refused -- "
                "NEEDS_YOU with no needs_you_kind",
                extra={"_fields": {"type": self.type}},
            )
            raise ValueError(
                f"journal event type {self.type!r} is attention_class="
                "NEEDS_YOU and must declare a needs_you_kind (AD-28)"
            )
        if self.attention_class is AttentionClass.AMBIENT and self.needs_you_kind is not None:
            log.journal.error(
                "[journal] EventTypeSpec.__post_init__: refused -- "
                "AMBIENT with a needs_you_kind",
                extra={"_fields": {"type": self.type}},
            )
            raise ValueError(
                f"journal event type {self.type!r} is attention_class=AMBIENT "
                "and must NOT declare a needs_you_kind (AD-28)"
            )
        # 4. EXIT
        log.journal.debug(
            "[journal] EventTypeSpec.__post_init__: exit -- valid",
            extra={"_fields": {"type": self.type}},
        )


class VersionEntry(NamedTuple):
    """One kept-older-version's shape (Story 2.10, AD-3's additive-evolution
    rule): the ``attrs`` model that version's rows were written with, and the
    callable that upcasts a raw ``dict`` of that shape into the CURRENT
    version's ``attrs`` dict. ``upcaster`` is ``None`` only for the entry
    ``versions_for()`` synthesizes for a type's own current version, which
    needs no upcasting -- it already IS the target shape.
    """

    model: builtins.type[JournalAttrsBase]
    upcaster: Callable[[dict[str, object]], dict[str, object]] | None


class EventRegistry:
    """Process-wide registry of declared event types."""

    def __init__(self) -> None:
        self._specs: dict[str, EventTypeSpec] = {}
        #: Story 2.10 -- older, still-live ``schema_version``s kept per type,
        #: keyed by version number. Never includes a type's CURRENT version
        #: (``versions_for()`` synthesizes that entry from ``self._specs``
        #: itself, so the two can never disagree about what "current" means).
        self._kept_versions: dict[str, dict[int, VersionEntry]] = {}
        self._lock = threading.RLock()

    def register(self, spec: EventTypeSpec) -> None:
        """Declare one event type. Raises on a duplicate ``type``.

        AD-3: "A type emitted from both processes is illegal" -- re-declaring
        an already-registered type (whatever the emitting process) is refused
        rather than silently replaced, so a second definition can never shadow
        the first one some other code already validates against.
        """
        with self._lock:
            existing = self._specs.get(spec.type)
            if existing is not None:
                raise ValueError(
                    f"journal event type {spec.type!r} already registered by "
                    f"{existing.emitting_process!r} -- one type, one "
                    "declaration (AD-3)"
                )
            self._specs[spec.type] = spec

    def get(self, type_name: str) -> EventTypeSpec:
        """Look up a declared type. Raises :class:`JournalEventTypeUnregisteredError`."""
        with self._lock:
            spec = self._specs.get(type_name)
        if spec is None:
            from stackowl.exceptions import JournalEventTypeUnregisteredError

            raise JournalEventTypeUnregisteredError(type_name)
        return spec

    def all_types(self) -> tuple[str, ...]:
        """Every declared type name -- for a later coverage tripwire."""
        with self._lock:
            return tuple(self._specs)

    def tables_covered(self) -> frozenset[str]:
        """Every sqlite table at least one registered type's ``record_ref``
        points into (Story 2.10, AD-3's coverage tripwire). Skips types whose
        ``table`` is ``None`` -- an `md`-backed type declares no table to
        cover; ``journal/coverage.py`` handles it as its own excused entry
        instead.
        """
        with self._lock:
            return frozenset(
                spec.table for spec in self._specs.values() if spec.table is not None
            )

    def register_upcaster(
        self,
        type_name: str,
        version: int,
        model: builtins.type[JournalAttrsBase],
        upcaster: Callable[[dict[str, object]], dict[str, object]] | None,
    ) -> None:
        """Declare one OLDER, still-live ``schema_version`` for an already
        registered type (AD-3: "additive-only ... a registered upcaster fills
        [new fields] for older rows on read ... each version's model and
        upcaster stay until its last row is pruned").

        Raises on: ``type_name`` not yet registered (``register()`` must run
        first -- a version belongs to a type, and an unregistered type has no
        current ``schema_version`` to be older THAN); ``version`` not strictly
        below the type's current ``schema_version`` (a version cannot be kept
        "older" than or equal to itself); a duplicate registration for the
        same ``(type_name, version)`` pair (mirrors ``register()``'s own
        duplicate-declaration refusal -- one entry, one declaration).
        """
        # 1. ENTRY
        log.journal.debug(
            "[journal] EventRegistry.register_upcaster: entry",
            extra={"_fields": {"type": type_name, "version": version}},
        )
        # 2. DECISION -- model must actually be a JournalAttrsBase subclass,
        # the same cross-field invariant EventTypeSpec.__post_init__ enforces
        # for attrs_model at construction (AD-3: attrs are always this shape).
        if not (isinstance(model, builtins.type) and issubclass(model, JournalAttrsBase)):
            log.journal.error(
                "[journal] EventRegistry.register_upcaster: refused -- "
                "model is not a JournalAttrsBase subclass",
                extra={"_fields": {"type": type_name, "version": version}},
            )
            raise ValueError(
                f"journal event type {type_name!r}: register_upcaster() "
                f"model must be a JournalAttrsBase subclass, got {model!r}"
            )
        if version < 1:
            log.journal.error(
                "[journal] EventRegistry.register_upcaster: refused -- "
                "version is below 1",
                extra={"_fields": {"type": type_name, "version": version}},
            )
            raise ValueError(
                f"journal event type {type_name!r}: version {version} is "
                "not a valid schema_version -- versions start at 1"
            )
        with self._lock:
            # 3. DECISION -- the type must already be registered.
            spec = self._specs.get(type_name)
            if spec is None:
                log.journal.error(
                    "[journal] EventRegistry.register_upcaster: refused -- "
                    "type not registered",
                    extra={"_fields": {"type": type_name, "version": version}},
                )
                raise ValueError(
                    f"journal event type {type_name!r} is not registered -- "
                    "register() it before declaring an older kept version for it"
                )
            # 4. STEP -- version must be strictly older than current.
            if version >= spec.schema_version:
                log.journal.error(
                    "[journal] EventRegistry.register_upcaster: refused -- "
                    "version is not older than the current schema_version",
                    extra={"_fields": {
                        "type": type_name, "version": version,
                        "current_schema_version": spec.schema_version,
                    }},
                )
                raise ValueError(
                    f"journal event type {type_name!r}: version {version} is "
                    f"not older than its current schema_version "
                    f"({spec.schema_version}) -- register_upcaster() is for "
                    "KEPT OLDER versions only"
                )
            kept = self._kept_versions.setdefault(type_name, {})
            if version in kept:
                log.journal.error(
                    "[journal] EventRegistry.register_upcaster: refused -- "
                    "duplicate version",
                    extra={"_fields": {"type": type_name, "version": version}},
                )
                raise ValueError(
                    f"journal event type {type_name!r} version {version} is "
                    "already registered -- one declaration per kept version"
                )
            kept[version] = VersionEntry(model=model, upcaster=upcaster)
        # 5. EXIT
        log.journal.info(
            "[journal] EventRegistry.register_upcaster: exit -- registered",
            extra={"_fields": {"type": type_name, "version": version}},
        )

    def versions_for(self, type_name: str) -> dict[int, VersionEntry]:
        """Every version of ``type_name`` this process can still read: every
        kept older version (:meth:`register_upcaster`), plus the type's
        current version, synthesized from its own registered spec (model =
        ``spec.attrs_model``, no upcaster needed -- it already IS the target
        shape). Raises :class:`~stackowl.exceptions.JournalEventTypeUnregisteredError`
        for an unregistered type, mirroring :meth:`get`.
        """
        with self._lock:
            spec = self._specs.get(type_name)
            if spec is None:
                from stackowl.exceptions import JournalEventTypeUnregisteredError

                raise JournalEventTypeUnregisteredError(type_name)
            versions = dict(self._kept_versions.get(type_name, {}))
            versions[spec.schema_version] = VersionEntry(
                model=spec.attrs_model, upcaster=None,
            )
            return versions


_registry = EventRegistry()


def get_registry() -> EventRegistry:
    """The process-wide singleton every emitter and ``record()`` shares."""
    return _registry
