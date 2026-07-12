# Owl Ground-Truth Visibility + Dedup Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the actual architectural root cause behind the recurring "Hi" → `owl_build` misfire (confirmed via live log analysis: 7 distinct turns tonight invoked `owl_build`, 10/16 calls failed, 5 of those 10 failures were the identical "an owl named 'Brain' already exists" error) — the model has **no ground-truth visibility into which owls already exist**, so it can only discover a collision via a failed tool call, and the one automated dedup safety net that exists (`owl_build_existence.py`'s semantic near-match) is confirmed live and active (real semantic embedder, not hash-fallback) but did not catch "Researcher Brain" / "research_brain" as duplicates of "Brain" — so the model slipped past it by inventing a new name each retry.

**Architecture:** Two complementary, independent fixes. (1) Give the model the missing ground truth: inject a cheap, deterministic "owls that already exist" fact into every turn's system prompt (mirrors the existing `capabilities`/`banner` injection pattern already in `assemble.py` — same file, same shape, no new abstraction). This is the PRIMARY fix — once the model can see Brain exists, it has no reason to guess-and-retry with name variants. (2) Harden the existing dedup check as defense-in-depth for the cases ground truth alone doesn't stop (e.g. a stale/pre-ground-truth conversation history still nudging a retry): add a cheap, deterministic normalized-name-overlap check to `owl_build_existence.py`, layered BEFORE the semantic embedding call, so a name variant like `research_brain` is caught by exact token overlap against `brain` even when generated-specialty-text cosine similarity doesn't clear the 0.85 threshold.

**Explicitly NOT in this plan:** the `tool_outcome_ledger`/`delivery_gate.py` give-up-gate mismatch (a differently-named eventual success not clearing an earlier same-intent failure) — this symptom should stop recurring once Task 1 prevents the model from inventing redundant names in the first place; touching `tool_outcome_ledger.py` has wide blast radius (many consumers) and isn't warranted for what should become a non-issue.

**Tech Stack:** Python 3.14, pydantic, pytest.

## Global Constraints

- Minimal code changes — touch only the exact lines needed.
- Every except/error path must log (4-point logging standard already used throughout this codebase).
- No new dependencies.
- Every non-trivial change gets a test.
- Fail-open everywhere (matches every other block in `assemble.py` — a failure here must degrade to no-block, never crash the turn).

---

### Task 1: Inject existing-owls ground truth into every turn's system prompt

**Files:**
- Modify: `src/stackowl/pipeline/steps/assemble.py:74-101` (add a new block after persona resolution), `src/stackowl/pipeline/steps/assemble.py:219-222` (add the new block to `parts`)
- Test: `tests/pipeline/test_assemble_*.py` — check exact existing test file names first with `ls tests/pipeline/ | grep -i assemble`

**Interfaces:**
- Consumes: `services.owl_registry.list() -> list[OwlAgentManifest]` (already used identically in `owl_build_existence.py:38`: `others = list(registry.all())` — this task uses `.list()` instead of `.all()` for a stable, sorted-by-name order, per `registry.py:220-222`). Each `OwlAgentManifest` has `.name: str` and `.role: str` (confirmed: `owl_build_existence.py:44` reads `f"{m.name} {m.role}"`).
- Produces: a new local `owls_block: str` variable, added to the existing `parts` list in `assemble.py:219-221` alongside `base, capabilities, banner, persona, skills_block, state.memory_context`.

Today, `assemble.py:74-101` resolves ONLY the acting owl's own persona (`registry.get(state.owl_name)`) and injects it — no other owl's existence is ever surfaced. This is the exact gap: the model's only signal about a DIFFERENT owl (e.g. "Brain") existing comes from stale conversation history, never from a deterministic fact.

- [ ] **Step 1: Write the failing test**

First check the exact existing test setup pattern for `assemble.run()` (fixture shape for `services`/`state`) — run `ls tests/pipeline/ | grep -i assemble` and read one existing test in that file to match its fixture/mock shape exactly (do not invent a different `services`/`state` construction than what's already established). Once you know the pattern, add a test asserting: given an `owl_registry` with owls `["secretary", "Brain"]` and `state.owl_name == "secretary"`, the returned `state.system_prompt` (or wherever `assemble.run`'s output system prompt lands — confirm the exact field name from `PipelineState` before writing the assertion) contains the string `"Brain"` and does NOT contain `"secretary"` in that specific block (since the acting owl itself is excluded — it already has its own persona injected separately).

