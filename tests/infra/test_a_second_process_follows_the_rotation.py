"""When one process rotates the log, the others must follow it to the new file.

WHY THIS EXISTS, and it is the cause under 43 broken acceptance queries.

`setup_logging()` is called by FOUR entry points in `cli/app.py`, so the gateway, the
core and any CLI command each own their own `TimedRotatingFileHandler` on the SAME
`~/.stackowl/logs/stackowl.jsonl`. Rotation is an in-process rename: whoever fires first
renames the file and opens a fresh one, and every other process keeps an open descriptor
on the RENAMED INODE and goes on writing into yesterday's file.

MEASURED 2026-09-07, three midnights in a row, and the boundary names both processes.
`stackowl-2026-09-06.jsonl` holds **326 records dated 2026-09-07**, from 00:00:18 to
00:36:58, ending on `[startup] core: exec-replacing with fresh code` — the CORE, writing
into the rotated file until CodeWatcher restarted it 37 minutes later. Meanwhile
`stackowl.jsonl` opened at 00:00:01 and then said NOTHING until 00:36:54. The same shape
is in the two midnights before it: 748 records dated 09-06 inside the 09-05 file, 23
dated 09-05 inside the 09-04 file.

WHAT IT COST. `doc_check.py` reports 43 Verification commands across 22 documents that
grep `stackowl.jsonl` alone. Every one of them is blind to whatever the core wrote after
the last midnight, and a midnight-blind query returns 0 — which reads as *not yet*, not
as *wrong instrument*. Draining those documents one at a time was fixing the symptom
twenty-two times; this is the cause.

THE PRECISE DEFECT is in the stdlib's own multi-process concession. Python 3.14's
`doRollover` opens with:

    dfn = self.rotation_filename(...)
    if os.path.exists(dfn):
        # Already rolled over.
        return

That early return is right to refuse the rename — it is what stops the second process
DELETING the first one's rotated file, which older Pythons did. But it returns while the
stale stream is still open and without advancing `rolloverAt`, so the handler neither
follows the rotation nor stops trying: it re-enters `doRollover` on every subsequent
record, stats the file, and returns again, writing to the old inode the whole time.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from stackowl.infra.observability import DailyJsonlRotatingFileHandler, JsonlFormatter


def _handler(path: Path) -> DailyJsonlRotatingFileHandler:
    """A handler shaped exactly like `setup_logging`'s, including the namer."""
    h = DailyJsonlRotatingFileHandler(
        filename=str(path), when="midnight", utc=True, backupCount=30, encoding="utf-8"
    )

    def _namer(default_name: str) -> str:
        stem, _, date = default_name.rpartition(".")
        return str(Path(stem).parent / f"stackowl-{date}.jsonl")

    h.namer = _namer
    h.setFormatter(JsonlFormatter())
    return h


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("stackowl.test", logging.INFO, __file__, 1, msg, None, None)


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    return tmp_path / "stackowl.jsonl"


def test_the_second_handler_writes_to_the_new_file_not_the_renamed_one(logs: Path) -> None:
    """The whole defect, in the smallest shape that reproduces it.

    Two handlers, one path. A rolls; B must land in the fresh file afterwards. Before the
    fix B's record went to the rotated file, because its `doRollover` returned early on
    "already rolled over" without ever reopening.
    """
    a, b = _handler(logs), _handler(logs)
    try:
        a.emit(_record("before midnight, from A"))
        b.emit(_record("before midnight, from B"))

        # Midnight, as the stdlib sees it: both handlers are due.
        a.rolloverAt = b.rolloverAt = 0
        a.emit(_record("after midnight, from A"))   # A performs the rename
        b.emit(_record("after midnight, from B"))   # B must FOLLOW it

        rotated = next(logs.parent.glob("stackowl-*.jsonl"))
        assert "after midnight, from B" not in [r["msg"] for r in _lines(rotated)], (
            "B wrote into the rotated file — it is still holding the renamed inode"
        )
        assert "after midnight, from B" in [r["msg"] for r in _lines(logs)]
    finally:
        a.close()
        b.close()


def test_the_follower_stops_trying_to_roll_on_every_record(logs: Path) -> None:
    """`rolloverAt` must advance, or the handler stats the directory forever.

    The stdlib's early return leaves it in the past, so `shouldRollover` stays true and
    every single record re-enters `doRollover`. That is a real cost on the busiest writer
    in the process and it is invisible — nothing fails, it just never stops asking.
    """
    a, b = _handler(logs), _handler(logs)
    try:
        a.rolloverAt = b.rolloverAt = 0
        a.emit(_record("A rolls"))
        b.emit(_record("B follows"))

        assert b.rolloverAt > 0, "the follower never advanced its next rollover time"
        assert not b.shouldRollover(_record("next")), (
            "the follower would re-enter doRollover on every subsequent record"
        )
    finally:
        a.close()
        b.close()


def test_following_a_rotation_leaves_a_line_saying_so(logs: Path) -> None:
    """A silent correction is one nobody can confirm happened.

    Production runs at INFO, and this programme has already paid for evidence that lived
    only at DEBUG. The notice goes into the file it is describing, so the first record of
    a new day says who followed whom.
    """
    a, b = _handler(logs), _handler(logs)
    stackowl = logging.getLogger("stackowl")
    # B is INSTALLED, the way `setup_logging` installs it in a real process — the notice
    # is logged through the named logger, so a detached handler would never see it. That
    # is the wiring the fix depends on, and testing it detached would prove nothing.
    stackowl.addHandler(b)
    # …and at INFO, which `setup_logging` sets and a bare test process does not: the
    # `stackowl` logger is NOTSET there, so its effective level is WARNING and the notice
    # is dropped before it reaches any handler. Replicating the production wiring rather
    # than lowering the bar — and it states the real precondition, that this evidence
    # exists only while the platform runs at INFO, as it does.
    previous_level = stackowl.level
    stackowl.setLevel(logging.INFO)
    try:
        a.rolloverAt = b.rolloverAt = 0
        a.emit(_record("A rolls"))
        b.emit(_record("B follows"))

        notices = [r for r in _lines(logs) if "another process rotated" in r["msg"]]
        assert notices, [r["msg"] for r in _lines(logs)]
        assert {r["level"] for r in notices} == {"INFO"}, "the notice must be INFO"

        # The FIELDS are the discriminating half: a message alone says a rotation
        # happened, the fields say which file was left and which is now being written.
        # Only this code path can produce them, which is the evidence shape this
        # programme reaches for rather than a pre-existing line that merely fires again.
        fields = notices[0]["fields"]
        assert fields["now_writing"] == "stackowl.jsonl"
        assert fields["rotated_to"].startswith("stackowl-")
    finally:
        stackowl.setLevel(previous_level)
        stackowl.removeHandler(b)
        a.close()
        b.close()


def test_the_rotating_handler_still_rotates_when_it_is_alone(logs: Path) -> None:
    """The control. A fix that stopped rotation would pass every test above."""
    a = _handler(logs)
    try:
        a.emit(_record("day one"))
        a.rolloverAt = 0
        a.emit(_record("day two"))

        rotated = list(logs.parent.glob("stackowl-*.jsonl"))
        assert len(rotated) == 1, f"expected exactly one rotated file, got {rotated}"
        assert [r["msg"] for r in _lines(rotated[0])] == ["day one"]
        assert [r["msg"] for r in _lines(logs)] == ["day two"]
    finally:
        a.close()
