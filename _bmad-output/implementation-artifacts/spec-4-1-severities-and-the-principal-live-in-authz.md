---
title: 'Severities and the principal live in authz'
type: 'refactor'
created: '2026-09-19'
status: 'in-progress'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['oversized']
deferred: []
baseline_revision: '13b4f64da98f33375547358caeb6697d2a6282c8'
---

<intent-contract>

## Intent

**Problem:** `READ`, `WRITE`, `CONSEQUENTIAL`, `ALL_SEVERITIES`, `ControlPrincipal` and its `may()` check live in `control_plane/auth.py`, but epic 4's "one door" command gate (AD-1, AD-27) needs one shared severity/principal home in `authz/` that survives control_plane's later deletion (AD-7) and that every future epic-4 surface (`commands/spec/`, the action-policy gate, standing authority) can depend on without importing `control_plane`.

**Approach:** Move the five symbols verbatim into a new `authz/severity.py` module (re-exported from `authz/__init__.py`), have `control_plane/auth.py` import and re-export them (no second definition, no behavior change), and add one `pytest.mark.tripwire` guard that AST-scans `src/stackowl` for a module outside `authz/` defining its own `READ`/`WRITE`/`CONSEQUENTIAL`-style severity constant or a class with a `.may(` authorization method.

## Boundaries & Constraints

**Always:**
- Symbols move byte-for-byt (docstrings, dataclass shape, `may()` body) — this is a relocation, not a redesign.
- `control_plane/auth.py` re-exports the five symbols from `authz` (`from stackowl.authz import ...`) so every existing import path (`stackowl.control_plane.auth.READ`, `...ALL_SEVERITIES`, `...ControlPrincipal`) keeps working unchanged.
- `authz/__init__.py` exports the five new symbols alongside its existing bounds/enforcement exports.
- Every existing control_plane auth test (`tests/control_plane/test_the_control_plane_is_locked_before_it_is_built.py`, `tests/control_plane/test_the_schedules_are_visible_as_a_set.py`) passes with zero edits.
- New module carries 4-point structured logging only where it already existed (the moved code has none — `ControlPrincipal`/severities are pure data + a pure predicate, no I/O) — no new logging is invented where the original had none.
- The new tripwire lives under `authz/` or `tests/authz/` per repo convention (cross-cutting guards live in `tests/<owning-package>/`) and is marked `pytest.mark.tripwire` so `scripts/tripwires.sh` picks it up automatically.

