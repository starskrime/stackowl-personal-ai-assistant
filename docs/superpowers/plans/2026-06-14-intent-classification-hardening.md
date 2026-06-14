# Intent-Classification Hardening + Graceful Bare-Timeout Floor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A purely social message (greeting/thanks/compliment/opinion/chit-chat) reliably classifies `conversational` via the existing single routing call (zero tools, instant reply), and any give-up with no real capability data delivers a warm honest message instead of a blank capability template that leaks `budget cap reached: time…`.

**Architecture:** Two localized edits. (1) `router.py`: broaden the line-2 definition, scan ALL reply lines for the class token, raise the token cap; fail-safe stays `standard`, owl parse unchanged. (2) `supervisor.py` + `localize.py` + `execute.py`: a new `self_heal_floor_graceful` localize key; `synthesize_floor` returns it when there is no failed capability / attempts / partial; the default-backstop empty-timeout floor call stops feeding the raw budget error into the user-facing message.

**Tech Stack:** Python 3.13, existing `localize`/`localize_format` (`setup/localize.py`), `SecretaryRouter` (`owls/router.py`), `synthesize_floor` (`pipeline/supervisor.py`), pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/owls/router.py` | Modify (`_build_prompt`, `_parse_intent_class`, `_ROUTING_MAX_TOKENS`) | Robust piggybacked conversational classification |
| `src/stackowl/setup/localize.py` | Modify (add key) | `self_heal_floor_graceful` (en/de/fr/es) |
| `src/stackowl/pipeline/supervisor.py` | Modify (`synthesize_floor`) | Graceful branch when no capability data |
| `src/stackowl/pipeline/steps/execute.py` | Modify (line ~986) | Stop leaking the budget error into the floor |
| `tests/owls/test_router_intent_class.py` | **Create** | classification parse units |
| `tests/pipeline/test_graceful_floor.py` | **Create** | graceful-floor units |
| `tests/journeys/test_conversational_and_graceful_journey.py` | **Create** | gateway journeys |

---

## Task 1: Router classification hardening

**Files:**
- Modify: `src/stackowl/owls/router.py` (`_ROUTING_MAX_TOKENS` line 29; `_build_prompt` lines 158-172; `_parse_intent_class` lines 186-194)
- Test: `tests/owls/test_router_intent_class.py`

**Context:** `_parse_intent_class` currently reads ONLY `lines[1]` and fail-safes to `standard`. We broaden the prompt definition, scan all lines after the owl name, and raise the token cap. Owl parse (`_parse_choice`, line 1) is UNCHANGED. `_VALID_CLASSES = {"conversational", "standard"}` already exists at module top.

- [ ] **Step 1: Write the failing test** `tests/owls/test_router_intent_class.py`:

```python
from stackowl.owls.router import SecretaryRouter, _ROUTING_MAX_TOKENS


def _r() -> SecretaryRouter:
    # _parse_intent_class / _parse_choice are pure methods; a router with no
    # registries is fine for parsing-only unit tests.
    return SecretaryRouter(provider_registry=None, owl_registry=None)  # type: ignore[arg-type]


def test_token_cap_raised():
    assert _ROUTING_MAX_TOKENS == 64


def test_class_on_line_2():
    assert _r()._parse_intent_class("secretary\nconversational") == "conversational"


def test_class_on_later_line_is_scanned():
    # weak model put a blank line / reasoning before the token
    assert _r()._parse_intent_class("secretary\n\nconversational") == "conversational"
    assert _r()._parse_intent_class("secretary\nlet me think\nconversational") == "conversational"


def test_class_token_with_punctuation():
    assert _r()._parse_intent_class("secretary\n'conversational'.") == "conversational"


def test_standard_when_explicit():
    assert _r()._parse_intent_class("secretary\nstandard") == "standard"


def test_failsafe_standard_when_no_class_token():
    assert _r()._parse_intent_class("secretary") == "standard"
    assert _r()._parse_intent_class("secretary\nblah blah") == "standard"


def test_owl_name_line_not_treated_as_class():
    # line 1 is the owl name; even if it contained the word, scanning starts at line 2.
    assert _r()._parse_intent_class("standard\nconversational") == "conversational"


def test_prompt_mentions_compliment_and_no_task():
    p = _r()._build_prompt([("secretary", "general")], "i liked your style")
    low = p.lower()
    assert "conversational" in low and "standard" in low
    # broadened definition references social/compliment + a no-action notion
    assert "compliment" in low or "social" in low
