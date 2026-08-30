"""D09.2 — a correction at the session boundary becomes a POSITIVE instruction.

MEASURED before building this: 219 feedback classifications in the retained
window, of which 17 were NEGATIVE at 0.9-1.0 confidence, producing 8 preference
notes and ZERO procedural knowledge. All 17 fell in one 5-hour session on
2026-08-28 — the operator corrected the platform repeatedly and it learned nothing
reusable.

The operator's condition (ESC-71): store the CORRECTED BEHAVIOUR, never the
correction. "answer directly; no preamble", not "was too verbose".
"""

from __future__ import annotations

from stackowl.memory.rollover_summary_handler import _parse_summary, parse_correction

# --------------------------------------------------------------------------- #
# The summary must not be able to regress. It has 273 staged artifacts behind it
# and is not being put at risk to add a second feature to the same call.
# --------------------------------------------------------------------------- #


def test_the_OLD_shape_still_parses_unchanged() -> None:
    assert _parse_summary('{"notable": true, "summary": "we fixed the VPN"}') == "we fixed the VPN"
    assert _parse_summary('{"notable": false, "summary": ""}') == ""
    assert _parse_summary("not json") is None


def test_a_correction_field_does_not_disturb_the_summary() -> None:
    raw = '{"notable": true, "summary": "S", "correction": "answer directly"}'
    assert _parse_summary(raw) == "S"


def test_a_MALFORMED_correction_cannot_cost_the_summary() -> None:
    """The new field is optional by construction: the summary is parsed from its
    own key and never looks at this one."""
    raw = '{"notable": true, "summary": "S", "correction": {"unexpected": "shape"}}'
    assert _parse_summary(raw) == "S"
    assert parse_correction(raw) is None


# --------------------------------------------------------------------------- #
# The correction itself
# --------------------------------------------------------------------------- #


def test_a_correction_is_extracted() -> None:
    raw = '{"notable": true, "summary": "S", "correction": "Answer directly; no preamble."}'
    assert parse_correction(raw) == "Answer directly; no preamble."


def test_no_correction_is_the_NORMAL_case() -> None:
    """Most sessions contain no correction. Absent, empty and whitespace all mean
    the same thing and none of them may invent an instruction."""
    for raw in (
        '{"notable": true, "summary": "S"}',
        '{"notable": true, "summary": "S", "correction": ""}',
        '{"notable": true, "summary": "S", "correction": "   "}',
        '{"notable": false, "summary": ""}',
    ):
        assert parse_correction(raw) is None, raw


def test_unusable_input_yields_None_never_raises() -> None:
    for raw in ("", "   ", "not json", "[]", "null", '{"correction": 42}'):
        assert parse_correction(raw) is None, raw


def test_a_fenced_response_is_still_read() -> None:
    """The model fences its JSON often enough that the summary path already
    strips fences; the correction must be read from the same stripped text
    rather than re-implementing the rule."""
    raw = '```json\n{"notable": true, "summary": "S", "correction": "Be brief."}\n```'
    assert parse_correction(raw) == "Be brief."
    assert _parse_summary(raw) == "S"


# --------------------------------------------------------------------------- #
# WIRING. The parser being right proves nothing about it being REACHED, and this
# programme has shipped a correct-but-unreached mechanism repeatedly.
# --------------------------------------------------------------------------- #


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    async def fetch_all(self, sql, params=()):
        self.queries.append(sql)
        return self.rows


def _handler(db):
    from stackowl.memory.rollover_summary_handler import RolloverSummaryHandler

    h = RolloverSummaryHandler.__new__(RolloverSummaryHandler)
    h._db = db
    return h