Also add a test for the fail-open path: an `owl_registry` that raises on `.list()` must not crash `assemble.run()` — it should just omit the block (matches every other try/except in this file).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_assemble_*.py -k "owls_block or existing_owls" -v` (adjust `-k` filter/file name to whatever you find in Step 1)
Expected: FAIL — the block doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

In `src/stackowl/pipeline/steps/assemble.py`, immediately after the existing persona-resolution block (after line 101, before the "Inject owned-skill playbooks" comment at line 102), add:

```python
    # Ground-truth owl visibility — without this the model has NO way to know
    # which owls already exist except stale conversation history, so it keeps
    # guess-and-retrying owl_build with name variants after a collision
    # (confirmed live incident: "Brain" -> "Researcher Brain" -> "research_brain",
    # each a fresh attempt at the same already-existing persona). Cheap: name +
    # one-line role only, never full personas — this is NOT a second persona
    # injection, just a deterministic existence fact.
    owls_block = ""
    if registry is not None:
        try:
            others = [m for m in registry.list() if m.name != state.owl_name]
            if others:
                lines = [f"- {m.name}: {m.role}" for m in others]
                owls_block = (
                    "Owls that ALREADY EXIST — do not call owl_build with "
                    "action='create' for any of these; use action='edit' or "
                    "delegate_task to reach one instead:\n" + "\n".join(lines)
                )
        except Exception as exc:  # no-hidden-errors: never crash the turn
            log.engine.error(
                "[pipeline] assemble: existing-owls block FAILED — skipped",
                exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
            )
```

Then update the `parts` list (currently `assemble.py:219-221`):
```python
    parts = [
        p for p in (base, capabilities, banner, persona, owls_block, skills_block, state.memory_context) if p
    ]
```
(inserts `owls_block` between `persona` and `skills_block` — keep the rest of the line identical, just add the one new name in the tuple)

Also add `owls_len` to the exit-log `_fields` dict at `assemble.py:223-231` (matching the existing pattern of `persona_len`/`banner_len`):
```python
            "owls_len": len(owls_block),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_assemble_*.py -v` (the whole file, to confirm no regression to existing tests)
Expected: all PASS, including the new tests from Step 1.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/assemble.py tests/pipeline/test_assemble_*.py
git commit -m "fix(pipeline): inject existing-owls ground truth into every turn's system prompt

The model had no way to know which owls already exist except stale
conversation history — assemble.py injected only the ACTING owl's own
persona, never a list of other owls. This is the confirmed root cause
of a recurring live incident: a plain greeting kept triggering
owl_build create attempts for an owl ('Brain') that already existed,
because the model could only discover the collision via a failed tool
call, then invented a new name and tried again. Adds a cheap,
deterministic name+role block (no full personas) so the model can see
Brain exists and route to edit/delegate_task instead of guessing."
```

---

### Task 2: Harden owl_build's duplicate-detection with a cheap name-overlap check

**Files:**
- Modify: `src/stackowl/tools/meta/owl_build_existence.py`
- Test: `tests/tools/meta/test_owl_build_existence.py` — check exact filename with `ls tests/tools/meta/ | grep -i existence`

**Interfaces:**
- Consumes: same `registry.all()` / `OwlAgentManifest.name` already used in this file.
- Produces: `existing_near_match()`'s existing return contract is unchanged (`str | None` — the name of the near-duplicate, or `None`). The new check runs BEFORE the semantic embedding call and can short-circuit to a match without needing an embedder at all — this makes the guard work even in a fail-open (no-embedder) deployment, which is currently a silent gap (`existing_near_match` returns `None` immediately when `reg is None`, meaning ZERO duplicate protection beyond exact name-equality in that mode today).

Today, `existing_near_match` (the whole function) only catches a near-duplicate via semantic embedding cosine similarity at a 0.85 threshold, comparing `f"{spec.name} {spec.specialty or ''}"` against `f"{m.name} {m.role}"` for every existing owl. Confirmed via live incident: `research_brain` (created) and `Researcher Brain` (attempted, rejected only for having a space in the name — a SEPARATE validation error, not this dedup check) were never flagged as duplicates of `Brain` by this function, because the SPECIALTY TEXT the model generated didn't score high enough cosine similarity against Brain's own role text — even though the NAMES themselves obviously share the token "brain".

- [ ] **Step 1: Write the failing test**

