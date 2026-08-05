"""ADR-19 — the shared LLM-JSON parser now does what its docstring promised.

The docstring has always claimed it "tolerates ```json fenced blocks, leading
prose, and trailing prose". Two of the three were real:

  * the fence was only stripped when it was at position 0, so the very common
    "Here is the result:\\n```json\\n{...}\\n```" kept its fence;
  * trailing prose was never handled at all — the code sliced from the first
    brace to EOF, so "{...}\\nHope that helps." was handed to json.loads whole.

Measured against five realistic model outputs: two were rejected. This parser
feeds the critic scorer, the reflection writer and the skill synthesizer, so a
model adding one polite closing sentence silently lost its entire response.

The same shape as the resilience-marker bug found the same day: a docstring
describing behaviour the code did not implement, and the docstring being the
part people read.
"""

from __future__ import annotations

import pytest

from stackowl.memory.json_parser import parse_json_response


@pytest.mark.parametrize(
    ("shape", "text"),
    [
        ("bare",                  '{"a": 1}'),
        ("fenced json",           '```json\n{"a": 1}\n```'),
        ("fenced plain",          '```\n{"a": 1}\n```'),
        ("leading prose",         'Here is the result:\n{"a": 1}'),
        ("TRAILING prose",        '{"a": 1}\nHope that helps.'),
        ("prose both sides",      'Sure!\n{"a": 1}\nLet me know.'),
        ("prose then fence",      '**Result:**\n```json\n{"a": 1}\n```'),
        ("prose around a fence",  'Sure!\n```json\n{"a": 1}\n```\nAnything else?'),
    ],
)
def test_realistic_model_outputs_parse(shape, text):
    assert parse_json_response(text) == {"a": 1}, shape


def test_nested_objects_are_not_truncated_at_the_first_close():
    """Brace COUNTING, not 'find the first }'. Getting this wrong silently
    truncates every nested payload."""
    assert parse_json_response('{"a": {"b": {"c": 1}}} trailing') == {"a": {"b": {"c": 1}}}


def test_a_brace_inside_a_string_does_not_end_the_object():
    """This platform's models genuinely emit regexes, templates and code inside
    JSON values. A non-string-aware scanner would cut the object in half."""
    assert parse_json_response('{"re": "a{2}b", "ok": true} trailing') == {
        "re": "a{2}b", "ok": True,
    }


def test_escaped_quotes_do_not_confuse_the_string_scanner():
    assert parse_json_response('{"s": "he said \\"hi\\" {", "n": 2} x') == {
        "s": 'he said "hi" {', "n": 2,
    }


def test_a_top_level_array_is_still_rejected():
    """The contract is a JSON OBJECT. Widening tolerance must not widen the
    return type — every caller indexes the result by key."""
    assert parse_json_response("[1, 2, 3]") is None


def test_truncated_output_is_still_rejected():
    """A cut-off response must not half-parse into a plausible-looking dict."""
    assert parse_json_response('{"a": 1') is None


def test_no_json_at_all_is_None():
    assert parse_json_response("I could not complete that request.") is None


def test_required_keys_still_gate():
    assert parse_json_response('{"a": 1} trailing', required_keys=["a"]) == {"a": 1}
    assert parse_json_response('{"a": 1} trailing', required_keys=["b"]) is None
