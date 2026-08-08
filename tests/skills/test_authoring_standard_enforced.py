"""D10.2 — the standard is ENFORCED at the write, not merely defined.

The D05.2 lesson applied ahead of time: a validator nothing calls passes all its
own tests and stops nothing. These drive the real `gated_skill_write`.

Ordering is the load-bearing part. The standard is checked BEFORE the security
scan and before consent, so a malformed skill never reaches a human for
approval — asking someone to approve something we already know we will not keep
wastes their attention and teaches them to click through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.skills import standard as std
from stackowl.skills.authoring import SkillWriteRequest, gated_skill_write
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore

_GOOD_BODY = "\n".join(f"## {s}\n\ncontent.\n" for s in std.REQUIRED_SECTIONS)


def _request(tmp_path: Path, *, name="good_skill", description="Fetch a page.",
             when_to_use="When a page must be fetched.", body=_GOOD_BODY):
    manifest = SkillManifest(
        name=name, description=description, when_to_use=when_to_use,
        source="learned",
    )
    return SkillWriteRequest(
        target_dir=tmp_path / "learned" / name,
        manifest=manifest,
        body=body,
        skill_md_text=f"---\nname: {name}\n---\n\n{body}",
        tool_name="skill_synthesizer",
        consent_summary=f"write skill {name}",
    )


class _ExplodingGate:
    """Consent must never be reached for a non-conforming write."""

    def __init__(self) -> None:
        self.asked = False

    async def check(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
        self.asked = True
        raise AssertionError("consent was asked for a skill we already refuse")


@pytest.mark.asyncio
async def test_a_numbered_name_is_refused(tmp_db, tmp_path):
    """The rule the 265 duplicates existed for want of."""
    store = SkillIndexStore(tmp_db)
    res = await gated_skill_write(
        _request(tmp_path, name="recover_tool_search-3"),
        store=store, consent_gate=None,
    )
    assert res.ok is False
    assert "numeric suffix" in res.reason


@pytest.mark.asyncio
async def test_nothing_is_written_to_disk_when_refused(tmp_db, tmp_path):
    """A refusal that still leaves a directory behind would let the next scan
    pick the skill up anyway."""
    store = SkillIndexStore(tmp_db)
    req = _request(tmp_path, name="bad_skill-1")
    await gated_skill_write(req, store=store, consent_gate=None)
    assert not req.target_dir.exists()


@pytest.mark.asyncio
async def test_consent_is_never_asked_for_a_non_conforming_skill(tmp_db, tmp_path):
    """ORDERING. The standard runs before the consent prompt on purpose."""
    gate = _ExplodingGate()
    res = await gated_skill_write(
        _request(tmp_path, name="bad-2"), store=SkillIndexStore(tmp_db),
        consent_gate=gate,  # type: ignore[arg-type]
    )
    assert res.ok is False
    assert gate.asked is False


@pytest.mark.asyncio
async def test_every_violation_is_reported_in_one_response(tmp_db, tmp_path):
    """R6Q22 — one retry, not three. A long description AND a bad name AND a
    missing body must all appear."""
    res = await gated_skill_write(
        _request(
            tmp_path, name="bad_name-7", description="x" * 90, body="## Nope\n",
        ),
        store=SkillIndexStore(tmp_db), consent_gate=None,
    )
    assert res.ok is False
    for expected in ("numeric suffix", "90 characters", "missing required section"):
        assert expected in res.reason, f"{expected!r} absent from: {res.reason}"


@pytest.mark.asyncio
async def test_the_rejection_names_the_standard_version(tmp_db, tmp_path):
    """So a skill refused today can be told apart from one refused under a later
    version of the rules (R6Q24)."""
    res = await gated_skill_write(
        _request(tmp_path, name="bad-1"), store=SkillIndexStore(tmp_db),
        consent_gate=None,
    )
    assert f"v{std.STANDARD_VERSION}" in res.reason


@pytest.mark.asyncio
async def test_a_conforming_skill_still_writes(tmp_db, tmp_path):
    """The gate must not have become 'refuse everything'. With 0 of 437 existing
    skills conforming, this is the test that proves authoring still works.

    Needs a real consent gate: consent_gate=None fails closed by design, which
    is why the refusal tests above prove ORDERING — they return the standard's
    reason, not 'no consent gate available'."""
    from stackowl.tools.consent import ConsentPolicy, TrustTier
    from stackowl.tools.registry import ConsequentialActionGate

    gate = ConsequentialActionGate(
        ConsentPolicy(tiers={"skill_synthesizer": TrustTier.AUTO})
    )
    store = SkillIndexStore(tmp_db)
    req = _request(tmp_path)
    res = await gated_skill_write(req, store=store, consent_gate=gate)
    assert res.ok is True, res.reason
    assert (req.target_dir / "SKILL.md").exists()
