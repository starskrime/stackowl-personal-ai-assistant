"""PA5(a) — effect_class → verification-surface coverage RATCHET.

A tool that declares a durable ``effect_class`` (``creates_persistent_entity`` /
``sends_message`` / ``schedules``) is one whose success the honesty layer must be
able to MEASURE — otherwise a "✅ done" draft can ship over an effect that never
landed (a lying success). The MEASURED verdict comes from a tool OVERRIDING at
least one verification surface: :meth:`Tool.verify` (file artifacts) or
:meth:`Tool.post_condition` (text / http / delivery). Both have base no-op
defaults, so a NEW effect-classed tool that forgets to wire one ships unverifiable.

This meta-test is the ratchet: it enumerates the REAL production registry
(:meth:`ToolRegistry.with_defaults`) and fails if any effect-classed tool overrides
NEITHER surface — naming the offender(s) so the author wires verify()/post_condition()
before merge. Read-only tools (``effect_class is None``) are exempt: the ~92
un-migrated tools stay byte-identical (ADR-1 opt-in, base.py).
"""

from __future__ import annotations

from stackowl.tools.base import Tool
from stackowl.tools.registry import ToolRegistry

# Bounded, documented debt: effect-classed tools that ship today WITHOUT a
# verification surface. The ratchet exempts ONLY these, so it still catches every
# NEW addition while keeping the existing gap visible. Keep this set EMPTY where
# possible; each entry needs a reason + a removal path.
#
# TODO(persistence-arc): cronjob declares effect_class="schedules" but overrides
# neither verify() nor post_condition() — installing a recurring job is not yet
# proven by a world-read (e.g. re-querying the scheduler that the row exists). It
# can over-claim "scheduled!" on a silent install failure. Wire a post_condition
# (DeliveryAck/Custom reading the JobScheduler back) and DELETE this entry.
_KNOWN_UNVERIFIED: frozenset[str] = frozenset({"cronjob"})


def _overrides_verification_surface(tool: Tool) -> bool:
    """True iff the tool overrides at least one MEASURED verification surface."""
    return (
        type(tool).verify is not Tool.verify
        or type(tool).post_condition is not Tool.post_condition
    )


def _effect_classed_tools() -> list[Tool]:
    """Every tool in the production registry that declares a durable effect_class."""
    return [
        t for t in ToolRegistry.with_defaults().all()
        if t.manifest.effect_class is not None
    ]


def test_effect_class_tools_have_a_verification_surface() -> None:
    """RATCHET: every effect-classed tool must override verify() or post_condition().

    A new effect-classed tool with neither fails here — telling the author to wire a
    MEASURED verdict so it ships verifiable, never a lying success."""
    offenders = sorted(
        t.name
        for t in _effect_classed_tools()
        if not _overrides_verification_surface(t) and t.name not in _KNOWN_UNVERIFIED
    )
    assert not offenders, (
        f"effect-classed tool(s) {offenders} declare an effect_class but override "
        "NEITHER Tool.verify nor Tool.post_condition. A durable effect MUST be "
        "MEASURABLE — implement verify() (file artifacts) or post_condition() "
        "(text/http/delivery) so the honesty layer can demand a verified==True "
        "receipt before the success enters the answer. Do not weaken this test; "
        "if the gap is a genuine, bounded debt, add the tool to _KNOWN_UNVERIFIED "
        "with a reason + removal path."
    )


def test_ratchet_is_non_vacuous() -> None:
    """Prove the ratchet actually inspects covered tools: a few known effect-classed
    tools ARE present AND DO override a surface (so a green run is real coverage, not
    an empty enumeration)."""
    covered = {
        t.name: _overrides_verification_surface(t) for t in _effect_classed_tools()
    }
    # These three are migrated and MUST carry a verification surface.
    for name in ("send_message", "skill_manage", "owl_build"):
        assert covered.get(name) is True, f"{name} must override a verification surface"


def test_known_unverified_allowlist_is_still_a_real_gap() -> None:
    """Self-policing: a tool only earns its _KNOWN_UNVERIFIED exemption while it
    genuinely lacks a surface AND is still registered with an effect_class. When the
    debt is paid (a surface is wired) this fails — forcing the entry's removal so the
    allowlist never silently masks a now-covered tool."""
    by_name = {t.name: t for t in _effect_classed_tools()}
    for name in _KNOWN_UNVERIFIED:
        tool = by_name.get(name)
        assert tool is not None, (
            f"{name!r} is allowlisted but is no longer a registered effect-classed "
            "tool — remove it from _KNOWN_UNVERIFIED."
        )
        assert not _overrides_verification_surface(tool), (
            f"{name!r} now overrides a verification surface — remove it from "
            "_KNOWN_UNVERIFIED so the ratchet enforces it."
        )
