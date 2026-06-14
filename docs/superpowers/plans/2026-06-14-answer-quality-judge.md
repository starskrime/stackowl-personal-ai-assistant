# Answer-Quality Judge (grade the answer, not the tool) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The persistence judge first decides whether the request needs an external action and accepts a real tool-free reply to a no-action request as DELIVERED, without weakening the dressed-up-giveup / lazy-refusal detection for action requests — so a no-task message (a residual-misclassified compliment, or a knowledge question on a standard turn) is no longer nudged into a spin.

**Architecture:** A single prompt-only reframe of `_build_messages` in `pipeline/persistence.py`. The system prompt gains a leading "needs an external action?" gate + a directly-answerable=delivered criterion; the four existing give-up shapes are preserved verbatim under an explicit "FOR A REQUEST THAT REQUIRES AN EXTERNAL ACTION" heading. `judge_delivery`, the schema, the nudge, and the structural-veto composition are unchanged.

**Tech Stack:** Python 3.13, existing `judge_delivery`/`summarize_tool_outcomes` (`pipeline/persistence.py`), pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/pipeline/persistence.py` | Modify (`_build_messages` only) | Reframed judge prompt: needs-a-tool gate + directly-answerable=delivered |
| `tests/pipeline/test_answer_quality_judge.py` | **Create** | prompt-content + wiring units |
| `tests/journeys/test_no_action_not_giveup_journey.py` | **Create** | verdict→no-nudge gateway journey + control |

---

## Task 1: Reframe the judge prompt

**Files:**
- Modify: `src/stackowl/pipeline/persistence.py` (`_build_messages`, lines 265-349 — ONLY the `system` + `user` Message content)
- Test: `tests/pipeline/test_answer_quality_judge.py`

**Context:** `_build_messages` builds the judge prompt. Today every criterion is framed around an action/escape-hatch, with no "needs no tool" path, so a tool-free reply reads as give-up. Reframe the prompt content ONLY. Do NOT change the function signature, `_TOOLS_CAP`/`_REQUEST_CAP`/`_DRAFT_CAP` usage, the `tool_list` line, `judge_delivery`, or `summarize_tool_outcomes`.

- [ ] **Step 0:** Read the EXISTING persistence tests (grep `tests/` for `_build_messages` / `judge_delivery` / `summarize_tool_outcomes` — e.g. `tests/pipeline/test_persistence.py`). Note any test that asserts CURRENT prompt wording (so you know what must still hold). Confirm the import path for `_build_messages` (it's module-private — the existing tests show how they import it). Report what you find.

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_answer_quality_judge.py`:

```python
from stackowl.pipeline.persistence import _build_messages


def _prompt(req="hello", draft="hi there", tools=None) -> str:
    msgs = _build_messages(req, draft, tools or [])
    return "\n".join(m.content for m in msgs).lower()


def test_prompt_has_needs_external_action_gate():
    p = _prompt()
    assert "external action" in p
    # an "answerable directly / from knowledge" notion is present
    assert "directly" in p or "knowledge" in p


def test_prompt_accepts_tool_free_reply_to_no_action_request():
    p = _prompt()
    # the directly-answerable DELIVERED criterion: no tools is correct here
    assert "no-action" in p or "no tools is correct" in p or "tool-free reply" in p


def test_prompt_preserves_dressed_up_giveup_shape():
    p = _prompt()
    # claims-but-failed
    assert "failed" in p and "not delivery" in p
    # hand-back
    assert "manual steps" in p or "hands the task back" in p
    # technical-excuse-without-running
    assert "no command was run" in p or "never ran a command" in p


def test_prompt_scopes_giveup_to_action_requests():
    p = _prompt()
    # the give-up shapes are explicitly scoped to action-requiring requests
    assert "requires an external action" in p or "needs an external action" in p


def test_schema_instruction_unchanged():
    p = _prompt()
    assert '{"delivered"' in p or '"delivered": true' in p
```

