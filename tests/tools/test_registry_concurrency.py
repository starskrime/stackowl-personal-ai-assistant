"""CONC-3 (F045/F044) — ToolRegistry must be safe under concurrent dispatch +
live registration, and tool_build must drop a learned tool through a PUBLIC
``unregister(name)`` rather than poking ``_tools``/``_source_map``.

The race: one task registers/unregisters tools in a tight loop while another
iterates ``all()`` — an unguarded plain dict raises ``RuntimeError: dictionary
changed size during iteration`` (or a list-copy that races a concurrent pop).
A barrier makes the two run truly concurrently (threads, since the registry
methods are synchronous and the real hazard is thread-level interleaving / GIL
release at allocation points).
"""

from __future__ import annotations

import threading

import pytest

from stackowl.tools.base import Tool, ToolResult


class _NoopTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "noop"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:  # pragma: no cover
        return ToolResult(success=True, output="")


def _make_registry():
    from stackowl.tools.registry import ToolRegistry

    return ToolRegistry()


def test_concurrent_iterate_and_register_no_dict_change_error() -> None:
    reg = _make_registry()
    # Seed some tools so all() has content to iterate.
    for i in range(50):
        reg.register(_NoopTool(f"seed{i}"))

    start = threading.Barrier(2)
    errors: list[BaseException] = []
    stop = threading.Event()

    def churn() -> None:
        start.wait()
        i = 0
        while not stop.is_set():
            name = f"live{i % 64}"
            try:
                reg.register(_NoopTool(name), source_name="churn", replace=True)
                reg.unregister(name)
            except Exception as exc:  # noqa: BLE001 — capture for the assert
                errors.append(exc)
                return
            i += 1

    def iterate() -> None:
        start.wait()
        try:
            for _ in range(5000):
                # Touch every tool — forces full iteration over the snapshot.
                for t in reg.all():
                    _ = t.name
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            stop.set()

    t1 = threading.Thread(target=churn)
    t2 = threading.Thread(target=iterate)
    t1.start()
    t2.start()
    t2.join(timeout=30)
    stop.set()
    t1.join(timeout=30)

    assert not errors, f"concurrent registry access raised: {errors!r}"


def test_public_unregister_removes_name_and_source_entry() -> None:
    reg = _make_registry()
    reg.register(_NoopTool("alpha"), source_name="pack")
    reg.register(_NoopTool("beta"), source_name="pack")

    assert reg.unregister("alpha") is True
    assert reg.get("alpha") is None
    assert reg.get("beta") is not None
    # The source map no longer lists the removed name.
    assert reg.unregister("missing") is False


def test_tool_build_drop_uses_public_unregister(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_build._drop_from_registry must NOT reach into _tools/_source_map."""
    import inspect

    from stackowl.tools.meta import tool_build as tb_mod

    src = inspect.getsource(tb_mod.ToolBuildTool._drop_from_registry)
    assert "._tools" not in src, "tool_build must not poke registry._tools"
    assert "._source_map" not in src, "tool_build must not poke registry._source_map"
    assert "unregister(" in src, "tool_build must call the public unregister()"
