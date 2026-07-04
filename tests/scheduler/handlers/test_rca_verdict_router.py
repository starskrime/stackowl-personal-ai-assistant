"""Task 7 — rca_verdict_router: consume a verified RcaVerdict into the EXISTING
gated fix/alternative machinery. No new execution path: a "fix" verdict is
submitted through the REAL tool_build gate (security_scan_gate + consent,
unmodified); an "alternative" verdict only CONSULTS
capability_substitution.find_substitute (a pure, read-only decision function).

Mirrors tests/tools/meta/test_tool_build_gateway.py's isolation fixtures
(tmp_home + _live_io) and consent-gate construction so the fix path drives the
GENUINE gate end to end, mocked only at the external consent-prompter boundary
(ConsentPolicy's own TrustTier.AUTO short-circuit — no human UI to mock — is
the same seam tool_build's own gateway tests use).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.learning.failure_outcome_miner import RcaVerdict
from stackowl.paths import StackowlHome
from stackowl.pipeline.capability_substitution import find_substitute
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.handlers import rca_verdict_router as mod
from stackowl.scheduler.handlers.rca_verdict_router import (
    consume_alternative_verdict,
    consume_fix_verdict,
    extract_argv_from_fix,
    route_rca_verdict,
)
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


def _verdict(
    *, capability_class: str = "cache_warmer", failure_class: str = "ToolTimeoutError",
    fix_pattern: str = "Investigate the connection pool exhaustion.",
    skill_name: str = "fix_cache_warmer_timeout", verified: bool = True,
) -> RcaVerdict:
    return RcaVerdict(
        capability_class=capability_class, failure_class=failure_class,
        skill_name=skill_name, description="d", when_to_use="w",
        root_cause="rc", fix_pattern=fix_pattern, verified=verified,
    )


# --------------------------------------------------------------------------- #
# extract_argv_from_fix — pure, bounded text extraction.
# --------------------------------------------------------------------------- #

def test_extract_argv_from_fenced_block() -> None:
    fix = "Root cause is X.\n```\nsystemctl restart cache-warmer\n```\nDone."
    assert extract_argv_from_fix(fix) == ["systemctl", "restart", "cache-warmer"]


def test_extract_argv_from_inline_span() -> None:
    fix = "Run `systemctl restart cache-warmer` to clear it."
    assert extract_argv_from_fix(fix) == ["systemctl", "restart", "cache-warmer"]


def test_extract_argv_none_for_prose_only_fix() -> None:
    # The common case: the hypothesis/verifier prompts never ask for a literal
    # command, so most fix_pattern text is pure guidance.
    assert extract_argv_from_fix("Investigate the connection pool exhaustion.") is None


def test_extract_argv_none_on_unbalanced_quotes() -> None:
    assert extract_argv_from_fix("Run `systemctl restart 'cache") is None


# --------------------------------------------------------------------------- #
# consume_fix_verdict — genuine submission through the REAL tool_build gate.
# --------------------------------------------------------------------------- #

@pytest.fixture()
def _tool_build_services(tmp_home: Path) -> None:
    """Bind an interactive TraceContext + a real, AUTO-trusted consent gate —
    the SAME seam tool_build_gateway tests use (ConsentPolicy's own TrustTier
    short-circuit, not a mocked gate.request()). tmp_home isolates the learned
    tool's persisted spec file under an ephemeral STACKOWL_HOME."""
    token = TraceContext.start("s-fix", interactive=True, channel="cli")
    gate = ConsequentialActionGate(ConsentPolicy(tiers={"tool_build": TrustTier.AUTO}))
    services_token = set_services(StepServices(consent_gate=gate, tool_registry=None))
    yield
    reset_services(services_token)
    TraceContext.reset(token)


@pytest.mark.asyncio
async def test_consume_fix_verdict_submits_through_real_gate(_tool_build_services: None) -> None:
    verdict = _verdict(
        skill_name="restart_cache_warmer",
        fix_pattern="Run `systemctl restart cache-warmer` to clear the stuck pool.",
    )
    outcome = await consume_fix_verdict(verdict)
    assert outcome == "submitted"
    # The REAL tool_build gate persisted the learned spec — not mocked/bypassed.
    spec_path = StackowlHome.learned_tools_dir() / "restart_cache_warmer.json"
    assert spec_path.exists()


