"""D10.2 slice 7 — migrating the pre-standard catalog.

This is the only pass in the arc that rewrites what a skill SAYS, so the tests
that matter are the ones proving it cannot quietly destroy content: it archives
before rewriting, it refuses to write a rewrite that fails the standard, and it
records conformance only when it actually achieved it.

Measured context: 168 live skills after consolidation, 157 of them over the
60-character description cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from stackowl.providers.base import Message
from stackowl.skills import standard
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.standard_migration import SkillStandardMigrator
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.registry import ConsequentialActionGate

pytestmark = pytest.mark.asyncio

_STAMP = "20260808-190000"
_CONFORMING_BODY = "\n".join(
    f"## {s}\n\nSomething the section can honestly say.\n"
    for s in standard.REQUIRED_SECTIONS
)
_LONG_DESC = "A very long description " * 8  # ~192 chars, like the real median


@dataclass
class _Completion:
    content: str


class _ScriptedProvider:
    """Returns canned JSON, one response per call.

    ENFORCES THE REAL CONTRACT. This double originally accepted anything, so the
    migrator passed plain dicts and every test passed — while the first live
    batch failed on all three skills with AttributeError inside the provider,
    which reads ``message.documents`` to decide if a turn carries content blocks.
    A double looser than the thing it stands in for tests nothing.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        _assert_real_messages(messages)
        if not self._responses:
            raise AssertionError("provider called more times than scripted")
        return _Completion(self._responses.pop(0))


def _assert_real_messages(messages) -> None:  # noqa: ANN001
    for m in messages:
        assert isinstance(m, Message), (
            f"provider was handed a {type(m).__name__}, not a Message — the real "
            f"provider reads attributes off these and would raise"
        )


class _ExplodingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        _assert_real_messages(messages)
        raise RuntimeError("model is down")


def _good_response(description="Fetch a page.", body=_CONFORMING_BODY) -> str:
    import json
    return json.dumps({
        "description": description,
        "when_to_use": "When a page must be fetched. Not for local files.",
        "body": body,
    })


def _gate() -> ConsequentialActionGate:
    return ConsequentialActionGate(
        ConsentPolicy(tiers={"skill_synthesizer": TrustTier.AUTO}),
    )


async def _seed(store: SkillIndexStore, root: Path, name: str, *,
                description: str = _LONG_DESC, execs: int = 0) -> int:
    d = root / "learned" / name
    d.mkdir(parents=True)
    body = "# old\n\nfree-form original content\n"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nwhen_to_use: w\n"
        f"source: learned\n---\n\n{body}", encoding="utf-8",
    )
    skill_id = await store.upsert(LoadedSkill(
        manifest=SkillManifest(
            name=name, description=description, when_to_use="w", source="learned",
        ),
        path=d, body=body, tools_registered=0, owls_registered=0,
    ))
    for _ in range(execs):
        await store.increment_n_executions(skill_id)
    return skill_id


def _migrator(store, tmp_path, provider) -> SkillStandardMigrator:
    return SkillStandardMigrator(
        store, provider,
        archive_root=tmp_path.parent / "migrated",
        consent_gate=_gate(),
    )


# --------------------------------------------------------------------------- #
# It must not act unless told to.
# --------------------------------------------------------------------------- #


