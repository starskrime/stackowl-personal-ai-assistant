"""Interaction substrate — mid-turn user clarification (turn-yield model).

The ``clarify`` primitive lets an owl ask the user a question mid-turn. Unlike a
blocking rendezvous, the turn ENDS once the question is delivered (no parked
coroutine, crash-safe), and the NEXT inbound text message on the matching
session+channel resolves it via a fresh turn. The gateway loop calls
:meth:`ClarifyGateway.try_resolve` on every inbound message before normal
routing; a match short-circuits the message into a clarify resolution.

``ClarifyGateway`` is the DI singleton holding the in-memory registry of pending
questions plus a per-channel adapter map for delivery. It is constructed and
wired by the startup/gateway layer (the gateway loop itself is wired by the
operator, not here).

Provenance: the pause-and-resume ALGORITHM (registry-keyed pending question,
session+channel-bound resolution, single-pending cap, TTL sweep, channel-bound
delivery, sentinel-on-no-answer) is ported HYBRID from a reference agent and
re-implemented asyncio-native — the reference's threading.Event + watchdog
slices (sync-worker artifacts) are discarded; nothing here blocks. See
``_bmad-output/research/tool-port-analysis.md`` (E5 ``clarify`` row) and
``_bmad-output/research/tools/clarify.md``.
"""

from __future__ import annotations

from stackowl.interaction.clarify_gateway import ClarifyGateway, PendingClarify

__all__ = ["ClarifyGateway", "PendingClarify"]