**Never:**
- Do not touch `TraceContext.principal` (`src/stackowl/infra/trace.py`) or `PRINCIPAL_AUTONOMOUS_SCHEDULER` (`src/stackowl/tools/consent.py`) — that is a separate, already-shipped concept (a string routing label for `ConsentPolicy`, not `ControlPrincipal`'s severity-grant object). Epic 4 does not ask this story to unify them.
- Do not touch `commands/spec/`, the action-policy gate, or standing authority — those are stories 4.3, 4.4 and 4.6.
- Do not change `ControlPrincipal.may()`'s behavior, `authenticate()`'s logic, or any credential/token code in `control_plane/auth.py` — only the five severity/principal symbols move.
- Do not add a `commands/` package or touch `bridge/` — out of scope for 4.1.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Existing import path | `from stackowl.control_plane.auth import ALL_SEVERITIES, READ, ControlPrincipal` | Resolves to the same objects as `from stackowl.authz import ...` (identity, not just equality) | N/A |
| New canonical import path | `from stackowl.authz import READ, WRITE, CONSEQUENTIAL, ALL_SEVERITIES, ControlPrincipal` | Works, same objects | N/A |
| A module outside `authz/` defines `WRITE: Final = "write"` or similar severity-shaped constant | tripwire runs | Tripwire fails, names the offending module | Failure message states the rule and the file |
| A module outside `authz/` defines a class with a `def may(self, ...)` method | tripwire runs | Tripwire fails, names the offending class/module | Failure message states the rule and the file |
| `control_plane/auth.py` itself, which re-exports (not redefines) | tripwire runs | Passes — re-export via `from stackowl.authz import X` is not a definition | N/A |

</intent-contract>

## Code Map

- `src/stackowl/control_plane/auth.py` (lines 37-83) -- current home of `READ`, `WRITE`, `CONSEQUENTIAL`, `ALL_SEVERITIES` (module-level `Final` constants) and `ControlPrincipal` (frozen dataclass with `may()`) -- these five move out; the rest of the module (credential mint/read/rotate, `authenticate()`, `check_origin()`, `is_loopback()`, `CredentialUnavailable`, `UNAUTHORIZED_BODY`/`UNAUTHORIZED_STATUS`, `SECRET_SERVICE`) stays exactly where it is.
- `src/stackowl/authz/__init__.py` -- current exports are `BoundsSpec`, `BoundsViolation`, `NetworkRule`, `ResourceCaps`, `check_effective_bounds`, `check_tool_bounds`, `effective_bounds` (owl-capability-bounds domain, Epic 2). Add the five new severity/principal exports here without touching the existing ones.
- `src/stackowl/authz/bounds.py`, `bounds_guard.py`, `enforcement.py` -- existing authz modules; new `severity.py` sits alongside them, no cross-imports needed (severity/principal is a standalone leaf: no dependency on `BoundsSpec`).
- New file `src/stackowl/authz/severity.py` -- destination module. Copy the docstring context (the "why a set not a bool" rationale) verbatim from `control_plane/auth.py` lines 37-83, adjusted only to remove the control-plane-specific framing ("this credential may read but not write" stays; drop nothing else).
- `src/stackowl/control_plane/server.py` line 43-51 -- imports `READ` from `control_plane.auth`; unaffected by the re-export (no edit required, but verify after the move that `from stackowl.control_plane.auth import READ` still resolves — it does, via re-export).
- `src/stackowl/control_plane/password.py` -- does not import any of the five symbols; unaffected.
- `tests/control_plane/test_the_control_plane_is_locked_before_it_is_built.py` line 20-27 -- imports `ALL_SEVERITIES, READ` from `stackowl.control_plane.auth`; must keep passing unedited.
- `tests/control_plane/test_the_schedules_are_visible_as_a_set.py` line 35 -- imports `ALL_SEVERITIES, READ, ControlPrincipal` from `stackowl.control_plane.auth`, constructs `ControlPrincipal(...)` and calls `.may(READ)`; must keep passing unedited.
- `tests/tenancy/test_no_owner_scope_bypass.py` -- reference pattern for an `ast`-based, `pytest.mark.tripwire`-marked repo-wide guard (module scan under `src/stackowl`, allowlist-free failure naming the offending file). Model the new tripwire's scan style on this file's structure, not its content.
- `tests/commands/test_every_command_meta_is_guarded.py` -- reference pattern for `pkgutil.iter_modules` + `ast`/`inspect` based structural scanning across a package.
- New test file `tests/authz/test_severities_and_principal_have_one_home.py` -- houses the relocation-behavior test (re-export identity) and the new tripwire (AST scan of `src/stackowl` excluding `src/stackowl/authz/` for severity-shaped constants and `may(` methods).
- `scripts/tripwires.sh` -- no edit needed; `pytest -m tripwire` already picks up any new `@pytest.mark.tripwire` test automatically.

## Tasks & Acceptance

**Execution:**
- `src/stackowl/authz/severity.py` -- create -- new home for `READ`, `WRITE`, `CONSEQUENTIAL`, `ALL_SEVERITIES`, `ControlPrincipal` (verbatim body, adjusted docstring framing only)
- `src/stackowl/authz/__init__.py` -- add imports/`__all__` entries for the five new symbols -- makes `authz/` the canonical import surface per AD-7
- `src/stackowl/control_plane/auth.py` -- delete the five symbols' definitions (lines ~37-83), replace with `from stackowl.authz import READ, WRITE, CONSEQUENTIAL, ALL_SEVERITIES, ControlPrincipal` (module-level re-export, no `__all__` needed since none exists currently — verify) -- keeps every existing `stackowl.control_plane.auth.X` import path working
- `tests/authz/test_severities_and_principal_have_one_home.py` -- create -- (a) identity test: `stackowl.control_plane.auth.ControlPrincipal is stackowl.authz.ControlPrincipal` (and same for the four constants), (b) `pytest.mark.tripwire` AST-scan test: walk every `.py` file under `src/stackowl` excluding `src/stackowl/authz/`, fail if any module-level assignment target matches a severity-constant shape (`READ`/`WRITE`/`CONSEQUENTIAL`-named `Final` string constants) or any class defines a method named `may` — report the offending file(s) in the assertion message

**Acceptance Criteria:**
- Given `READ`, `WRITE`, `CONSEQUENTIAL`, `ALL_SEVERITIES`, `ControlPrincipal` and its `may` check in `control_plane/auth.py`, when this story lands, then they live in `authz/`, `control_plane` imports them from there, and no second definition remains (AD-7)
- Given `control_plane`'s existing auth tests, when they run unedited, then they pass (`tests/control_plane/test_the_control_plane_is_locked_before_it_is_built.py`, `tests/control_plane/test_the_schedules_are_visible_as_a_set.py`, `tests/control_plane/test_the_dashboard_has_a_login.py`)
- Given the codebase, when the tripwires run, then they fail any module outside `authz/` that defines a severity constant or its own principal check (proven by a red-then-green test: the new tripwire test is run once against a deliberately-reintroduced violation to confirm it actually fails, then against the real (clean) tree to confirm it passes)
- Given the full tripwire suite (`./scripts/tripwires.sh`), when it runs after the move, then it passes, including the control_plane auth invariants

## Design Notes

The tripwire's AST scan needs a precise, low-false-positive shape test since `may` and severity-like names could appear elsewhere for unrelated reasons. Concretely:
- **Severity constant:** a module-level assignment (not inside a function/class body) whose target name is exactly one of `READ`, `WRITE`, `CONSEQUENTIAL`, or `ALL_SEVERITIES`, found via `ast.walk` on `ast.parse(source)`, restricted to `ast.Assign`/`ast.AnnAssign` nodes at module scope (`ast.Module.body`, not nested). This mirrors control_plane/auth.py's own shape (`READ: Final = "read"` etc.) precisely enough to catch a reintroduction without false-triggering on unrelated `READ`-named locals inside functions.
- **Principal `.may(` check:** an `ast.FunctionDef` named `may` whose parent is an `ast.ClassDef` (i.e., a method, not a free function — avoids flagging unrelated free functions named `may` if any exist, though a repo-wide grep should confirm none do before finalizing).
- Both checks exclude `src/stackowl/authz/` (by path prefix) since that is the new home, and exclude test files (`tests/` is not under `src/stackowl`, so no explicit exclusion needed there — the scan root is `src/stackowl` only).
- Verify via `grep -rn "^def may\|    def may" src/stackowl` before writing the AST test, to confirm zero pre-existing unrelated `may` methods elsewhere in `src/` that would need an explicit allowlist entry.

## Verification

**Commands:**
- `uv run pytest tests/authz/ tests/control_plane/ -q -p no:cacheprovider` -- expected: all pass, including the two pre-existing severity-touching tests and the new relocation/tripwire test
- `uv run pytest -m tripwire -q -p no:cacheprovider` -- expected: passes (this is exactly what `scripts/tripwires.sh` step 1 runs)
- `./scripts/tripwires.sh` -- expected: `TRIPWIRES PASS`, ruff/mypy baselines unchanged or improved
- `uv run python -c "from stackowl.control_plane.auth import ControlPrincipal as A; from stackowl.authz import ControlPrincipal as B; assert A is B"` -- expected: no AssertionError (proves re-export identity, not just a copy)
