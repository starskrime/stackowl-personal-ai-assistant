"""Two terminal surfaces share one channel name, and only one of them says so.

WHY THIS EXISTS — D13.1 asks "which surfaces matter?", and the platform cannot answer
it about its own.

MEASURED 2026-09-06. Three surfaces are built (CLI, Textual TUI, messaging gateway) plus
four messaging adapters, and of everything only two have ever carried a conversational
turn: telegram 65 and `cli` 8, against rca 112 autonomous. discord, slack and whatsapp are
at zero.

**But `cli` is TWO surfaces.** `CLIAdapter` (the Textual TUI) and `HeadlessCliAdapter` (no
terminal at all) both return ``channel_name == "cli"``, and they must — that string is the
routing identity: consent provenance reads it through `_gateway_channels()`, the clarify
gateway registers the adapter under it, proactive delivery addresses it, and
`browser/sessions.py` branches on it. Renaming would be a security-relevant change to
answer a reporting question.

So the surface has to be recorded ALONGSIDE the routing identity, never instead of it.

AND THE DISTINCTION IS ALREADY COMPUTED. `CLIAdapter.__init__` sets ``self._mode`` to
"fullzone" or "raw" and logs it at **DEBUG**, where production cannot see it. The boot line
that does run at INFO — `[startup] gateway: starting CLI adapter`, 815 occurrences — names
no surface at all. The only way to tell today is SUBTRACTION: 815 CLI-adapter starts minus
195 "stdin is not a TTY" lines implies 620 TUI boots. A fact you can only reach by knowing
which two numbers to subtract is not a fact the record carries.

THE SAME SHAPE AS DEBT-126, one item ago: the line that should answer the question logs
everything except the field the question needs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from stackowl.channels.cli_adapter import CLIAdapter, HeadlessCliAdapter

_ROOT = Path(__file__).resolve().parents[2]


class TestTheRoutingIdentityIsUNCHANGED:
    """The safety half. `cli` is load-bearing in consent, clarify and delivery; a
    reporting improvement may not move it."""

    def test_both_adapters_still_answer_cli(self) -> None:
        assert CLIAdapter().channel_name == "cli"
        assert HeadlessCliAdapter().channel_name == "cli"


class TestTheSurfaceIsNameable:
    def test_a_headless_process_says_headless(self) -> None:
        """No terminal at all — `start.sh` launches with stdin at /dev/null."""
        assert HeadlessCliAdapter().surface == "headless"

    def test_the_legacy_fallback_says_raw(self) -> None:
        """`CLIAdapter` with no tui_components is the tests/dry-run path. Calling it
        "tui" would be the more flattering answer and the wrong one — the whole point
        is that the record distinguishes what actually ran."""
        assert CLIAdapter().surface == "raw"

    def test_the_real_tui_says_tui(self) -> None:
        """The 4-zone Textual mode, which is what an operator means by "the TUI"."""

        class _Bus:
            def subscribe(self, *_a: object, **_k: object) -> None: ...

        class _Components:
            app = object()

        adapter = CLIAdapter(tui_components=_Components(), event_bus=_Bus())  # type: ignore[arg-type]

        assert adapter.surface == "tui"

    def test_surface_is_derived_from_the_mode_the_adapter_ALREADY_chose(self) -> None:
        """VACUITY CONTROL. A `surface` that is a second, independently-set string
        would be free to disagree with the behaviour it describes — the two-copies-of-
        one-rule shape. It must read the same `_mode` the constructor decided on."""
        adapter = CLIAdapter()

        assert adapter.surface == "raw"
        adapter._mode = "fullzone"  # noqa: SLF001 — asserting the derivation, not the API
        assert adapter.surface == "tui", (
            "surface does not follow _mode, so it is a second source of truth for "
            "which surface is running"
        )


class TestTheBootLineCarriesIt:
    @pytest.mark.tripwire
    def test_the_startup_line_names_the_surface(self) -> None:
        """Without this the answer is only reachable by subtracting two counts, and
        the operator has to know which two. `[startup] gateway: starting CLI adapter`
        fires on every boot and is the natural place to say which surface booted."""
        src = (_ROOT / "src" / "stackowl" / "startup" / "orchestrator.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant):
                continue
            if first.value != "[startup] gateway: starting CLI adapter":
                continue
            for kw in node.keywords:
                if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                    for k, v in zip(kw.value.keys, kw.value.values, strict=False):
                        if isinstance(k, ast.Constant) and k.value == "_fields":
                            assert isinstance(v, ast.Dict)
                            fields = {
                                c.value for c in v.keys
                                if isinstance(c, ast.Constant)
                            }
                            assert "surface" in fields, (
                                "the boot line does not name the surface; which of the "
                                "two cli surfaces booted stays a subtraction"
                            )
                            return
            raise AssertionError("the startup line carries no _fields at all")
        raise AssertionError("the 'starting CLI adapter' log call was not found")


class TestEveryAdapterHasAnHonestAnswer:
    def test_the_default_is_the_channel_name_not_a_placeholder(self) -> None:
        """A default of "unknown" would make an adapter that FORGOT to answer look
        identical to one that genuinely has nothing to add — the silent-fallback shape.
        For almost every adapter the surface IS the channel, so that is the default."""
        from stackowl.channels.base import ChannelAdapter

        class _Telegramish(ChannelAdapter):
            @property
            def channel_name(self) -> str:
                return "telegram"

            async def receive(self) -> object: ...  # type: ignore[override]
            async def send(self, chunks: object) -> None: ...  # type: ignore[override]
            async def send_text(self, text: str) -> object | None: ...  # type: ignore[override]

        assert _Telegramish().surface == "telegram"


class TestTheSplitProcessCoreSaysSo:
    """THE THIRD BRANCH, and the live log is what found it. `orchestrator.py:2110`
    binds `SocketChannelAdapter(core_conn, channel_name="cli")` in the split-process
    CORE role — the branch most boots on this deployment actually take. With only the
    base default it reported a bare "cli", which reads as "a cli surface with nothing
    to add" when the truth is that no terminal is attached to that process at all."""

    def test_a_socket_proxy_is_not_mistaken_for_a_terminal(self) -> None:
        from stackowl.channels.socket_adapter import SocketChannelAdapter

        adapter = SocketChannelAdapter(object(), channel_name="cli")  # type: ignore[arg-type]

        assert adapter.channel_name == "cli", "the routing identity must not move"
        assert adapter.surface == "socket:cli"

    def test_it_keeps_the_channel_it_proxies(self) -> None:
        """It proxies every channel, not just cli, so the answer has to carry which."""
        from stackowl.channels.socket_adapter import SocketChannelAdapter

        assert SocketChannelAdapter(object(), channel_name="telegram").surface == (  # type: ignore[arg-type]
            "socket:telegram"
        )
