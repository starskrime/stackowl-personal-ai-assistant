"""_output_cap must account for tool_schemas' own token cost — live incident
2026-07-23: 192+ recurring ContextWindowExceededError 400s over 3 days
(confirmed via read_logs), root-caused to _output_cap only ever estimating
`messages`, never the `tools=tool_schemas` the SAME request also sends. A
tool-using owl with a large presented toolset silently requested more
output than the window had room for once the (unaccounted) tool schemas'
own input-token cost was added by the provider.

Reuses test_disable_thinking.py's fake-client / window-cache pattern.
"""

from __future__ import annotations

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.providers import model_window as mw
from stackowl.providers.openai_provider import OpenAIProvider

_MODEL = "neraai-v1-raw"
_WINDOW = 262_144


@pytest.fixture(autouse=True)
def _clear_window_cache():
    mw._WINDOW_CACHE.clear()
    yield
    mw._WINDOW_CACHE.clear()


def _provider(name: str = "NeraAiRaw") -> OpenAIProvider:
    config = ProviderConfig(
        name=name, protocol="openai", base_url="https://x/v1",
        default_model=_MODEL, tier="fast",
    )
    provider = OpenAIProvider(config, api_key="")
    mw._WINDOW_CACHE[(name, _MODEL)] = _WINDOW
    return provider


def _messages(n_chars: int) -> list[dict[str, object]]:
    return [{"role": "user", "content": "x" * n_chars}]


def _tool_schemas(n: int) -> list[dict[str, object]]:
    """A realistic-shaped tool schema list — big enough to matter once
    JSON-serialized (matches this platform's up-to-150-tool presentation
    budget, HARD_TOOL_COUNT_CAP)."""
    return [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "A moderately verbose tool description " * 5,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "arg_one": {"type": "string", "description": "first arg " * 5},
                        "arg_two": {"type": "string", "description": "second arg " * 5},
                    },
                    "required": ["arg_one"],
                },
            },
        }
        for i in range(n)
    ]


def test_tool_schemas_reduce_the_output_cap_versus_messages_alone() -> None:
    provider = _provider()
    messages = _messages(2000)
    schemas = _tool_schemas(60)

    cap_without_tools = provider._output_cap(_MODEL, messages)
    cap_with_tools = provider._output_cap(_MODEL, messages, schemas)

    assert cap_with_tools < cap_without_tools


def test_large_tool_catalog_plus_messages_never_exceeds_the_window() -> None:
    """The regression itself: reproduce the live incident's shape (a sizeable
    prompt PLUS a large tool catalog) and assert the resulting request would
    fit — i.e. estimated_input + returned_max_tokens <= window, which is
    exactly the invariant openai.BadRequestError's ContextWindowExceededError
    proved false before this fix (input=12145, requested=250000, sum
    262145 > window 262144)."""
    from stackowl.parliament.token_estimate import estimate_tokens

    provider = _provider()
    messages = _messages(8000)  # a real multi-turn tool-loop prompt
    schemas = _tool_schemas(100)  # a large presented toolset

    import json
    estimated_input = estimate_tokens(
        "".join(str(m.get("content", "")) for m in messages)
    ) + estimate_tokens(json.dumps(schemas, default=str))

    cap = provider._output_cap(_MODEL, messages, schemas)

    assert estimated_input + cap <= _WINDOW


def test_no_tool_schemas_is_byte_identical_to_the_old_signature() -> None:
    """None (every non-tool call site: complete(), the wrap-up round) must
    compute EXACTLY what the pre-fix two-argument call did."""
    provider = _provider()
    messages = _messages(2000)

    cap_positional_none = provider._output_cap(_MODEL, messages, None)
    cap_omitted = provider._output_cap(_MODEL, messages)

    assert cap_positional_none == cap_omitted


def test_empty_tool_schemas_list_is_also_a_no_op() -> None:
    provider = _provider()
    messages = _messages(2000)

    assert provider._output_cap(_MODEL, messages, []) == provider._output_cap(_MODEL, messages)
