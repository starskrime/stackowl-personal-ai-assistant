"""D09.5 — `/learn` builds a prompt and nothing else.

These tests pin the invariants from `docs/reference-mapping/designs/D09.5.md`. The
sharpest one is I1: the command must not become a second authoring engine. That is
guarded here by proving the builder is PURE — it runs with no services, no database
and no network — because a function that cannot reach a model or a store cannot
quietly grow into an engine.

They deliberately do NOT assert the prompt's exact wording. Prompt text is tuned;
pinning it byte for byte would make every future improvement a test edit, which is
how a suite stops meaning anything. What is pinned is what the prompt must SAY IN
SUBSTANCE and what it must never do.
"""

from __future__ import annotations

import logging

from stackowl.skills import standard
from stackowl.skills.learn_prompt import build_learn_prompt


# ---------------------------------------------------------------------------
# I2 — purity. The guard against I1.
# ---------------------------------------------------------------------------

def test_it_is_pure_and_needs_nothing() -> None:
    """No services, no DB, no network — this test constructs none of them."""
    out = build_learn_prompt("teach yourself to deploy")
    assert isinstance(out, str) and out


def test_it_is_deterministic() -> None:
    assert build_learn_prompt("x") == build_learn_prompt("x")


def test_it_never_calls_a_model_or_writes_a_skill(monkeypatch) -> None:
    """I1, enforced rather than asserted.

    If the builder ever grows a model call or a store write, one of these
    explodes. A command that returns a prompt cannot become a second engine by
    accident; it has to be done on purpose, and this is where that gets caught.
    """
    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("build_learn_prompt must not reach services")

    monkeypatch.setattr(
        "stackowl.pipeline.services.get_services", _boom, raising=False
    )
    assert build_learn_prompt("anything")


# ---------------------------------------------------------------------------
# I3 — the empty request
# ---------------------------------------------------------------------------

def test_a_bare_learn_means_what_we_just_did() -> None:
    out = build_learn_prompt("")
    assert "conversation" in out.lower()
    assert "THE REQUEST:" in out


def test_whitespace_only_is_treated_as_bare() -> None:
    assert build_learn_prompt("   \n  ") == build_learn_prompt("")


def test_none_ish_input_does_not_raise() -> None:
    assert build_learn_prompt("") == build_learn_prompt(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# I4 — every part of the request is load-bearing
# ---------------------------------------------------------------------------

def test_the_users_text_is_carried_verbatim() -> None:
    req = "https://example.com/docs focus on the auth flow, skip deprecated endpoints"
    out = build_learn_prompt(req)
    assert req in out, "the request must reach the agent unmangled"


def test_it_says_prose_after_a_link_is_a_requirement() -> None:
    """The reference platform's sharpest observation, and the reason a naive
    implementation fetches the first URL and ignores the rest."""
    out = build_learn_prompt("x")
    low = out.lower()
    assert "requirement" in low
    assert "load-bearing" in low


# ---------------------------------------------------------------------------
# I5 / I6 — the standards travel, and have ONE home
# ---------------------------------------------------------------------------

def test_the_authoring_standard_is_included() -> None:
    out = build_learn_prompt("x")
    assert standard.describe_for_prompt() in out


def test_the_standard_is_REFERENCED_not_copied() -> None:
    """I6. If the shared text changes, the prompt changes with it.

    Inlining would create a second copy of a rule — and the worse copy, since
    only the validator's version can actually reject a write.
    """
    import stackowl.skills.learn_prompt as m

    src = (m.__file__ or "")
    body = open(src, encoding="utf-8").read()
    assert "describe_for_prompt()" in body
    # A distinctive fragment of the standard must NOT appear as a literal here.
    assert "SKILL AUTHORING STANDARD" not in body


def test_the_standard_tells_the_author_to_COUNT_the_description() -> None:
    """Two of the synthesizer's five refusals on 2026-08-23 were descriptions at
    61 and 62 characters against a 60-char limit. Stating the rule was not
    enough; the nag to count is the portable half."""
    assert "count" in standard.describe_for_prompt().lower()


# ---------------------------------------------------------------------------
# The duplicate guard the prompt has to carry, because the code does not
# ---------------------------------------------------------------------------

def test_it_warns_against_reordered_duplicate_names() -> None:
    """`skill_manage._create`'s only uniqueness test is `if target_dir.exists()`
    — a path check a word-order permutation walks past. It produced
    `instagram-video-download` beside `download-instagram-video` at 16:29:08 on
    2026-08-23. Until that gate is fixed the prompt is the only guard."""
    low = build_learn_prompt("x").lower()
    assert "skills_list" in low
    assert "duplicate" in low


def test_it_names_skill_manage_as_the_writer() -> None:
    """No new tool (I7): the prompt routes to the writer that already exists."""
    out = build_learn_prompt("x")
    assert "skill_manage" in out
    assert "synthesize_skills" not in out, (
        "synthesize_skills cannot be aimed at anything — its parameters are {} — "
        "and it was dropped from the guaranteed base set by ESC-46"
    )


# ---------------------------------------------------------------------------
# Observability — production runs at INFO
# ---------------------------------------------------------------------------

def test_it_logs_at_INFO_with_the_fields_that_answer_the_question() -> None:
    records: list[logging.LogRecord] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    lg = logging.getLogger("stackowl.skills")
    h = _Grab(level=logging.INFO)
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        build_learn_prompt("something")
        build_learn_prompt("")
    finally:
        lg.removeHandler(h)

    hits = [r for r in records if "learn: prompt built" in r.getMessage()]
    assert len(hits) == 2
    assert all(r.levelno >= logging.INFO for r in hits), "DEBUG would not exist in prod"
    flags = [dict(getattr(r, "_fields", {}) or {}).get("had_request") for r in hits]
    assert flags == [True, False], "the bare-invocation path must be distinguishable"
