"""Two durable stores have a DECOY at the obvious sibling path, pointing opposite ways.

MEASURED on the operator's box 2026-09-08:

    ~/.stackowl/stackowl.db                0 bytes, 2026-07-25     <- decoy
    ~/.stackowl/workspace/stackowl.db      359 MB                  <- live
    ~/.stackowl/kuzu                       41 MB, 2 files          <- live
    ~/.stackowl/workspace/kuzu             empty, 0 files          <- decoy

The database's decoy is at the HOME root and the graph's decoy is at the
WORKSPACE root, so there is no rule of thumb that saves you: whichever one you
guess, you are wrong half the time. Open `~/.stackowl/stackowl.db` — the obvious
name at the obvious place — and SQLite hands back an empty database rather than
an error, so the honest conclusion from that file is "the platform has lost
everything".

THIS HAS ALREADY COST THIS PROGRAMME ONCE, and `paths.py` says so in
`kuzu_dir`'s own docstring: `kuzu_dir()` used to return `workspace()/kuzu` while
`MemoryAssembly.build` opened `home()/kuzu` directly, "so there were two graph
locations and only one of them had ever been written". It then names the cure and
why the defect survived: *"Nothing had pinned this location — no test asserted it
and the only consumer passed its own path — which is exactly why the split
survived unnoticed."*

THAT TEST WAS NEVER WRITTEN. The closest existing assertion,
`test_ensure_exists_creates_directories`, checks `(home / "kuzu").exists()` —
that the live directory is CREATED, which is adjacent to but not the same as
pinning what the accessor RETURNS, and it says nothing about the sibling. This
file is the missing pin.

AND THE DEFINING FILE CONTRADICTS ITSELF ABOUT ONE OF THEM. `paths.py:64`
returns `home()/kuzu`; `paths.py:224`, in `downloads_dir`'s docstring 160 lines
below, describes "the persistent stores (stackowl.db / kuzu / knowledge) that
live at the workspace ROOT". Two claims about one location in one file — the
two-copies-of-one-rule shape, and the stale copy is precisely the belief that
created the split. Corrected in the same change as this test.

No guard is shipped for the DOCSTRING half, and the reason is recorded: matching
prose is how four guards in this session broke or were satisfied by a comment.
The accessors are pinned instead, because a wrong docstring beside a pinned
accessor is a documentation bug, while a wrong accessor is a data-loss bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh home with no STACKOWL_DATA_DIR override."""
    root = tmp_path / "home"
    monkeypatch.setenv("STACKOWL_HOME", str(root))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    return root


def test_the_graph_is_at_the_home_root_not_the_workspace(home: Path) -> None:
    """41 MB lives at one of these and an empty directory at the other."""
    assert StackowlHome.kuzu_dir() == home / "kuzu"
    assert StackowlHome.kuzu_dir() != StackowlHome.workspace() / "kuzu"


def test_the_database_is_at_the_workspace_root_not_the_home(home: Path) -> None:
    """The MIRROR of the graph, which is what makes a rule of thumb useless."""
    assert StackowlHome.db_path() == home / "workspace" / "stackowl.db"
    assert StackowlHome.db_path() != home / "stackowl.db"


def test_setting_up_a_fresh_home_creates_neither_decoy(home: Path) -> None:
    """THE DURABLE HALF.

    Pinning the accessors stops the code from moving; this stops the DISK from
    growing a second candidate. A future `ensure_exists` that helpfully creates
    both spellings would hand the next reader the same coin-flip, and every
    assertion above would still pass while it did.
    """
    StackowlHome.ensure_exists()

    assert not (home / "stackowl.db").exists(), (
        "a database was created at the HOME root; the live one is under workspace/, "
        "and an empty file at the obvious name reads as total data loss"
    )
    assert not (home / "workspace" / "kuzu").exists(), (
        "a graph directory was created under workspace/; the live one is at the "
        "home root, and this exact empty sibling already cost one investigation"
    )


def test_the_live_locations_are_the_ones_that_get_created(home: Path) -> None:
    """The other side of the same coin — a pin that forbids both spellings and
    creates neither would pass vacuously."""
    StackowlHome.ensure_exists()

    assert StackowlHome.kuzu_dir().exists()
    assert StackowlHome.workspace().exists()
    assert StackowlHome.db_path().parent.exists()


def test_an_explicit_data_dir_moves_the_database_and_not_the_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STACKOWL_DATA_DIR relocates the WORKSPACE. The graph is not under it, so
    the override must not silently take the graph with it — that would be the
    split again, created by configuration rather than by code."""
    root = tmp_path / "home"
    data = tmp_path / "elsewhere"
    monkeypatch.setenv("STACKOWL_HOME", str(root))
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(data))

    assert StackowlHome.db_path() == data / "stackowl.db"
    assert StackowlHome.kuzu_dir() == root / "kuzu"
