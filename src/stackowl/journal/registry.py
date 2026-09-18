"""``EventRegistry`` -- one versioned event-type registry (AD-3).

Dict-based and mirrors ``channels/registry.py`` / ``tools/registry.py``'s
style: ``register()`` raises on a duplicate declaration rather than silently
overwriting one, because two registrations for the same ``type`` is exactly
the "one type meaning two things" AD-3 forbids. An ``RLock`` guards mutation
even though today's only writers are module-import-time registration side
effects (``task_events.py``) -- the same defensive stance
``tools/registry.py`` takes rather than trusting that timing holds forever.
"""

from __future__ import annotations

import builtins
import threading
from collections.abc import Callable
from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.journal.enums import AttentionClass, Intensity, RecordKind
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
        # 4. EXIT
        log.journal.debug(
            "[journal] EventTypeSpec.__post_init__: exit -- valid",
            extra={"_fields": {"type": self.type}},
        )


class EventRegistry:
    """Process-wide registry of declared event types."""

    def __init__(self) -> None:
        self._specs: dict[str, EventTypeSpec] = {}
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


_registry = EventRegistry()


def get_registry() -> EventRegistry:
    """The process-wide singleton every emitter and ``record()`` shares."""
    return _registry
