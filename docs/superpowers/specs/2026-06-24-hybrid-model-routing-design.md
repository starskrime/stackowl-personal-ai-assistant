# Hybrid Model Routing — Design

Date: 2026-06-24
Branch: `feat/hybrid-model-routing`
Status: approved (brainstorm), pending implementation

## Problem

Every LLM call in the platform currently runs on `qwen3.5:122b` because all three
tiers (`fast`/`standard`/`powerful`) are pinned to the 122b in config. The internal
machine-to-machine (M2M) helper calls — judge, router/triage, intent-classifier,
fact-extractor — each pay for a 256K-context thinking giant to answer a yes/no.
Combined with the per-iteration judge inside the tool loop, a single Telegram turn
takes minutes. This is the deep cause of the remaining latency after the
"weird answers" arc (see `project_weird_answer_arc_backlog`).

The helpers are **already coded to request the `"fast"` tier** — the latency is not
a wrong-tier bug; it is that `fast` *maps to* 122b. The fix is therefore two coupled
parts: (1) make the tiers map to genuinely different-sized models, and (2) stop the
user-facing answer from starting on the small model just because it shares the `fast`
tier string.

## Remote model inventory (`http://172.30.60.31:11434`)

| Model           | Size   | Role                                   |
|-----------------|--------|----------------------------------------|
| `qwen3.5:2b`    | 2.7 GB | cheap M2M decisions + conversational answers |
| `qwen3.6:35b`   | 24 GB  | standard answers (mid tier)            |
| `qwen3.5:122b`  | 81 GB  | escalation ceiling, hardest turns      |

## Decisions (from brainstorm)

- **Answer-lane policy: router decides per turn.** The existing triage router already
  classifies `intent_class ∈ {conversational, standard, clarify}` and fails safe to
  `standard`. Its verdict picks the answer's *starting* tier; escalation handles the rest.
- **Tier ladder: true 3-step.**
  - `fast = qwen3.5:2b` — M2M + conversational answers
  - `standard = qwen3.6:35b` — standard answers
  - `powerful = qwen3.5:122b` — escalation ceiling
- **Fact-extractor → `standard` (35b), not `fast` (2b).** Extraction feeds long-term
  memory; 2b is too weak. Still off the 122b. Pure yes/no helpers stay on `fast`.
- **Parliament synthesis stays `powerful`** — deliberate multi-owl quality synthesis,
  not a yes/no helper.

## Architecture — two lanes

Everything reduces to "which tier does this call *start* on?":

- **Helper lane (M2M)** — resolved directly via `registry.get_with_cascade(tier)`:
  - Classifiers/judges (judge, router, intent-classifier, planner-proposer) → `fast` (2b).
    Already coded to `"fast"`; activated purely by the config change.
  - Extractors (fact-extractor, entity-extractor) → `standard` (35b). One edit.
  - Parliament synthesis → `powerful` (122b). Unchanged.
- **Answer lane** — non-pinned turns go through `LLMGateway.complete_with_tools` with a
  `floor`/`ceiling` span. Today `floor` is hardcoded `"fast"`. Replace with a pure
  function of `state.intent_class`.

## Change 1 — answer floor by intent (the one real code change)

`src/stackowl/pipeline/steps/execute.py:~1299` currently:

```python
floor="fast",
ceiling=choice.ceiling_tier,
```

Replace `floor` with the result of a new pure helper:

```python
def answer_floor_for_intent(intent_class: str, *, ceiling: str) -> str:
    """Starting tier for the user-facing answer, clamped to <= ceiling by rank.

    conversational/clarify -> "fast"   (cheap first pass; escalates on judge give-up)
    standard               -> "standard"
    anything else          -> "fast"   (== legacy behaviour)
    """
```

- **Mapping:** `conversational` → `fast`, `standard` → `standard`, everything else
  (incl. `clarify`, unknown) → `fast`. `state.intent_class` defaults to `"standard"`
  when the router did not run; `intent_classified` distinguishes a real verdict from
  the default. A real `standard` verdict starts on 35b; an unclassified turn that kept
  the `standard` default also starts on 35b (acceptable — 35b is the standard tier).
- **Clamp:** `floor` is clamped so its tier rank never exceeds `ceiling` (a manifest
  pin with a low ceiling can never be violated). Tier rank order: `fast < standard < powerful`.
- **Escalation preserved:** `ceiling` is unchanged (`choice.ceiling_tier`, default
  `powerful`), so a botched 2b conversational answer still climbs 2b→35b→122b through
  the existing gateway + judge-give-up machinery.
- **Pinned path untouched:** `execute.py:1274` (session `/tier`, owl-name pins) still
  calls the resolved provider directly — a pin means "exactly this, no routing."

### Feature flag

A single boolean setting gates the new floor selection (default **on**, since this is
the intended behaviour). When **off**, `answer_floor_for_intent` is bypassed and the
floor is `"fast"` for all turns — **byte-identical to today**. This gives a clean
test pivot and an instant rollback without a redeploy.

When all tiers map to the same model (today's config), the mapping is also a behavioural
no-op — the change only "activates" once the config splits the tiers.

## Change 2 — extractor tier

`src/stackowl/memory/assembly.py:220`: `get_with_cascade("powerful")` →
`get_with_cascade("standard")` for the fact-extractor. Check `entity_extractor`
construction for the same treatment.

## Change 3 — config (activation)

`~/.stackowl/stackowl.yaml` (local, gitignored):

```yaml
- name: ollama            # tier: fast
  default_model: qwen3.5:2b
- name: ollama-standard   # tier: standard
  default_model: qwen3.6:35b
- name: ollama-powerful   # tier: powerful
  default_model: qwen3.5:122b
```

Applied as the final activation step, after code + tests are green.

## Constraints honoured

- **No vendor-specific logic** — code maps `intent_class` strings → tier strings; model
  identity lives only in config. (`feedback_no_vendor_specific_logic`.)
- **Thinking stays on** everywhere, including the 2b. Routing is by call-type, never by
  disabling thinking.
- **No artificial limits** — context window and output budget stay model-derived.

## Testing

- **Unit:** `answer_floor_for_intent` — each intent → expected tier; clamp to a low
  ceiling; flag-off path returns `"fast"` for all (byte-identity).
- **Integration:** conversational turn starts on `fast` and escalates on judge give-up;
  standard turn starts on `standard`; pinned turn bypasses routing entirely.
- **Live validation (the real risk):**
  1. 2b reliably emits parseable router verdicts and judge JSON (router fail-safe and
     judge give-up preservation are the backstops if not).
  2. Conversational 2b answers are not "weird"; escalation catches the ones that are.
  3. End-to-end Telegram latency drops materially versus all-122b.

## Out of scope

Backlog #2/#3 (summarize-on-window-approach, char-budget → token-budget), #4 (extra
escalation triggers: no-progress-streak / LoopGuard-trip), #5 (memory-test isolation
leak), #6 (pre-existing `app.py` mypy error). Hybrid routing only.
