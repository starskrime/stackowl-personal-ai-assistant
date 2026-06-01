"""J8 JOURNEY — "Batch-approved multi-step automation" (PRD §3, J8).

The business requirement, from the PRD User Journey J8 (restated in
``tests/journeys/README.md`` §3, the PRD source for this repo):

  > **J8 — Batch-approved multi-step automation.** *"Run my morning routine."*
  > The owl plans N consequential actions and presents them as ONE BATCH
  > ("I will: 1… 2… 3 — approve all / pick / reject") rather than N separate
  > prompts. Approve-all executes under a bounded, audited window.

  Business outcome: the user approves a multi-step automation as ONE batch
  decision (NOT N separate consent prompts), and the approved actions then
  execute (audited).

STATUS: ⏳ NOT BUILT — the J8 batch-consent UX does not exist. This file is a
SKIP-MARKED PLACEHOLDER that documents the gap so it is tracked (per the journey
README's "real finding → STOP + inform; never patch the test to hide it" rule),
NOT a green journey test. Faking a batch flow to force a pass is explicitly the
wrong outcome here.

-------------------------------------------------------------------------------
THE GAP — what exists today vs what J8 needs (evidence, file:line)
-------------------------------------------------------------------------------
What EXISTS (E0 consent gate) is irreducibly PER-ACTION:

  * ``ConsentRequest`` carries exactly ONE ``tool_name`` + ONE ``summary``
    — there is no field for a LIST of planned actions.
    src/stackowl/tools/consent.py:71-82
  * ``ConsentPolicy.request(tool_name=..., ...)`` decides ONE tool at a time.
    src/stackowl/tools/consent.py:186-256
  * ``TelegramConsentPrompter.prompt(req)`` sends ONE inline keyboard and
    suspends on ONE Future per ``ConsentRequest``; the buttons are
    ONCE / DENY / SESSION / WINDOW — there is NO "approve all the planned
    actions" affordance, because no list of planned actions ever reaches it.
    src/stackowl/channels/telegram/consent.py (the four-button inline keyboard)
    + src/stackowl/tools/consent.py (ConsentScope members, all single-action)
  * The pipeline calls ``gate.check(t, ...)`` ONCE PER INDIVIDUAL tool dispatch
    inside the tool loop — so N consequential tools ⇒ N independent prompts.
    src/stackowl/pipeline/steps/execute.py:130-134
  * The closest thing to "batch" is ``ConsentScope.SESSION`` — but that only
    auto-allows FUTURE calls of the SAME tool name for the session; the FIRST
    call to each distinct tool still prompts, and it is NOT a one-shot
    approve-all over a pre-presented plan of N DIFFERENT actions.
    src/stackowl/tools/consent.py:53-59, 213-214, 242-243

What J8 NEEDS and is MISSING:
  1. A PLAN→BATCH presentation step that gathers the N planned consequential
     actions and shows them to the user as ONE message ("I will: 1… 2… 3").
  2. A single batch consent decision (approve-all / pick-subset / reject-all)
     — one tap covering the whole plan, not N taps.
  3. Execution of the approved set under ONE bounded, audited window, with the
     user prompted ONCE rather than N times.

None of (1)-(3) exist in ``src/stackowl/`` (verified: no aggregated /
multi-action consent request, no approve-all button, no plan→consent bridge;
the only "batch" token is the per-tool-name session grant above).

-------------------------------------------------------------------------------
This placeholder asserts the GAP (so it can't silently false-pass once present)
-------------------------------------------------------------------------------
The single (skipped) test below codifies the J8 outcome to drive when the batch
UX lands. The non-skipped structural guard ``test_j8_batch_consent_surface_absent``
proves — from the REAL consent code, not constants — that no batch-approval
affordance exists yet; when J8 is built it will fail, flagging that the skipped
journey above must be turned on.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.tools import consent as consent_mod
from stackowl.tools.consent import ConsentRequest, ConsentScope

# Tokens any genuine "approve ALL the planned actions in one tap" affordance
# would have to introduce. None are control-tokens of the existing per-tool
# SESSION grant (which means "this tool for the session", not "this whole plan").
_BATCH_APPROVE_ALL_MARKERS = ("approve_all", "approveall", "batch_approve", "approve_plan")


def test_j8_batch_consent_surface_absent() -> None:
    """GUARD (NOT skipped): prove the J8 batch-approval affordance is NOT built.

    Derived from the REAL consent module — if someone adds a batch-approve scope
    or a multi-action consent request, this guard fails, signalling that the
    skipped J8 journey below must be implemented and enabled (no silent gap).
    """
    # 1. ConsentScope has no "approve all the planned actions" member. The four
    #    members are single-action scopes (ONCE/SESSION/WINDOW/DENY); SESSION is
    #    per-tool-name reuse, not a one-shot approve-all over a presented plan.
    scope_values = {s.value for s in ConsentScope}
    assert scope_values == {"once", "session", "window", "deny"}, (
        "ConsentScope changed — if a batch/approve-all scope was added, J8 may now "
        f"be buildable; enable the skipped journey. Scopes: {sorted(scope_values)}"
    )
    for marker in _BATCH_APPROVE_ALL_MARKERS:
        assert marker not in scope_values, (
            f"A batch-approval scope {marker!r} now exists — implement + enable the "
            "skipped J8 journey test."
        )

    # 2. ConsentRequest carries a SINGLE action (one tool_name), with NO field
    #    holding a LIST of planned actions to present as one batch.
    req_fields = set(inspect.signature(ConsentRequest).parameters)
    assert "tool_name" in req_fields, req_fields
    assert not (req_fields & {"tool_names", "actions", "plan", "steps", "batch"}), (
        "ConsentRequest gained a multi-action field — the J8 batch-presentation "
        f"surface may now exist; enable the skipped journey. Fields: {sorted(req_fields)}"
    )

    # 3. The consent module exposes no batch/approve-all symbol at all.
    src = inspect.getsource(consent_mod)
    for marker in _BATCH_APPROVE_ALL_MARKERS:
        assert marker not in src, (
            f"consent.py now references {marker!r} — the J8 batch path may be built; "
            "enable the skipped journey test."
        )


@pytest.mark.skip(
    reason=(
        "J8 batch-consent UX is NOT built. E0 consent is irreducibly per-action: "
        "ConsentRequest carries one tool_name (consent.py:71-82), the gate fires "
        "once per dispatch (execute.py:130-134), and the prompter shows one keyboard "
        "per action with ONCE/DENY/SESSION/WINDOW buttons (channels/telegram/consent.py). "
        "No plan->batch presentation, no approve-all-over-N-actions decision, no "
        "single audited window. See this file's module docstring for the full gap "
        "report. Turn this on when the batch-approval UX lands — the guard test "
        "test_j8_batch_consent_surface_absent will fail at that point to remind us."
    )
)
async def test_j8_run_morning_routine_one_batch_approval() -> None:
    """J8 OUTCOME (driven when the batch-approval UX ships).

    Drive *"Run my morning routine"* through the REAL gateway (Telegram adapter →
    scanner → AsyncioBackend → execute._dispatch → ToolRegistry), mocking ONLY the
    AI provider (a scripted secretary honoring the ModelProvider contract, exactly
    like the sibling J1/J6 journeys). The owl plans N (>= 3) consequential actions.

    ASSERT THE USER OUTCOME (NOT tool return-shapes):
      1. ONE batch consent prompt is delivered to the user listing all N planned
         actions ("I will: 1… 2… 3 — approve all / pick / reject") — exactly ONE
         keyboard, not N separate prompts.
      2. The user approves-all with ONE tap.
      3. All N actions then EXECUTE, with REAL audited side-effects derived from
         REAL outputs (mutation-mindful: assert on real execution artifacts, e.g.
         N rows in the jobs/notification_log/audit_log tables, NOT on constants).
      4. The user was prompted EXACTLY ONCE — count the consent keyboards
         delivered to the user's chat and assert it equals 1, NOT N. This is the
         load-bearing J8 assertion (one batch decision, not N consent prompts).

    Until the batch UX exists this body cannot be written against a real path
    without fabricating the very affordance under test — so it is skipped, not
    faked. The structural guard above keeps the gap honest.
    """
    raise NotImplementedError(
        "J8 batch-consent UX not built — see module docstring gap report."
    )
