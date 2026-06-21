"""CommandSequenceStore — durable per-owner command-sequence learning.

A lightweight first-order Markov model over the slash commands an owner
dispatches: each turn records a ``prev → next`` edge and bumps its frequency.
The store then answers "given the command you just ran, what do you usually run
next?" — the durable backend for the TUI ``☆ suggested`` lane.

Honesty spine (see plan): this is suggest-only telemetry. It NEVER fires a
command, NEVER reorders the deterministic dropdown, and is gated by a config
kill-switch (``ui.command_suggestions``) so the whole layer can be removed,
leaving the dropdown byte-identical to the deterministic baseline.

Owner scoping mirrors :class:`~stackowl.memory.preferences.PreferenceStore`: the
tenancy ``owner_id`` (principal) plus a per-channel ``owner_key`` (the CLI
session handle, ``telegram:{chat_id}``, ...) so suggestions never leak across
owners.

Only the command PATH is recorded, never the freeform argument tail — see
:func:`canonical_invocation`. So ``/memory remember buy milk`` is learned as
``/memory remember`` (a paste-able, repeatable invocation), not the secret-laden
fact text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from stackowl.commands.metadata import CommandMeta, resolve_path
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository


@dataclass(frozen=True)
class SequenceSuggestion:
    """One learned next-command suggestion: the invocation + how often it followed."""

    invocation: str
    count: int


def canonical_invocation(command: str, meta: CommandMeta, args: str) -> str:
    """Reduce a dispatched command + raw args to its canonical command PATH.

    Greedily walks the leading argument tokens through the command's
    sub-command tree (alias-aware via :func:`resolve_path`), stopping at the
    first token that is not a declared sub-command. The freeform tail (e.g. the
    fact text after ``/memory remember``) is dropped, so the recorded value is a
    stable, paste-able invocation and never carries a secret.

    * ``/memory remember buy milk`` → ``/memory remember``
    * ``/memory rm 1234`` (``rm`` aliases ``forget``) → ``/memory forget``
    * ``/tier powerful`` (flag/leaf grammar) → ``/tier``
    """
    parts = [command]
    subs = meta.subcommands
    for tok in args.split():
        node = resolve_path(subs, [tok])
        if node is None:
            break
        parts.append(node.name)  # canonical name, never the alias the user typed
        subs = node.children
    return "/" + " ".join(parts)


async def record_dispatch(
    store: CommandSequenceStore,
    command: str,
    meta: CommandMeta,
    args: str,
    owner_key: str,
) -> str | None:
    """Record a successfully-dispatched command as a sequence edge.

    Returns the canonical invocation recorded, or ``None`` when the turn is
    skipped: a ``??`` dry-run (the handler never ran — previewing isn't doing)
    is not learning data. Keeping this thin helper here (rather than inline in
    the orchestrator closure) makes the skip/canonicalize/record decision unit
    testable.
    """
    from stackowl.commands.dry_run import strip_sigil

    is_dry_run, cleaned = strip_sigil(args)
    if is_dry_run:
        return None
    invocation = canonical_invocation(command, meta, cleaned)
    await store.record(owner_key, invocation)
    return invocation


class CommandSequenceStore(OwnedRepository):
    """Async SQLite store for command-sequence edges (migration 0065).

    Always scopes by ``owner_key`` so suggestions never cross owners. The
    tenancy ``owner_id`` defaults to :data:`DEFAULT_PRINCIPAL_ID` (single-user),
    matching :class:`PreferenceStore`.
    """

    _table = "command_sequence_edges"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        log.gateway.debug("[commands] sequence_store.init: ready")

    async def record(self, owner_key: str, invocation: str) -> None:
        """Record that ``invocation`` was dispatched, forming an edge from the
        owner's previous command.

        Self-loops (the same command twice in a row) are NOT recorded — the lane
        suggests where to go *next*, not the command you just ran. The owner's
        position (``last``) is always advanced.
        """
        invocation = invocation.strip()
        if not invocation:
            return
        log.gateway.debug(
            "[commands] sequence_store.record: entry",
            extra={"_fields": {"owner_key": owner_key, "invocation": invocation}},
        )
        now = time.time()
        last = await self._get_last(owner_key)
        if last is not None and last != invocation:
            # Upsert the prev→next edge, bumping its frequency.
            await self._db.execute(
                """INSERT INTO command_sequence_edges
                       (owner_id, owner_key, prev_invocation, next_invocation, count, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(owner_id, owner_key, prev_invocation, next_invocation)
                   DO UPDATE SET count = count + 1, updated_at = excluded.updated_at""",
                (self._owner_id, owner_key, last, invocation, now),
            )
        # Advance the owner's position.
        await self._db.execute(
            """INSERT INTO command_sequence_last
                   (owner_id, owner_key, last_invocation, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(owner_id, owner_key)
               DO UPDATE SET last_invocation = excluded.last_invocation,
                             updated_at = excluded.updated_at""",
            (self._owner_id, owner_key, invocation, now),
        )
        log.gateway.debug(
            "[commands] sequence_store.record: exit",
            extra={"_fields": {"owner_key": owner_key, "edge_from": last}},
        )

    async def suggest_next(
        self, owner_key: str, *, limit: int = 3, min_count: int = 1
    ) -> list[SequenceSuggestion]:
        """Return the most-frequent commands that followed the owner's current
        position, highest frequency first.

        Returns ``[]`` when the owner has no recorded history or nothing meets
        ``min_count``. Ties break alphabetically for deterministic output.
        """
        last = await self._get_last(owner_key)
        if last is None:
            return []
        rows = await self._db.fetch_all(
            """SELECT next_invocation, count FROM command_sequence_edges
               WHERE owner_id = ? AND owner_key = ? AND prev_invocation = ?
                 AND count >= ?
               ORDER BY count DESC, next_invocation ASC
               LIMIT ?""",
            (self._owner_id, owner_key, last, min_count, limit),
        )
        out = [
            SequenceSuggestion(invocation=row["next_invocation"], count=int(row["count"]))
            for row in rows
        ]
        log.gateway.debug(
            "[commands] sequence_store.suggest_next: exit",
            extra={"_fields": {"owner_key": owner_key, "from": last, "n": len(out)}},
        )
        return out

    async def _get_last(self, owner_key: str) -> str | None:
        """The owner's most recently dispatched canonical invocation, or None."""
        rows = await self._db.fetch_all(
            "SELECT last_invocation FROM command_sequence_last "
            "WHERE owner_id = ? AND owner_key = ?",
            (self._owner_id, owner_key),
        )
        return rows[0]["last_invocation"] if rows else None


class SequenceSuggestionProvider:
    """Thin owner-bound facade over :class:`CommandSequenceStore` for the TUI.

    Bound to one ``owner_key`` (the local terminal user) so the compose area can
    ask ``await provider.suggest()`` without knowing the tenancy plumbing. A
    ``None`` provider (feature off / no DB) means no lane — the dropdown stays
    byte-identical to the deterministic baseline.
    """

    def __init__(self, store: CommandSequenceStore, owner_key: str) -> None:
        self._store = store
        self._owner_key = owner_key

    @property
    def owner_key(self) -> str:
        return self._owner_key

    async def suggest(self, *, limit: int = 3) -> list[SequenceSuggestion]:
        try:
            return await self._store.suggest_next(self._owner_key, limit=limit)
        except Exception as exc:  # suggestions are best-effort — never crash the TUI
            log.gateway.warning(
                "[commands] sequence_provider.suggest: failed", exc_info=exc
            )
            return []
