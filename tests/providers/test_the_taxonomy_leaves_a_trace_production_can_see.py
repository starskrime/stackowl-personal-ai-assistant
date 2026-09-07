"""A taxonomy whose only evidence line is DEBUG cannot be shown to work.

WHY THIS EXISTS. D02.6 built an eleven-cause failure taxonomy that decides WHICH
RECOVERY to attempt — cascade to another tier, rotate a credential, back off,
abort. Its Verification section closes with "Not proven live" and a `jq` reading
`.fields.recovery`, and then, in its own words:

    "Note: that line is `log.engine.debug`, and production runs at INFO — which is
     why the `cause` field appears zero times in 15 days of logs despite the
     classifier running on every fault."

The document DIAGNOSED the defect and filed it as a parenthetical, leaving its own
verification query one that reality could never answer. MEASURED 2026-09-07, a month
later and across ten retained days: the `recovery` field appears **zero times in
580,225 records**. Not "not yet" — impossible. (The 20 hits for `"cause":` in the
same window belong to `[prompt] invalidate` and have nothing to do with this; the
raw records were printed before the count was believed.)

THE CAUSE IS PLACEMENT, NOT THE LEVEL. A decision about which recovery to attempt
was attached to the routine 4-point EXIT line, whose level is set by a logging
convention — exits are DEBUG — rather than by the value of what it carries. Nothing
was wrong with the convention; the load-bearing field was riding on the wrong line.
So the classification gets its own INFO line and the exit line stays DEBUG.

Volume was measured before the line was written: ten days of logs carry 81
`[resilient_round]` records out of 580,225. This is not a firehose.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.providers._resilient_round import (
    RECOVERY_FOR_CAUSE,
    FailureCause,
    RecoveryAction,
    resilient_round,
)

pytestmark = pytest.mark.asyncio


def _raiser(exc: BaseException):
    async def _round():
        raise exc
    return _round


class _Status(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.status_code = status


class TestTheClassificationReachesProduction:
    async def test_a_classified_fault_logs_cause_and_recovery_at_INFO(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """THE DEFECT, directly. At DEBUG this record does not exist in production,
        so no volume of traffic could ever close D02.6's live check."""
        with caplog.at_level(logging.INFO, logger="stackowl.engine"):
            with pytest.raises(_Status):
                await resilient_round(None, None, _raiser(_Status(404)))

        classified = [
            r for r in caplog.records
            if "fault classified" in r.getMessage() and r.levelno >= logging.INFO
        ]
        assert classified, (
            "the taxonomy's cause and recovery are not emitted at INFO, so the "
            "document's own verification query can never return a row"
        )
        fields = getattr(classified[0], "_fields", {})
        assert fields.get("cause") == FailureCause.MODEL_NOT_FOUND.value, fields
        assert fields.get("recovery") == RecoveryAction.FALLBACK_MODEL.value, fields

    async def test_a_non_fault_is_not_announced(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The other direction, and it is what keeps the line honest. Control flow
        and our own bugs run through here too; announcing them at INFO would turn a
        fault signal into noise, and noise is how a log line stops being read."""
        with caplog.at_level(logging.INFO, logger="stackowl.engine"):
            with pytest.raises(ValueError):
                await resilient_round(None, None, _raiser(ValueError("our own bug")))

        assert not [r for r in caplog.records if "fault classified" in r.getMessage()], (
            "a NOT_A_FAULT was announced as a classified fault"
        )

    @pytest.mark.parametrize(
        ("status", "cause"),
        [
            (402, FailureCause.BILLING),
            (413, FailureCause.PAYLOAD_TOO_LARGE),
            (429, FailureCause.RATE_LIMIT),
            (503, FailureCause.SERVER_5XX),
            (400, FailureCause.BAD_REQUEST),
        ],
    )
    async def test_every_cause_the_document_tabulates_is_visible(
        self, caplog: pytest.LogCaptureFixture, status: int, cause: FailureCause,
    ) -> None:
        """D02.6's taxonomy table, made observable. Its "not proven live" note says
        402/404/408/413/529 have never appeared — that is a statement about our
        providers, and it stays true. What changes is that if one DOES appear, the
        log will say so at a level production records."""
        with caplog.at_level(logging.INFO, logger="stackowl.engine"):
            with pytest.raises(_Status):
                await resilient_round(None, None, _raiser(_Status(status)))

        rec = [r for r in caplog.records if "fault classified" in r.getMessage()]
        assert rec, f"status {status} produced no INFO classification"
        fields = getattr(rec[0], "_fields", {})
        # DERIVED FROM THE MAP, never a hardcoded string. The action MEMBER is
        # `ROTATE_CREDENTIAL` and its logged VALUE is `rotate` — writing the value
        # out by hand is how a test drifts from the code it is pinning, and it is
        # also the one place a reader of D02.6's table can be misled: the table
        # lists member names, the log carries values.
        expected = RECOVERY_FOR_CAUSE[cause].value
        assert (fields.get("cause"), fields.get("recovery")) == (cause.value, expected), fields


class TestTheExitLineIsStillDebug:
    """The fix must not promote the routine exit line — that convention is correct
    and reversing it would trade one unread log for a noisy one."""

    async def test_the_4_point_exit_line_did_not_move_to_INFO(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="stackowl.engine"):
            with pytest.raises(_Status):
                await resilient_round(None, None, _raiser(_Status(503)))

        assert not [r for r in caplog.records if "exit — round raised" in r.getMessage()]
