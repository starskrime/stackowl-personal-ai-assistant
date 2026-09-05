"""D18.4 — `~` in a config file was a directory named `~`, silently.

A config file is written by a person and a person writes `~/photos`. Python does
not expand that: `Path("~/photos")` is a RELATIVE directory literally named `~`,
created beside the working directory, with no error.

HOW THIS WAS FOUND IS THE POINT, and it is why both halves are asserted here.
`docs/stackowl.yaml.example` emits `screenshots_dir: ~/.stackowl/screenshots` on
purpose — so the published artifact carries no machine-specific absolute path and
no operator's home directory name (D18.2). Loading it back returned an ABSOLUTE
path, so the tilde looked harmless and the hazard was dismissed.

It looked harmless because a SECOND defect was hiding it. `settings_customise_sources`
never returned `init_settings`, so the YAML value was discarded entirely and the
absolute DEFAULT came back. Fixing that unmasked this one:
`PosixPath('~/.stackowl/screenshots')`, relative. **One defect was the other's
alibi**, and the measurement that "proved" the tilde was safe was really measuring
the bug that was swallowing it.

So the guard has to assert the EFFECT — that a `~` path resolves under the real
home — and not merely that a validator is attached. And it has to assert the rule
reaches EVERY path field, because "same rule, one case short" is how this domain
keeps failing: the accessor rule held everywhere in `src/` and broke in bash
(D18.3), and the containment rule held for 25 accessors and broke for one frozen
constant (D18.3 again).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from stackowl.config.settings import Settings


def _path_fields() -> list[tuple[str, object]]:
    """(dotted name, "expands"|"bare") for every Path-typed field in the tree.

    THE EVIDENCE IS IN `field.metadata`, NOT `field.annotation`. Pydantic strips
    the `Annotated` wrapper: a `ConfigPath` field reports `annotation=Path` with
    the `BeforeValidator` moved into `metadata`. A first version of this walker
    read `annotation` and reported all four CONVERTED fields as bare — the guard
    measuring the wrong attribute and confidently accusing the fix.

    For a `list[ConfigPath]` the wrapper survives INSIDE the annotation instead,
    so both places have to be looked at.
    """
    seen: set[type] = set()
    found: list[tuple[str, object]] = []

    def walk(model: type[BaseModel], prefix: str = "") -> None:
        if model in seen:
            return
        seen.add(model)
        for name, field in model.model_fields.items():
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                walk(annotation, f"{prefix}{name}.")
            elif annotation is Path or "Path" in str(annotation):
                expands = any(
                    type(m).__name__ == "BeforeValidator" for m in (field.metadata or ())
                ) or "BeforeValidator" in str(annotation)
                found.append((f"{prefix}{name}", "expands" if expands else "bare"))

    walk(Settings)
    return found


@pytest.mark.tripwire
def test_every_path_setting_expands_the_tilde() -> None:
    """A BARE `Path` annotation is the defect. Assert none survives.

    `ConfigPath` carries a BeforeValidator that expands `~` and `$VARS`; a plain
    `Path` accepts `~` as a directory name. The two are indistinguishable at a
    glance, which is exactly why this is checked mechanically.
    """
    fields = _path_fields()
    assert fields, "no Path-typed settings fields found — the walker is broken"

    bare = [name for name, kind in fields if kind == "bare"]
    assert not bare, (
        f"path setting(s) using a bare Path: {bare}\n"
        "D18.4: use `ConfigPath` from stackowl.config.config_path. A config file is "
        "written by a human, and `Path('~/x')` is a RELATIVE directory named `~` — "
        "created silently beside the working directory."
    )


@pytest.mark.tripwire
def test_the_tilde_actually_resolves_under_the_home() -> None:
    """The EFFECT, not the annotation.

    A validator can be attached and still not fire — and in this case a second
    defect once made a broken path LOOK correct. Measure what comes out.
    """
    settings = Settings(browser={"screenshots_dir": "~/shots"})
    resolved = settings.browser.screenshots_dir

    assert resolved.is_absolute(), (
        f"a `~` config path stayed relative: {resolved!r}. It would be created as a "
        "directory literally named `~` beside the working directory."
    )
    assert str(resolved).startswith(os.path.expanduser("~")), (
        f"`~/shots` resolved to {resolved}, which is not under the user's home"
    )


def test_the_shipped_example_round_trips_to_absolute_paths() -> None:
    """The artifact that exposed this must stay loadable AND correct.

    D18.2 renders home-derived paths as `~/.stackowl/...` so the published file
    carries no machine-specific path and no operator username. That is only safe
    while loading one gives back a real absolute path.
    """
    import yaml

    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "docs" / "stackowl.yaml.example").read_text())
    settings = Settings(**data)

    for name in ("screenshots_dir", "profiles_dir", "downloads_dir", "browser_cache_dir"):
        value = getattr(settings.browser, name)
        assert value.is_absolute(), f"example config yields a relative {name}: {value!r}"
        assert "~" not in str(value), f"unexpanded tilde survived in {name}: {value!r}"


def test_a_real_path_is_not_second_guessed() -> None:
    """Expansion must not touch a value that is already a proper path."""
    settings = Settings(browser={"screenshots_dir": "/srv/shots"})
    assert settings.browser.screenshots_dir == Path("/srv/shots")
