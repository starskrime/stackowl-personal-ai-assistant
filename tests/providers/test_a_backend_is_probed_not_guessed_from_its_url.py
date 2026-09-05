"""D04.2 — the window resolver guessed a vendor from a URL substring.

The standing constraint is recorded in `progress.yml`: Bakir runs a single LiteLLM
gateway, his users run many different backends, and therefore **no code may branch on
a provider's name — dispatch on response SHAPE and declared CAPABILITY instead.** A
memory entry records him rejecting exactly this once already: "caught `_output_cap`
branching on ollama. General abstractions, config-driven."

Measured 2026-09-05, the guess existed TWICE:

    providers/model_window.py:268    ":11434" in base_url or "ollama" in base_url.lower()
    providers/openai_provider.py:1283  not (":11434" in base) and not ("ollama" in base.lower())

De Morgan-identical, never diverged — but two copies of one rule, and both wrong in
the same two ways: a gateway path like `gw.example.com/ollama/v1` matches, and so does
**a vLLM server on port 11434**, which then receives an `options.num_ctx` body it may
reject.

**THE CAPABILITY IS REAL; THE INSTRUMENT WAS A VENDOR SNIFF.** Some backends expose a
native `/api/show` endpoint reporting the model's true context length, and no other
OpenAI-compatible server does. That is a genuine capability difference — unlike
`_output_cap`, which branched on vendor for a POLICY choice. So the fix is not to
delete the capability but to stop guessing at it: `_probe_native_window_api` already
returns an int or None and catches everything, which makes it a perfect capability
test. Probe; if it answers, use it.

The irony this replaces is fifteen lines away in the same file: the comment at
`model_window.py:271-288` describes the live incident where a URL-shaped assumption
cost a 32x window error, and `config/provider.py:152` already names the pattern —
"a hardcoded guess wearing discovery's clothes."
"""

from __future__ import annotations

import pytest

from stackowl.providers import model_window


@pytest.mark.tripwire
def test_the_url_sniff_is_gone() -> None:
    """Retired means deleted — not left beside its replacement."""
    assert not hasattr(model_window, "_looks_like_ollama"), (
        "the vendor sniff is still present; a guess left next to a probe is the "
        "second copy that eventually disagrees"
    )


@pytest.mark.tripwire
def test_no_module_decides_a_backend_from_a_url_substring() -> None:
    """Both copies, and any future third.

    The literals are what make it a guess. A provider is identified by what it
    ANSWERS, never by what its URL looks like.
    """
    import ast
    import pathlib

    # AST, NOT LINE SCANNING. A first version skipped `#` lines and any line
    # containing a triple quote, and still flagged this module's own docstring where
    # it DESCRIBES the deleted guess. Prose about a defect is not the defect — the
    # same distinction D18.6 had to make for `/tmp` literals in security regexes.
    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # THE DEFECT IS A MEMBERSHIP TEST, not the literal. `cli/providers_cli.py`
        # shows "Base URL (e.g. http://localhost:11434/v1)" as a PROMPT HINT during
        # setup — display text, and flagging it would be the third false positive
        # this guard produced before it was aimed properly. What is banned is
        # `"…11434" in base_url`: deciding what a backend IS from what its address
        # looks like.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                continue
            probe = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            if "11434" in probe and probe not in docstrings:
                offenders.append(f"{path.relative_to(src)}:{node.lineno}")
    assert not offenders, (
        f"a backend is being identified by a port literal in its URL: {offenders}. "
        "Probe the capability instead — the standing rule is to dispatch on response "
        "SHAPE and declared CAPABILITY, never on a vendor guessed from a substring."
    )


@pytest.mark.asyncio
async def test_a_backend_that_answers_the_native_api_is_used(monkeypatch) -> None:
    """The capability, measured rather than guessed — on a URL that looks nothing
    like the vendor."""
    model_window.reset_window_cache()

    async def _fake_native(base_url: str, model: str) -> int | None:
        return 131072

    monkeypatch.setattr(model_window, "_probe_native_window_api", _fake_native)
    window = await model_window.resolve_window(
        provider_name="anything",
        model="m",
        context_chars=None,
        base_url="https://gateway.example.com/v1",   # no port, no vendor name
        protocol="openai",
    )
    assert window == 131072
    assert model_window.answered_native_window_api("anything", "m") is True


@pytest.mark.asyncio
async def test_a_backend_that_does_not_answer_falls_through(monkeypatch) -> None:
    """A non-answering endpoint must cost nothing but a fast miss."""
    model_window.reset_window_cache()

    async def _no_native(base_url: str, model: str) -> int | None:
        return None

    async def _compatible(base_url: str, model: str, api_key: str | None) -> int | None:
        return 262144

    monkeypatch.setattr(model_window, "_probe_native_window_api", _no_native)
    monkeypatch.setattr(model_window, "_probe_openai_compatible", _compatible)
    window = await model_window.resolve_window(
        provider_name="p", model="m", context_chars=None,
        base_url="http://127.0.0.1:11434/v1",        # LOOKS like the vendor, is not
        protocol="openai",
    )
    assert window == 262144
    assert model_window.answered_native_window_api("p", "m") is False, (
        "a server on that port that does NOT answer the native API must not be "
        "treated as though it does — this is the vLLM-on-11434 false positive"
    )
