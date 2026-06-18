"""Tests for the `pdf` tool (E3-S4) — two-mode reader, self-healing, untrusted text.

pypdf cannot *create* PDFs and reportlab isn't on this box, so Mode A is driven by
mocking ``pypdf.PdfReader`` to return pages with known ``extract_text()`` output
(the most robust approach on ARM64 per the story). Mode B uses a fake
document-capable provider injected through the same StepServices mechanism the
pipeline uses (``set_services``), exactly like the browser tool tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest

from stackowl.paths import StackowlHome
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.io import pdf as pdf_mod
from stackowl.tools.io.pdf import PdfTool


# --------------------------------------------------------------------------- #
# fixtures / fakes
# --------------------------------------------------------------------------- #
@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: root))
    return root


def _make_pdf(workspace: Path, name: str = "doc.pdf", content: bytes = b"%PDF-1.4 stub") -> Path:
    p = workspace / name
    p.write_bytes(content)
    return p


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakeReader:
    """Stand-in for pypdf.PdfReader, configured per-test via _install_reader."""

    def __init__(self, *_a: object, **_k: object) -> None:
        self.pages = list(_FakeReader.PAGES)
        self.is_encrypted = _FakeReader.ENCRYPTED

    def decrypt(self, _password: str) -> int:
        return _FakeReader.DECRYPT_RESULT

    # class-level config, set by _install_reader
    PAGES: list[_FakePage] = []
    ENCRYPTED: bool = False
    DECRYPT_RESULT: int = 0


def _install_reader(
    monkeypatch: pytest.MonkeyPatch,
    *,
    texts: list[str],
    encrypted: bool = False,
    decrypt_result: int = 0,
) -> None:
    _FakeReader.PAGES = [_FakePage(t) for t in texts]
    _FakeReader.ENCRYPTED = encrypted
    _FakeReader.DECRYPT_RESULT = decrypt_result

    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)


class _FakeDocProvider(ModelProvider):
    """Document-capable fake provider. Bypasses TestModeGuard like MockProvider."""

    def __init__(self, name: str = "vision-fake", text: str = "MODEL EXTRACTED TEXT", supports: bool = True) -> None:
        self._name = name
        self._text = text
        self._supports = supports
        self.received_docs = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    @property
    def supports_document(self) -> bool:
        return self._supports

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        yield self._text

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.received_docs = sum(len(m.documents) for m in messages)
        return CompletionResult(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model="fake-doc-model",
            provider_name=self._name,
            duration_ms=1.0,
        )


def _registry_with(*providers: ModelProvider) -> ProviderRegistry:
    reg = ProviderRegistry()
    for p in providers:
        reg.register_mock(p.name, p)
    return reg


# --------------------------------------------------------------------------- #
# Mode A — text extraction
# --------------------------------------------------------------------------- #
class TestModeAText:
    async def test_text_pdf_extracts(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=["Hello world, this is a real text document page."])
        result = await PdfTool().execute(path="doc.pdf")
        assert result.success is True
        assert "Hello world" in result.output

    async def test_extracted_text_is_marked_untrusted(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=["Plenty of genuine extracted prose right here on the page."])
        result = await PdfTool().execute(path="doc.pdf")
        assert pdf_mod._UNTRUSTED_OPEN in result.output
        assert pdf_mod._UNTRUSTED_CLOSE in result.output

    async def test_max_pages_caps_extraction(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=["page one text here", "page two text here", "page three text here"])
        result = await PdfTool().execute(path="doc.pdf", max_pages=1)
        assert result.success is True
        assert "page one" in result.output
        assert "page two" not in result.output

    async def test_mode_text_on_garbage_returns_structured_error(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=[""])  # scanned/empty
        result = await PdfTool().execute(path="doc.pdf", mode="text")
        assert result.success is False
        assert "scanned" in (result.error or "").lower() or "document" in (result.error or "").lower()


# --------------------------------------------------------------------------- #
# Mode A — edge / error paths (structured, never raises)
# --------------------------------------------------------------------------- #
class TestModeAEdges:
    async def test_encrypted_pdf_structured_error(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=["secret"], encrypted=True, decrypt_result=0)
        result = await PdfTool().execute(path="doc.pdf")
        assert result.success is False
        assert "encrypt" in (result.error or "").lower()

    async def test_oversized_pdf_rejected_before_read(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        big = b"x" * (pdf_mod._MAX_FILE_BYTES + 1)
        _make_pdf(workspace, content=big)
        # PdfReader must never be reached for an oversized file.
        result = await PdfTool().execute(path="doc.pdf")
        assert result.success is False
        assert "too large" in (result.error or "").lower()

    async def test_timeout_path_structured_error(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        monkeypatch.setattr(pdf_mod, "_EXTRACT_TIMEOUT_S", 0.05)

        def _slow(_self: object, _target: Path, _max_pages: int) -> tuple[str, int]:
            import time as _t

            _t.sleep(0.5)
            return "text", 1

        monkeypatch.setattr(PdfTool, "_extract_text", _slow)
        result = await PdfTool().execute(path="doc.pdf")
        assert result.success is False
        assert "timed out" in (result.error or "").lower()

    async def test_malformed_pdf_structured_error(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        import pypdf
        from pypdf.errors import PdfReadError

        def _boom(*_a: object, **_k: object) -> _FakeReader:
            raise PdfReadError("EOF marker not found")

        monkeypatch.setattr(pypdf, "PdfReader", _boom)
        result = await PdfTool().execute(path="doc.pdf")
        assert result.success is False
        assert "malformed" in (result.error or "").lower()

    async def test_missing_file_structured_error(self, workspace: Path) -> None:
        result = await PdfTool().execute(path="nope.pdf")
        assert result.success is False
        assert "not found" in (result.error or "").lower()

    async def test_path_escape_blocked(self, workspace: Path) -> None:
        result = await PdfTool().execute(path="../../etc/passwd")
        assert result.success is False
        assert "traversal" in (result.error or "").lower()


# --------------------------------------------------------------------------- #
# Mode B — document routing + egress disclosure
# --------------------------------------------------------------------------- #
class TestModeB:
    async def test_garbage_routes_to_document_provider(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=[""])  # empty extraction → self-heal
        provider = _FakeDocProvider()
        token = set_services(StepServices(provider_registry=_registry_with(provider)))
        try:
            result = await PdfTool().execute(path="doc.pdf")
        finally:
            reset_services(token)
        assert result.success is True
        assert "MODEL EXTRACTED TEXT" in result.output
        # egress disclosure — the result must name the provider that handled it.
        assert provider.name in result.output
        assert provider.received_docs == 1

    async def test_mode_document_forces_routing(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        # Even with extractable text, mode='document' must route to the model.
        _install_reader(monkeypatch, texts=["lots of perfectly good extractable text here"])
        provider = _FakeDocProvider()
        token = set_services(StepServices(provider_registry=_registry_with(provider)))
        try:
            result = await PdfTool().execute(path="doc.pdf", mode="document")
        finally:
            reset_services(token)
        assert result.success is True
        assert "MODEL EXTRACTED TEXT" in result.output
        assert provider.received_docs == 1

    async def test_mode_b_output_marked_untrusted(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=[""])
        token = set_services(StepServices(provider_registry=_registry_with(_FakeDocProvider())))
        try:
            result = await PdfTool().execute(path="doc.pdf")
        finally:
            reset_services(token)
        assert pdf_mod._UNTRUSTED_OPEN in result.output

    async def test_no_capable_provider_returns_guidance(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=[""])
        text_only = _FakeDocProvider(name="text-only", supports=False)
        token = set_services(StepServices(provider_registry=_registry_with(text_only)))
        try:
            result = await PdfTool().execute(path="doc.pdf")
        finally:
            reset_services(token)
        assert result.success is False
        assert "document-capable" in (result.error or "").lower()

    async def test_no_registry_returns_structured_error(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=[""])
        token = set_services(StepServices(provider_registry=None))
        try:
            result = await PdfTool().execute(path="doc.pdf")
        finally:
            reset_services(token)
        assert result.success is False

    async def test_provider_failure_is_structured_not_raised(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pdf(workspace)
        _install_reader(monkeypatch, texts=[""])

        class _Boom(_FakeDocProvider):
            async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
                raise RuntimeError("provider exploded")

        token = set_services(StepServices(provider_registry=_registry_with(_Boom())))
        try:
            result = await PdfTool().execute(path="doc.pdf")
        finally:
            reset_services(token)
        assert result.success is False
        assert "failed" in (result.error or "").lower()


# --------------------------------------------------------------------------- #
# arg validation
# --------------------------------------------------------------------------- #
class TestArgs:
    async def test_missing_path(self, workspace: Path) -> None:
        result = await PdfTool().execute()
        assert result.success is False

    async def test_invalid_mode(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_pdf(workspace)
        result = await PdfTool().execute(path="doc.pdf", mode="bogus")
        assert result.success is False
        assert "mode" in (result.error or "").lower()
