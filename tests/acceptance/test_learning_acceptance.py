"""Learning-arc acceptance gate (LS8 — ADR-S1..S3, spec ``.ralph/LEARNING_ARCHITECTURE.md``).

This is the SINGLE discoverable regression gate that encodes "did the agent ACTUALLY
LEARN" for the preference/skill learning arc. It exists so the validated chat failure —
*a stated output style lost turn after turn, the model drifting back to ``*`` while
claiming "✅ learned"* — can never silently return.

EVERY assertion here is on a MEASURED fact: a row in the real ``PreferenceStore`` /
``SkillIndexStore``, or the POST-SEAM BYTES that the deterministic delivery enforcer
(``OutputStyle.enforce`` / ``apply_output_preferences``) actually produced. NONE assert
on response prose or on an LLM judge — the only thing faked is the provider (the
classifier verdict is scripted; the stores, the delivery seam and the feedback step are
all real). A pref that "looks stored" but is not enforced on the next bytes is the exact
lie this gate refuses to pass.

The 6 evals (LEARNING_ARCHITECTURE.md "Murat's acceptance suite"):

  1. pref persists+applies across RESTART — a stored ``output_style`` survives a real
     pool reopen (fresh PreferenceStore over the same DB file) and the delivered bytes
     of a table+``*`` render carry NO table and NO ``*``. The restart IS the point.
  2. negative flips next output over a MULTI-TURN loop — a scripted negative/format
     reaction sets ``markdown=minimal`` in the store, and over N≥3 later renders the
     rejected ``*`` shape NEVER recurs (a single-turn check would miss the chat bug).
  3. enforcement fired — under an active style the transform runs and the post-seam
     bytes conform regardless of model (non-)compliance; asserted on bytes, not a flag.
  4. aspect-scope — mixed positive/content + negative/format changes ONLY the format
     rule; a content-only positive writes NO format rule.
  5. skill stats move on use + no-op-no-tick — ``skill_view`` ticks ``n_executions``;
     an injected-but-unviewed skill does NOT tick it (the fake-learning tripwire).
  6. feedback classification is measured — a scripted verdict yields the right
     polarity×aspect (incl. mixed) and abstains on low confidence.

Reuse (code-simplifier): the proven per-story doubles/tests are imported rather than
rebuilt — the LS3 classifier provider-fakes, the LS4 ``ScriptedClassifier``/result
builder, the LS7 skill-stat tests — aliased to ``_``-names so pytest does not re-collect
them. Evals 5 & 6 invoke the proven tests directly; evals 1 & 2 add the cross-cutting
cases (restart persistence, multi-turn negative-flip) not covered by any single story.
"""

from __future__ import annotations

import json
from pathlib import Path

from stackowl.channels._format import (
    OUTPUT_STYLE_KEY,
    OutputStyle,
    load_output_style,
    resolve_output_style,
)
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.interaction.feedback_classifier import FeedbackSignal
from stackowl.memory.preferences import PreferenceStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import feedback
from stackowl.providers.base import Message

# --- reuse proven per-story doubles/tests (no rebuilt harnesses) ------------------
from tests.interaction.test_feedback_classifier import (
    test_low_confidence_sets_abstain as _proven_abstain,
)
from tests.interaction.test_feedback_classifier import (
    test_mixed_content_positive_format_negative as _proven_mixed,
)
from tests.interaction.test_feedback_classifier import (
    test_positive_keep_it as _proven_positive,
)
from tests.pipeline.test_feedback_capture import (
    ScriptedClassifier,
    _result,
)
from tests.skills.test_skill_usage_loop import (
    test_injected_but_unviewed_skill_does_not_tick as _proven_no_tick,
)
from tests.skills.test_skill_usage_loop import (
    test_skill_view_increments_n_executions as _proven_tick,
)

# A render whose NATURAL form has both a GFM table and ``*`` emphasis — the "table
# bait" the gate pushes through the delivery seam. Enforcement must strip both.
_TABLE_AND_STARS = (
    "Here is the data:\n\n"
    "| Name | Age |\n"
    "| --- | --- |\n"
    "| Bob | 3 |\n\n"
    "And a **bold** closing note."
)


async def _open_pool(db_path: Path) -> DbPool:
    """Open a fresh DbPool over an on-disk DB (migrations already applied)."""
    pool = DbPool(db_path=db_path)
    await pool.open()
    return pool


def _fb_state(render: str, owner_key: str) -> PipelineState:
    """A pipeline state whose last render is ``render`` (the thing being reacted to)."""
    return PipelineState(
        trace_id="t-acc", session_id="sess-acc", input_text="(user reaction)",
        channel="telegram", owl_name="secretary", pipeline_step="feedback",
        identity_key=owner_key,
        history=(Message(role="assistant", content=render),),
    )