> Match the existing persistence-test import idiom from Step 0 (the tests already import module-private helpers, so importing `_build_messages` is consistent).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_answer_quality_judge.py -q`
Expected: FAIL — the gate / no-action / scoping phrases are not in the current prompt.

- [ ] **Step 3: Implement.** In `src/stackowl/pipeline/persistence.py`, replace the `system` and `user` `Message(...)` content in `_build_messages` (keep the `tool_list = ...` line above and the `return [system, user]` below) with:

```python
    system = Message(
        role="system",
        content=(
            "You are a delivery judge for an autonomous AI agent that can also "
            "run commands, install or build software, author new skills, and "
            "search for methods. Given the user's request, the agent's draft "
            "reply, and the tools the agent used this turn, decide — by the "
            "user's intent in ANY language — whether the agent DELIVERED or "
            "gave up.\n\n"
            "STEP 1 — DOES THE REQUEST NEED AN EXTERNAL ACTION? First decide "
            "whether fulfilling the request REQUIRES an external action — "
            "sending, creating, changing, or running something, or fetching "
            "live/external data — OR is answerable directly from the "
            "conversation or the agent's own knowledge: a greeting, thanks, a "
            "compliment, an opinion or reaction, chit-chat, an acknowledgement, "
            "or a question the agent can answer from what it knows. Judge this "
            "by meaning, not by any fixed keyword.\n\n"
            "DELIVERED (delivered=true) — any of these:\n"
            "  • The request was answerable directly (needs no external action) "
            "and the draft gives a real, on-point reply. Using NO tools is "
            "CORRECT for such a request — a tool-free reply to a no-action "
            "request is NOT a give-up.\n"
            "  • The request needed an external action and the agent produced "
            "the requested outcome.\n"
            "  • The agent asked ONE necessary clarifying question because the "
            "request is genuinely ambiguous and cannot proceed without that "
            "information — asking is taking action, not giving up.\n"
            "  • The agent stated a specific, concrete blocker AFTER actually "
            "trying the escape hatch — i.e. the TOOLS USED list shows it really "
            "ran commands and/or installed/built a tool, and those genuinely "
            "failed (or a required credential it cannot obtain / a hardware "
            "resource it does not have remained the true obstacle).\n\n"
            "THE UNIVERSAL ESCAPE HATCH (applies ONLY when the request needs an "
            "external action). The agent's single most powerful capability is "
            "running a command in a shell and installing or building a tool. "
            "Before concluding an action task is impossible, the agent MUST try "
            "that escape hatch. The TOOLS USED list gives each tool used this "
            "turn AND its outcome as name(ok) or name(failed); name(failed) "
            "means that call did NOT do what it was supposed to. Reason about "
            "whether the tools that matter actually succeeded (running a "
            "command / installing / building) versus only browsing, reading, or "
            "fetching. (Judge by meaning, not by any fixed keyword.)\n\n"
            "GAVE UP (delivered=false) — FOR A REQUEST THAT REQUIRES AN EXTERNAL "
            "ACTION, any of these:\n"
            "  • The draft claims something was produced, sent, accessed, "
            "converted, or done, but the tool call that would accomplish it is "
            "marked failed (or no tool capable of it succeeded). A failed tool "
            "call is NOT delivery. Rule this a give-up.\n"
            "  • The agent refused, apologized, or deferred WITHOUT exhausting "
            "its capabilities: it could have run a command, installed or built "
            "something, authored a skill, or searched for a method — but did "
            "not.\n"
            "  • The agent claims a technical or capability limitation as the "
            "reason it did not deliver BUT the TOOLS USED list shows it only "
            "browsed, read, or fetched and never ran a command nor installed or "
            "built anything. A plausible-sounding technical excuse is NOT "
            "acceptable until the agent has actually attempted to overcome it by "
            "running a command or installing/building a tool. Rule this a "
            "give-up.\n"
            "  • HANDS THE TASK BACK: gives the user manual steps or "
            "instructions to do it themselves, or claims to have 'built'/'set "
            "up' something for the user INSTEAD OF performing the requested "
            "action.\n\n"
            "Return ONLY a JSON object — no prose, no markdown fences. Schema: "
            '{"delivered": true|false, "reason": "<one short sentence>"}.'
        ),
    )
    user = Message(
        role="user",
        content=(
            f"USER REQUEST:\n{user_request[:_REQUEST_CAP]}\n\n"
            f"AGENT DRAFT REPLY:\n{draft_answer[:_DRAFT_CAP]}\n\n"
            f"TOOLS USED THIS TURN (name and outcome): {tool_list}\n\n"
            "First decide whether this request needs an external action or is "
            "answerable directly (a greeting, an opinion, an acknowledgement, or "
            "a question answerable from knowledge). If it is answerable directly "
            "and the draft gives a real on-point reply, that is DELIVERED — "
            "using no tools is correct. If it requires an external action: a "
            "draft that claims it produced, sent, accessed, or did something "
            "while the backing tool is marked failed (or no capable tool "
            "succeeded) is NOT delivered; and a draft that claims a technical or "
            "capability limitation while the outcomes show no command was run "
            "and nothing was installed or built is also a give-up.\n"
            'Output exactly: {"delivered": true, "reason": "..."}'
        ),
    )
    return [system, user]
```

- [ ] **Step 4: Run to verify it passes** + regression.

Run: `uv run pytest tests/pipeline/test_answer_quality_judge.py -q` (pass). Existing persistence + self-heal regression (MUST stay green — the give-up shapes are preserved): `uv run pytest tests/pipeline/ -q -k "persistence or judge or self_heal or giveup"`. `uv run mypy src/stackowl/pipeline/persistence.py` (clean). `uv run ruff check` the 2 files.

> If a Step-0 test asserted now-removed exact wording, evaluate: does the give-up SHAPE it checks still hold under the reframe? If yes, update that assertion to the new phrasing (note it). If a test asserts something the reframe genuinely changes in intended behavior, STOP and report — do not silently weaken a dressed-up-giveup assertion.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/persistence.py tests/pipeline/test_answer_quality_judge.py
git commit -m "feat(v2): answer-quality judge — gate on 'needs an external action', accept tool-free reply to no-action request"
```

