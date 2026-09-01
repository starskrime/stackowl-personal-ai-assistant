"""A tool NAME field was carrying a 1,400-character JSON payload.

MEASURED 2026-09-01 against the live 20,062-row ``task_outcomes``. Of the 83
distinct values in ``tool_sequence``, five are not names at all::

    'ACTION: delegate_task\\n{"to_owl": "jobmarket", "goal": "Sea...'   2026-08-26
    'ACTION: shell\\n```json\\n{"command": "curl -sS -o /tmp/li_tes...'  2026-08-26
    'ACTION: shell\\n{"command": "curl -sS -o /tmp/li_test.html -...'    2026-08-26
    'run_command\\n```json\\n{\\n  "tool": "run_command", "command"...'   2026-07-14
    'web_fetch\\n</parameter'                                            2026-07-19

The most recent is six days old, so this is live, not residue.

WHERE IT COMES FROM. ``backends/shared.py`` records
``tuple(tc.tool_name for tc in state.tool_calls)`` — every call the model EMITTED,
with no check that a tool name is a name. When the tool-call parser mis-frames a
hallucinated shape, the whole payload lands in the NAME field.

WHY IT IS NOT COSMETIC. ``classify._gather_recent_actions`` builds a prompt block
from those rows::

    f"- {glyph} {o.input_text[:100]} | tools: {tools}{tag} -> {o.response_text[:120]}"

Every user-controlled field on that line is sliced. ``tools`` is the ONE that is
not — because a tool name is "obviously" short. So the blob is spliced into the
prompt under "tools:", telling the model that ``ACTION: shell\\n```json{...}`` is a
tool it used, which reinforces the very hallucination that produced it.

AND IT REACHES FURTHER, because five components read this column as truth:
``tool_usage`` (orders the discretionary half of the PRESENTED tool set),
``revalidate_learned_tools`` (quarantines a learned tool on its win record),
``failure_outcome_miner`` (attributes blame to a capability),
``learned_tool_loader._report_unused`` and ``shadow_validator``.

A CLAIM THAT DID NOT SURVIVE MEASUREMENT, kept because the refutation is the
useful part. I first wrote that a malformed entry makes a TOOL-FREE turn look
tool-using and so drops it from the shadow replay pool — which would have tied
this to today's ``librarian ... eligible: 2, required: 5``. Queried: **ZERO turns
have an entirely-malformed ``tool_sequence``**, so replay eligibility is not
affected at all. librarian has 5 outcomes, all clean, 2 of them tool-free; its
cold start is simply a small history.

WHAT THE MEASUREMENT DID SHOW: the five rows belong to secretary (1, success),
Brain (1, success) and **headhunter (3, every one ``failure_class=stop``)**. The
miner buckets a failure only if the capability's tool appears in
``tool_sequence``, so those three real failures name nothing attributable and
cannot form an incident.

THE ``ACTION:`` SHAPE IS DELIBERATELY NOT RECOVERED. Its real name sits in the
SECOND token (``ACTION: shell``), and recovering it would mean teaching this
function one hallucinated format — over-fitting to three rows. The marker plus the
logged ``raw_head`` makes the shape findable without encoding it here.

THE INVARIANT BELONGS TO THE STORE that owns the column, not to one caller — any
future writer gets it for free, and there is one definition rather than five.

WHAT IS DELIBERATELY KEPT: a well-formed name for a tool that does NOT exist
(``read_webpage``, ``search_google``, ``exec``) is still recorded. That is the
self-extension signal — the platform learns which capabilities the model reaches
for and does not have — and CLAUDE.md already records the cost of removing a
writer without asking what it fed. Only values that CANNOT be a name are repaired.
"""

from __future__ import annotations

import logging

from stackowl.memory.outcome_store import normalize_tool_sequence

_LIVE_BLOB = (
    'ACTION: shell\n```json\n{"command": "curl -sS -o /tmp/li_test.html -w '
    "'%{http_code} %{size_download}\\n' --compressed -H 'User-Agent: Mozilla/5.0'\"}\n```"
)


def test_a_real_name_passes_through_untouched() -> None:
    """The overwhelming majority. Any change here corrupts 20,062 rows' worth of
    signal, so it is asserted first and byte-for-byte."""
    names = ("web_search", "browser_navigate", "note_applied_lesson", "memory")
    assert normalize_tool_sequence(names) == names


def test_the_live_blob_is_not_recorded_as_a_name() -> None:
    """The defect, verbatim from the live table."""
    (got,) = normalize_tool_sequence((_LIVE_BLOB,))
    assert "\n" not in got and len(got) <= 64, (
        f"a tool name still carries a payload: {got[:80]!r}"
    )
    assert "curl" not in got


def test_a_recoverable_name_is_RECOVERED_not_discarded() -> None:
    """``web_fetch\\n</parameter`` was a real call to a real tool that the parser
    mis-framed. Recording ``web_fetch`` is more truthful than recording a marker,
    and it keeps the turn's actual tool usage in the signal."""
    assert normalize_tool_sequence(("web_fetch\n</parameter",)) == ("web_fetch",)
    assert normalize_tool_sequence(("run_command\n```json\n{}",)) == ("run_command",)


def test_an_unrecoverable_value_becomes_a_bounded_marker() -> None:
    """``ACTION: shell`` has no name in its first token, so nothing can be
    recovered — but the fact that a malformed call happened must not vanish."""
    (got,) = normalize_tool_sequence((_LIVE_BLOB,))
    assert got and got.isidentifier()


def test_an_unknown_but_WELL_FORMED_name_survives() -> None:
    """The expensive direction. These are the self-extension signal — what the
    model reaches for and does not have. Dropping them would remove a writer
    without asking what it fed."""
    unknown = ("read_webpage", "search_google", "exec", "image_generate")
    assert normalize_tool_sequence(unknown) == unknown


def test_the_leak_is_visible(caplog) -> None:  # noqa: ANN001
    """A silent repair would leave the PARSER bug invisible for another six weeks.
    The store fixes the column; the log is what makes the cause findable."""
    with caplog.at_level(logging.INFO):
        normalize_tool_sequence((_LIVE_BLOB,))
    assert any("malformed tool name" in r.getMessage() for r in caplog.records)


def test_empty_and_odd_inputs_never_raise() -> None:
    """Recording an outcome may never be the thing that fails a turn."""
    assert normalize_tool_sequence(()) == ()
    assert normalize_tool_sequence(("",)) == ()
    assert normalize_tool_sequence(("   ",)) == ()


def test_the_prompt_block_bounds_the_tools_field() -> None:
    """The read-side half. Even with the column repaired, 20,062 existing rows
    still hold the blobs, and the prompt builder must not splice one in — every
    OTHER field on that line is already sliced."""
    import inspect

    from stackowl.pipeline.steps import classify

    src = inspect.getsource(classify._gather_recent_actions)  # noqa: SLF001
    tools_line = next(
        (line for line in src.splitlines() if "tools = " in line), ""
    )
    assert "[:" in tools_line, (
        f"the tools field is unbounded while its siblings are sliced: {tools_line.strip()!r}"
    )
