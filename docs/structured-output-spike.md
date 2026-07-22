# Structured-output spike: does the deployed gateway support JSON-schema-constrained generation?

**Status:** confirmed supported — recommendation below.
**Filed:** 2026-07-22, Workstream A / Phase 0 of the classifier-unification plan
(`~/.claude/plans/woolly-dreaming-wilkes.md`), before `classifier_base.py`'s API
was designed.

## Question

At least 10 fast-tier classifier call sites in this codebase get output
reliability purely by narrowing the possibility space (short prompt, tiny
`max_tokens`, `disable_thinking=True`) and then fuzzy-parsing free text with
regex/substring matching. Before designing a shared `classifier_base.py`
around that same pattern, verify whether the actual deployed gateway
(`NeraAiRaw`, model `neraai-v1-raw`, served from
`http://llm-gateway.dev.nera.gov:4000/v1`, OpenAI-compatible per
`providers/openai_provider.py`) honors `response_format={"type":
"json_schema", ...}` — the general industry answer to "stop parsing free text
with regex."

## Method

Three live calls against the real gateway (not mocked), using the same
`openai.AsyncOpenAI` client construction `openai_provider.py` already uses,
with a strict JSON schema (`{"verdict": "COMMIT"|"NONE"}`, `strict: True`):

1. `response_format=<schema>`, no `disable_thinking`, `max_tokens=50`.
2. `response_format=<schema>`, no `disable_thinking`, `max_tokens=500`.
3. `response_format=<schema>` + `chat_template_kwargs={"enable_thinking":
   False}` (the existing `disable_thinking` mechanism), at `max_tokens=20`
   and `max_tokens=4`.

## Findings

- **The gateway accepts and honors `response_format={"type": "json_schema",
  ...}`** — no error, and the returned `content` is valid JSON conforming to
  the schema every time it wasn't truncated.
- **Call 1 (`max_tokens=50`, thinking not disabled) returned `content: None`.**
  The full response showed why: `reasoning_content` contained ~300 words of
  chain-of-thought, and the 50-token budget was entirely consumed by it before
  any schema-conforming content could be emitted. **This is the exact same
  root-cause bug already patched 5+ times elsewhere in this codebase
  (`owls/router.py`, `feedback_classifier.py`, `owls/evolution.py`,
  `delivery_gate.py`'s apology generator, `schedule_commit_classifier.py`) —
  structured output does NOT make a classifier immune to it.** `disable_thinking`
  is still mandatory, not optional, even with a schema constraint.
- **Call 2 (`max_tokens=500`) succeeded**: `{"verdict": "NONE"}`, confirming
  the schema constraint works once the reasoning-token problem is out of the
  way.
- **Call 3a (`disable_thinking=True`, `max_tokens=20`) succeeded cleanly**:
  `finish_reason: "stop"`, valid `{"verdict": "NONE"}`, only 13 completion
  tokens used.
- **Call 3b (`disable_thinking=True`, `max_tokens=4` — the EXACT budget
  today's one-word-verdict classifiers use) truncated**: `finish_reason:
  "length"`, content cut off mid-JSON (`{"`). **A JSON-wrapped verdict needs
  more tokens than a bare one-word reply** — the schema's braces/quotes/field
  name are real tokens the model must emit before the value itself, so the
  existing `_MAX_TOKENS = 4` budget is incompatible with schema-constrained
  JSON output as-is.

## Recommendation

**Adopt structured output in `classifier_base.py`'s Piece B (`safe_complete`)
from day one**, gated behind an optional `response_format`/schema parameter —
not a separate spike-deferred follow-on. It's a real reliability upgrade over
today's fuzzy substring/JSON-fence parsing (no more "both tokens present →
ambiguous → fail-safe default" case; the gateway itself refuses to emit
anything outside the schema).

Two things the migration must account for, both confirmed by this spike,
neither optional:

1. `disable_thinking=True` stays mandatory for every fast-tier call, schema or
   not — schema constraints only bound the SHAPE of the final content, not
   whether the model burns its budget on invisible reasoning first.
2. Classifiers migrating to a JSON-schema verdict need their token budget
   raised from the current bare-word-shaped values (e.g. `_MAX_TOKENS = 4`)
   to a JSON-shaped value (roughly 20 tokens minimum for a single-field
   enum verdict, more for anything with a `reason` string field) — this is a
   deliberate, understood increase in per-call token cost in exchange for
   eliminating parse ambiguity, not an accidental regression.

Piece C (`parse_two_token_verdict`, the fuzzy substring parser) is NOT being
removed — it remains the correct approach for any classifier this migration
doesn't reach, and as the fallback if a specific model/gateway combination
ever changes and stops honoring `response_format`. Piece B's schema parameter
is additive.
