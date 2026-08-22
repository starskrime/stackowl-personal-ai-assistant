"""D05.8 — a cut in the presented set must be witnessed, and the operator's cap must reach it.

Three defects, all measured live on 2026-08-22 before being written as tests:

* **A** — ``OrchestratorSettings.tool_count_cap`` reached only the budgeted path.
  ``ToolPresentation`` was constructed with no config at all three call sites in
  ``registry.py``, so ``select()`` was pinned to ``_DEFAULT_CAP`` and the operator's
  lever silently did not apply. 80 turns in an 8-day window presented exactly 36
  tools while the configured cap was 30 and then 40; no other path can produce 36.

* **B** — the ``restrict_to`` (planned-envelope) branch truncated ``planned`` and
  returned *before* both the entry log and the drop log. ``[presentation] select:
  eligible tools NOT presented`` fired ZERO times in 8 days against 955 from
  ``to_provider_schema``. A cut with no witness.

* **C** — the drop lines emitted ``dropped[:20]``. ``dropped`` preserves rank order,
  which for tools carrying no usage score and no declared priority is the ALPHABET,
  so the field was a fixed alphabetical prefix: ``send_message``, ``objective``,
  ``todo``, ``run_tests``, ``web_search``, ``session_search`` and others could never
  appear in it. ``dropped_truncated`` was ``true`` on 178 of 178 records that day —
  never once false, so it marked the permanent state rather than an edge case.
"""

from __future__ import annotations

import logging

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


class _T(Tool):
    def __init__(self, name: str, *, group: str | None = None) -> None:
        self._name, self._group = name, group

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self._name, parameters=self.parameters,
            action_severity="read", toolset_group=self._group,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


def _registry_with(names: list[str], *, group: str | None = None) -> ToolRegistry:
    r = ToolRegistry()
    for n in names:
        r.register(_T(n, group=group))
    return r


def _present_names(schemas: list[dict]) -> set[str]:  # type: ignore[type-arg]
    out = set()
    for s in schemas:
        n = s.get("name") or (s.get("function") or {}).get("name")
        if n:
            out.add(n)
    return out


def _drop_records(caplog: object) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records  # type: ignore[attr-defined]
        if "eligible tools NOT presented" in r.getMessage()
    ]


def _fields(record: logging.LogRecord) -> dict[str, object]:
    return dict(getattr(record, "_fields", {}) or {})


# ---------------------------------------------------------------------------
# A — the operator's cap reaches the envelope path
# ---------------------------------------------------------------------------

def test_the_operators_cap_reaches_the_planned_envelope_path() -> None:
    """`max_tools` must bind on `restrict_to`, not just on the budgeted branch.

    Before the fix this branch was pinned to `_DEFAULT_CAP` and no configuration
    could move it, so an operator lowering `tool_count_cap` for a weak model got
    the documented behaviour on one path and silence on the other.
    """
    planned = [f"planned_{i:02d}" for i in range(30)]
    r = _registry_with(["tool_search", "tool_describe", *planned])

    tight = _present_names(
        r.to_provider_schema(
            "anthropic", restrict_to=frozenset(planned), max_tools=8,
        )
    )
    assert len(tight) == 8, tight

    loose = _present_names(
        r.to_provider_schema(
            "anthropic", restrict_to=frozenset(planned), max_tools=20,
        )
    )
    assert len(loose) == 20, loose

    # The discovery pair is non-evictable on both — an owl must always be able to
    # look for what it lacks, however tight the envelope.
    assert {"tool_search", "tool_describe"} <= tight
    assert {"tool_search", "tool_describe"} <= loose


def test_omitting_max_tools_leaves_the_envelope_path_unchanged() -> None:
    """Callers that pass nothing keep the previous default. No silent widening."""
    from stackowl.tools._infra.presentation import _DEFAULT_CAP

    planned = [f"planned_{i:02d}" for i in range(_DEFAULT_CAP + 20)]
    r = _registry_with(["tool_search", "tool_describe", *planned])
    got = _present_names(
        r.to_provider_schema("anthropic", restrict_to=frozenset(planned))
    )
    assert len(got) == _DEFAULT_CAP


