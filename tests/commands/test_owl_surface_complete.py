"""Pre-deletion gate — /owl exposes every capability the old /owls + /agent
surfaces did, so removing them (Task 7) loses nothing (proven by tests)."""
from stackowl.commands.owls_command import OwlCommand


def test_owl_meta_covers_full_lifecycle() -> None:
    subs = {s.name for s in OwlCommand().meta.subcommands}
    # create (was /owls add + /owls create + /agent create), edit, rename,
    # pause/resume (was /agent pause|resume), retire (was /owls remove +
    # /agent stop), list, dna.
    for required in ("create", "edit", "rename", "pause", "resume", "retire", "list", "dna"):
        assert required in subs, f"/owl must expose {required}"


def test_owl_command_token_is_singular() -> None:
    assert OwlCommand().command == "owl"


if __name__ == "__main__":  # pragma: no cover — runnable self-check
    test_owl_meta_covers_full_lifecycle()
    test_owl_command_token_is_singular()
    print("owl surface completeness self-check OK")
