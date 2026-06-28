# Ralph loop driver — Skill & Preference Learning arc

Spec: `.ralph/LEARNING_ARCHITECTURE.md`. Plan + story status: `.ralph/LEARNING_IMPLEMENTATION_PLAN.md`.

## Each iteration
1. Read `.ralph/LEARNING_IMPLEMENTATION_PLAN.md`; pick the FIRST story not marked done.
2. Delegate implementation to a fresh subagent with a precise spec grounded in LEARNING_ARCHITECTURE.md.
   Reuse-first (code-simplifier mandate): PreferenceStore, deliver._enforce_output_prefs, owl_build _elicit_missing,
   tool_build closed pattern, the verification primitive / ToolResult.verified. No new subsystems. No 1000-line rewrites.
   Typed, 4-point logging, NO hardcoded English keyword lists.
3. Run the `code-simplifier` agent on the resulting diff to cut it to the minimum before commit.
4. Verify yourself: targeted tests + `uv run ruff check` + `uv run mypy` on changed files. NEVER full pytest (hangs).
   QA+dev review; fix findings.
5. Commit that story + push to main; mark it done in the plan with the commit hash.
6. Honor every rule in MEMORY.md.

## Stories in order (ENFORCEMENT-FIRST)
- LS1 — `output_style` structured preference key on PreferenceStore (delivery-transform vocabulary only: markdown
  off|minimal|full, links inline|titles, tables on|off subsuming output_tables, emoji, length). Widen set_output_preference.
- LS2 — deterministic delivery formatter + verifier in _enforce_output_prefs / channels/_format.py. markdown:minimal strips
  asterisks; links:titles renders titled tappable links via Telegram HTML parse mode; idempotent; records it fired; cheap
  post-transform verifier asserts the spec held. Assert on post-seam bytes.
- LS3 — feedback classification (LLM-semantic, multilingual, no wordlist) → polarity x aspect(content|format|length|tone)
  x referent (this = last agent message; non-adjacent ask). Abstain+ask on low confidence.
- LS4 — bidirectional capture: thumbs-up reads effective style of last render and writes output_style; thumbs-down corrects
  the spec + writes an outcome row (NOT a negative lesson). Aspect-scoped. Read-back confirmation in plain terms, no prose
  "learned" claim (the next render is the receipt); on "you broke it" name the exact defect and ship the fix.
- LS5 — /style command showing the active per-channel style in plain language.
- LS6 — routing rule (disposition->preference vs procedure->skill) + honest skill_manage schema (content required) +
  reuse _elicit_missing if a gap remains.
- LS7 — close the skill-usage loop: increment_n_executions at the APPLICATION seam (not injection) + set_success_rate from
  ToolResult.verified. Meta-test: no-op application -> counter does NOT move.
- LS8 — eval suite (assert stores + post-seam bytes, never prose: pref persists+applies across RESTART; negative flips next
  output over a multi-turn loop; skill stats move on use + no-op-no-tick; enforcement fired; aspect-scope) + live re-test of
  the exact chat scenario on the running server (clean Telegram, no asterisks, titled source links, survives restart, "you
  broke it" stops regression). Capture traceIds. Merge to main.

## Completion
Stop only when LS1 through LS8 are ALL implemented, tested green, committed, pushed, and the live style re-test passed.
