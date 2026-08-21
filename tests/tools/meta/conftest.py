"""Fixtures for the meta-tool (tool_build) tests: tmp home + live-IO guard."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard


@pytest.fixture()
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point StackowlHome at an isolated tmp root for the duration of a test.

    Clears the per-path legacy overrides so every sub-path (including
    workspace()/learned_tools_dir()) derives from this fresh root.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_CONFIG_FILE", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture()
def _live_io() -> Generator[None]:
    """Disable the TestModeGuard so tools may spawn real subprocesses."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    try:
        yield
    finally:
        TestModeGuard._active = prev  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _official_origin(request: pytest.FixtureRequest) -> Generator[None]:
    """Register a gateway channel named ``cli``, because production has one.

    WHY THIS EXISTS, and it is a fix to a regression I shipped. Commit `0f1431e9`
    ("authority follows the ORIGIN of the request") added ``owl_build`` to
    ``_DEFAULT_ALWAYS_ASK_CATEGORIES``. That makes ``_is_always_ask`` true, which skips
    the ``tier is TrustTier.AUTO`` branch in ``ConsentPolicy.request`` — so the
    ``ConsentPolicy(tiers={"owl_build": TrustTier.AUTO})`` every owl_build test
    constructs stopped taking effect. With no channel UX in a harness, the
    FailClosedPrompter then denies, and 15 tests across 8 files went red.

    I did not notice, and the commit message claimed "205 meta" green. It was not:
    190 passed and 15 failed. That claim is corrected in `progress.yml`.

    THE GATE IS NOT WEAKENED HERE. Refusing an authority request that has no official
    origin is the intended behaviour and it stays. What was wrong is that the harness
    did not resemble production: live, `ChannelRegistry` holds a real adapter (measured:
    `telegram`, 585 registrations), so a real turn from the operator's own ingress IS
    official and is granted. These tests set ``channel="cli"`` and registered nothing,
    so they were asserting against a world that does not exist.

    Autouse, because the alternative is 15 near-identical opt-ins and the next
    owl_build test would silently be written without one.

    OPT OUT with ``@pytest.mark.no_official_origin`` when the test is ABOUT consent
    rather than about owl_build. Adding this fixture without the opt-out broke a test
    that had been passing — `test_always_ask_owl_build_still_prompts` asserts the
    prompter IS called, and an official origin short-circuits before any prompter
    runs. A fixture that silently changes what a test means is worse than the failure
    it was fixing.
    """
    if request.node.get_closest_marker("no_official_origin") is not None:
        yield
        return
    from stackowl.channels.registry import ChannelRegistry

    class _CliAdapter:
        channel_name = "cli"

        async def send(self, *_a: object, **_k: object) -> None:  # pragma: no cover
            return None

    registry = ChannelRegistry.instance()
    # ASK, rather than register-and-swallow. A try/except/pass here would also have
    # hidden a real registration failure, which would leave every test in this
    # directory silently unofficial again — the same silence this fixture exists to
    # end, and a violation of the standing "every except logs" rule.
    already = any(getattr(a, "channel_name", None) == "cli" for a in registry.all())
    if not already:
        registry.register(_CliAdapter(), source_name="test_official_origin")  # type: ignore[arg-type]
    try:
        yield
    finally:
        if not already:
            registry.unregister_by_source("test_official_origin")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "no_official_origin: this test is ABOUT consent — do not register a gateway "
        "channel for it, so the provenance path is exercised rather than bypassed",
    )
