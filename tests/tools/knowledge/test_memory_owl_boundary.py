"""ESC-38 — an owl may not write another owl's memory, nor the shared profile.

MEASURED 2026-08-23. `MemoryTool._target` was
``str(kwargs.get("target") or "user").strip()`` — the calling owl was never
mentioned, so `target` was unbound model text with no authority check. Any owl
could write into any other owl's notes and into USER.md, which every owl reads and
which sits in every prompt.

Path traversal was already closed: `CuratedMemory.path_for` validates the filename
SHAPE so a name with a separator cannot escape the memory directory. But shape is
not identity. `Collector` is a perfectly safe filename and entirely the wrong file.

THE ORPHANS ARE THE SAME DEFECT, which is why one change closes both. 8 of the 13
files in ~/.stackowl/memory/ are read by nothing, because the model writes the
DISPLAY name it sees in the prompt: Collector.md is archivist's, Falcon.md and
falcon.md are both scout's, and agent.md / owl.md / Brain.md are generic words that
match no owl at all.

THE DEFAULT WAS THE WORST PART. It was `user`, so every write that simply omitted
`target` landed in the single most privileged file on the platform, by omission.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from stackowl.tools.knowledge.memory import MemoryTool


@dataclass
class _Owl:
    name: str
    display_name: str | None


LIVE = [
    _Owl("archivist", "Collector"),
    _Owl("jobmarket", None),
    _Owl("scout", "Falcon"),
    _Owl("secretary", "Friday"),
]


class _Registry:
    def all(self) -> list[_Owl]:
        return list(LIVE)


@pytest.fixture
def _ctx(monkeypatch: pytest.MonkeyPatch):
    """Drive the REAL resolution path: a real trace context and a real registry."""

    def _install(caller: str | None):
        monkeypatch.setattr(
            "stackowl.tools.knowledge.memory.TraceContext",
            type("_T", (), {"get": staticmethod(lambda: {"owl_name": caller})}),
        )
        monkeypatch.setattr(
            "stackowl.tools.knowledge.memory.get_services",
            lambda: type("_S", (), {"owl_registry": _Registry()})(),
        )

    return _install


def _resolve(kwargs: dict[str, object]) -> tuple[str, str | None]:
    return MemoryTool._resolve_target(kwargs)


# ---------------------------------------------------------------------------
# Rule 1 — the default is the CALLER, not the shared profile
# ---------------------------------------------------------------------------

def test_omitting_target_writes_the_callers_own_notes(_ctx) -> None:
    _ctx("jobmarket")
    assert _resolve({}) == ("jobmarket", None)


def test_the_old_default_of_user_is_gone(_ctx) -> None:
    """The whole point: a write that omits `target` must no longer land in the
    most privileged file on the platform by accident."""
    _ctx("jobmarket")
    target, refusal = _resolve({})
    assert target != "user"
    assert refusal is None


# ---------------------------------------------------------------------------
# Rule 2 — canonicalisation, and no new orphans
# ---------------------------------------------------------------------------

def test_a_display_name_reaches_the_real_owl(_ctx) -> None:
    """Bakir's own example: `Falcon` must reach scout.md."""
    _ctx("secretary")
    assert _resolve({"target": "Falcon"}) == ("scout", None)


def test_an_unknown_target_falls_back_instead_of_minting_an_orphan(_ctx) -> None:
    """`agent`, `owl`, `Brain` are how the existing orphans were born. The write
    survives on the caller's own file, where a reader actually looks."""
    _ctx("jobmarket")
    for junk in ("agent", "owl", "Brain"):
        assert _resolve({"target": junk}) == ("jobmarket", None), junk


# ---------------------------------------------------------------------------
# Rule 3 — user and other owls need root
# ---------------------------------------------------------------------------

def test_a_non_root_owl_may_not_write_the_user_profile(_ctx) -> None:
    _ctx("jobmarket")
    target, refusal = _resolve({"target": "user"})
    assert refusal is not None
    assert "user profile" in refusal
    assert "jobmarket" in refusal, "the refusal must name the alternative it has"
    assert target == ""


def test_the_root_administrator_MAY_write_the_user_profile(_ctx) -> None:
    """The secretary is the platform's root administrator, so the ordinary
    'record what the user told me' flow is untouched — which is what makes this
    change safe to ship rather than a regression."""
    _ctx("secretary")
    assert _resolve({"target": "user"}) == ("user", None)


def test_an_owl_may_not_write_ANOTHER_owls_notes(_ctx) -> None:
    _ctx("jobmarket")
    target, refusal = _resolve({"target": "scout"})
    assert refusal is not None and target == ""
    assert "scout" in refusal


def test_the_root_administrator_may_write_another_owls_notes(_ctx) -> None:
    _ctx("secretary")
    assert _resolve({"target": "archivist"}) == ("archivist", None)


def test_an_owl_writing_its_OWN_name_explicitly_is_allowed(_ctx) -> None:
    _ctx("scout")
    assert _resolve({"target": "scout"}) == ("scout", None)


def test_an_owl_writing_its_own_DISPLAY_name_is_allowed(_ctx) -> None:
    """It resolves to the caller, so the boundary check must not fire on it."""
    _ctx("scout")
    assert _resolve({"target": "Falcon"}) == ("scout", None)


# ---------------------------------------------------------------------------
# The unattributed case — fails OPEN, but loudly
# ---------------------------------------------------------------------------

def test_no_calling_owl_preserves_todays_behaviour(_ctx) -> None:
    """A CLI or utility turn acts for the operator, not an owl. Refusing would
    break the operator's own path, so this fails open — and logs at INFO so the
    gap is measurable rather than assumed."""
    _ctx(None)
    assert _resolve({}) == ("user", None)
    assert _resolve({"target": "scout"}) == ("scout", None)


def test_a_broken_trace_context_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authority resolution must not be able to kill a memory write."""

    def _boom():
        raise RuntimeError("no context")

    monkeypatch.setattr(
        "stackowl.tools.knowledge.memory.TraceContext",
        type("_T", (), {"get": staticmethod(_boom)}),
    )
    target, refusal = _resolve({"target": "user"})
    assert refusal is None and target == "user"


# ---------------------------------------------------------------------------
# What the model is TOLD must match what happens
# ---------------------------------------------------------------------------

def test_the_schema_no_longer_advertises_a_default_of_user() -> None:
    """The ESC-34 lesson: changing behaviour without changing what names it just
    moves the defect one layer up."""
    params = MemoryTool().parameters
    target = params["properties"]["target"]  # type: ignore[index]
    assert "default" not in target, (
        "the default is now the CALLING owl, which the schema cannot name; "
        "advertising 'user' would tell the model the opposite of what happens"
    )
    assert "root administrator" in target["description"]