```

> Read the EXISTING router tests (grep `tests/owls/` for `_parse_intent_class` / `_build_prompt` / `route`) first to confirm the construction idiom and that `SecretaryRouter(None, None)` is acceptable for parse-only tests; if those tests construct it differently, match them.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/owls/test_router_intent_class.py -q`
Expected: FAIL — `test_token_cap_raised` (still 32) + `test_class_on_later_line_is_scanned` (only line 2 read) + `test_prompt_mentions_compliment`.

- [ ] **Step 3: Implement.** In `src/stackowl/owls/router.py`:

(a) Raise the cap (line 29):
```python
_ROUTING_MAX_TOKENS = 64
```

(b) Broaden the line-2 instruction in `_build_prompt` (replace the final `"Reply with exactly two lines: ..."` block, lines 168-172):
```python
            "Reply with exactly two lines:\n"
            "Line 1: the owl name (required).\n"
            "Line 2: 'conversational' if the user is ONLY being social — a "
            "greeting, thanks, a compliment, an opinion or reaction, or chit-chat "
            "— with NO request to do, find, make, change, or look up anything. "
            "Otherwise 'standard'. Judge by meaning, in any language."
```

(c) Scan all lines after the owl name in `_parse_intent_class` (replace lines 186-194):
```python
    def _parse_intent_class(self, raw: str) -> Literal["conversational", "standard"]:
        """Scan every line AFTER the owl-name line for the class token. Fail-safe → 'standard'."""
        lines = (raw or "").strip().splitlines()
        for line in lines[1:]:
            token = line.strip().strip("\"'`.,:;()[]{}<>").lower()
            if token in _VALID_CLASSES:
                return "conversational" if token == "conversational" else "standard"
        return "standard"
```

- [ ] **Step 4: Run to verify it passes** + regression.

Run: `uv run pytest tests/owls/test_router_intent_class.py -q` (pass). Existing router regression: `uv run pytest tests/owls/ -q -k "router or route or intent"`. `uv run mypy src/stackowl/owls/router.py` (clean). `uv run ruff check src/stackowl/owls/router.py tests/owls/test_router_intent_class.py`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/owls/router.py tests/owls/test_router_intent_class.py
git commit -m "feat(v2): robust conversational classification — broadened definition + scan-all-lines + token cap 64"
```

---

## Task 2: Graceful bare-timeout floor

**Files:**
- Modify: `src/stackowl/setup/localize.py` (add `self_heal_floor_graceful` after `self_heal_floor_minimal`, ~line 99)
- Modify: `src/stackowl/pipeline/supervisor.py` (`synthesize_floor`, lines 183-210)
- Modify: `src/stackowl/pipeline/steps/execute.py` (the empty-partial floor call, lines 986-991)
- Test: `tests/pipeline/test_graceful_floor.py`

**Context:** `synthesize_floor` (supervisor.py:147) always renders the 5-slot capability template `self_heal_floor`. For a bare timeout with no failed tool the fields are blank and `error` leaks `budget cap reached: time…`. Add a graceful no-slot message and return it when there is no capability data. `localize(key, lang)` and `localize_format(key, lang, **slots)` are the existing helpers; `localize` is imported in supervisor.py (used for `self_heal_floor_minimal`).

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_graceful_floor.py`:

```python
from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.setup.localize import localize


def test_graceful_when_no_capability_data():
    out = synthesize_floor(goal="i liked your message style", error="budget cap reached: time limit=120.0",
                           attempts=[], partial=None)
    assert out  # non-empty
    assert "capability that failed" not in out.lower()
    assert "budget cap reached" not in out.lower()
    assert "technical detail" not in out.lower()


def test_capability_template_kept_when_capability_present():
    out = synthesize_floor(goal="send mail", error="smtp blocked",
                           attempts=["send_email"], partial=None, failed_capability="send_email")
    assert "send_email" in out  # real failure still names the capability


def test_graceful_localize_key_exists_all_langs():
    for lang in ("en", "de", "fr", "es"):
        msg = localize("self_heal_floor_graceful", lang)
        assert msg and "{" not in msg  # present, non-empty, no leftover slots


