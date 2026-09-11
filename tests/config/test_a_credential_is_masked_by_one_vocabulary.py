"""`mcp_server.auth_token` was redacted in the logs and printed by `/config list`.

TWO MECHANISMS, TWO VOCABULARIES, ONE QUESTION. The log redactor asks a NAME
test with a maintained exemption set. `/config list` asked something else
entirely: an opt-in `sensitive=True` marker a person had to remember per field.

MEASURED 2026-09-11 across all 342 fields reachable from `Settings`:

  * the name test flags **15**; the marker flagged **7**
  * the marked set is a SUBSET — "marked but not caught by name" is **EMPTY**,
    so the marker has never once expressed something the name does not say
  * the eight it missed include `providers[].api_key`, `webhook.sources[].secret`,
    `mcp_server.auth_token`, `browser.default_proxy[].password`,
    `governance.audit_export_key`, `web_search.brave_api_key`, and
    `tts.cloud_api_key` / `image.cloud_api_key` — whose SIBLING
    `transcription.cloud_api_key` IS marked. Same name, same class, three
    siblings, one remembered.

`mcp_server.auth_token`'s own description reads "Sensitive: auto-redacted in
logs by the *token key-pattern." Its author thought about it, wrote down that it
was covered, and was right about the logs and wrong about the config surface.
That is what two vocabularies do: satisfying one reads as done.

AND A MARKER ALONE WOULD NOT HAVE SAVED THE LIST FIELDS. `flatten` descended
into `dict` and stopped at `list`, so `providers` — a list of models, each with
an `api_key` — was stringified WHOLE and printed as one value. The key never
reached the masking comparison. Both halves are fixed together here; either one
alone is decoration.

ON THIS DEPLOYMENT the exposure was the secret-store LAYOUT rather than key
material: every set credential is a `file:`/`keychain:` reference. The fields
accept literals, and `telegram_channel.bot_token` holds a reference and is
masked anyway — so the platform's own policy already treats a reference as worth
hiding, and the inconsistency is the defect.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import BaseModel

from stackowl.commands.config_helpers import collect_sensitive, flatten
from stackowl.config.settings import Settings
from stackowl.infra.observability import is_credential_name


class _Inner(BaseModel):
    name: str
    api_key: str | None = None
    max_tokens: int = 4096


class _Outer(BaseModel):
    providers: list[_Inner] = []
    label: str = ""


class TestOneVocabulary:
    @pytest.mark.tripwire
    def test_the_config_masker_asks_the_predicate_the_LOGS_ask(self) -> None:
        """The structural half. Two independent name lists would drift, and the
        drift is invisible until something is printed that should not be."""
        import inspect

        from stackowl.commands import config_helpers

        src = inspect.getsource(config_helpers)
        assert "is_credential_name" in src, (
            "the config masker no longer asks the shared predicate — a second "
            "vocabulary is how `auth_token` came to be redacted in logs and "
            "printed by `/config list`"
        )

    @pytest.mark.tripwire
    def test_the_marker_is_a_WIDENING_and_the_name_test_is_the_floor(self) -> None:
        """MEASURED 2026-09-11: every `sensitive=True` field is also caught by
        name — the marked set is a strict subset. This asserts that relationship
        rather than the counts, so it stays true as settings are added.

        If it ever fails, the marker has started doing real work (a credential
        whose NAME does not advertise it), which is exactly when a reader needs
        to know — so it reports the field rather than being deleted.
        """
        marked: set[str] = set()

        def walk(model: type[BaseModel], prefix: str) -> None:
            for name, field in model.model_fields.items():
                dotted = f"{prefix}.{name}" if prefix else name
                extra = (
                    field.json_schema_extra
                    if isinstance(field.json_schema_extra, dict)
                    else {}
                )
                if extra.get("sensitive"):
                    marked.add(name)
                annotation = field.annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    walk(annotation, dotted)
                for arg in typing.get_args(annotation):
                    if isinstance(arg, type) and issubclass(arg, BaseModel):
                        walk(arg, f"{dotted}[]")

        walk(Settings, "")
        assert marked, "no field carries `sensitive=True` — this control is blind"

        uncovered = sorted(n for n in marked if not is_credential_name(n))
        assert not uncovered, (
            f"{uncovered} is marked sensitive but its NAME does not say so. The "
            "marker is now carrying weight the name test cannot — read this "
            "before assuming the name test is sufficient on its own"
        )


class TestTheListHole:
    @pytest.mark.tripwire
    def test_a_credential_inside_a_list_of_models_is_masked(self) -> None:
        """THE FOUNDING CASE. `providers` is a list of models with an `api_key`
        on each, and it was printed whole."""
        data = {
            "providers": [
                {"name": "local", "api_key": None, "max_tokens": 1},
                {"name": "remote", "api_key": "sk-thisisaliteralkeyvalue", "max_tokens": 2},
            ],
            "label": "visible",
        }
        sensitive: set[str] = set()
        collect_sensitive(_Outer, "", sensitive)
        pairs: list[tuple[str, str]] = []
        flatten("", data, sensitive, pairs)
        rendered = dict(pairs)

        assert rendered["providers.1.api_key"] == "***", (
            f"a literal key was rendered as {rendered.get('providers.1.api_key')!r}"
        )
        assert "sk-thisisaliteralkeyvalue" not in " ".join(
            f"{k}{v}" for k, v in pairs
        ), "the key appears somewhere in the flattened output"

    @pytest.mark.tripwire
    def test_the_index_is_rendered_so_ONE_provider_is_named(self) -> None:
        """A reader with four providers and one broken key needs to know which."""
        data = {"providers": [{"name": "a"}, {"name": "b"}], "label": "x"}
        pairs: list[tuple[str, str]] = []
        flatten("", data, set(), pairs)
        keys = [k for k, _ in pairs]
        assert "providers.0.name" in keys and "providers.1.name" in keys, keys

    @pytest.mark.tripwire
    def test_collect_sensitive_descends_into_a_list_of_models(self) -> None:
        """The other half. Marking a field inside a list did nothing, because the
        walk never reached it — all eight list-of-model fields in `Settings` were
        invisible to it."""
        out: set[str] = set()
        collect_sensitive(_Outer, "", out)
        assert any("api_key" in k for k in out), (
            f"the walk never reached the list element's credential: {sorted(out)}"
        )

    @pytest.mark.tripwire
    def test_a_plain_list_of_scalars_is_still_ONE_value(self) -> None:
        """The list branch fires only for a list OF MODELS. A list of strings —
        `tiers`, `target_channels` — reads better whole than as three indexed
        rows, and exploding it would be a gratuitous change to every reader."""
        pairs: list[tuple[str, str]] = []
        flatten("", {"tiers": ["fast", "standard"]}, set(), pairs)
        assert pairs == [("tiers", "['fast', 'standard']")], pairs


class TestItDoesNotOverMask:
    @pytest.mark.tripwire
    @pytest.mark.parametrize(
        "name",
        ["max_tokens", "max_output_tokens", "token_budget", "max_input_tokens"],
    )
    def test_an_LLM_BUDGET_is_not_a_credential(self, name: str) -> None:
        """`_SENSITIVE_PATTERNS` matches `*token`, a SUFFIX — so `max_tokens`
        (plural) and `token_budget` (prefix) never match, and no exemption list
        is needed. Measured rather than assumed: masking a budget would make
        every capacity question unanswerable from `/config list`."""
        assert not is_credential_name(name)

    @pytest.mark.tripwire
    def test_an_identifier_is_not_a_credential(self) -> None:
        """`*_key` is a heuristic for API keys and it once masked `session_key`,
        the most-logged field in the tree. The exemption set that fixed it is
        the one this now shares."""
        assert not is_credential_name("session_key")
        assert not is_credential_name("idempotency_key")
