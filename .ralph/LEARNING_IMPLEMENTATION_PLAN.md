# Skill & Preference Learning — Implementation Plan

Spec: [[LEARNING_ARCHITECTURE.md]]. Build the FULL fix, ENFORCEMENT-FIRST, reuse-first (code-simplifier).
Method: subagent-driven, story-granular commits+push, QA+dev review, eval test per story. Use the `code-simplifier`
agent on each story's diff to cut it to the minimum. Follow MEMORY.md. Ships ON.

## STATUS: LS1 @f360b20d ✅ · LS2 @c80c9382 ✅ (enforcement core done) — next: LS3

## Epic L1 — Enforcement core (the load-bearing 20% — build first)
- **LS1. `output_style` preference key**: a structured record (JSON) on PreferenceStore (reuse it; per-(identity,channel)
  scope + global). Closed vocabulary of DELIVERY TRANSFORMS only: `markdown: off|minimal|full`, `links: inline|titles`,
  `tables: on|off` (subsume the existing `output_tables`), `emoji: on|off`, `length: terse|normal`. Key-admissibility
  rule: only mechanically-enforceable keys. Widen `set_output_preference` allowlist to accept it; validate inline (no framework).
- **LS2. Deterministic delivery formatter + verifier**: in `deliver._enforce_output_prefs` / `channels/_format.py`,
  apply each transform regardless of model compliance — `markdown:minimal` strips/normalizes `*`/bold; `links:titles`
  renders each link as a titled tappable hyperlink (Telegram HTML parse mode — survives headline punctuation); `tables`
  reuses the existing flatten. Idempotent; record that it fired. After transform, a cheap VERIFIER asserts the spec held
  (no stray `*`, links titled); on violation re-strip. Assert on post-seam bytes.

## Epic L2 — Capture + honest UX
- **LS3. Feedback classification**: LLM-semantic (no English wordlist, multilingual) → {polarity, aspect∈
  content|format|length|tone, referent}. Referent "this" = immediately-preceding agent message; non-adjacent → ask.
  Abstain + ask one question on low confidence.
- **LS4. Bidirectional capture + read-back confirm**: thumbs-up → read the effective style of the last render → write
  `output_style`; thumbs-down → re-assert/correct the spec + write an outcome row (NOT a negative lesson — keep
  positive-only corpus intact). Aspect-scoped (only the rejected aspect changes). Confirmation reads the rule back in
  plain observable terms (no prose "✅ learned" — Sally: weld claim to artifact; the next render is the receipt). On
  "you broke it": drop cheer, name the exact defect, ship the fixed version.
- **LS5. `/style` command**: show the active style for this channel in plain language (durability tell; don't re-ask).

## Epic L3 — Routing + skill_manage honesty
- **LS6. Routing + honest schema**: a routing rule (next to the owl/skill rule) — dispositional "remember/I like/always/
  never [output quality]" → preference; "author a procedure with steps" → skill. Make `skill_manage` create schema
  HONEST (`content` required) → kills the 3ms empty-fail + breaker cascade. Reuse owl_build `_elicit_missing` if a gap remains.

## Epic L4 — Close the skill-usage loop
- **LS7. Usage→stats wiring**: call `increment_n_executions` at the skill APPLICATION seam (NOT injection) +
  `set_success_rate` from the existing `ToolResult.verified` signal. Revives synthesizer refine/deprecate. Copy
  tool_build's closed pattern. Meta-test: force application to no-op → counter must NOT move (fake-learning tripwire).

## Epic L5 — Prove it
- **LS8. Eval suite + live re-test**: Murat's 6 evals (pref persists+applies across RESTART; negative flips next output
  over a MULTI-TURN loop; skill stats move on use + no-op-no-tick; enforcement fired; aspect-scope) asserting on stores +
  post-seam bytes, never prose — wired as a gate. Then live re-test of the EXACT chat scenario on the running server:
  "make my Telegram output clean (no *), links as titles" → next replies obey deterministically + survive restart; "you
  broke it" stops the regression; AI-news reply carries real titled source links. Capture traceIds. Merge to main.

## Verification (every story)
Unit + delivery-seam/eval (mock only provider; assert stores + post-seam bytes) + targeted suite (no full pytest — Jetson hangs).
Run the `code-simplifier` agent on the diff before commit. Live at LS8. Keep boot green (reachability block + acceptance_authority on).