# ---------------------------------------------------------------------------
# B — the envelope path says what it cut
# ---------------------------------------------------------------------------

def test_the_envelope_path_names_what_it_dropped(caplog: object) -> None:
    """A planned envelope wider than the cap must leave a record naming the loss.

    This is the check that was structurally impossible before: the branch returned
    above every log site, so a task envelope could lose its tail with no line in
    any log at any level.
    """
    planned = [f"planned_{i:02d}" for i in range(30)]
    r = _registry_with(["tool_search", "tool_describe", *planned])

    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        presented = _present_names(
            r.to_provider_schema(
                "anthropic", restrict_to=frozenset(planned), max_tools=10,
            )
        )

    records = _drop_records(caplog)
    assert records, "the envelope path cut 22 tools and said nothing"
    f = _fields(records[-1])
    assert f["presented"] == len(presented) == 10
    assert f["dropped_count"] == 22
    assert f["cap"] == 10
    dropped = list(f["dropped"])  # type: ignore[call-overload]
    assert len(dropped) == 22, "every dropped name must be present, not a prefix"
    assert set(dropped).isdisjoint(presented)


def test_an_envelope_that_fits_stays_silent(caplog: object) -> None:
    """No cut, no line. An audit that cries wolf trains its reader to ignore it."""
    planned = [f"planned_{i:02d}" for i in range(5)]
    r = _registry_with(["tool_search", "tool_describe", *planned])

    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        r.to_provider_schema(
            "anthropic", restrict_to=frozenset(planned), max_tools=20,
        )

    assert not _drop_records(caplog)


# ---------------------------------------------------------------------------
# C — the eviction record names every tool, not an alphabetical prefix
# ---------------------------------------------------------------------------

def test_the_profile_drop_line_names_every_tool_not_the_first_twenty(
    caplog: object,
) -> None:
    """`dropped` must be complete.

    The old `[:20]` slice was applied to a rank-ordered list whose tail is
    alphabetical, so the field had a fixed alphabetical ceiling and the programme
    spent a week reading it as if it were the whole answer.
    """
    from stackowl.tools._infra.presentation import _DEFAULT_ALWAYS

    group_tools = [f"z_grouped_{i:02d}" for i in range(40)]
    r = _registry_with([*sorted(_DEFAULT_ALWAYS), *group_tools], group="widgets")

    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        r.to_provider_schema("anthropic", profile=["widgets"], max_tools=12)

    records = _drop_records(caplog)
    assert records, "40 grouped tools against a cap of 12 dropped nothing?"
    f = _fields(records[-1])
    dropped = list(f["dropped"])  # type: ignore[call-overload]
    assert f["dropped_count"] > 20, "test needs more than 20 drops to be meaningful"
    assert len(dropped) == f["dropped_count"], (
        "the emitted list is shorter than the count it reports — still truncated"
    )
    # The alphabetically-last dropped name must be reachable. Under the old slice
    # it never was, which is precisely how send_message became invisible.
    assert max(dropped) in dropped  # type: ignore[type-var]
    assert dropped[-1] == max(dropped)  # type: ignore[type-var]


def test_the_budgeted_drop_line_names_every_tool(caplog: object) -> None:
    """Same contract on the budgeted path, which is where 955 of 955 events came from."""
    from stackowl.tools._infra.presentation import _DEFAULT_ALWAYS

    group_tools = [f"z_grouped_{i:02d}" for i in range(40)]
    r = _registry_with([*sorted(_DEFAULT_ALWAYS), *group_tools], group="widgets")

    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        r.to_provider_schema(
            "anthropic",
            profile=["widgets"],
            budget={"window": 262_144, "fixed_cost_tokens": 4_000, "max_tools": 12},
        )

    records = _drop_records(caplog)
    assert records
    f = _fields(records[-1])
    dropped = list(f["dropped"])  # type: ignore[call-overload]
    assert f["dropped_count"] > 20
    assert len(dropped) == f["dropped_count"]
    assert f["dropped_truncated"] is False
