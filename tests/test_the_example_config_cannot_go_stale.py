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


def _credential_keys(data: object, path: str = "") -> list[tuple[str, object]]:
    """Every credential-shaped key in the artifact, ASKING THE ONE PREDICATE.

    The version this replaces carried its own word list —
    `("key", "token", "secret", "password")` — which is a fourth copy of the
    vocabulary DEBT-309 spent an item reducing to one. `is_credential_name` is
    the predicate the log redactor asks and the one the generator now asks, so
    a field that starts being treated as a credential anywhere is treated as one
    here without a second edit.
    """
    from stackowl.infra.observability import is_credential_name

    out: list[tuple[str, object]] = []
    if isinstance(data, dict):
        for k, v in data.items():
            out += _credential_keys(v, f"{path}{k}.")
        return out
    name = path.rstrip(".").rsplit(".", 1)[-1]
    if name and is_credential_name(name):
        out.append((path.rstrip("."), data))
    return out


@pytest.mark.tripwire
def test_no_secret_value_reaches_the_artifact() -> None:
    """Every credential-shaped field shows a REFERENCE, never a value.

    THE OLD VERSION OF THIS TEST WAS A GUARD OVER AN EMPTY POPULATION. It
    asserted that no credential-shaped key held a non-empty string — true, and
    true by ACCIDENT: measured 2026-09-12, 13 of the 215 emitted fields are
    credential-named and TWELVE of them defaulted to `""`. Nothing made that so.
    The generator emitted every default verbatim while the file's own header had
    said "secrets do NOT belong in this file" since D18.2, so the rule lived in
    prose and the test passed on luck.

    DEBT-310 then added `control_plane.password = "admin"` at the operator's
    explicit request, and the thirteenth field broke it — 44 minutes into a full
    run, because this test was the one in its file with no `tripwire` marker.
    Both halves are fixed: the generator MASKS them, so the property is now an
    invariant it enforces rather than one the defaults happened to satisfy, and
    the marker is on, so the gate sees the next one in four minutes instead of
    the suite seeing it in forty-four.

    AND THE ASSERTION IS STRONGER, NOT WEAKER. "Non-empty" passed the twelve
    `bot_token: ""` lines, which taught an operator nothing about how to supply a
    credential. "Starts with a reference scheme" fails those too.
    """
    text = _ARTIFACT.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    found = _credential_keys(data)
    assert len(found) >= 13, (
        f"only {len(found)} credential-shaped keys found in the artifact — this "
        "guard has gone blind, or the walk no longer reaches them"
    )
    literal = [
        f"{k} = {str(v)[:16]}"
        for k, v in found
        if not (isinstance(v, str) and v.startswith(("keychain:", "file:")))
    ]
    assert not literal, (
        f"a credential-shaped field shows a VALUE rather than a reference: "
        f"{literal} — the generator masks these; see `secret_placeholder`"
    )
    assert "keychain:" in text, "the example must teach the reference form"


@pytest.mark.tripwire
def test_the_masking_fires_on_a_CONSTRUCTED_secret() -> None:
    """The control. Every credential default in the live model is now a
    reference, so the test above can no longer tell a working mask from a model
    that happens to hold nothing — which is exactly how its predecessor passed
    for months. The population is built here instead of counted.
    """
    sys.path.insert(0, str(_ROOT / "scripts"))
    import gen_config_example as gen  # noqa: PLC0415
    from pydantic import BaseModel, Field  # noqa: PLC0415

    class _Sub(BaseModel):
        api_key: str = Field(default="sk-live-REAL-SECRET-VALUE")
        retries: int = Field(default=3)

    out: list[str] = []
    gen._emit(_Sub, 0, out, "vendor.")  # noqa: SLF001
    body = "\n".join(out)

    assert "sk-live-REAL-SECRET-VALUE" not in body, (
        f"a literal credential default reached the output: {body}"
    )
    assert "api_key: keychain:stackowl-vendor-api-key" in body, (
        f"the placeholder is not the field's own reference form: {body}"
    )
    assert "retries: 3" in body, (
        f"a non-credential default was masked as well — the predicate is too "
        f"wide: {body}"
    )