async def test_the_default_is_a_dry_run(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")
    provider = _ScriptedProvider([])  # any call would raise

    report = await _migrator(store, tmp_path, provider).run(stamp=_STAMP)

    assert provider.calls == 0, "a dry run must not pay for an LLM call"
    assert report.applied is False
    assert report.remaining == 1
    assert "old_skill" in report.outcomes[0].name
    # A preview entry is neither a success nor a failure. The first live dry run
    # printed "failed 5" for five skills nothing had been attempted on.
    assert report.outcomes[0].planned is True
    assert report.failed == 0
    assert report.planned == 1
    assert "would migrate 1 of 1" in report.summary()


async def test_the_batch_is_bounded(tmp_db, tmp_path):
    """157 LLM calls in one unattended pass is not a cost to discover afterwards."""
    store = SkillIndexStore(tmp_db)
    for i in range(5):
        await _seed(store, tmp_path, f"skill_{i}")

    report = await _migrator(store, tmp_path, _ScriptedProvider([])).run(
        stamp=_STAMP, limit=2,
    )

    assert len(report.outcomes) == 2
    assert report.remaining == 5


# --------------------------------------------------------------------------- #
# The rewrite.
# --------------------------------------------------------------------------- #


async def test_a_conforming_rewrite_is_written_and_recorded(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")

    report = await _migrator(
        store, tmp_path, _ScriptedProvider([_good_response()]),
    ).run(apply=True, stamp=_STAMP, limit=1)

    assert report.migrated == 1, report.outcomes[0].reason
    sk = await store.get("learned", "old_skill")
    assert sk is not None
    assert sk.description == "Fetch a page."
    assert sk.standard_version == standard.STANDARD_VERSION
    assert "## Verification" in (tmp_path / "learned" / "old_skill" / "SKILL.md").read_text()


async def test_the_version_is_bumped_because_the_content_changed(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")

    await _migrator(
        store, tmp_path, _ScriptedProvider([_good_response()]),
    ).run(apply=True, stamp=_STAMP, limit=1)

    sk = await store.get("learned", "old_skill")
    assert sk is not None
    assert sk.version == "0.1.1"


async def test_the_original_is_archived_before_the_rewrite(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")

    report = await _migrator(
        store, tmp_path, _ScriptedProvider([_good_response()]),
    ).run(apply=True, stamp=_STAMP, limit=1)

    assert report.archive_path is not None
    archived = (report.archive_path / "old_skill" / "SKILL.md").read_text()
    assert "free-form original content" in archived


# --------------------------------------------------------------------------- #
# What must NOT happen.
# --------------------------------------------------------------------------- #


async def test_a_non_conforming_rewrite_is_refused_and_the_original_survives(
    tmp_db, tmp_path,
):
    """THE test. The validator was built before the migration precisely so an
    LLM that ignores the seven sections cannot damage the catalog."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")
    bad = _good_response(body="## Just One Section\n\nnope\n")

    report = await _migrator(store, tmp_path, _ScriptedProvider([bad])).run(
        apply=True, stamp=_STAMP, limit=1,
    )

    assert report.migrated == 0
    assert "rejected" in report.outcomes[0].reason
    text = (tmp_path / "learned" / "old_skill" / "SKILL.md").read_text()
    assert "free-form original content" in text, "the original was overwritten"
    sk = await store.get("learned", "old_skill")
    assert sk is not None
    assert sk.standard_version == 0, "conformance recorded for a rewrite we refused"


async def test_an_over_long_rewritten_description_is_refused(tmp_db, tmp_path):
    """The single most common violation in the backlog is the one the model is
    most likely to reproduce."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")
    bad = _good_response(description="x" * (standard.MAX_DESCRIPTION_CHARS + 5))

    report = await _migrator(store, tmp_path, _ScriptedProvider([bad])).run(
        apply=True, stamp=_STAMP, limit=1,
    )

    assert report.migrated == 0
    sk = await store.get("learned", "old_skill")
    assert sk is not None and sk.standard_version == 0


async def test_a_provider_failure_leaves_the_skill_alone(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")

    report = await _migrator(store, tmp_path, _ExplodingProvider()).run(
        apply=True, stamp=_STAMP, limit=1,
    )

    assert report.migrated == 0
    assert "provider" in report.outcomes[0].reason
    sk = await store.get("learned", "old_skill")
    assert sk is not None and sk.standard_version == 0


async def test_a_failed_skill_is_retried_on_the_next_run(tmp_db, tmp_path):
    """Because standard_version is written only on success. Recording it
    optimistically would make the migrator skip exactly the skills it failed
    on — invisible in its own report, and permanent."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")

    first = await _migrator(store, tmp_path, _ExplodingProvider()).run(
        apply=True, stamp=_STAMP, limit=1,
    )
    assert first.migrated == 0

    second = await _migrator(
        store, tmp_path, _ScriptedProvider([_good_response()]),
    ).run(apply=True, stamp=_STAMP, limit=1)

    assert second.migrated == 1


async def test_an_already_migrated_skill_is_not_touched_again(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")
    await _migrator(
        store, tmp_path, _ScriptedProvider([_good_response()]),
    ).run(apply=True, stamp=_STAMP, limit=1)

    provider = _ScriptedProvider([])  # any call would raise
    report = await _migrator(store, tmp_path, provider).run(
        apply=True, stamp=_STAMP, limit=5,
    )

    assert provider.calls == 0
    assert report.remaining == 0


async def test_archived_skills_are_not_migrated(tmp_db, tmp_path):
    """An archived skill is not offered, so paying an LLM call to reformat one
    buys nothing."""
    store = SkillIndexStore(tmp_db)
    skill_id = await _seed(store, tmp_path, "dead_skill")
    await store.set_lifecycle_state(skill_id, "archived", 0.0)

    provider = _ScriptedProvider([])
    report = await _migrator(store, tmp_path, provider).run(apply=True, stamp=_STAMP)

    assert provider.calls == 0
    assert report.remaining == 0


async def test_builtins_are_not_rewritten_by_an_llm(tmp_db, tmp_path):
    """Built-ins are shipped files under version control. Rewriting one here
    would put generated content into the repository."""
    store = SkillIndexStore(tmp_db)
    d = tmp_path / "builtin" / "shipped"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: shipped\n---\n\nx\n", encoding="utf-8")
    await store.upsert(LoadedSkill(
        manifest=SkillManifest(
            name="shipped", description=_LONG_DESC, when_to_use="w", source="builtin",
        ),
        path=d, body="x", tools_registered=0, owls_registered=0,
    ))

    provider = _ScriptedProvider([])
    report = await _migrator(store, tmp_path, provider).run(apply=True, stamp=_STAMP)

    assert provider.calls == 0
    assert report.remaining == 0


async def test_the_most_used_skills_are_migrated_first(tmp_db, tmp_path):
    """If a bounded run is cut short, the skills that actually get retrieved
    should be the ones that got fixed."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "never_used")
    await _seed(store, tmp_path, "workhorse", execs=40)

    report = await _migrator(
        store, tmp_path, _ScriptedProvider([_good_response()]),
    ).run(apply=True, stamp=_STAMP, limit=1)

    assert report.outcomes[0].name == "workhorse"


# --------------------------------------------------------------------------- #
# One corrective retry. Added after a live rewrite missed by ONE character.
# --------------------------------------------------------------------------- #


async def test_a_rejected_rewrite_gets_one_retry_with_the_reason(tmp_db, tmp_path):
    """Seen live: "61 characters exceeds the 60-character limit". Without a
    retry that costs a whole fresh LLM call on the next run to re-ask the
    identical prompt and hope for a better sample."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")
    provider = _ScriptedProvider([
        _good_response(description="x" * (standard.MAX_DESCRIPTION_CHARS + 1)),
        _good_response(description="Fetch a page."),
    ])

    report = await _migrator(store, tmp_path, provider).run(
        apply=True, stamp=_STAMP, limit=1,
    )

    assert provider.calls == 2
    assert report.migrated == 1, report.outcomes[0].reason
    sk = await store.get("learned", "old_skill")
    assert sk is not None and sk.description == "Fetch a page."


async def test_the_retry_is_told_what_was_wrong(tmp_db, tmp_path):
    """A bare re-ask is just a second sample from the same distribution."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")
    seen: list[str] = []

    class _Capturing(_ScriptedProvider):
        async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
            seen.append(messages[-1].content)
            return await super().complete(messages, **kw)

    provider = _Capturing([
        _good_response(description="x" * 90),
        _good_response(description="Fetch a page."),
    ])
    await _migrator(store, tmp_path, provider).run(apply=True, stamp=_STAMP, limit=1)

    assert "REJECTED" in seen[1]
    assert "90 characters" in seen[1]
    assert "REJECTED" not in seen[0], "the first attempt is not a retry"


async def test_only_one_retry_is_taken(tmp_db, tmp_path):
    """Bounded. A model that cannot conform in two attempts will not conform in
    ten, and the skill is retried on the next run anyway."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, tmp_path, "old_skill")
    provider = _ScriptedProvider([
        _good_response(description="x" * 90),
        _good_response(description="y" * 90),
    ])

    report = await _migrator(store, tmp_path, provider).run(
        apply=True, stamp=_STAMP, limit=1,
    )

    assert provider.calls == 2
    assert report.migrated == 0
    sk = await store.get("learned", "old_skill")
    assert sk is not None and sk.standard_version == 0
