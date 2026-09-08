"""ONE answer to "does this destination name somebody?".

A task's ``destination`` is either ``<channel>:<address>`` ("telegram:72055773")
or a bare channel name ("telegram", "rca", "cli") or NULL. Only the first names
an addressee; the other two say WHERE a reply would go without saying WHO it
goes to.

WHY THIS MODULE EXISTS. That rule was written twice — ``_address_of`` in
``store.py`` and ``_chat_id_of`` in ``task_loop_runner.py``, byte-identical
logic under two names — and the two copies decide the SAME question on the two
halves of one lifecycle: the runner uses it to choose whether the retry actuator
gets a target, and the store uses it to choose whether a dead letter is escalated
to a waiting person or retired as having nobody to tell. Had they drifted, the
platform would have sent an answer to a destination it had already classified as
unaddressable, or retired work somebody was still waiting for.

That is failure mode #3 from ``CLAUDE.md`` — two copies of one rule — and the
cure it prescribes: one source, and the other asks it.
"""

from __future__ import annotations


def address_of(destination: str | None) -> str | None:
    """``"telegram:72055773"`` -> ``"72055773"``; ``None`` when nobody is named.

    ``None`` for a channel-only destination such as ``cli``, which addresses its
    single terminal implicitly, and for ``rca``/``telegram``/NULL, which name no
    recipient at all. A caller that needs to tell those two apart asks the
    ADAPTER (``ChannelAdapter.implicitly_addressable``), because whether a
    channel can reach somebody without an address is a property of the channel,
    not of the string.
    """
    if not destination or ":" not in destination:
        return None
    return destination.split(":", 1)[1] or None


class NoAddresseeCompletion(Exception):
    """The work finished and there was nobody to deliver it to.

    A CONTROL SIGNAL, not a failure. The loop has exactly two terminal outcomes —
    delivered, or failed and requeued — and this is a third that neither fits:
    re-running cannot conjure an addressee, so requeueing spends attempts on an
    outcome no attempt can change, while `mark_delivered` would stamp
    `delivered_at` on an answer that reached nobody.

    MEASURED LIVE 2026-09-08 with the third missing. A recovered task whose
    destination was the bare channel name "telegram" produced its answer, sent it
    nowhere (correctly), and the loop still logged `[loop] task COMPLETE — its
    outcome reached its destination` with `delivered_at` set, because
    `_dispatch` marks delivery on any non-empty result string.

    It carries the result so the loop can still RECORD the work; the runner
    raises it because, as `task_loop_runner` puts it, "a raise is the only
    channel this runner has back to the loop".
    """

    def __init__(self, result: str) -> None:
        super().__init__("the answer had no addressee, so nothing was delivered")
        self.result = result
