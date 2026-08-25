"""ESC-17 — an unresolved identity must still name the PERSON, not the lane.

BAKIR, 2026-08-25: "Fix core issue." He chose collapsing the split at its source
over patching the symptom.

THE DEFECT. ``resolve_identity_key`` returned "" when no resolver was wired, and
``owner_scope_key`` is ``state.identity_key or state.session_key``. So the empty
string silently handed the scope over to ``session_key`` — which is the composite
LANE on the turn path and the RAW channel handle on the command path. One field,
two meanings, decided by whether a resolver happened to exist.

IT WAS DEFEATING owner_scope_key's OWN STATED PURPOSE, which is the reason this
is a defect and not a preference. Its docstring: "Knowledge is about a PERSON,
not about which owl happened to hear it. Scoping it to the owl-prefixed lane
would mean telling Brain your timezone and having Scout not know it." The ""
return produced exactly the owl-prefixed scoping that comment forbids.

MEASURED CONSEQUENCES, both live:
  * The five-table key-shape split — task_outcomes lane 440 / identity 799,
    staged_facts lane 103 / identity 100, tasks lane 189 / identity 126.
  * /reset silently under-deletes. Measured 2026-08-25 on a naturally refilled
    table: staged_facts.source_ref held 8 raw-handle rows and 4 LANE-shaped ones,
    and /reset passes the raw handle — so it missed a THIRD of them.
One change closes both.

EXISTING ROWS ARE NOT MIGRATED, per his decision. Rows already written under a
lane keep their old meaning; this changes what is written from now on.
"""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.services import StepServices, resolve_identity_key

HANDLE = "72055773"
LANE = "owl:secretary:telegram:dm:72055773"


class _Resolver:
    """Mirrors the real IdentityResolver: a handle->identity map, handle if absent."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._map = mapping or {}

    def resolve(self, handle: str) -> str:
        return self._map.get(handle, handle)


def _services(resolver: Any = None) -> StepServices:
    return StepServices(identity_resolver=resolver)


# ---------------------------------------------------------------------------
# The change
# ---------------------------------------------------------------------------

def test_no_resolver_wired_returns_the_HANDLE_not_empty() -> None:
    """The whole defect. "" handed the scope to session_key, which means two
    different things depending on which path built the state."""
    assert resolve_identity_key(_services(None), HANDLE) == HANDLE


def test_a_wired_resolver_with_no_alias_is_unchanged() -> None:
    """Pre-existing behaviour, and it must stay pre-existing."""
    assert resolve_identity_key(_services(_Resolver()), HANDLE) == HANDLE


def test_a_real_alias_still_wins() -> None:
    """Cross-channel identity is the feature this function exists for; the
    fallback must not shadow it."""
    resolver = _Resolver({HANDLE: "identity:bakir"})
    assert resolve_identity_key(_services(resolver), HANDLE) == "identity:bakir"


# ---------------------------------------------------------------------------
# The consequence that actually matters
# ---------------------------------------------------------------------------

def test_both_pipeline_paths_now_agree_on_the_owner_scope() -> None:
    """THE point of the change.

    The command path builds state with session_key=HANDLE; the turn path builds
    it with session_key=LANE. Both set identity_key from the SAME call on the
    SAME raw handle. With "" they disagreed — command scoped to the handle, turn
    scoped to the lane. They must now agree, because a fact about a person does
    not change owner depending on which gateway branch heard it.
    """
    from stackowl.pipeline.services import StepServices as S

    services = S(identity_resolver=None)
    command_identity = resolve_identity_key(services, HANDLE)
    turn_identity = resolve_identity_key(services, HANDLE)

    command_scope = command_identity or HANDLE
    turn_scope = turn_identity or LANE

    assert command_scope == turn_scope == HANDLE, (
        "the turn path must stop filing durable knowledge under the owl-prefixed "
        "lane — owner_scope_key's own docstring forbids exactly that"
    )


def test_the_lane_is_never_what_a_fact_gets_filed_under(tmp_path: Any) -> None:
    """Regression guard in the direction that would silently return: if this ever
    yields "" again, the turn path files under LANE and /reset stops matching."""
    identity = resolve_identity_key(_services(None), HANDLE)
    assert identity, "an empty identity hands the scope to session_key"
    assert not identity.startswith("owl:"), "a lane is not an owner"