async def test_a_correction_is_written_to_the_OWL(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db = _FakeDb([{"owl_name": "secretary"}])
    h = _handler(db)

    assert await h._record_correction("Answer directly; no preamble.",
                                      ended="conv-1", job_id="j1") is True
    db_saw_the_owl_query = any("conversations" in q for q in db.queries)

    # Assert the EFFECT on disk, not that the call returned True. A conditional
    # assertion here would pass whether or not anything was written — the vacuous
    # shape this codebase has been bitten by before.
    from stackowl.memory.curated import memory_dir

    written = (memory_dir() / "secretary.md").read_text()
    assert "Answer directly; no preamble." in written
    assert db_saw_the_owl_query


async def test_no_correction_writes_NOTHING_and_asks_the_db_nothing(tmp_path, monkeypatch) -> None:
    """The normal case must be free: no correction means no query, no write."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db = _FakeDb([{"owl_name": "secretary"}])
    h = _handler(db)

    assert await h._record_correction(None, ended="conv-1", job_id="j1") is False
    assert db.queries == [], "a no-op still hit the database"


async def test_a_conversation_with_NO_OWL_does_not_fall_back_to_the_user_profile(
    tmp_path, monkeypatch, caplog
) -> None:
    """The blast-radius rule. A model-generated instruction applied to the global
    profile would enter EVERY owl's prompt forever; scoped to the owl it came
    from, it is as wide as the conversation that produced it."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    h = _handler(_FakeDb([{"owl_name": ""}]))

    with caplog.at_level("WARNING"):
        assert await h._record_correction("Be brief.", ended="c", job_id="j") is False
    assert any("NOT writing to the global profile" in r.message for r in caplog.records)


async def test_a_db_failure_cannot_fail_the_ROLLOVER(tmp_path, monkeypatch, caplog) -> None:
    """This runs AFTER the summary is staged. A learning extra that turns a
    successful rollover into a failed job would be a bad trade."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))

    class _Boom:
        async def fetch_all(self, sql, params=()):
            raise RuntimeError("db gone")

    with caplog.at_level("ERROR"):
        assert await _handler(_Boom())._record_correction("Be brief.", ended="c", job_id="j") is False
    assert any("the summary is unaffected" in r.message for r in caplog.records)


async def test_execute_ACTUALLY_CALLS_the_correction_path(tmp_path, monkeypatch) -> None:
    """The mutation target. Written after M11 — unwiring the call from execute() —
    left all the tests above GREEN, because they call _record_correction directly.
    A correct-but-unreached mechanism is this programme's most repeated defect and
    I had just reproduced it in my own change.

    Drives the real execute(), stubbing only its neighbours.
    """
    from stackowl.memory.rollover_summary_handler import RolloverSummaryHandler
    from stackowl.scheduler.job import Job

    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    h = RolloverSummaryHandler.__new__(RolloverSummaryHandler)
    h._db = _FakeDb([{"owl_name": "secretary"}])
    seen: dict = {}

    async def _transcript(_ended):
        return [{"role": "user", "content": "stop explaining"}]

    async def _complete(_t):
        return '{"notable": true, "summary": "S", "correction": "Answer directly."}'

    async def _stage(*a, **k):
        return True

    async def _open(*a, **k):
        return "task-1"

    async def _close(*a, **k):
        return None

    async def _record(correction, *, ended, job_id):
        seen["correction"] = correction
        return True

    h._read_transcript = _transcript          # type: ignore[method-assign]
    h._complete = _complete                   # type: ignore[method-assign]
    h._stage = _stage                         # type: ignore[method-assign]
    h._open_task = _open                      # type: ignore[method-assign]
    h._close_task = _close                    # type: ignore[method-assign]
    h._record_correction = _record            # type: ignore[method-assign]

    await h.execute(Job(
        job_id="j1", handler_name="rollover_summary",
        schedule="on rollover", idempotency_key="j1",
        last_run_at=None, next_run_at="2026-08-30T00:00:00+00:00", status="running",
        params={"session_key": "owl:secretary:cli:dm:1",
                "ended_conversation_id": "conv-1"},
    ))

    assert seen.get("correction") == "Answer directly.", (
        "execute() did not reach the correction path"
    )


def test_the_TEMPLATE_still_asks_for_the_field() -> None:
    """M12 caught this gap: removing `correction` from the prompt killed no test,
    yet it silently disables the whole feature — the model simply never returns
    the field and every parse yields None, which is indistinguishable from "no
    session had a correction".

    The template IS the interface here, so it gets pinned like one. Asserting on
    the positive-framing instruction too, because that instruction is the operator's
    condition for adopting this at all (ESC-71), not a stylistic preference.
    """
    from stackowl.memory.rollover_summary_handler import _PROMPT_DIR, _TEMPLATE_NAME

    text = (_PROMPT_DIR / _TEMPLATE_NAME).read_text()
    assert '"correction"' in text, "the prompt no longer asks for the field"
    assert "CORRECTED BEHAVIOUR" in text, "the positive-framing instruction is gone"
    assert "never a description of what went wrong" in text
