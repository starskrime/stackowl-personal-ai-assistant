"""``journal.narrator`` -- AD-30's one narrator: a plain-English ``full``
sentence and a generic kind+count ``public`` rendering, with a tombstone
fallback that never lets name resolution raise out of ``narrate()``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stackowl.exceptions import JournalInvalidAttrsError
from stackowl.journal import (
    ActorKind,
    JournalEvent,
    Outcome,
    RecordKind,
    RecordRef,
    register_name_resolver,
)
from stackowl.journal.narrator import narrate
from stackowl.journal.registry import get_registry
from stackowl.journal.task_events import (
    TaskClaimedAttrs,
    TaskDeadLetteredAttrs,
    TaskEnqueuedAttrs,
    TaskFinishedAttrs,
)

pytestmark = pytest.mark.asyncio

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _event(**over: object) -> JournalEvent:
    defaults: dict[str, object] = {
        "type": "task.enqueued",
        "schema_version": 1,
        "actor_kind": ActorKind.AUTONOMOUS,
        "actor_id": "principal-default",
        "target_kind": ActorKind.OWNER,
        "target_id": "t1",
        "outcome": Outcome.PENDING,
        "record_ref": RecordRef(
            kind="sqlite", locator={"table": "tasks", "task_id": "t1"},
        ),
        "attrs": TaskEnqueuedAttrs(trigger_kind="chat", depends_on_count=0, max_attempts=30),
    }
    defaults.update(over)
    return JournalEvent(**defaults)  # type: ignore[arg-type]


async def _return(value: str | None) -> str | None:
    return value


class TestNarrateProducesAPlainSentencePerTaskEventType:
    async def test_enqueued(self) -> None:
        register_name_resolver(RecordKind.TASK, lambda _id: _return("Buy milk"))
        result = await narrate(_event(type="task.enqueued"))
        assert result.full == "Task Buy milk was queued."

    async def test_claimed(self) -> None:
        register_name_resolver(RecordKind.TASK, lambda _id: _return("Buy milk"))
        result = await narrate(_event(
            type="task.claimed",
            attrs=TaskClaimedAttrs(lease_owner="w1", lease_seconds=60),
        ))
        assert result.full == "Task Buy milk was claimed."

    async def test_finished(self) -> None:
        register_name_resolver(RecordKind.TASK, lambda _id: _return("Buy milk"))
        result = await narrate(_event(
            type="task.finished",
            attrs=TaskFinishedAttrs(completion_mode="delivered"),
        ))
        assert result.full == "Task Buy milk finished."

    async def test_dead_lettered_names_the_attempt_count(self) -> None:
        register_name_resolver(RecordKind.TASK, lambda _id: _return("Buy milk"))
        result = await narrate(_event(
            type="task.dead_lettered",
            outcome=Outcome.DEAD_LETTERED,
            attrs=TaskDeadLetteredAttrs(
                task_kind="chat", attempt_count=5, max_attempts=5,
                lease_owner="w1", failure_class="auth", permanent=True,
            ),
        ))
        assert result.full == "Task Buy milk gave up after 5 attempts."


class TestNarrateValidatesAttrsAgainstTheRegisteredModel:
    """``narrate()`` need not be reached via ``record()`` -- a ``JournalEvent``
    can be hand-built with a mismatched ``type``/``attrs`` pair, same as the
    write-path proof in ``tests/journal/test_registry_and_leak_guard.py``. It
    must fail loudly with the same typed error, not crash inside the type's
    own ``narrate`` callable with an opaque ``AttributeError``."""

    async def test_mismatched_attrs_raises_before_any_name_resolution(self) -> None:
        event = _event(
            type="task.finished",
            attrs=TaskEnqueuedAttrs(trigger_kind=None, depends_on_count=0, max_attempts=1),
        )
        with pytest.raises(JournalInvalidAttrsError):
            await narrate(event)


class TestPublicMentionsKindOnlyNeverTheName:
    async def test_public_has_no_name_in_it(self) -> None:
        register_name_resolver(RecordKind.TASK, lambda _id: _return("Secret Project Name"))
        result = await narrate(_event(target_id="t-public"))
        assert "Secret Project Name" not in result.public
        assert result.public == "1 task update"


class TestNameResolutionNeverRaises:
    async def test_no_resolver_registered_falls_back_to_tombstone(self) -> None:
        result = await narrate(_event(target_id="t-no-resolver"))
        assert result.full == "Task a retired task was queued."

    async def test_resolver_returning_none_falls_back_to_tombstone(self) -> None:
        register_name_resolver(RecordKind.TASK, lambda _id: _return(None))
        result = await narrate(_event(target_id="t-none"))
        assert result.full == "Task a retired task was queued."

    async def test_a_raising_resolver_falls_back_to_tombstone_not_an_exception(self) -> None:
        async def _boom(_task_id: str) -> str | None:
            raise RuntimeError("simulated resolver failure")

        register_name_resolver(RecordKind.TASK, _boom)
        result = await narrate(_event(target_id="t-raises"))
        assert result.full == "Task a retired task was queued."


class TestEveryDeclaredTypeHasANarration:
    @pytest.mark.tripwire
    def test_no_registered_type_has_a_none_narrate(self) -> None:
        registry = get_registry()
        for type_name in registry.all_types():
            spec = registry.get(type_name)
            assert spec.narrate is not None, (
                f"{type_name!r} is registered with no narrate= callable"
            )
            assert callable(spec.narrate)


class TestNoSurfaceStoresItsOwnSentenceForAnEvent:
    """AD-30: ``EventTypeSpec`` (and therefore ``narrate=``) is declared ONLY
    inside ``journal/`` -- a subsystem that constructs its own ``EventTypeSpec``
    would be inventing its own wording/classification outside the one registry,
    exactly what this story exists to prevent."""

    @pytest.mark.tripwire
    def test_EventTypeSpec_is_constructed_only_inside_journal(self) -> None:
        src_root = _REPO_ROOT / "src" / "stackowl"
        journal_dir = src_root / "journal"
        offenders: list[str] = []
        pattern = re.compile(r"EventTypeSpec\s*\(")
        for path in src_root.rglob("*.py"):
            if journal_dir in path.parents or path.parent == journal_dir:
                continue
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
        assert offenders == [], (
            f"EventTypeSpec constructed outside journal/: {offenders}"
        )
