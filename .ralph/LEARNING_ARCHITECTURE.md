# Skill & Preference Learning Architecture — fix "it fakes learning"

Status: PROPOSED (post BMAD-party 2026-06-28). Voices: Winston (arch), Murat (test/measured), Mary (analyst), Sally (UX).

## Validated failure (from code+logs, not chat)
User: "remember this output style / improve your skill / no you lost it again / I don't see the links."
- "remember style" routes to `skill_manage` (procedure-authoring); its JSON schema marks only `action` required
  (NOT `content`), so an empty spec arrives → "Content cannot be empty" in ~3ms → NO elicitation/recovery →
  3 fast fails open the same-tool circuit breaker → empty "What I tried: . Technical detail:" floor.
- The RIGHT tool `set_output_preference` enforces exactly ONE key (`output_tables`). No key/enforcement branch
  for "clean Telegram / no asterisks / links-as-titles" → a stated style has nowhere structured to land.
- Learning is positive-only + unbound: "I like this" → ignorable memory hint; "this broke" → DROPPED
  (reflection trigger gates `failure_class IS NULL`). Neither becomes a durable applied rule.
- Skill usage loop OPEN: `increment_n_executions`/`set_success_rate` have ZERO callers → synthesizer
  refine/deprecate phases are dead → "registered ≠ measured". `tool_build` is the reference CLOSED loop.

## Root principle (same as the trust arc, two new surfaces)
**Preference must be ENFORCED, not advised. Usage must be MEASURED, not asserted.** A weak model will drift;
a delivery-seam transform won't. A counter at the application seam is real; a self-report isn't.
Sally's corollary: **weld the claim to the artifact — the next message IS the receipt; never claim learning in prose.**

## ADRs

### ADR-S1 — Preference-vs-skill routing + honest schemas (kills the live incident)
- Routing split (a prompt rule next to the owl/skill rule — NOT a new classifier): dispositional
  "remember / I like / always / never / stop doing [a quality of OUTPUT]" → **preference**; "author a
  reusable PROCEDURE with steps" → **skill**. The discriminator is *persistent disposition vs authored artifact*.
- `skill_manage`: make the JSON schema HONEST — `content` is genuinely required for create (mark it required).
  This alone kills the 3ms empty-spec hard-fail → no breaker cascade → no empty floor. Then, if a gap remains,
  reuse owl_build's `_elicit_missing` resumable pattern (don't write new elicitation). Honest schema = mandatory now;
  elicitation = second, and mostly moot once routing sends style away from skill_manage.

### ADR-S2 — Enforced style preferences (the core; build FIRST — it's the load-bearing 20%)
- Generalize the ENFORCED key set (not the store — PreferenceStore is already general k/v, per-channel+global):
  one new key `output_style` = a small STRUCTURED record (JSON) of **delivery transforms**, each independently
  enforced in `deliver._enforce_output_prefs`: `markdown: off|minimal|full` (Telegram→minimal, strip `*`),
  `links: inline|titles` (headline-as-tappable-link), `tables: on|off` (subsumes the existing key), `emoji`, `length`.
- **Deterministic post-generation formatter + VERIFIER at the delivery seam** — applied regardless of model
  compliance; idempotent; records that it fired. After rendering, a cheap checker asserts the spec held (no `*`,
  links titled); on violation, re-transform/strip. This is Mary's most-dangerous case defused: persist-acked-but-
  not-enforced "looks fixed" but the model drifts — so enforcement, NOT a prompt hint, is the guarantee.
- **Key-admissibility rule (future-proofing):** a key is admissible ONLY if the delivery layer can mechanically
  enforce it. Free-form style desires route to the owl charter, not the store. Keeps it from becoming a junk drawer.
- **Bidirectional capture:** thumbs-up ("I like this") → read the effective style of the LAST render → write the
  `output_style` spec. thumbs-down ("you lost it / broke it") → re-assert/correct the spec + write an outcome row.
  Negative is a **preference correction, NOT a negative lesson** — keeps positive-only-learning corpus intact.
