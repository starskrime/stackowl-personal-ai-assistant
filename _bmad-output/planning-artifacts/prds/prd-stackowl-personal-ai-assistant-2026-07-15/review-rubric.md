# PRD Quality Review — Owl DNA Self-Improvement Lifecycle

## Overall verdict
Holds up well. Decisions are stated as decisions (failure-learning excluded, evolve_now gated behind the shadow gate), the thesis (reuse existing safety machinery, close specific wiring gaps, sequence safety before activation) drives feature order rather than "easy first," and FRs are mostly testable. One thin spot: Feature 7 (cross-signal) is looser than the rest — acceptable since it's explicitly the lowest-priority, first-to-cut item, not accidental vagueness.

## Decision-readiness — strong
Explicitly Out of Scope section does real work (names what was rejected and why, not just what was chosen). Epic ordering states a real trade-off: Feature 5 ships later specifically because shipping it before Feature 4 would reproduce the reactive-only-safety failure mode research flagged — this is a stated cost (slower activation), not smoothed away.

## Substance over theater — strong
No personas (correct for single-operator internal tool — see Shape fit). NFRs are product-specific (migration discipline, 4-point logging, gateway-integration-test convention) rather than generic "must be scalable/secure" boilerplate. Vision statement names this system's actual constraints (positive-only, reversible, pre-ship floor) — not swappable into an unrelated PRD.

## Strategic coherence — strong
Thesis is explicit in Background + Epics: most of the hard safety work already exists, the gap is wiring + one novel gate, and activation must not outrun safety. Success Metrics test the thesis directly (restore reverts exactly, bad deltas get caught, evolve_now provably uses the safe path) rather than measuring unrelated activity. Counter-metric named (drift rate must not increase).

### Findings
- **low** Feature 7's FRs (§ Feature 7 — Skills↔DNA Cross-Signal) are looser than Features 1–6 — "becomes an available input signal" doesn't specify whether attribution's band logic itself changes or a field is merely exposed. *Fix:* acceptable to leave loose given Feature 7 is the explicitly-lowest-priority, first-cut candidate (see Epics) — but epics/stories should treat this FR as needing a design decision at story-write time, not implementation-ready as-is.

## Done-ness clarity — adequate
Most FRs carry a testable consequence, several explicitly mirrored in Success Metrics (FR-2↔restore test, FR-9↔shadow-gate test, FR-13↔evolve_now-path test, FR-4↔reflect-recall test). FR-16/17 (Feature 7) are the exception — see finding above.

## Scope honesty — strong
Explicitly Out of Scope section present and substantive. The one Discovery-time assumption (stakes = internal) was confirmed via direct question and folded into Constraints as a settled fact, not left as a dangling `[ASSUMPTION]` tag. No Open Questions remain — both flagged Discovery-time decisions were resolved and are logged in `.memlog.md`.

## Downstream usability — adequate
FR IDs (FR-1…FR-17) and NFR IDs (NFR-1…NFR-5) are contiguous and unique. No formal glossary section — acceptable at this shape/stakes (small, stable vocabulary: DNA, trait, checkpoint, shadow-validation gate), but `addendum.md`'s file/class map effectively serves that role for downstream architecture work.

## Shape fit — strong
Correctly uses capability-spec shape: no UJs, no personas, matches "internal tool, single-operator role" exactly. Not over-formalized.

## Mechanical notes
ID continuity clean. No UJs, so no protagonist check applies. No dangling `[ASSUMPTION]` tags — the one Discovery-time assumption was confirmed and resolved before drafting.
