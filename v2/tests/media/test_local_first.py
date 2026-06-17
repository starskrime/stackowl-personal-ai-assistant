"""CFG-4 (F019) — the shared local-first selector control flow.

One tested implementation of the local-first-then-cloud policy that image and
tts selectors both delegate to. The policy lives HERE so a change is edited
once; per-modality settings/messages remain the caller's concern.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from stackowl.media.local_first import select_local_first


@dataclass
class _Avail:
    available: bool
    reason: str | None = None


async def _yes() -> _Avail:
    return _Avail(True)


async def _no(reason: str = "down"):  # type: ignore[no-untyped-def]
    async def _probe() -> _Avail:
        return _Avail(False, reason)

    return _probe


@pytest.mark.asyncio
async def test_local_preferred_when_available() -> None:
    sel = await select_local_first(
        engine="auto",
        local_probe=_yes,
        cloud_probe=_yes,
        local_factory=lambda: "LOCAL",
        cloud_factory=lambda: "CLOUD",
        unavailable=lambda lr, cr: f"none: {lr} / {cr}",
    )
    assert sel.backend == "LOCAL"
    assert sel.is_local is True
    assert sel.available


@pytest.mark.asyncio
async def test_cloud_fallback_when_local_down_and_auto() -> None:
    async def local_down() -> _Avail:
        return _Avail(False, "no gpu")

    sel = await select_local_first(
        engine="auto",
        local_probe=local_down,
        cloud_probe=_yes,
        local_factory=lambda: "LOCAL",
        cloud_factory=lambda: "CLOUD",
        unavailable=lambda lr, cr: f"none: {lr} / {cr}",
    )
    assert sel.backend == "CLOUD"
    assert sel.is_local is False


@pytest.mark.asyncio
async def test_local_only_engine_skips_cloud() -> None:
    async def local_down() -> _Avail:
        return _Avail(False, "no gpu")

    sel = await select_local_first(
        engine="local",  # any non-auto, non-cloud value = local-only
        local_probe=local_down,
        cloud_probe=_yes,
        local_factory=lambda: "LOCAL",
        cloud_factory=lambda: "CLOUD",
        unavailable=lambda lr, cr: f"none: {lr} / {cr}",
    )
    assert sel.backend is None
    assert not sel.available
    assert "no gpu" in (sel.reason or "")
    # cloud was NOT consulted (engine is local-only) → reason reflects disabled.


@pytest.mark.asyncio
async def test_cloud_engine_skips_local_probe() -> None:
    probed = {"local": False}

    async def local_probe() -> _Avail:
        probed["local"] = True
        return _Avail(True)

    sel = await select_local_first(
        engine="cloud",
        local_probe=local_probe,
        cloud_probe=_yes,
        local_factory=lambda: "LOCAL",
        cloud_factory=lambda: "CLOUD",
        unavailable=lambda lr, cr: f"none: {lr} / {cr}",
    )
    assert sel.backend == "CLOUD"
    assert sel.is_local is False
    assert probed["local"] is False, "cloud engine must not probe local"


@pytest.mark.asyncio
async def test_all_down_returns_structured_unavailable() -> None:
    async def down(reason: str):  # type: ignore[no-untyped-def]
        return _Avail(False, reason)

    sel = await select_local_first(
        engine="auto",
        local_probe=lambda: down("local-gone"),
        cloud_probe=lambda: down("cloud-gone"),
        local_factory=lambda: "LOCAL",
        cloud_factory=lambda: "CLOUD",
        unavailable=lambda lr, cr: f"none: {lr} / {cr}",
    )
    assert sel.backend is None
    assert sel.reason == "none: local-gone / cloud-gone"
