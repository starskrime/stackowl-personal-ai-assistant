"""StagedCommand — ``/staged`` slash command for staged-fact management.

Subcommands:

* ``/staged list [--status staged|committed|rejected]`` — browse staged facts
* ``/staged review <fact_id>``  — show full content + metadata for one fact
* ``/staged reject <fact_id>``  — delete a staged fact (with confirmation)
* ``/staged promote <fact_id>`` — force-promote bypassing both gates

All deps are constructor-injected so wiring decides what is real vs ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.staged_helpers import (
    find_staged_by_id,
    format_review,
    format_staged_table,
    parse_list_args,
)
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.events.bus import EventBus
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.pipeline.state import PipelineState


_STAGED_META = CommandMeta(
    grammar="verb",
    group="Memory & Knowledge",
    subcommands=(
        SubCommand(
            name="list",
            summary="Browse staged facts, optionally by status",
            args=(
                Arg(
                    name="--status",
                    required=False,
                    summary="filter by status",
                    choices=("staged", "committed", "rejected"),
                ),
            ),
            examples=(
                Example(invocation="/staged list --status committed"),
            ),
        ),
        SubCommand(
            name="review",
            summary="Show full content and metadata for one fact",
            args=(Arg(name="fact_id", summary="staged fact identifier"),),
        ),
        SubCommand(
            name="reject",
            summary="Delete a staged fact",
            description="You discard a staged fact. Confirmed with YES.",
            args=(Arg(name="fact_id", summary="staged fact identifier"),),
            examples=(
                Example(invocation="/staged reject a1b2c3 YES", note="Confirm rejection"),
            ),
        ),
        SubCommand(
            name="promote",
            summary="Force-promote a fact, bypassing both gates",
            description="You promote a staged fact to durable memory, skipping the usual gates.",
            args=(Arg(name="fact_id", summary="staged fact identifier"),),
        ),
    ),
)
_CONFIRMATION = "YES"


class StagedCommand(SlashCommand):
    """``/staged`` slash command — browse and manage staged facts."""

    def __init__(
        self,
        bridge: MemoryBridge | None,
        promoter: FactPromoter | None,
        event_bus: EventBus | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug("[commands] staged.init: entry")
        self._bridge = bridge
        self._promoter = promoter
        self._bus = event_bus
        # 4. EXIT
        log.memory.debug("[commands] staged.init: exit")

    @property
    def command(self) -> str:
        return "staged"

    @property
    def description(self) -> str:
        return "Browse and manage staged facts awaiting promotion."

    @property
    def meta(self) -> CommandMeta:
        return _STAGED_META

    async def handle(self, args: str, state: PipelineState) -> str:
        # 1. ENTRY
        log.memory.debug(
            "[commands] staged.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        if self._bridge is None:
            log.memory.warning("[commands] staged.handle: bridge not configured")
            return "✗ /staged: not configured (memory bridge unavailable)"
        stripped = args.strip()
        if not stripped:
            return render_usage("staged", _STAGED_META)
        parts = stripped.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            # 2. DECISION
            if sub == "list":
                result = await self._list(rest)
            elif sub == "review":
                result = await self._review(rest)
            elif sub == "reject":
                result = await self._reject(rest)
            elif sub == "promote":
                result = await self._promote(rest)
            else:
                log.memory.debug(
                    "[commands] staged.handle: unknown subcommand",
                    extra={"_fields": {"sub": sub[:40]}},
                )
                return render_usage("staged", _STAGED_META)
        except Exception as exc:
            # B5
            log.memory.error(
                "[commands] staged.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /staged {sub}: {exc}"
        # 4. EXIT
        log.memory.debug(
            "[commands] staged.handle: exit",
            extra={"_fields": {"sub": sub, "out_len": len(result)}},
        )
        return result

    # --- subcommands ---------------------------------------------------------

    async def _list(self, rest: str) -> str:
        assert self._bridge is not None  # guarded by handle()
        log.memory.debug(
            "[commands] staged.list: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        status = parse_list_args(rest)
        log.memory.debug(
            "[commands] staged.list: fetching",
            extra={"_fields": {"status": status}},
        )
        facts = await self._bridge.list_staged(status=status)
        log.memory.debug(
            "[commands] staged.list: fetched",
            extra={"_fields": {"status": status, "count": len(facts)}},
        )
        out = format_staged_table(facts, status)
        log.memory.debug(
            "[commands] staged.list: exit",
            extra={"_fields": {"count": len(facts)}},
        )
        return out

    async def _review(self, rest: str) -> str:
        assert self._bridge is not None  # guarded by handle()
        log.memory.debug(
            "[commands] staged.review: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if not rest:
            return "Usage: /staged review <fact_id>"
        fact = await find_staged_by_id(self._bridge, rest)
        if fact is None:
            log.memory.debug(
                "[commands] staged.review: not found",
                extra={"_fields": {"fact_id_prefix": rest[:16]}},
            )
            return f"✗ Staged fact not found: '{rest}'"
        out = format_review(fact)
        log.memory.debug(
            "[commands] staged.review: exit",
            extra={"_fields": {"fact_id": fact.fact_id}},
        )
        return out

    async def _reject(self, rest: str) -> str:
        assert self._bridge is not None  # guarded by handle()
        log.memory.debug(
            "[commands] staged.reject: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if not rest:
            return "Usage: /staged reject <fact_id> [YES]"
        parts = rest.split(maxsplit=1)
        fact_id = parts[0]
        confirmation = parts[1].strip() if len(parts) > 1 else ""
        if confirmation != _CONFIRMATION:
            log.memory.debug(
                "[commands] staged.reject: awaiting confirmation",
                extra={"_fields": {"fact_id_prefix": fact_id[:8]}},
            )
            return (
                f"Reject fact {fact_id[:8]}? [y/N]\n"
                f"   Type: /staged reject {fact_id} YES to confirm."
            )
        # Existence check before delete — avoid false "Rejected" for bogus ids
        fact = await find_staged_by_id(self._bridge, fact_id)
        if fact is None:
            log.memory.debug(
                "[commands] staged.reject: not found — honest refusal",
                extra={"_fields": {"fact_id_prefix": fact_id[:16]}},
            )
            return f"✗ Staged fact not found: '{fact_id}'"
        await self._bridge.delete(fact_id)
        log.memory.info(
            "[commands] staged.reject: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )
        return f"✓ Rejected {fact_id}"

    async def _promote(self, rest: str) -> str:
        log.memory.debug(
            "[commands] staged.promote: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if not rest:
            return "Usage: /staged promote <fact_id>"
        if self._promoter is None:
            log.memory.warning("[commands] staged.promote: promoter not configured")
            return "✗ /staged promote: not configured (promoter unavailable)"
        promoted = await self._promoter.force_promote(rest.strip())
        if not promoted:
            log.memory.warning(
                "[commands] staged.promote: fact_id not found",
                extra={"_fields": {"fact_id_prefix": rest[:16]}},
            )
            return f"✗ Staged fact not found: '{rest.strip()}'"
        log.memory.info(
            "[commands] staged.promote: exit",
            extra={"_fields": {"fact_id": rest.strip()}},
        )
        return f"✓ Promoted {rest.strip()}"

    # --- factory -------------------------------------------------------------

    @classmethod
    def create_and_register(
        cls,
        bridge: MemoryBridge | None,
        promoter: FactPromoter | None,
        event_bus: EventBus | None = None,
    ) -> StagedCommand:
        """Construct a :class:`StagedCommand` and register it on the singleton."""
        cmd = cls(bridge=bridge, promoter=promoter, event_bus=event_bus)
        CommandRegistry.instance().register(cmd)
        return cmd
