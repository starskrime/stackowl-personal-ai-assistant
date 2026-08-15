"""MemoryProvider — the plugin kind behind the `memory` tool (D08.2 slice C).

WHY THIS EXISTS. Memory was one hardcoded implementation. A user who wants their
notes in something else had no seam to reach, and every alternative would have had
to be merged into the core. This makes memory pluggable WITHOUT widening the
waist: providers contribute behind the existing `memory` tool, so the model's tool
catalogue does not grow by one entry per provider installed.

FOOTPRINT LADDER: rung 4 (plugin), explicitly NOT rung 6 (new core tool). 77 tools
are already registered on every API call, which is why the ceiling below counts
what providers ADD rather than the total.

LAW 1 — the active set is resolved ONCE and frozen for the incarnation. Re-resolving
mid-session would change tool schemas underneath a live prompt cache and void every
turn already in the window. :meth:`MemoryProviderRegistry.resolve` is idempotent by
construction: the second call returns the first result, whatever it is passed.

LAW 2 — the waist stays narrow because provider schemas are capped (default 6,
`memory.provider_schema_ceiling`) and a provider that would breach the cap is
REFUSED at activation. Refused, not truncated: half a provider's tools working is
worse than none of them, because the failure is invisible until a user calls the
missing half.

THE BUILT-IN IS EXPRESSED THROUGH THIS INTERFACE, and that is deliberate. An
interface with no implementation is a guess about what implementers need; this way
the contract is exercised by something real on every boot. But D08.1's curated
memory is EXEMPT from the ceiling and NON-REMOVABLE — it shipped as a guarantee,
not an option, and a plugin must not be able to squeeze the platform's own memory
out of its own budget.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from stackowl.infra.observability import log

#: The reserved name of the built-in curated-memory provider. A third-party
#: provider claiming it would inherit both the cap exemption and the
#: non-removability, so the name is refused rather than shadowed.
BUILTIN_PROVIDER_NAME = "builtin"


class ProviderRefused(ValueError):
    """A provider was rejected at activation. Never raised for a cap breach —
    that path is a logged refusal, because one bad plugin must not stop a boot."""


class MemoryProvider(ABC):
    """What a memory plugin must offer.

    An ABC rather than a Protocol, and for a concrete reason rather than taste:
    ``LocalPluginLoader`` discovers extension points with ``issubclass``, and a
    Protocol carrying a non-method member (``name`` is a property) raises
    "Protocols with non-method members don't support issubclass()". Every other
    extension point in the loader's table — Tool, JobHandler, SlashCommand,
    ChannelAdapter, OwlSource — is an ABC, so this matches the pattern the loader
    was built for instead of asking it to change.

    Deliberately small. Everything here is called at ACTIVATION, not per turn, so
    a slow provider costs one boot rather than every message.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identity. Also the key the registry refuses duplicates on."""

    @abstractmethod
    def is_available(self) -> bool:
        """Can this provider run right now? False is a normal answer, not an error
        — a provider backed by an offline service says so and is skipped."""

    @abstractmethod
    def tool_schemas(self) -> list[dict[str, object]]:
        """Schemas this provider contributes behind the `memory` tool. Counted
        against the ceiling; see the module docstring for why the total matters."""


class BuiltinCuratedProvider(MemoryProvider):
    """D08.1's curated two-file memory, expressed as a provider.

    Contributes NO tool schemas: curated memory is already reachable through the
    `memory` tool's own actions, and adding duplicates would spend ceiling budget
    to offer the model a second way to do what it can already do.
    """

    @property
    def name(self) -> str:
        return BUILTIN_PROVIDER_NAME

    def is_available(self) -> bool:
        # Two text files under the home directory. If they are missing they are
        # created on write, so there is no unavailable state to report.
        return True

    def tool_schemas(self) -> list[dict[str, object]]:
        return []


class MemoryProviderRegistry:
    """Resolves the active provider set once per incarnation, under a ceiling."""

    def __init__(self, *, ceiling: int = 6) -> None:
        self._ceiling = max(0, ceiling)
        self._active: tuple[MemoryProvider, ...] | None = None
        self._candidates: list[MemoryProvider] = []
        #: (name, schemas_requested, ceiling) for each provider refused on budget.
        #: Kept so the registry can be asked WHY a provider is missing, rather
        #: than the operator inferring it from absence.
        self.refused: list[tuple[str, int, int]] = []
        log.memory.debug(
            "[memory] provider_registry.init",
            extra={"_fields": {"ceiling": self._ceiling}},
        )

    def register(self, provider: MemoryProvider, *, source_name: str = "") -> None:
        """Collect a candidate. Called by LocalPluginLoader at plugin-load time.

        Registration is NOT activation — candidates accumulate here and the active
        set is decided once by :meth:`resolve`. Keeping them separate is what lets
        the ceiling be enforced over the whole set rather than first-come-first-
        served, and what keeps Law 1's freeze at a single point.
        """
        self._candidates.append(provider)
        log.memory.info(
            "[memory] provider_registry: candidate registered (not yet active)",
            extra={"_fields": {
                "provider": self._safe_name(provider), "from_plugin": source_name,
            }},
        )

    def resolve(self, providers: list[MemoryProvider] | None = None) -> tuple[MemoryProvider, ...]:
        """Activate providers under the ceiling. Idempotent for the incarnation.

        A SECOND CALL RETURNS THE FIRST RESULT and ignores its argument — that is
        Law 1 expressed in code rather than in a comment nobody reads. The
        built-in is always first and always present.
        """
        # Default BEFORE the entry log: logging len(providers) first meant a
        # no-argument resolve() — the path the plugin loader uses — died on
        # len(None). Every test passed an explicit list, so none of them saw it.
        providers = self._candidates if providers is None else providers
        # 1. ENTRY
        log.memory.debug(
            "[memory] provider_registry.resolve: entry",
            extra={"_fields": {"offered": len(providers), "ceiling": self._ceiling}},
        )
        # 2. DECISION — already resolved: the incarnation keeps what it started with.
        if self._active is not None:
            log.memory.debug(
                "[memory] provider_registry.resolve: already resolved — returning the "
                "frozen set (Law 1)",
                extra={"_fields": {"active": len(self._active)}},
            )
            return self._active

        active: list[MemoryProvider] = [BuiltinCuratedProvider()]
        spent = 0
        seen = {BUILTIN_PROVIDER_NAME}
        for provider in providers:
            name = self._safe_name(provider)
            if name == BUILTIN_PROVIDER_NAME:
                raise ProviderRefused(
                    f"a plugin may not claim the reserved provider name "
                    f"{BUILTIN_PROVIDER_NAME!r} — it carries the ceiling exemption "
                    "and the non-removability guarantee"
                )
            if name in seen:
                log.memory.info(
                    "[memory] provider_registry: duplicate provider name — refused",
                    extra={"_fields": {"provider": name}},
                )
                continue
            # 3. STEP — a provider is untrusted code. Neither an unavailable one
            # nor a raising one may cost the boot (I4).
            try:
                if not provider.is_available():
                    log.memory.info(
                        "[memory] provider_registry: provider unavailable — not activated",
                        extra={"_fields": {"provider": name}},
                    )
                    continue
                schemas = len(provider.tool_schemas())
            except Exception as exc:
                log.memory.error(
                    "[memory] provider_registry: provider raised during activation — "
                    "skipped, memory continues without it",
                    exc_info=exc,
                    extra={"_fields": {"provider": name}},
                )
                continue
            # A provider is admitted WHOLE or not at all. Truncating its schemas
            # would leave half its tools working, which fails invisibly.
            if spent + schemas > self._ceiling:
                self.refused.append((name, schemas, self._ceiling))
                log.memory.info(
                    "[memory] provider_registry: provider REFUSED — would breach the "
                    "tool-schema ceiling; it is not partially loaded",
                    extra={"_fields": {
                        "provider": name,
                        "schemas_requested": schemas,
                        "already_spent": spent,
                        "ceiling": self._ceiling,
                    }},
                )
                continue
            spent += schemas
            seen.add(name)
            active.append(provider)

        self._active = tuple(active)
        # 4. EXIT
        log.memory.info(
            "[memory] provider_registry: active set resolved and frozen",
            extra={"_fields": {
                "active": [self._safe_name(p) for p in self._active],
                "schemas_used": spent,
                "ceiling": self._ceiling,
                "refused": [r[0] for r in self.refused],
            }},
        )
        return self._active

    @property
    def active(self) -> tuple[MemoryProvider, ...]:
        """The frozen set. Empty tuple before resolution — never resolves lazily,
        because an accidental first call from a request path would freeze the set
        at whatever happened to be loaded then."""
        return self._active or ()

    @staticmethod
    def _safe_name(provider: MemoryProvider) -> str:
        """A provider's own `name` is third-party code and may itself raise."""
        try:
            return str(provider.name)
        except Exception:  # pragma: no cover — defensive, logged by the caller
            return "<unnamed provider>"


__all__ = [
    "BUILTIN_PROVIDER_NAME",
    "BuiltinCuratedProvider",
    "MemoryProvider",
    "MemoryProviderRegistry",
    "ProviderRefused",
]