---

## Task 2: Gateway journey — no-action reply not nudged + control + regression

**Files:**
- Create: `tests/journeys/test_no_action_not_giveup_journey.py`

**Context:** End-to-end: a `standard` turn with a no-action message where the model replies without tools and the judge (returning `delivered=true`, consistent with the reframed criteria) → assert NO persistence nudge is injected and the reply is delivered. Control: an action turn whose draft claims success with a FAILED tool → the structural veto / give-up path still fires (dressed-up-giveup intact). STUDY `tests/journeys/test_self_heal_lying_judge.py` (dual judge/router double + real OpenAIProvider) and `tests/journeys/test_no_dressed_up_giveup_journey.py` (the consequential-failure → floor harness); reuse them.

- [ ] **Step 1: No-action-not-nudged journey.** Router double routes secretary/standard (force the standard path so the judge runs); the execute provider double replies with a substantive no-tool draft; the judge double returns `{"delivered": true, "reason": "..."}`. Assert: the turn delivers the draft AND no persistence nudge/second-iteration occurred (mirror how `test_self_heal_lying_judge.py` detects a nudge — e.g. the provider's iteration count stays 1, or the `PERSISTENCE_DIRECTIVE`/"persistence nudge" log is absent). This proves a `delivered=true` verdict ends the turn cleanly.

- [ ] **Step 2: Control — dressed-up give-up still caught.** Reuse the `test_no_dressed_up_giveup_journey.py` harness: a consequential tool FAILS + the draft claims success → assert the honest floor still replaces the draft (the structural veto path is unchanged by the judge reframe).

```python
# tests/journeys/test_no_action_not_giveup_journey.py
# (1) standard no-action turn, no-tool draft, judge delivered=true → delivered, NO nudge.
# (2) control: consequential fail + claimed success → honest floor (structural veto intact).
```

- [ ] **Step 2b: Run; confirm PASS.** `uv run pytest tests/journeys/test_no_action_not_giveup_journey.py -q`. If the no-action turn still nudges with a delivered=true verdict, the wiring is wrong — STOP, report BLOCKED.

- [ ] **Step 3: Full regression.**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: prior green counts + the new journey, ZERO failures. Watch `test_no_dressed_up_giveup_journey`, `test_self_heal_*`, the conversational/graceful journeys. If any regress, STOP and report BLOCKED.

- [ ] **Step 4: Lint + commit.**

Run: `uv run ruff check tests/journeys/test_no_action_not_giveup_journey.py`.

```bash
git add tests/journeys/test_no_action_not_giveup_journey.py
git commit -m "test(v2): journeys — no-action reply not nudged (FR1 wiring), dressed-up give-up still floored (FR2/FR4)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1 no-action delivered → T1 (prompt content) + T2 (verdict→no-nudge wiring; LLM verdict live-verified); FR2 dressed-up give-up caught → T1 (shape preserved) + T2 control; FR3 prompt content → T1; FR4 composition → T2 control (structural veto intact); FR5 regression → T1 + T2 Step 3. All covered.
- **Placeholder scan:** T1-Step0 and T2 instruct READING existing tests/harnesses and reusing them (concrete grounding); the full reframed prompt is written out verbatim in T1-Step3; assertions are concrete. The T1-Step4 note about a possibly-existing wording assertion is a conditional with a guard (never weaken a dressed-up-giveup assertion). No TBD/TODO.
- **Type consistency:** `_build_messages(user_request, draft_answer, tools_tried) -> list[Message]` signature UNCHANGED (only content edited); `judge_delivery`/`summarize_tool_outcomes` untouched. The new test imports `_build_messages` (module-private, consistent with existing persistence tests). Consistent.

## Risk & containment
- **Risk:** the judge becomes too lenient → a real action task dodged as "conversational." **Contained:** the structural veto (`is_consequential_giveup_now`, ledger severity) fires for any real consequential tool failure regardless of the judge (T2 control asserts it); the give-up shapes for action requests are preserved verbatim.
- **Risk:** a Step-0 test pinned exact old wording. **Contained:** T1-Step4 handles it — update to the new phrasing IF the give-up shape still holds, else STOP (never silently weaken).
- **Risk:** the LLM judge ignores the gate. **Contained (honestly):** the prompt + structural veto are the mechanisms; whether a weak judge model obeys the gate is LIVE verification (box offline) — stated in the spec.
- **Rollback:** single-file prompt revert (see spec).
