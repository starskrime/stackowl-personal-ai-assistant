"""A recovered answer was thrown away because the task never recorded an address.

MEASURED on the live database and the retained logs.

    [deliver] stream-miss: no durable fallback available — answer not delivered
    {'request_id': 'recover-task-74e6b23',
     'session_key': 'owl:secretary:recovery:task-74e6b23',
     'has_deliverer': True, 'has_target': False, 'body_len': 2053}

**2,053 characters of a real answer, discarded** — and it belongs to the very Gmail
digest job Bakir was paged about. 181 such records across four days.

THE ADDRESS EXISTED AT EVERY LAYER EXCEPT THE ONE THAT NEEDED IT::

    jobs.target_addresses   {"telegram": 72055773}
    tasks.channel           telegram
    tasks.destination       NULL          <- and only 15 of 1,257 rows have one

Compare a conversation-turn task, which recovers fine::

    57e8a0722050...  channel=telegram  destination='telegram:72055773'  delivered

Same recovery code path, same helper. `reply_target_for_task` reads `destination`
and was added on 2026-08-18 after "222 characters of a real reply thrown away". The
READER was built; for a scheduled goal the WRITER never had anything to write.

WHY THE ROW IS BLANK, AND IT IS NOT AN OVERSIGHT. goal_execution deliberately sets
no reply_target: "THIS handler owns delivery via the durable seam — the pipeline
deliver step must NOT also send (prevents a double-send). No reply_target is set: a
cron poll has no live session, so the recipient comes from the job's durable target
columns." Correct for the NORMAL path. But `destination_for_turn` then sees
channel="telegram" with no address, and its own rule — "A CHANNEL NAME IS NOT AN
ADDRESS" — correctly refuses to record it. So the task row is honest and empty, and
the recovery months later has only that row.

SETTING reply_target CANNOT CAUSE THE DOUBLE-SEND THAT COMMENT GUARDS AGAINST.
`deliver.run` opens with `if state.defer_delivery: return state` — the first
statement in the function. The guard is the FLAG, not the absence of an address.

So the row now records where its answer belongs, which is also what its own
achievement string has always claimed: "the answer is delivered to the job's
targets".
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.turn_task import destination_for_turn
from stackowl.scheduler.handlers.goal_execution import reply_target_for_job


class _Job:
    def __init__(self, primary_channel=None, target_addresses=None):  # noqa: ANN001
        self.primary_channel = primary_channel
        self.target_addresses = target_addresses


def test_the_live_job_shape_yields_its_address() -> None:
    """Verbatim from the jobs table: {"telegram": 72055773}."""
    job = _Job("telegram", {"telegram": 72055773})

    assert reply_target_for_job(job) == 72055773


def test_a_JSON_STRING_of_addresses_is_read_too() -> None:
    """The column is TEXT; whether the row arrives parsed depends on the reader."""
    job = _Job("telegram", '{"telegram": 72055773}')

    assert reply_target_for_job(job) == 72055773


def test_a_job_with_NO_address_yields_None() -> None:
    """A sweep with nobody waiting must stay unaddressed — inventing a recipient
    would be worse than having none."""
    assert reply_target_for_job(_Job("telegram", None)) is None
    assert reply_target_for_job(_Job(None, {"telegram": 1})) is None
    assert reply_target_for_job(_Job("telegram", {})) is None


def test_an_address_for_a_DIFFERENT_channel_is_not_used() -> None:
    """Delivering a telegram answer to a slack id is worse than not delivering."""
    assert reply_target_for_job(_Job("telegram", {"slack": "C123"})) is None


def test_MALFORMED_addresses_never_raise() -> None:
    """This runs inside a scheduler tick; a parsing error here would cost the run."""
    assert reply_target_for_job(_Job("telegram", "not json")) is None
    assert reply_target_for_job(_Job("telegram", 17)) is None


def test_the_task_now_records_a_real_destination() -> None:
    """The end of the chain: with an address, the row stops being blank."""
    assert destination_for_turn(
        channel="telegram", reply_target=72055773, defer_delivery=True,
    ) == "telegram:72055773"


def test_without_an_address_the_row_is_STILL_honestly_blank() -> None:
    """Unchanged, and load-bearing: "A CHANNEL NAME IS NOT AN ADDRESS", and a bare
    channel makes update_status refuse to close the row — two RCA tasks reached
    attempts 11/30 that way."""
    assert destination_for_turn(
        channel="telegram", reply_target=None, defer_delivery=True,
    ) is None


def test_the_handler_sets_it() -> None:
    """Structural. A helper nothing calls is the defect this platform keeps paying
    for — and here the READER already existed for eight days with no writer."""
    import inspect

    from stackowl.scheduler.handlers import goal_execution

    source = inspect.getsource(goal_execution)
    assert "reply_target=reply_target_for_job(job)" in source, (
        "the job's address never reaches the state, so the task row stays blank "
        "and a recovered answer has nowhere to go"
    )