async def _run_feedback(
    render: str, owner_key: str, classifier: object, store: PreferenceStore,
    db: DbPool,
) -> PipelineState:
    """Drive the REAL LS4 feedback step with only the classifier (provider) scripted."""
    services = StepServices(preference_store=store, db_pool=db)
    services.feedback_classifier = classifier  # type: ignore[assignment]
    token = set_services(services)
    try:
        # LAT.3 — feedback.run() now only STARTS classification as a concurrent
        # task; join it here so this helper returns the same final state
        # run() returned synchronously before that story.
        out = await feedback.run(_fb_state(render, owner_key))
        if out.feedback_classify_task is not None:
            out = await out.feedback_classify_task
        return out
    finally:
        reset_services(token)


# ---------------------------------------------------------------------------
# Eval 1 — pref persists + applies across RESTART  (ADR-S2 / LS1+LS2)
# ---------------------------------------------------------------------------
async def test_eval1_pref_persists_and_applies_across_restart(tmp_path: Path) -> None:
    """Eval #1. Set ``output_style`` (markdown=minimal, tables=off) → SIMULATE A RESTART
    by closing the pool and reopening a fresh ``PreferenceStore`` over the SAME DB file →
    the pref ROW still exists AND the delivery enforcement strips both the table and the
    ``*`` from a table-bait render. The restart (fresh connections re-reading disk) is the
    "you lost it" guard; the assertion is on the post-seam bytes, never on prose."""
    owner_key = "telegram:7"
    db_path = tmp_path / "restart.db"
    MigrationRunner(db_path=db_path).run()

    # --- pre-restart: write the explicit style through a real store, then close it. ---
    pool_a = await _open_pool(db_path)
    try:
        await PreferenceStore(db=pool_a).set(
            owner_key, OUTPUT_STYLE_KEY,
            json.dumps({"markdown": "minimal", "tables": "off"}),
        )
    finally:
        await pool_a.close()

    # --- RESTART: a brand-new pool + store over the same on-disk DB. -----------------
    pool_b = await _open_pool(db_path)
    try:
        store_b = PreferenceStore(db=pool_b)

        # (i) measured: the pref ROW survived the restart and resolves to the spec.
        raw = await store_b.get(owner_key, OUTPUT_STYLE_KEY)
        assert raw is not None, "output_style row did not survive the restart"
        persisted = resolve_output_style({OUTPUT_STYLE_KEY: raw})
        assert persisted.markdown == "minimal"
        assert persisted.tables == "off"

        # (ii) measured: the delivery seam (store → load → enforce) conforms the bytes.
        style = await load_output_style(store_b, owner_key)
        delivered = style.enforce(_TABLE_AND_STARS)
    finally:
        await pool_b.close()

    assert "|" not in delivered, "a table survived enforcement after restart"
    assert "*" not in delivered, "an asterisk survived enforcement after restart"
    assert "Name: Bob" in delivered and "bold" in delivered  # content kept, form stripped


# ---------------------------------------------------------------------------
# Eval 2 — negative flips next output over a MULTI-TURN loop  (ADR-S2 / LS3+LS4)
# ---------------------------------------------------------------------------
async def test_eval2_negative_flips_next_output_multi_turn(tmp_db: DbPool) -> None:
    """Eval #2. A scripted negative/format reaction to a render containing ``*`` drives
    the REAL LS4 feedback step → ``output_style.markdown`` becomes ``minimal`` in the
    store. Then N≥3 SUBSEQUENT renders (each naturally full of ``*``) are pushed through
    the delivery seam and the rejected ``*`` shape NEVER recurs in ANY of them. A
    single-turn assert would miss the chat bug (drift returns on turn 2+) — so we iterate
    and re-read the store every turn, proving the rule is durable, not momentary."""
    owner_key = "telegram:99"
    store = PreferenceStore(db=tmp_db)
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))

    out = await _run_feedback("Here is **bold** again.", owner_key, classifier, store, tmp_db)
    assert out.feedback_handled is True

    # (i) measured: the rejection landed as an enforceable rule in the store.
    style0 = await load_output_style(store, owner_key)
    assert style0.markdown == "minimal"

    # (ii) the multi-turn loop: every later render re-reads the store and is enforced;
    # the rejected shape must be absent each turn (durability across turns, not 1 turn).
    later_drafts = (
        "**Headline one** — big news today.",
        "Quick update: see **this** and __that__.",
        "Third reply with **stars** and more **bold** text.",
        "Even later, the model tried **bold** yet again.",
    )
    for turn, draft in enumerate(later_drafts):
        style = await load_output_style(store, owner_key)  # re-read every turn
        assert style.markdown == "minimal", f"rule lost by turn {turn}"
        delivered = style.enforce(draft)
        assert "*" not in delivered, f"rejected `*` shape recurred on turn {turn}"
        assert "__" not in delivered


