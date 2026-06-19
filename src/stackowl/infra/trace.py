"""TraceContext — UUIDv4 trace/span IDs propagated automatically via contextvars."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from uuid import uuid4

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.authz.bounds import BoundsSpec


class _TraceToken(NamedTuple):
    trace: Token[str | None]
    span: Token[str | None]
    parent: Token[str | None]
    session: Token[str | None]
    interactive: Token[bool]
    channel: Token[str | None]
    reply_target: Token[str | int | None]
    delegation_depth: Token[int]
    delegation_chain: Token[tuple[str, ...]]
    owl_name: Token[str | None]
    creation_ceiling: Token[Any]
    task_id: Token[str | None]
    durable_owner_id: Token[str | None]


class TraceContext:
    """Stores and propagates trace/span IDs across async hops via contextvars."""

    _trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
    _span_id: ContextVar[str | None] = ContextVar("span_id", default=None)
    _parent_span_id: ContextVar[str | None] = ContextVar("parent_span_id", default=None)
    _session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
    _interactive: ContextVar[bool] = ContextVar("interactive", default=False)
    _channel: ContextVar[str | None] = ContextVar("channel", default=None)
    # Per-turn delivery target (a Telegram chat_id / Slack channel id) mirrored
    # from PipelineState.reply_target so producer paths (e.g. a scheduler handler)
    # that reach tools via TraceContext can address their durable send. A primitive
    # chat id — LOG-SAFE, included in get() like ``channel``. Default None.
    _reply_target: ContextVar[str | int | None] = ContextVar("reply_target", default=None)
    # E8-S1 — delegation recursion depth of the current (sub-)pipeline. 0 for a
    # top-level user turn; one per A2ADelegator spawn level. delegate_task reads
    # this off TraceContext (tools never see PipelineState) for its depth
    # backstop and to stamp the reconstructed parent_state. Default 0.
    _delegation_depth: ContextVar[int] = ContextVar("delegation_depth", default=0)
    # E8-S1 — the owl running the current (sub-)pipeline. delegate_task reads this
    # to attribute the TRUE caller (from_owl) instead of hardcoding "secretary",
    # avoiding mis-attribution + a self-delegation loop when a non-secretary owl
    # delegates. Mirrors the delegation_depth propagation. Default None.
    _owl_name: ContextVar[str | None] = ContextVar("owl_name", default=None)
    # E2-S2 — the parent's creation_ceiling (a BoundsSpec | None). Carried so
    # child-spawn sites (delegate_task, sessions_spawn, sessions_send) can clamp
    # the delegated child to the PARENT'S EFFECTIVE bounds (owl ∩ ceiling), not just
    # the current owl bounds. Typed loosely (Any) to avoid a layering cycle with
    # authz; the TYPE_CHECKING import above provides the annotation for mypy.
    _creation_ceiling: ContextVar[Any] = ContextVar("creation_ceiling", default=None)
    # E8-S1 — the ordered list of owl names that delegated to the current turn,
    # oldest-first (e.g. ["secretary", "scout"]). len() == delegation_depth.
    # Powers cycle detection: delegate_task refuses if the target is already in the
    # chain. Governor-stamped and model-untouchable. Default empty tuple.
    _delegation_chain: ContextVar[tuple[str, ...]] = ContextVar("delegation_chain", default=())
    # D1 §8.1 — the durable task being driven by the current (sub-)pipeline, and
    # its owning principal. delegate_task reads these off TraceContext to decide
    # durable-vs-fail-open. ONLY the fail-open durability signal rides the
    # ContextVar (safe to lose: you degrade to the non-durable path); the
    # identity-determining child id is computed explicitly, never inferred from
    # this ambient state. Default None ⇒ non-durable turn ⇒ D1 is a no-op.
    _task_id: ContextVar[str | None] = ContextVar("durable_task_id", default=None)
    _durable_owner_id: ContextVar[str | None] = ContextVar(
        "durable_owner_id", default=None
    )

    @classmethod
    def start(
        cls,
        session_id: str | None = None,
        *,
        trace_id: str | None = None,
        interactive: bool = False,
        channel: str | None = None,
        reply_target: str | int | None = None,
        delegation_depth: int = 0,
        delegation_chain: tuple[str, ...] = (),
        owl_name: str | None = None,
        creation_ceiling: BoundsSpec | None = None,
        task_id: str | None = None,
        durable_owner_id: str | None = None,
    ) -> _TraceToken:
        """Set trace context for the current async task; return a token to reset later.

        ``trace_id`` is used verbatim when provided (the typical case: the channel
        adapter already minted one and we propagate it through the pipeline).
        When ``trace_id`` is None we mint a fresh UUID — useful for background
        jobs/scheduler handlers that start their own root trace.

        ``interactive`` and ``channel`` mirror the originating PipelineState so
        tools (which read TraceContext, not PipelineState) can tell whether a
        user is present to answer a clarify and on which channel to deliver it.
        FAIL-CLOSED: ``interactive`` defaults to False — a human is assumed
        absent unless a user-facing channel explicitly declares True.
        """
        return _TraceToken(
            trace=cls._trace_id.set(trace_id or str(uuid4())),
            span=cls._span_id.set(str(uuid4())),
            parent=cls._parent_span_id.set(None),
            session=cls._session_id.set(session_id),
            interactive=cls._interactive.set(interactive),
            channel=cls._channel.set(channel),
            reply_target=cls._reply_target.set(reply_target),
            delegation_depth=cls._delegation_depth.set(delegation_depth),
            delegation_chain=cls._delegation_chain.set(delegation_chain),
            owl_name=cls._owl_name.set(owl_name),
            creation_ceiling=cls._creation_ceiling.set(creation_ceiling),
            task_id=cls._task_id.set(task_id),
            durable_owner_id=cls._durable_owner_id.set(durable_owner_id),
        )

    @classmethod
    def reset(cls, token: _TraceToken) -> None:
        """Restore previous context from a token returned by start()."""
        cls._trace_id.reset(token.trace)
        cls._span_id.reset(token.span)
        cls._parent_span_id.reset(token.parent)
        cls._session_id.reset(token.session)
        cls._interactive.reset(token.interactive)
        cls._channel.reset(token.channel)
        cls._reply_target.reset(token.reply_target)
        cls._delegation_depth.reset(token.delegation_depth)
        cls._delegation_chain.reset(token.delegation_chain)
        cls._owl_name.reset(token.owl_name)
        cls._creation_ceiling.reset(token.creation_ceiling)
        cls._task_id.reset(token.task_id)
        cls._durable_owner_id.reset(token.durable_owner_id)

    @classmethod
    @asynccontextmanager
    async def span(cls, name: str) -> AsyncIterator[None]:  # noqa: ARG003
        """Create a child span; restores previous span_id on exit."""
        old_span = cls._span_id.get()
        new_span_token = cls._span_id.set(str(uuid4()))
        parent_token = cls._parent_span_id.set(old_span)
        try:
            yield
        finally:
            cls._span_id.reset(new_span_token)
            cls._parent_span_id.reset(parent_token)

    @classmethod
    def creation_ceiling(cls) -> BoundsSpec | None:
        """The acting turn's creation_ceiling (parent ceiling for delegated children).

        Read by child-spawn sites (delegate_task, sessions_spawn, sessions_send) to
        clamp the delegated child to the PARENT'S EFFECTIVE bounds (owl ∩ ceiling),
        closing the TOCTOU-delegation gap. Not included in get() — a BoundsSpec
        object must not appear in log records.
        """
        return cast("BoundsSpec | None", cls._creation_ceiling.get())

    @classmethod
    def durable_owner_id(cls) -> str | None:
        """The owning principal of the durable task driving this (sub-)pipeline.

        Read by delegate_task alongside ``get()["task_id"]`` to assemble the
        child's durable scope. Kept off :meth:`get` (a dedicated accessor mirrors
        ``creation_ceiling``) — it is consumed at the delegation seam, not in logs.
        """
        return cls._durable_owner_id.get()

    @classmethod
    def get(cls) -> dict[str, Any]:
        """Return current trace context as a dict (safe to embed in log records).

        LOG-SAFE by construction: deliberately OMITS ``creation_ceiling`` (a
        BoundsSpec object that must never land in a log record) and
        ``durable_owner_id`` (consumed at the delegation seam, not in logs). Use
        :meth:`snapshot` when you need the FULL context for reconstruction.
        """
        return {
            "trace_id": cls._trace_id.get(),
            "span_id": cls._span_id.get(),
            "parent_span_id": cls._parent_span_id.get(),
            "session_id": cls._session_id.get(),
            "interactive": cls._interactive.get(),
            "channel": cls._channel.get(),
            "reply_target": cls._reply_target.get(),
            "delegation_depth": cls._delegation_depth.get(),
            "delegation_chain": cls._delegation_chain.get(),
            "owl_name": cls._owl_name.get(),
            "task_id": cls._task_id.get(),
        }

    @classmethod
    def snapshot(cls) -> dict[str, Any]:
        """Return the FULL current context for reconstruction (F025).

        Unlike :meth:`get` (which is log-safe and intentionally omits them), this
        includes ``durable_owner_id`` and ``creation_ceiling`` (a BoundsSpec | None)
        so a caller that must REBUILD the acting context at a delegation/durable
        seam has the complete set in one call — no need to remember to also call the
        two dedicated accessors. NOT for log records (it carries a BoundsSpec and an
        owner id); use :meth:`get` for anything that may be logged.
        """
        snap = cls.get()
        snap["durable_owner_id"] = cls._durable_owner_id.get()
        snap["creation_ceiling"] = cls._creation_ceiling.get()
        return snap