- **Aspect-scoping (Mary, mandatory):** classify feedback as polarity × aspect (content / format / length / tone).
  "Good content but lose the stars" = positive-content + negative-format → only the format rule changes. Whole-message
  polarity is the wrong-capture bug.
- **Scope:** (identity_key, channel) default; owl-override only when named. Explicit > inferred; last-write-wins;
  don't auto-expire explicit prefs (auto-expiry IS the "lost it" bug). Surface on a `/style` command.
- **Telegram links:** HTML parse mode (survives punctuation in headlines that breaks Markdown `*`-parsing — literally
  the transcript bug); link text = headline; one link per item; honest "(no public link yet)" beats a dead 🔗.

### ADR-S3 — Closed skill-usage loop (registered → measured)
- Wire `increment_n_executions` at the skill APPLICATION seam (NOT injection) + `set_success_rate` from the
  existing `ToolResult.verified` signal (reuse the verification primitive — don't invent a judge). ~2 call sites at
  the exec seam. Revives the synthesizer's refine + deprecate phases (dead only because inputs were never written).
- Copy `tool_build`'s closed pattern (mint→vet→persist→reload→**measure**). No new telemetry service.

## Feedback classification (shared by S2)
LLM-semantic, no hardcoded English wordlists, multilingual; abstain + ask one question on low confidence (a
wrong-polarity write is worse than a question). Output = {polarity, aspect, referent}. Referent for "this" = the
immediately preceding agent message; non-adjacent → ask with candidate snippets.

## Build order (enforcement first — it's the only thing the user feels)
1. **ADR-S2 enforcement core** — `output_style` key + deterministic delivery formatter+verifier (+ Telegram HTML links).
2. **ADR-S2 capture** — thumbs-up/down → spec write, aspect-scoped; the read-back confirmation UX (no prose "✅ learned").
3. **ADR-S1** — routing rule + honest `skill_manage` schema (+ elicitation reuse if needed).
4. **ADR-S3** — close the skill-usage loop.
5. **Eval suite + live re-test** of the exact chat scenario.

## Murat's acceptance suite (assert on stores + post-seam bytes, never model prose)
| Eval | Setup | Measured assertion |
|---|---|---|
| pref persists+applies | set style → RESTART → ask table-bait | pref row exists; delivered bytes have no table/`*` |
| negative flips next output | thumbs-down on a style → N more turns | rejected shape NEVER recurs (multi-turn loop, not 1 turn) |
| skill stats move on use | skill-applying turn ×2 | n_executions delta == expected; success_rate from verified; monotonic |
| no-op does NOT tick | inject skill, force application to no-op | n_executions does NOT move (the fake-learning tripwire) |
| enforcement fired | any reply under a style | the transform ran + post-seam bytes conform, independent of model compliance |
| aspect-scope | "good content, bad format" | only the format rule changes; content pref untouched |

## Biggest risks
- **(Mary) persist-acked-but-not-enforced** — store right, `/style` right, user still sees `*`. Enforcement at the
  delivery seam (deterministic formatter+verifier) is non-negotiable; prompt-injection alone re-ships the lie.
- **(Murat) `n_executions++` wired at INJECTION not application** — green dashboard, dead loop again. Meta-test: no-op → no tick.
- **(Winston) style vocabulary junk drawer** — admit only mechanically-enforceable keys; the rest → charter.
- **(Sally) detached success string** — delete the ability to claim learning in prose; the next render is the proof.

## Code-simplifier — what NOT to build (founder demand: reuse, minimal diff)
No new intent classifier (prompt rule). No new preference store (PreferenceStore + a dict). No new enforcement engine
(`_enforce_output_prefs` runs at the seam). No new telemetry service (2 call sites). No new elicitation engine
(`_elicit_missing` exists). No negative-learning corpus (negatives = preference corrections + outcome rows). If the
diff exceeds a few hundred lines, someone is rebuilding something that already works.

## Founder decisions (defaults chosen — confirm/override)
1. Scope: (identity, channel) default + owl-override. 2. Feedback abstain threshold: ask on low confidence. 3. Formatter
runs POST-model (deterministic). 4. Style attributes = the closed enforceable set above. 5. Conflict: explicit>inferred, last-write-wins.
