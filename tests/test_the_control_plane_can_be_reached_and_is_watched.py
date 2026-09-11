"""A surface nobody can log into, that nothing notices going dark.

MEASURED 2026-09-11, the morning after the control plane shipped ON and bound its
port — every one of these was ZERO:

  * CLI commands mentioning it                      0
  * occurrences of `control_plane` under `cli/`     0
  * documents saying where the token lives          0
  * occurrences of `control_plane` under `health/`  0

So the operator could not reach what shipped without reading source and guessing a
path, and nothing anywhere would have said if the surface stopped serving. Both
gaps were NAMED in A05.1's own design document as deliberately-not-built, which is
how they survived the loop that created them: naming a gap is not closing it.

`health/reachability/census.py`'s docstring had already written the rule this
breaks — *"it cannot, by itself, discover a brand-new subsystem that never
registers ... keep REQUIRED_PROBES updated when a consequential default-path
subsystem is added."* One was added, default ON, and the set was not updated.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestTheOperatorCanReachIt:
    @pytest.mark.tripwire
    def test_the_command_exists_and_is_named_for_the_thing(self) -> None:
        from stackowl.cli.app import app

        names = {
            getattr(c, "name", None) or getattr(c, "callback", None).__name__
            for c in app.registered_commands
        }
        assert "control-plane" in names, (
            f"no `stackowl control-plane` command. Registered: {sorted(n for n in names if n)}"
        )

    @pytest.mark.tripwire
    def test_it_RESOLVES_a_credential_and_never_MINTS_one(self) -> None:
        """THE ONE THING THIS COMMAND MUST NOT DO.

        Minting here hands the operator a token the RUNNING SERVER does not hold.
        Worse on this host, where the keyring is genuinely unusable ("Failed to
        unlock the collection!") — a minting reader would forge a new credential
        every time it was run and each would be wrong.

        THE FIRST VERSION SUBSTRING-MATCHED THE SOURCE and failed on the command's
        own COMMENT, which explains why both backends are consulted and therefore
        names `store_secret`. That is the identical conflation caught the same day
        in the control plane's leak guard: a pattern matching something other than
        the thing. It walks CALLS now, so prose cannot reach it.
        """
        from stackowl.cli import app as mod

        tree = ast.parse(inspect.getsource(mod.control_plane))
        called = {
            n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
        }
        assert "ensure_credential" not in called, (
            "the command CALLS ensure_credential(), which MINTS. It must only read."
        )
        assert "store_secret" not in called, (
            "the command CALLS store_secret(). It is a reader."
        )
        assert "resolve" in called, "it no longer resolves an existing credential"

    @pytest.mark.tripwire
    def test_it_asks_BOTH_backends_in_the_servers_own_order(self) -> None:
        """Pins the RULE, not today's data. `store_secret` writes to the keyring
        when it can and a 0600 file when it cannot, and does not say which — so a
        reader asking one backend prints "no token" while the server is happily
        using the other. On a host WITH a keyring a file-only reader passes any
        behavioural test, and vice versa; only the source settles it."""
        from stackowl.cli import app as mod

        src = inspect.getsource(mod.control_plane)
        assert "keychain:" in src and "file:" in src, (
            "the command consults only one secret backend"
        )
        assert src.index("keychain:") < src.index("file:"), (
            "backends are consulted in a different order from the server's, so the "
            "two can disagree about which credential is live"
        )

    @pytest.mark.tripwire
    def test_no_token_is_a_failure_not_a_cheerful_nothing(self) -> None:
        """Exit code, because a script that pipes this needs to know."""
        from stackowl.cli import app as mod

        tree = ast.parse(inspect.getsource(mod.control_plane))
        exits = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "exit"
        ]
        assert exits, "the command never exits non-zero, so 'no token yet' looks like success"


class TestSomethingNoticesItGoingDark:
    @pytest.mark.tripwire
    def test_the_name_is_required_and_has_a_probe(self) -> None:
        import stackowl.health.reachability.probes  # noqa: F401 — registers them
        from stackowl.health.reachability.census import REQUIRED_PROBES, _PROBES

        name = "control_plane.serves_by_default"
        assert name in REQUIRED_PROBES, (
            "the control plane is a consequential default-path subsystem with no "
            "entry in REQUIRED_PROBES — the exact residual gap census.py's own "
            "docstring warns about"
        )
        assert name in _PROBES, f"{name} is required but no probe registers it"

    @pytest.mark.asyncio
    @pytest.mark.tripwire
    async def test_it_is_reachable_on_the_default_path_today(self) -> None:
        import stackowl.health.reachability.probes  # noqa: F401 — registers them
        from stackowl.health.reachability.census import _PROBES

        got = await _PROBES["control_plane.serves_by_default"]()
        assert got.reachable, f"the control plane is NOT reachable by default: {got.detail}"

    @pytest.mark.asyncio
    @pytest.mark.tripwire
    async def test_it_fails_when_the_surface_ships_OFF(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONSTRUCTED, because today both halves are true and an assertion over a
        satisfied population pins nothing. `webhook.enabled` defaults False and
        that receiver served zero requests across 873 boots — this is that state,
        built on purpose so the probe is proven to detect it."""
        from stackowl.config import settings as settings_mod
        import stackowl.health.reachability.probes  # noqa: F401 — registers them
        from stackowl.health.reachability.census import _PROBES

        real = settings_mod.Settings

        class _Off(real):  # type: ignore[misc, valid-type]
            pass

        def _settings_off(*a: object, **k: object) -> object:
            s = real(*a, **k)  # type: ignore[arg-type]
            object.__setattr__(
                s, "control_plane", s.control_plane.model_copy(update={"enabled": False})
            )
            return s

        monkeypatch.setattr(settings_mod, "Settings", _settings_off)
        got = await _PROBES["control_plane.serves_by_default"]()
        assert not got.reachable, (
            "the probe reports the control plane reachable while it is disabled by "
            "default — it would have passed over the exact shape that made the "
            "webhook receiver serve nobody for 873 boots"
        )
        assert "enabled=False" in got.detail