def test_graceful_when_only_goal_present():
    # goal present but no capability / attempts / partial → still graceful (goal not echoed awkwardly)
    out = synthesize_floor(goal="hi there", error=None, attempts=None, partial=None)
    assert "capability that failed" not in out.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_graceful_floor.py -q`
Expected: FAIL — graceful key missing + capability template still rendered for the no-data case.

- [ ] **Step 3a: Add the localize key.** In `src/stackowl/setup/localize.py`, after the `self_heal_floor_minimal` entries (~line 99, before the closing `}`):
```python
    # Graceful floor for a give-up with NO real capability data (e.g. a bare
    # time/step backstop timeout): warm + honest, NO slots so nothing internal
    # (raw error, blank fields) can leak. Used by synthesize_floor's no-data branch.
    ("self_heal_floor_graceful", "en"): (
        "Sorry — I got tangled up working on that and didn't finish cleanly. "
        "Could you tell me a bit more, or say it another way?"
    ),
    ("self_heal_floor_graceful", "de"): (
        "Entschuldigung — ich habe mich dabei verheddert und es nicht sauber "
        "abgeschlossen. Kannst du es anders formulieren oder mehr dazu sagen?"
    ),
    ("self_heal_floor_graceful", "fr"): (
        "Désolé — je me suis emmêlé et je n'ai pas terminé proprement. "
        "Peux-tu reformuler ou m'en dire un peu plus ?"
    ),
    ("self_heal_floor_graceful", "es"): (
        "Perdona — me hice un lío con eso y no terminé bien. "
        "¿Puedes decirlo de otra forma o darme algún detalle más?"
    ),
```

- [ ] **Step 3b: Add the graceful branch** in `synthesize_floor` (supervisor.py). Inside the `try:` (after `derived_capability` is computed, before the `localize_format(...)` call, ~line 187):
```python
        attempts_list = list(attempts) if attempts else []
        derived_capability = failed_capability
        if derived_capability is None:
            derived_capability = attempts_list[0] if attempts_list else ""
        # No real capability data (e.g. a bare time/step backstop timeout) → a warm,
        # honest, slot-free message instead of the blank capability template.
        if not derived_capability and not attempts_list and not partial:
            graceful = localize("self_heal_floor_graceful", lang)
            if graceful:
                log.engine.debug(
                    "supervisor.synthesize_floor: graceful (no capability data)",
                    extra={"_fields": {"lang": lang}},
                )
                return graceful
        result = localize_format(
            "self_heal_floor",
            lang,
            ...  # unchanged
        )
```
(Keep the rest of the function — the `localize_format` call, the `if not result` guard, and the except-path minimal fallback — exactly as is.)

- [ ] **Step 3c: Stop leaking the budget error** in `execute.py` (the empty-partial default-backstop floor call, lines 986-991). Change it to feed no capability data so the graceful branch fires:
```python
            # Empty partial under the default backstop → graceful floor (no raw
            # budget error / blank capability fields surfaced to the user; the
            # structured marker still goes to state.errors for observability).
            floor = synthesize_floor(
                goal=state.input_text,
                error=None,
                attempts=[],
                partial=None,
            )
```
(The `marker` is still appended to `state.errors` below this — leave that; only the user-facing floor text changes.)

- [ ] **Step 4: Run to verify it passes** + regression.

Run: `uv run pytest tests/pipeline/test_graceful_floor.py -q` (pass). Regression (the floor/self-heal suite MUST stay green): `uv run pytest tests/pipeline/ -q -k "floor or self_heal or supervisor or budget"` and `uv run pytest tests/journeys/test_self_heal_invariant.py tests/journeys/test_default_backstop_no_marker.py -q` (the latter if present — the bounded-turn marker test; the marker still goes to state.errors, only user content changed). `uv run mypy src/stackowl/pipeline/supervisor.py src/stackowl/setup/localize.py src/stackowl/pipeline/steps/execute.py` (no NEW errors). `uv run ruff check` the changed files.

> If `test_default_backstop_no_marker.py` asserted the user-facing empty-partial floor contained the raw error, that assertion is now WRONG by design (the spec forbids leaking it) — update that ONE assertion to assert the graceful message instead, and note it in the report. Do NOT weaken any marker-in-state.errors assertion.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/setup/localize.py src/stackowl/pipeline/supervisor.py src/stackowl/pipeline/steps/execute.py tests/pipeline/test_graceful_floor.py
git commit -m "feat(v2): graceful floor for bare timeouts — no blank capability template, no leaked budget error"
```

---

## Task 3: Gateway journeys + full regression

**Files:**
- Create: `tests/journeys/test_conversational_and_graceful_journey.py`