@pytest.mark.asyncio
async def test_consume_fix_verdict_skips_when_no_literal_command(
    _tool_build_services: None,
) -> None:
    verdict = _verdict(skill_name="no_command_here")
    outcome = await consume_fix_verdict(verdict)
    assert outcome == "skipped_no_argv"
    spec_path = StackowlHome.learned_tools_dir() / "no_command_here.json"
    assert not spec_path.exists()


@pytest.mark.asyncio
async def test_consume_fix_verdict_refused_when_no_interactive_user(tmp_home: Path) -> None:
    """Genuinely unattended (scheduler-tick) context: no TraceContext bound, so
    tool_build's OWN unmodified _consent_or_refuse fails closed. This is the
    correct, load-bearing safety behavior — never let an incident handler
    auto-mint a tool with no human present."""
    verdict = _verdict(
        skill_name="restart_cache_warmer_unattended",
        fix_pattern="Run `systemctl restart cache-warmer`.",
    )
    outcome = await consume_fix_verdict(verdict)
    assert outcome.startswith("refused:")
    spec_path = StackowlHome.learned_tools_dir() / "restart_cache_warmer_unattended.json"
    assert not spec_path.exists()


# --------------------------------------------------------------------------- #
# consume_alternative_verdict — read-only consult of find_substitute.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_consume_alternative_verdict_consults_find_substitute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves MY function calls find_substitute with the verdict's capability
    and handles a positive result — decoupled from the adapter's own arg-value
    requirements (a real call with no concrete failed args, see the test
    below, honestly degrades to 'no eligible sibling' since the incident
    carries no single call's original url/path)."""
    calls: list[tuple[str, dict]] = []

    def _fake_find_substitute(failed_tool, failed_args, **kwargs):
        calls.append((failed_tool, failed_args))
        return ("pdf", {"path": "x"})

    monkeypatch.setattr(mod, "find_substitute", _fake_find_substitute)
    verdict = _verdict(capability_class="read_file", failure_class="OSError")
    outcome = await consume_alternative_verdict(verdict, tool_registry=object())  # type: ignore[arg-type]
    assert outcome == "substitute:pdf"
    assert calls == [("read_file", {})]


@pytest.mark.asyncio
async def test_consume_alternative_verdict_real_registry_no_registry_wired() -> None:
    verdict = _verdict(capability_class="read_file", failure_class="OSError")
    outcome = await consume_alternative_verdict(verdict, tool_registry=None)
    assert outcome == "no_registry"


@pytest.mark.asyncio
async def test_consume_alternative_verdict_real_find_substitute_not_bypassed() -> None:
    """Uses the REAL find_substitute + a REAL ToolRegistry (read_file/pdf share
    the "file_read" capability tag, per capability_substitution.py). No sample
    args are available from an aggregated incident, so this honestly returns
    'no_substitute' — proving the REAL function is genuinely called (not
    mocked here), not that it always succeeds."""
    reg = ToolRegistry.with_defaults()
    # Sanity: the REAL find_substitute WOULD find "pdf" given a real path.
    assert find_substitute(
        "read_file", {"path": "doc.txt"}, registry=reg,
        in_bounds=lambda _n: True, already_substituted=set(),
    ) is not None
    verdict = _verdict(capability_class="read_file", failure_class="OSError")
    outcome = await consume_alternative_verdict(verdict, tool_registry=reg)
    assert outcome == "no_substitute"


# --------------------------------------------------------------------------- #
# route_rca_verdict — dispatch + the unverified-verdict guard.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_route_rca_verdict_skips_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _boom(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(mod, "consume_fix_verdict", _boom)
    verdict = _verdict(verified=False)
    await route_rca_verdict(verdict, "fix")
    assert called is False


@pytest.mark.asyncio
async def test_route_rca_verdict_never_raises_on_consumer_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("consumer exploded")

    monkeypatch.setattr(mod, "consume_fix_verdict", _boom)
    verdict = _verdict(verified=True)
    await route_rca_verdict(verdict, "fix")  # must not raise
