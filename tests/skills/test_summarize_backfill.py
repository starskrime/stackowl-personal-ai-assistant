import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly, _summarize_missing
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.registry import ToolRegistry


@dataclass
class _StubProvider:
    out: str = "Do X. Then Y."
    calls: int = 0
    async def complete(self, messages, **kw):
        self.calls += 1
        class _R:
            content = self.out
        return _R()


class _StubProviderRegistry:
    def __init__(self, provider): self._p = provider
    def get_with_cascade(self, tier): return self._p


class _PerNameProviderRegistry:
    """Routes ``get_with_cascade`` calls to the next queued behavior — used to
    build a mix of failed/empty/summarized skills in one ``_summarize_missing``
    pass for the count-logging test below."""

    def __init__(self, providers_by_call):
        self._providers = list(providers_by_call)
        self._i = 0

    def get_with_cascade(self, tier):
        p = self._providers[self._i]
        self._i += 1
        return p


@dataclass
class _RaisingProvider:
    async def complete(self, messages, **kw):
        raise RuntimeError("simulated provider outage")


@dataclass
class _EmptyProvider:
    async def complete(self, messages, **kw):
        class _R:
            content = "   "
        return _R()


@dataclass
class _OkProvider:
    text: str = "Do X. Then Y."
    async def complete(self, messages, **kw):
        class _R:
            content = self.text
        return _R()


def _write(root: Path, name="alpha", body="long body to summarize", summary=None):
    d = root / "user" / name
    d.mkdir(parents=True)
    fm = f"---\nname: {name}\ndescription: d\n"
    if summary is not None:
        fm += f"summary: {summary}\n"
    fm += f"---\n{body}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")


async def _build(tmp_db, root, provider):
    return await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=root, builtin_seed_dir=root / "none",
        provider_registry=_StubProviderRegistry(provider),
    )


@pytest.mark.asyncio
async def test_generates_summary_when_missing(tmp_db, tmp_path: Path):
    _write(tmp_path)
    prov = _StubProvider()
    comp = await _build(tmp_db, tmp_path, prov)
    sk = await comp.store.get("user", "alpha")
    assert sk.summary == "Do X. Then Y."
    assert sk.summary_source == "generated"
    assert prov.calls == 1


@pytest.mark.asyncio
async def test_skips_when_summary_present_and_hash_matches(tmp_db, tmp_path: Path):
    _write(tmp_path)
    prov = _StubProvider()
    await _build(tmp_db, tmp_path, prov)      # generates (1 call)
    await _build(tmp_db, tmp_path, prov)      # reboot, unchanged body → no new call
    assert prov.calls == 1


@pytest.mark.asyncio
async def test_empty_output_leaves_summary_null(tmp_db, tmp_path: Path):
    _write(tmp_path)
    comp = await _build(tmp_db, tmp_path, _StubProvider(out="   "))
    sk = await comp.store.get("user", "alpha")
    assert sk.summary is None


@pytest.mark.asyncio
async def test_author_summary_never_overwritten_by_generation(tmp_db, tmp_path: Path):
    # an authored summary must never be regenerated/overwritten by the back-fill
    _write(tmp_path, summary="AUTHORED PLAYBOOK")
    prov = _StubProvider(out="GENERATED — should not appear")
    comp = await _build(tmp_db, tmp_path, prov)
    sk = await comp.store.get("user", "alpha")
    assert sk.summary == "AUTHORED PLAYBOOK"
    assert sk.summary_source == "author"
    assert prov.calls == 0  # provider never invoked for an authored skill


@pytest.mark.asyncio
async def test_exit_log_reports_all_four_counts(
    tmp_db, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """LAT.5 AC #3/#4 — summarized/skipped/failed/empty must all be tracked
    and logged at pass exit, with empty distinguished from failed."""
    from stackowl.skills.loader import SkillLoader
    from stackowl.skills.store import _summary_hash

    # Alphabetical load order (SkillLoader sorts dir entries): already_current,
    # broken, fresh, silent.
    _write(tmp_path, name="already_current", body="body A")
    _write(tmp_path, name="broken", body="body B")
    _write(tmp_path, name="fresh", body="body C")
    _write(tmp_path, name="silent", body="body D")

    store = SkillIndexStore(tmp_db)
    loader = SkillLoader(tool_registry=ToolRegistry(), owl_registry=OwlRegistry())
    loaded = await loader.load_all(tmp_path, store=store, builtin_seed_dir=tmp_path / "none")

    # Pre-seed "already_current" with a summary whose hash matches its current
    # body so the pass's hash-gate skips it without ever calling the provider.
    index = await store.index_by_source_name()
    already = next(ls for ls in loaded if ls.manifest.name == "already_current")
    want_hash = _summary_hash(already, None)
    await store.set_summary(
        index[("user", "already_current")].skill_id, "pre-existing summary", "generated", want_hash,
    )

    # Only "broken", "fresh", "silent" reach the provider (in that load order).
    provider_registry = _PerNameProviderRegistry(
        [_RaisingProvider(), _OkProvider(text="Do X. Then Y."), _EmptyProvider()],
    )

    with caplog.at_level(logging.INFO, logger="stackowl.skills"):
        await _summarize_missing(loaded, store, provider_registry)

    exit_records = [r for r in caplog.records if r.message == "[skills] _summarize_missing: exit"]
    assert len(exit_records) == 1
    fields = exit_records[0]._fields
    assert fields["summarized"] == 1  # "fresh"
    assert fields["skipped"] == 1  # "already_current" — hash already current
    assert fields["failed"] == 1  # "broken" — provider raised
    assert fields["empty"] == 1  # "silent" — provider returned blank text

    # "failed" and "empty" are separate counter keys (not one bucket) — the
    # per-skill checks below confirm "broken" landed in failed and "silent" in
    # empty, not merged together.
    fresh = await store.get("user", "fresh")
    silent = await store.get("user", "silent")
    broken = await store.get("user", "broken")
    assert fresh.summary == "Do X. Then Y."
    assert silent.summary is None  # empty response never writes a summary
    assert broken.summary is None  # exception never writes a summary
