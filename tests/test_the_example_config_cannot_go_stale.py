"""D18.2 — the annotated example is generated, so it cannot drift from the model.

The reference platform ships a 1,616-line annotated config example, hand-written.
That is a second copy of the configuration surface, correct only until someone
forgets it — the shape `CLAUDE.md` calls two copies of one rule.

Ours is derived. `scripts/gen_config_example.py` walks `Settings` and emits every
field with its default and its own `Field(description=...)` as a comment. This
test regenerates and compares BYTES, so the checked-in artifact cannot fall
behind the model: adding a setting without regenerating fails the gate.

MEASURED 2026-09-05, which is why the artifact is worth having at all: the
surface is **210 settable fields across 33 top-level sections**, and an operator's only
alternative was reading 1,100 lines of `settings.py`. **107 of the 210 carry a
description** — the file annotates what exists and invents nothing, so the gaps
in it are the gaps in the model.

THE ROUND-TRIP IS THE PROPERTY THAT MAKES IT DOCUMENTATION RATHER THAN PROSE.
The example is not merely illustrative: it parses as YAML and `Settings(**it)`
accepts it. A "documentation" file that would not actually load is worse than
none, because the one person who tries it learns the docs are decorative.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ARTIFACT = _ROOT / "docs" / "stackowl.yaml.example"
_SCRIPT = _ROOT / "scripts" / "gen_config_example.py"


@pytest.mark.tripwire
def test_the_checked_in_example_matches_the_model() -> None:
    assert _ARTIFACT.exists(), f"{_ARTIFACT} is missing — run the generator with --write"

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        capture_output=True, text=True, cwd=_ROOT, check=False,
    )
    assert result.returncode == 0, f"generator failed: {result.stderr[-400:]}"

    assert result.stdout == _ARTIFACT.read_text(encoding="utf-8"), (
        "docs/stackowl.yaml.example is out of date with the Settings model.\n"
        "Run: uv run python scripts/gen_config_example.py --write\n"
        "It is generated precisely so it cannot become a stale second copy of "
        "the configuration surface."
    )


@pytest.mark.tripwire
def test_the_example_is_loadable_config_not_just_prose() -> None:
    """A documentation file that would not load is worse than none."""
    from stackowl.config.settings import Settings

    data = yaml.safe_load(_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and len(data) > 20, "expected the real surface"

    Settings(**data)  # raises if the example is not valid configuration

    # THIS CHECK WAS VACUOUS WHEN IT SHIPPED, and the control is why it no longer
    # is. `settings_customise_sources` never returned `init_settings`, so pydantic
    # discarded every keyword argument — `Settings(**data)` built pure defaults and
    # would have "accepted" literally anything, including a non-numeric port
    # (measured 2026-09-05, D18.4). A test that passes immediately may be vacuous;
    # this one did. Proving the call REJECTS nonsense is the only thing that makes
    # its accepting the real file mean something.
    with pytest.raises(ValidationError):
        Settings(webhook={"port": "not-a-number"})

    # And the values must actually ARRIVE, not merely be tolerated. The example
    # renders home-derived paths as `~/.stackowl/...` (no machine path, no
    # operator username), which is only safe because ConfigPath expands them.
    settings = Settings(**data)
    assert settings.browser.screenshots_dir.is_absolute(), (
        "the example's `~` path stayed relative — it would create a directory "
        "literally named `~` beside the working directory"
    )


def test_the_generator_is_deterministic() -> None:
    """Two runs must agree, or the byte comparison above would flake.

    Sets have no order, and several defaults are frozensets — they are sorted in
    the generator for exactly this reason.
    """
    runs = [
        subprocess.run(  # noqa: S603
            [sys.executable, str(_SCRIPT)],
            capture_output=True, text=True, cwd=_ROOT, check=True,
        ).stdout
        for _ in range(2)
    ]
    assert runs[0] == runs[1]


def test_no_secret_value_reaches_the_artifact() -> None:
    """Defaults are emitted verbatim, so this asserts none of them IS a secret.

    Measured when this shipped: no field whose name suggests a credential has a
    non-empty default. The example teaches a `keychain:` reference instead.
    """
    text = _ARTIFACT.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    def walk(node: object, path: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                found += walk(v, f"{path}{k}.")
        elif isinstance(node, str) and node:
            name = path.rstrip(".").rsplit(".", 1)[-1].lower()
            if any(w in name for w in ("key", "token", "secret", "password")):
                found.append(f"{path.rstrip('.')} = {node[:16]}")
        return found

    leaked = walk(data)
    assert not leaked, f"a credential-shaped default reached the example: {leaked}"
    assert "keychain:" in text, "the example must teach the reference form"
