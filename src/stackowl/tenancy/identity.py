"""IdentityResolver — map a per-channel handle to a stable cross-channel identity.

Single-user assistant: the same person reaches it from several channels. An
explicit alias map (config, not auto-mint) collapses their handles to one
``identity_key`` so durable knowledge follows them. Unmapped handle → itself,
so an unconfigured deployment is byte-identical to per-channel behavior.
"""
from __future__ import annotations

from stackowl.infra.observability import log


class IdentityResolver:
    def __init__(self, aliases: dict[str, list[str]]) -> None:
        # Invert {identity: [handles]} → {handle: identity}; tolerate malformed
        # values (log + skip) so one bad config row can't break resolution.
        self._handle_to_identity: dict[str, str] = {}
        for identity, handles in (aliases or {}).items():
            if not isinstance(handles, list):
                log.tenancy.error(
                    "[identity] malformed alias value — skipping",
                    extra={"_fields": {"identity": identity}},
                )
                continue
            for h in handles:
                self._handle_to_identity[str(h)] = identity

    def resolve(self, handle: str) -> str:
        return self._handle_to_identity.get(handle, handle)


def load_identity_resolver() -> IdentityResolver:
    """Read ``identity.aliases`` from Settings and return an :class:`IdentityResolver`.

    When the ``identity`` section is absent the default is an empty dict, so
    ``resolve(x) == x`` for all handles — byte-identical to pre-identity
    behaviour.  A Settings load failure logs and degrades to an empty resolver
    (no hidden errors, never crashes the caller).
    """
    try:
        from stackowl.config.settings import Settings

        aliases = Settings().identity.aliases
    except Exception as exc:
        log.tenancy.error(
            "[identity] failed to load settings — using empty alias map",
            exc_info=exc,
            extra={"_fields": {"exc_type": type(exc).__name__}},
        )
        aliases = {}
    return IdentityResolver(aliases)