Read the existing test file first (`tests/tools/meta/test_owl_build_existence.py` or whatever `ls` shows) to match its exact fixture shape (how `registry`/`spec`/`services` are constructed there). Add a test: given an existing owl named `"Brain"` and a new `OwlBuildSpec` with `name="research_brain"` (no semantic embedder wired, i.e. `services.embedding_registry = None` or however the existing tests represent "no embedder"), `existing_near_match(spec, registry, services)` returns `"Brain"` (i.e. catches it WITHOUT needing any embedding call at all). Also add a test confirming a genuinely unrelated name (e.g. `"weather_bot"` vs existing `"Brain"`) still returns `None` — the new check must not false-positive on unrelated names.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/meta/test_owl_build_existence.py -v`
Expected: the new tests FAIL (the function currently returns `None` immediately when there's no embedder, with zero name-based check).

- [ ] **Step 3: Write minimal implementation**

In `src/stackowl/tools/meta/owl_build_existence.py`, add a normalization helper and a name-overlap check that runs FIRST, before the embedder short-circuit:

```python
def _normalize_name_tokens(name: str) -> set[str]:
    """Lowercase, split on non-alphanumeric, drop empty tokens.

    'research_brain' -> {'research', 'brain'}; 'Researcher Brain' -> {'researcher', 'brain'};
    'Brain' -> {'brain'}. Used for a cheap, deterministic near-duplicate check that
    works even with no semantic embedder wired (today's fail-open path has ZERO
    duplicate protection beyond exact name-equality in that mode)."""
    import re
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def _name_token_overlap_match(spec_name: str, others: list[OwlAgentManifest]) -> str | None:
    """Return an existing owl's name if it shares a token with ``spec_name``, else None.

    Deliberately cheap and deterministic (no embedding call) — catches the exact
    incident shape confirmed live: 'research_brain' / 'Researcher Brain' both
    share the 'brain' token with an existing 'Brain' owl, even though their
    GENERATED SPECIALTY TEXT doesn't score high enough cosine similarity to trip
    the semantic check below. A single shared token is deliberately a LOW bar —
    this is a create-time refusal, not a silent auto-merge; a false positive just
    means the model gets redirected to delegate_task/edit and can still proceed
    under a genuinely different name if it disagrees."""
    spec_tokens = _normalize_name_tokens(spec_name)
    if not spec_tokens:
        return None
    for m in others:
        if _normalize_name_tokens(m.name) & spec_tokens:
            return m.name
    return None
```

Then in `existing_near_match`, add the new check right after the `others = list(registry.all())` / `if not others: return None` lines (before the `reg = getattr(...)` embedder resolution — actually reorder so the cheap check runs unconditionally first):

```python
async def existing_near_match(
    spec: OwlBuildSpec, registry: OwlRegistry, services: StepServices
) -> str | None:
    others = list(registry.all())
    if not others:
        return None
    token_match = _name_token_overlap_match(spec.name, others)
    if token_match is not None:
        log.tool.info(
            "owl_build.existence: name-token overlap found — redirecting to delegate",
            extra={"_fields": {"owl": spec.name, "match": token_match}},
        )
        return token_match
    reg = getattr(services, "embedding_registry", None)
    if reg is None:
        return None  # fail-open — no embedder wired
    ...
```
(keep the rest of the function body — the semantic embedding path — unchanged; only reorder the `others`/early-return lines to the top and insert the new token-overlap check between them and the embedder resolution)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/meta/test_owl_build_existence.py -v`
Expected: all PASS, including both new tests.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/tools/meta/owl_build_existence.py tests/tools/meta/test_owl_build_existence.py
git commit -m "fix(tools): add cheap name-token overlap check to owl_build's dedup guard

existing_near_match only caught duplicates via semantic embedding
cosine similarity of GENERATED SPECIALTY TEXT at a 0.85 threshold, and
returned None immediately with zero protection when no embedder was
wired. Confirmed live: 'research_brain' was never flagged as a
duplicate of 'Brain' by this check. Adds a deterministic
normalized-name-token-overlap check that runs first (no embedder
needed) — catches the exact incident shape and works even in a
no-embedder deployment. Complements Task 1's ground-truth fix as
defense-in-depth for cases where the model still attempts a create
despite seeing the existing-owls list."
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 addresses the confirmed primary root cause (no ground truth). Task 2 hardens the existing-but-insufficient automated safety net as defense-in-depth. Both are independent, minimal, fail-open, and tested.
- **Explicitly deferred, not a gap in this plan:** the `tool_outcome_ledger`/`delivery_gate.py` give-up-gate mismatch is intentionally out of scope (see plan header) — it's a symptom-reporting issue expected to stop mattering once Task 1 removes the redundant-creation pattern that triggers it.
- **Type/signature consistency:** `_name_token_overlap_match(spec_name: str, others: list[OwlAgentManifest]) -> str | None` is defined once and consumed once, in the same function, same file.