# ---------------------------------------------------------------------------
# Eval 3 — enforcement fired (post-seam bytes, independent of model compliance)
# ---------------------------------------------------------------------------
def test_eval3_enforcement_fired_on_bytes() -> None:
    """Eval #3 (ADR-S2 / LS2). Under an active style, a NON-COMPLIANT draft (the model
    ignored the style and emitted ``*`` + a table) is conformed at the delivery seam: the
    transform actually ran (bytes changed) AND the post-seam bytes hold the spec. Asserted
    on the produced bytes — not on a "did it fire" flag — and the verifier is a fixed
    point (idempotent), so enforcement is the guarantee, not model goodwill."""
    style = OutputStyle(markdown="minimal", tables="off")
    delivered = style.enforce(_TABLE_AND_STARS)

    assert delivered != _TABLE_AND_STARS  # the transform measurably ran
    assert "*" not in delivered
    assert "|" not in delivered
    # Independent of compliance + idempotent: re-enforcing conformed bytes is a no-op.
    assert style.enforce(delivered) == delivered


# ---------------------------------------------------------------------------
# Eval 4 — aspect-scope (only the rejected aspect changes)  (ADR-S2 / LS4)
# ---------------------------------------------------------------------------
async def test_eval4_aspect_scope(tmp_db: DbPool) -> None:
    """Eval #4. Mixed feedback (positive/content + negative/format) about a ``*`` render
    changes ONLY the format rule (markdown→minimal). A content-only positive about an
    equally ``*``-laden render writes NO format rule at all. Two owner keys over one real
    store; the assertion is on what rows exist, never on prose (the whole-message-polarity
    wrong-capture bug is exactly what this refutes)."""
    store = PreferenceStore(db=tmp_db)
    mixed_owner, content_owner = "telegram:mix", "telegram:content"

    # Mixed: positive content + negative format → only the format rule is written.
    mixed = ScriptedClassifier(_result(
        FeedbackSignal(polarity="positive", aspect="content", confidence=0.9),
        FeedbackSignal(polarity="negative", aspect="format", confidence=0.9),
    ))
    await _run_feedback("Great answer, but **bold** everywhere.", mixed_owner,
                        mixed, store, tmp_db)
    assert (await load_output_style(store, mixed_owner)).markdown == "minimal"

    # Content-only positive → NO output_style row (format untouched).
    content = ScriptedClassifier(_result(
        FeedbackSignal(polarity="positive", aspect="content", confidence=0.9)))
    out = await _run_feedback("Loved the substance — **bold** and a table maybe.",
                              content_owner, content, store, tmp_db)
    assert await store.get(content_owner, OUTPUT_STYLE_KEY) is None  # no format rule
    assert out.feedback_handled is False  # normal turn proceeds, nothing captured


# ---------------------------------------------------------------------------
# Eval 5 — skill stats move on use + no-op-no-tick  (ADR-S3 / LS7)
# ---------------------------------------------------------------------------
async def test_eval5_skill_stats_move_on_use_and_no_op_no_tick(tmp_db: DbPool) -> None:
    """Eval #5. Reuses the proven LS7 measured-fact tests wholesale: ``skill_view`` ticks
    ``n_executions`` (the application seam), and an injected-but-unviewed skill does NOT
    tick it (the fake-learning tripwire — counting at injection would fail the second).
    Both assert on the ``SkillIndexStore`` stats, never on prose."""
    await _proven_tick(tmp_db)      # skill_view → n_executions == 1
    await _proven_no_tick(tmp_db)   # injected-but-unviewed → n_executions stays 0


# ---------------------------------------------------------------------------
# Eval 6 — feedback classification is measured  (ADR-S2 / LS3)
# ---------------------------------------------------------------------------
async def test_eval6_feedback_classification_is_measured() -> None:
    """Eval #6. Reuses the proven LS3 classifier tests: a scripted model verdict yields
    the right polarity (positive), the right polarity×aspect for MIXED feedback
    (content=positive, format=negative), and ABSTAINS below the confidence threshold. The
    classifier is driven by the verdict (no English wordlist); every assertion is on the
    parsed :class:`FeedbackResult`, never on prose."""
    await _proven_positive()  # confident positive verdict → positive
    await _proven_mixed()     # mixed verdict → {content: positive, format: negative}
    await _proven_abstain()   # low-confidence verdict → abstain (ask, don't guess)
