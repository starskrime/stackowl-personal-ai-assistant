"""The settings were visible as a set all along — to whoever was at a chat prompt.

A05.2's gap has been WRONG TWICE, both times in the same direction. It first
read "the customer cannot change a setting without editing YAML on the host";
`/config set` validates, writes and re-reads the file to confirm (F-81). Its
replacement said settings were "not seen as a SET"; `/config list` shows every
configured setting, sorted, with credentials masked. Both halves describe a
capability that EXISTS and is UNREACHABLE from anywhere but a conversation —
which is the shape DEBT-307 found in seven of nine gaps in this series.

SO THIS ROUTE IS PRESENTATION, AND IT READS THROUGH THE CHAT COMMAND'S OWN FOUR
HELPERS — `config_path`, `load_yaml`, `collect_sensitive`, `flatten`. A second
masking list is how a credential reaches an HTTP response.

AND THE ORDER MATTERED. Shipping this before DEBT-309 would have put SEVEN
credential fields onto a network surface: `providers[].api_key`,
`webhook.sources[].secret`, `mcp_server.auth_token`,
`browser.default_proxy[].password`, `governance.audit_export_key`,
`web_search.brave_api_key` and `tts`/`image.cloud_api_key` carried no
`sensitive=True` marker, and `flatten` did not descend into lists at all. The
masking was fixed first and this route second, deliberately.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.control_plane.server import ControlPlaneServer

_TOKEN = "t0ken-for-tests-only"


class _Req:
    def __init__(self, **headers: str) -> None:
        self.headers = dict(headers)


def _server() -> ControlPlaneServer:
    class _Cfg:
        bind_address = "127.0.0.1"
        port = 8787

    class _Settings:
        control_plane = _Cfg()

    srv = ControlPlaneServer(_Settings())  # type: ignore[arg-type]
    srv._token = _TOKEN  # noqa: SLF001
    return srv


def _body(res: Any) -> dict[str, Any]:
    return json.loads(res.text)


class TestItIsLockedLikeEveryOtherDataRoute:
    @pytest.mark.tripwire
    async def test_no_credential_is_refused(self) -> None:
        assert (await _server()._handle_config(_Req())).status == 401  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_foreign_origin_is_refused_even_WITH_a_valid_token(self) -> None:
        res = await _server()._handle_config(  # noqa: SLF001
            _Req(Authorization=f"Bearer {_TOKEN}", Origin="http://evil.example",
                 Host="127.0.0.1:8787")
        )
        assert res.status == 401


class TestTheResponseCarriesNoCredential:
    """The property that made DEBT-309 a prerequisite rather than a nicety."""

    @pytest.mark.tripwire
    async def test_a_credential_is_masked_in_the_HTTP_response(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Built on a CONSTRUCTED config rather than the operator's file: a test
        that reads the live machine passes or fails on what happens to be
        configured there, which is not a property of the code.

        IT PATCHES `config_path` AS WELL AS `load_yaml`, and the first draft did
        not. The route checks `path.exists()` before reading, so with only
        `load_yaml` patched it took the "no config file" branch and returned 503
        — on THIS box the off-tree rehearsal never saw it, because the operator's
        real `stackowl.yaml` exists and the branch was never reached. Applying
        and running the derived path is what found it.
        """
        from stackowl.control_plane import server as mod

        present = tmp_path / "stackowl.yaml"
        present.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mod, "config_path", lambda: present)

        payload = {
            "providers": [
                {"name": "local", "api_key": None},
                {"name": "remote", "api_key": "sk-aliteralkeypastedbyahuman"},
            ],
            "telegram_channel": {"bot_token": "file:/home/x/.secrets/tg.key"},
            "autonomy_level": "high",
        }
        monkeypatch.setattr(mod, "load_yaml", lambda _path: payload)

        res = await _server()._handle_config(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001

        assert res.status == 200
        rendered = res.text
        assert "sk-aliteralkeypastedbyahuman" not in rendered, (
            "a literal key reached the HTTP response"
        )
        assert "/home/x/.secrets/tg.key" not in rendered, (
            "a secret-store PATH reached the HTTP response — the platform masks "
            "a reference too, and this surface must not be the exception"
        )
        keys = {s["key"]: s for s in _body(res)["settings"]}
        assert keys["providers.1.api_key"]["masked"] is True
        assert keys["telegram_channel.bot_token"]["masked"] is True
        assert keys["autonomy_level"]["value"] == "high", (
            "an ordinary setting was masked — the point is to SHOW the set"
        )

    @pytest.mark.tripwire
    def test_it_reads_through_the_chat_commands_own_helpers(self) -> None:
        """One reader, or two answers to one question. `/config list` and this
        route must agree about what is configured AND about what is secret."""
        import inspect

        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod.ControlPlaneServer._handle_config)
        for helper in ("config_path(", "load_yaml(", "collect_sensitive(", "flatten("):
            assert helper in src, f"the route no longer calls {helper}"


class TestItAnswersWithTheWholeSet:
    @pytest.mark.tripwire
    async def test_every_configured_key_is_listed_sorted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from stackowl.control_plane import server as mod

        present = tmp_path / "stackowl.yaml"
        present.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mod, "config_path", lambda: present)
        monkeypatch.setattr(
            mod, "load_yaml", lambda _p: {"zebra": 1, "alpha": {"beta": 2}}
        )
        res = await _server()._handle_config(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001

        keys = [s["key"] for s in _body(res)["settings"]]
        assert keys == ["alpha.beta", "zebra"], keys

    @pytest.mark.tripwire
    async def test_a_missing_config_file_CONFESSES_rather_than_returning_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A05.5's shape and A06.6's mapped item: a read failure that looks like
        an empty store. `503` + `wired: false` cannot be mistaken for "you have
        configured nothing"."""
        from pathlib import Path

        from stackowl.control_plane import server as mod

        monkeypatch.setattr(mod, "config_path", lambda: Path("/nonexistent/stackowl.yaml"))
        res = await _server()._handle_config(_Req(Authorization=f"Bearer {_TOKEN}"))  # noqa: SLF001

        assert res.status == 503
        assert _body(res) == {"settings": [], "wired": False}
