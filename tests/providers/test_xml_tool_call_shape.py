"""The Hermes/Qwen `<tool_call><function=…>` shape — parsed, not just hidden.

FOUND LIVE 2026-08-15, minutes after the local model was upgraded from qwen 3.6
27b to qwen 3.8 27b. Bakir asked it to compare the two models; StackOwl replied
"✓ done in 5s" and then showed him this, verbatim, in Telegram:

    Let me search for the latest info on these specific models before comparing.

    <tool_call>
    <function=web_search>
    <parameter=query>
    Qwen3.8 27B vs Qwen 3.6 27B comparison benchmarks
    </parameter>
    </function>
    </tool_call>

The search never ran. The model did everything right — it is trained on this
shape — but the OpenAI-protocol gateway in front of it does not translate the
block into a native `tool_calls` array, so it arrived as CONTENT.

WHY IT IS PARSED AND NOT MERELY SUPPRESSED. This is the fourth distinct leaked
tool-call shape the guard has had to learn. Suppression alone would have turned a
visible break into an invisible one: the user would stop seeing XML and start
seeing an assistant that had quietly lost the ability to search after a model
upgrade. Parsing restores the capability; the guard stays as the backstop for
variants the parser cannot resolve.
"""

from __future__ import annotations

from stackowl.providers._react import looks_like_tool_call, parse_react_action

KNOWN = {"web_search", "web_fetch", "memory", "read_file"}

LEAKED = """Let me search for the latest info on these specific models before comparing them.

<tool_call>
<function=web_search>
<parameter=query>
Qwen3.8 27B vs Qwen 3.6 27B comparison benchmarks
</parameter>
</function>
</tool_call>"""


class TestTheLiveIncident:
    def test_the_exact_leaked_block_now_dispatches(self) -> None:
        assert parse_react_action(LEAKED, KNOWN) == (
            "web_search",
            {"query": "Qwen3.8 27B vs Qwen 3.6 27B comparison benchmarks"},
        )

    def test_it_is_also_recognised_by_the_delivery_guard(self) -> None:
        """Belt and braces: if a future variant stops parsing, it must still be
        suppressed rather than shown."""
        assert looks_like_tool_call(LEAKED, KNOWN)

    def test_the_guard_works_without_a_known_tool_set(self) -> None:
        """Call sites on no-tool turns pass no `known`; the shape is structural,
        so it must be caught there too."""
        assert looks_like_tool_call(LEAKED)


class TestItDispatchesOnlyRealTools:
    def test_an_unknown_tool_name_does_NOT_dispatch(self) -> None:
        """A hallucinated name must never reach the dispatcher."""
        assert parse_react_action(LEAKED.replace("web_search", "not_a_tool"), KNOWN) is None

    def test_but_the_unknown_name_is_still_SUPPRESSED(self) -> None:
        """The half that matters for the user: it does not run AND is not shown."""
        assert looks_like_tool_call(LEAKED.replace("web_search", "not_a_tool"), KNOWN)


class TestShapeVariants:
    def test_the_attribute_form_is_accepted(self) -> None:
        """Some builds emit `<function name="x">` instead of `<function=x>`."""
        text = '<tool_call><function name="web_fetch"><parameter name="url">https://x.dev</parameter></function></tool_call>'
        assert parse_react_action(text, KNOWN) == ("web_fetch", {"url": "https://x.dev"})

    def test_multiple_parameters_are_all_captured(self) -> None:
        text = (
            "<tool_call><function=web_search>"
            "<parameter=query>rust async</parameter>"
            "<parameter=limit>5</parameter>"
            "</function></tool_call>"
        )
        assert parse_react_action(text, KNOWN) == (
            "web_search", {"query": "rust async", "limit": 5},
        )

    def test_a_zero_argument_call_is_valid(self) -> None:
        assert parse_react_action("<tool_call><function=memory></function></tool_call>", KNOWN) == (
            "memory", {},
        )

    def test_a_numeric_parameter_is_a_number_not_a_string(self) -> None:
        text = "<tool_call><function=web_search><parameter=limit>10</parameter></function></tool_call>"
        assert parse_react_action(text, KNOWN) == ("web_search", {"limit": 10})

    def test_a_json_object_parameter_is_decoded(self) -> None:
        text = ('<tool_call><function=memory><parameter=payload>{"a": 1}</parameter>'
                "</function></tool_call>")
        assert parse_react_action(text, KNOWN) == ("memory", {"payload": {"a": 1}})

    def test_prose_containing_the_word_null_stays_a_string(self) -> None:
        """The decode is gated on shape precisely so ordinary text is not mangled.
        A query is a string even when it happens to mention a JSON keyword."""
        text = ("<tool_call><function=web_search><parameter=query>"
                "what does null mean in rust</parameter></function></tool_call>")
        assert parse_react_action(text, KNOWN) == (
            "web_search", {"query": "what does null mean in rust"},
        )


class TestOrdinaryRepliesAreUntouched:
    def test_plain_prose_is_neither_parsed_nor_suppressed(self) -> None:
        prose = "I compared them: 3.8 is the newer release. No tool was needed."
        assert parse_react_action(prose, KNOWN) is None
        assert not looks_like_tool_call(prose, KNOWN)

    def test_prose_mentioning_a_tool_name_is_not_a_call(self) -> None:
        prose = "You could use web_search for that, but I already know the answer."
        assert not looks_like_tool_call(prose, KNOWN)

    def test_a_code_block_about_xml_is_not_a_call(self) -> None:
        """A user asking about markup must still get an answer. `<tool>` is not
        `<tool_call>`, and no function tag means nothing to dispatch."""
        prose = "In XML you write <tool>name</tool> to describe a tool."
        assert not looks_like_tool_call(prose, KNOWN)