**Context:** Two end-to-end regressions for the live bug. (a) A turn the router classifies `conversational` drives the zero-tool plain-stream path (NOT a 67-tool standard turn). (b) A default-backstop empty timeout delivers the graceful message, with `budget cap reached`/`capability that failed` ABSENT. STUDY `tests/journeys/test_self_heal_lying_judge.py` (real OpenAIProvider + fake client + a dual judge/router double) and `tests/journeys/test_budget_cap.py` (capped-turn harness) for the boot pattern; reuse it.

- [ ] **Step 1: Conversational-bypass journey.** Boot the real backend; scripted router/fast double returns `"secretary\nconversational"` for the turn's routing call; the execute provider double records whether it was given tools / entered the tool loop. Assert: the turn delivers a non-empty reply AND the tool loop was NOT entered (zero tools presented) — i.e. `intent_class` drove the bypass. (Mirror how `test_self_heal_lying_judge.py` distinguishes the routing call from the execute call on its double.)

- [ ] **Step 2: Graceful-timeout journey.** Drive a default-backstop timeout with an empty partial (reuse `test_budget_cap.py` / the bounded-turn harness to force a `BudgetBreach` on the default backstop with no partial). Assert the delivered user text equals the graceful message (`localize("self_heal_floor_graceful","en")`) and contains neither `budget cap reached` nor `capability that failed`; AND the structured marker is still in `state.errors` (observability preserved).

```python
# tests/journeys/test_conversational_and_graceful_journey.py
# (a) router double → 'secretary\nconversational' → zero-tool bypass, non-empty reply.
# (b) default-backstop empty timeout → graceful message delivered; budget string absent;
#     marker still in state.errors.
from stackowl.setup.localize import localize
```

- [ ] **Step 2b: Run; confirm PASS.** If the conversational turn still entered the tool loop, the intent_class isn't driving the bypass — STOP, report BLOCKED (do not relax). `uv run pytest tests/journeys/test_conversational_and_graceful_journey.py -q`.

- [ ] **Step 3: Full regression.**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: prior green counts + the 2 new journeys, ZERO failures. Watch `test_self_heal_*`, `test_budget_cap`, the conversational-bypass journey, and `test_default_backstop_*`. If any regress, STOP and report BLOCKED.

- [ ] **Step 4: Lint + commit.**

Run: `uv run ruff check tests/journeys/test_conversational_and_graceful_journey.py`.

```bash
git add tests/journeys/test_conversational_and_graceful_journey.py
git commit -m "test(v2): journeys — conversational message bypasses tools (FR1), bare timeout → graceful floor (FR6)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1 compliment→conversational→T1 (parse) + T3a (end-to-end); FR2 scan-all-lines→T1; FR3 routing unchanged→T1 regression; FR4 graceful floor→T2; FR5 capability floor intact→T2; FR6 no leaked budget string→T2c + T3b; FR7 regression→T3. All covered.
- **Placeholder scan:** T1-Step1 and T3 instruct the implementer to READ existing tests (router construction idiom, the self-heal/budget journey harness) and reuse them — concrete grounding, not deferred work; the assertions + the real edits are fully specified. The T2-Step4 note about a possibly-existing `test_default_backstop_no_marker` assertion is a conditional "if present, update this one assertion" with the rule (never weaken the marker-in-state.errors check). No TBD/TODO.
- **Type consistency:** `_parse_intent_class(self, raw: str) -> Literal["conversational","standard"]` unchanged signature; `_ROUTING_MAX_TOKENS` int; `synthesize_floor(goal, error, attempts, partial, *, failed_capability=None, lang="en")` signature UNCHANGED (only its body gains a branch); `localize("self_heal_floor_graceful", lang)` key consistent across localize.py + supervisor.py + tests. Consistent.

## Risk & containment
- **Risk:** the graceful branch swallows a real partial. **Contained:** it only fires when `derived_capability` AND `attempts_list` AND `partial` are all falsy; a turn with a real partial takes the bounded-turn `if exc.partial_text` branch (delivers the partial) before reaching the floor.
- **Risk:** broadened prompt degrades owl routing. **Contained:** owl parse (line 1) untouched; T1 keeps the routing regression green; the class never affects owl selection.
- **Risk:** a real task misclassified conversational → tool-stripped. **Contained:** fail-safe stays `standard`; only an explicit `conversational` token flips it; the definition requires "no request to do/find/make/change/look-up anything."
- **Rollback:** see spec — revert the router edits + the graceful branch/key + the execute `error=None`. Capability floor + owl routing untouched.
