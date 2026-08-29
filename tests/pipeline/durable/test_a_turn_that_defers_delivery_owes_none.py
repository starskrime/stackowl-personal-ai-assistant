"""A turn whose producer owns delivery must not also carry a delivery obligation.

MEASURED LIVE, 2026-08-29. Two RCA verifier tasks, both ``pending``, attempts 11/30
and 10/30, retrying every ~15 minutes toward a ceiling they can never clear::

    task_id      retry-0b9546da-...          destination 'rca'   attempt 11/30
    task_id      retry-recover-retry-0b9546  destination 'rca'   attempt 10/30

``rca`` is a channel NAME, not an address. ``update_status`` correctly refuses to
close a row that owes a delivery it cannot prove, so the task fails, requeues, and
climbs — burning a model run each time for an answer nobody is waiting for.

THE SITE ALREADY DOCUMENTS THE DEFECT. ``task_runner.py`` carries: *"That cured the
false COMPLETION and left a never-completion, because A CHANNEL NAME IS NOT AN
ADDRESS."* Both available answers are wrong in general:

  * a BARE CHANNEL  -> the row can never complete (today's bug)
  * NULL for everyone -> rows complete without delivering (the bug that fix cured)

THE DISCRIMINATOR IS ALREADY ON THE STATE. ``defer_delivery=True`` means "the
producer owns delivery" — deliver.py is a hard no-op for such a turn, and
DurableTask's own field comment says NULL means "the task has no destination of its
own (a pure sub-goal whose parent delivers)". That is exactly an RCA stage:
staged_rca sets ``defer_delivery=True`` with the comment "This stage has no user
stream and no reply_target — the text is read straight off final.responses".

So a deferred turn owes NOTHING and gets NULL; an ordinary turn keeps today's
behaviour byte-for-byte. An interactive turn that somehow has no address is a real
defect and stays loud rather than being silently nulled.
"""

from __future__ import annotations

import pytest


def test_a_deferred_turn_claims_no_destination() -> None:
    """The RCA case. Nobody is waiting, so nothing is owed."""
    from stackowl.pipeline.durable.turn_task import destination_for_turn

    assert destination_for_turn(
        channel="rca", reply_target=None, defer_delivery=True,
    ) is None


def test_an_ordinary_turn_is_UNCHANGED() -> None:
    """Byte-for-byte. This must not touch the path every real reply takes."""
    from stackowl.pipeline.durable.turn_task import destination_for_turn

    assert destination_for_turn(
        channel="telegram", reply_target=72055773, defer_delivery=False,
    ) == "telegram:72055773"
    assert destination_for_turn(
        channel="cli", reply_target=None, defer_delivery=False,
    ) == "cli"


def test_a_deferred_turn_WITH_an_address_still_claims_it() -> None:
    """Deferral says who delivers, not whether anyone is waiting.

    A scheduled job defers delivery and has a real target; nulling that would strip
    the achievement condition from work that genuinely owes an answer.
    """
    from stackowl.pipeline.durable.turn_task import destination_for_turn

    assert destination_for_turn(
        channel="telegram", reply_target=72055773, defer_delivery=True,
    ) == "telegram:72055773"


def test_an_INTERACTIVE_turn_with_no_address_stays_loud() -> None:
    """The bug the earlier fix cured must not be reintroduced.

    A live turn that lost its reply target is a real defect. Nulling it here would
    let the row complete without delivering — silently — which is exactly the false
    completion `task_runner.py`'s comment says was already paid for once.
    """
    from stackowl.pipeline.durable.turn_task import destination_for_turn

    assert destination_for_turn(
        channel="telegram", reply_target=None, defer_delivery=False,
    ) == "telegram"


@pytest.mark.parametrize("chat_id", [72055773, "72055773", "C123ABC", "+15551234"])
def test_every_channel_native_id_shape_survives(chat_id: object) -> None:
    """The int/str trap this helper already carries a scar from.

    Its docstring records the first live run failing with "'int' object has no
    attribute 'strip'" because a test double passed a string where the adapter
    passes an int. Slack ids are not numbers at all and whatsapp's would be
    silently truncated by int().
    """
    from stackowl.pipeline.durable.turn_task import destination_for_turn

    got = destination_for_turn(
        channel="telegram", reply_target=chat_id, defer_delivery=False,
    )
    assert got == f"telegram:{chat_id}"
