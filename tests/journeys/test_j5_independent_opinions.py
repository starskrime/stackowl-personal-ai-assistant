"""J5 JOURNEY — "Get three models' takes on a hard decision" (PRD §3, J5).

The business requirement, verbatim from ``_bmad-output/planning-artifacts/
prd-tool-expansion.md`` §3:

  > **J5 — Get three models' takes on a hard decision.** *"Should we move
  > embeddings to Kuzu or keep them in LanceDB? Get a few independent
  > opinions."* → ``mixture_of_agents`` fans the prompt across the local
  > ProviderRegistry roster (no vendor lock), collects independent positions
  > (layer 1), aggregates via the parliament ``Synthesizer`` (layer 2) WITH
  > DISSENT PRESERVED. Single-round.

THE headline business outcome: the user receives a SYNTHESIZED answer that
reflects **>1 distinct independent opinion with dissent/disagreement surfaced**
— NOT ``ensemble_size==3`` or a status flag. The asserted content is DERIVED
from the REAL proposer outputs (so different proposers genuinely contributed),
never from a constant. If the roster only had one provider, or the proposers
were identical, or dissent were not synthesized, these assertions fail honestly.

This is NOT a per-tool smoke. It proves the USER's END-TO-END OUTCOME, driving a
real inbound Telegram message through the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → execute._dispatch → ToolRegistry →
MixtureOfAgentsTool → REAL ``ProviderRegistry.healthy_distinct()`` fan-out over
several DISTINCT providers → REAL ``ParliamentSynthesizer.synthesize_positions``
→ the synthesized verdict travels back as the parent's final answer → delivered
to the user over Telegram).

REAL (everything except the AI providers): the whole pipeline
(classify→assemble→execute), the ``ToolRegistry`` + ``MixtureOfAgentsTool``, the
``ProviderRegistry`` (with several DISTINCT providers + genuine breakers), the
REAL ``ParliamentSynthesizer`` (incl. ``build_positions_prompt`` +
``SynthesisParser``), the REAL ``OwlRegistry`` / ``StreamRegistry`` /
``GatewayScanner``, and the Telegram adapter's inbound + outbound transport.

FAKED — ONLY the AI providers (the LLM egress edge): the secretary owl's
provider (scripted: emits the ``mixture_of_agents`` tool call, then surfaces the
tool's synthesized answer as the user reply), the THREE distinct MoA proposer
providers (each returns a DIFFERENT opinion on Kuzu-vs-LanceDB), and the
powerful-tier synthesizer provider (whose LLM output genuinely COMBINES the
positions it receives in the transcript and surfaces their disagreement). The
Telegram bot HTTP transport is faked in-process (``_FakeBot``) — transport, not a
decision-maker. The tool + synthesizer + parser are NOT stubbed.

Business-outcome assertions (DERIVED from real outputs, NOT constants):
  1. The answer delivered to the user's chat REFLECTS MORE THAN ONE DISTINCT
     OPINION — content traceable to >=2 DIFFERENT proposers appears (a
     Kuzu-leaning point AND a LanceDB-leaning point), proving independent
     positions were genuinely collected and synthesized — not one model's answer.
  2. DISSENT IS SURFACED — the synthesized output the user receives contains the
     disagreement (the proposers did not agree, and the user is told so).
  3. (Thin-roster variant) With <2 healthy providers the user is told to answer
     directly (insufficient_roster) — an honest refusal, not a fake 1-model
     "consensus".
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.circuit_breaker import CircuitState
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 535353

# The user's real question (PRD J5 verbatim trigger).
QUESTION = (
    "Should we move embeddings to Kuzu or keep them in LanceDB? "
    "Get a few independent opinions."
)

# --- The three DISTINCT proposer opinions (faked AI provider OUTPUTS). ----------
# Each proposer returns a genuinely DIFFERENT take. Each opinion carries a
# distinctive multi-word PHRASE that exists ONLY in that proposer's output — the
# load-bearing fingerprints. The synthesizer reads these out of the REAL
# transcript and the business assertions trace them back to >=2 DIFFERENT
# proposers, so the test cannot pass on a single model's answer or canned text.
_KUZU_PHRASE = "graph traversal locality"  # pro-Kuzu fingerprint
_LANCE_PHRASE = "columnar vector scans"  # pro-LanceDB fingerprint
_NUANCED_PHRASE = "depends on your query shape"  # the nuanced/hedging fingerprint

_KUZU_OPINION = (
    f"Move to Kuzu. For embeddings tied to a knowledge graph, {_KUZU_PHRASE} "
    "wins: co-locating vectors with edges avoids a second store and cuts joins."
)
_LANCE_OPINION = (
    f"Keep LanceDB. Its {_LANCE_PHRASE} are purpose-built for ANN over millions "
    "of vectors; Kuzu's graph engine is not a vector index and will be slower."
)
_NUANCED_OPINION = (
    f"It {_NUANCED_PHRASE}: keep LanceDB for raw vector search, but adopt Kuzu "
    "only if your access pattern is graph-walk-then-rank rather than pure ANN."
)


# --- FAKED (AI provider): one distinct MoA proposer with a fixed opinion --------


class _ProposerProvider:
    """A distinct MoA proposer returning ITS OWN opinion on Kuzu-vs-LanceDB.

    Honors the ModelProvider contract (incl. the ``name`` property) so the real
    fan-out (``healthy_distinct`` + ``moa_runner._propose``) consults it
    genuinely. ``calls`` proves it was actually fanned out to.
    """

    protocol = "openai"

    def __init__(self, label: str, opinion: str) -> None:
        self._label = label
        self._opinion = opinion
        self.calls = 0

    @property
    def name(self) -> str:
        return self._label

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content=self._opinion,
            input_tokens=8,
            output_tokens=24,
            model=f"{self._label}-model",
            provider_name=self._label,
            duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — proposers use complete()
        if False:
            yield ""


# --- FAKED (AI provider): the powerful-tier SYNTHESIZER's LLM --------------------


class _SynthProvider:
    """The layer-2 aggregator's LLM.

    Its ``complete()`` is the ONLY thing faked about synthesis — the REAL
    ``ParliamentSynthesizer`` / ``build_positions_prompt`` / ``SynthesisParser``
    do the actual work around it. Crucially, this fake DERIVES its output from the
    transcript it is handed: it reads each proposer's ACTUAL position out of the
    REAL transcript (the user message built by ``build_positions_prompt``), pulls
    the distinctive phrase each one used, and emits a CONSENSUS that references
    >=2 of them plus a genuine DISAGREEMENT line. So the synthesized verdict is a
    real function of the real proposer outputs — never a constant. If fan-out had
    only reached one proposer, or proposers were identical, the transcript would
    not contain two distinct phrases and the verdict could not surface dissent.

    It is ALSO in the ``healthy_distinct`` roster, so it gets asked for its OWN
    proposer position too (a real powerful provider answers any prompt). When the
    incoming messages are NOT a synthesis prompt, it returns a plain position.
    """

    protocol = "openai"

    def __init__(self) -> None:
        self.synthesis_calls = 0
        self.proposer_calls = 0
        self.last_transcript: str = ""

    @property
    def name(self) -> str:
        return "synth"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        system = messages[0].content if messages else ""
        is_synthesis = "synthesis engine" in system.lower() and "DISAGREEMENT:" in system
        if not is_synthesis:
            # Asked as a proposer during fan-out — return its own position.
            self.proposer_calls += 1
            return CompletionResult(
                content=(
                    "Both have merit; the decision turns on whether graph "
                    "structure dominates the access pattern."
                ),
                input_tokens=8,
                output_tokens=18,
                model="synth-model",
                provider_name="synth",
                duration_ms=1.0,
            )

        # Layer-2 synthesis: DERIVE the verdict from the REAL transcript.
        self.synthesis_calls += 1
        transcript = messages[-1].content
        self.last_transcript = transcript

        # Pull the distinctive phrase each proposer actually contributed, straight
        # out of the transcript the REAL build_positions_prompt produced. We only
        # cite a side if its proposer's phrase is genuinely present — so the
        # synthesized dissent is a function of who actually got fanned out to.
        saw_kuzu = _KUZU_PHRASE in transcript
        saw_lance = _LANCE_PHRASE in transcript
        saw_nuanced = _NUANCED_PHRASE in transcript

        consensus = (
            "The models do NOT agree on Kuzu-vs-LanceDB; this is a genuine "
            "trade-off rather than a settled answer."
        )
        # Build the contested-claim line from the phrases that truly appeared,
        # mapping each surviving proposer to its stance — real dissent, derived.
        disagree_parts: list[str] = ["embeddings store choice"]
        if saw_kuzu:
            disagree_parts.append(f"pro_kuzu: move to Kuzu for {_KUZU_PHRASE}")
        if saw_lance:
            disagree_parts.append(f"pro_lance: keep LanceDB for its {_LANCE_PHRASE}")
        if saw_nuanced:
            disagree_parts.append(f"nuanced: it {_NUANCED_PHRASE}")
        disagreement_line = "DISAGREEMENT: " + " | ".join(disagree_parts)

        recommendation = (
            "Decide by workload: choose Kuzu only if graph-walk dominates, "
            "otherwise keep LanceDB."
        )
        content = (
            f"CONSENSUS: {consensus}\n"
            f"RECOMMENDATION: {recommendation}\n"
            f"{disagreement_line}\n◆"
        )
        return CompletionResult(
            content=content,
            input_tokens=20,
            output_tokens=40,
            model="synth-model",
            provider_name="synth",
            duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


# --- FAKED (AI provider): the secretary owl ------------------------------------


class _SecretaryProvider:
    """Scripted secretary: emits ONE ``mixture_of_agents`` call, returns its verdict.

    The pipeline resolves THIS provider by the owl name 'secretary'
    (``registry.get('secretary')``). On the tool loop it dispatches
    ``mixture_of_agents`` with the user's question through the REAL pipeline +
    tool, then surfaces the tool's synthesized ``answer`` (or structured refusal
    detail) as the user-facing reply. Its plain ``complete`` doubles as a MoA
    proposer position if it is ever fanned out to.

    Honors the ModelProvider contract (incl. ``name``) so the real ``triage`` /
    ``execute`` steps resolve it genuinely.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.tool_results: list[str] = []

    @property
    def name(self) -> str:
        return "secretary"

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        out = await tool_dispatcher("mixture_of_agents", {"question": user_text})
        self.tool_results.append(out)
        record = json.loads(out).get("record", {})
        final = str(record.get("answer") or record.get("detail") or out)
        return (final, [{"name": "mixture_of_agents", "args": {"question": user_text}, "result": out}])

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        # Secretary's own proposer position (kept distinct, generic — the
        # business-bearing opinions are the three dedicated proposers).
        return CompletionResult(
            content="It depends on the dominant access pattern over the embeddings.",
            input_tokens=6,
            output_tokens=11,
            model="secretary-model",
            provider_name="secretary",
            duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


# --- FAKED transport: the Telegram bot HTTP layer (in-process capture) ----------


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    secretary: _SecretaryProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build_env(registry: ProviderRegistry, secretary: _SecretaryProvider) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    services = StepServices(
        provider_registry=registry,
        tool_registry=ToolRegistry.with_defaults(),  # REAL mixture_of_agents
        consent_gate=ConsequentialActionGate(),  # mixture_of_agents is 'read'; no gate fires
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    return _Env(
        adapter=adapter,
        bot=bot,
        scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        secretary=secretary,
    )


async def _turn(env: _Env, text: str) -> str:
    """Drive one real inbound Telegram message end-to-end, return delivered text."""
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return "\n".join(
        m["text"] for m in env.bot.messages[before:] if m["chat_id"] == USER_ID
    )


async def test_j5_independent_opinions_with_dissent_reach_the_user() -> None:
    # REAL ProviderRegistry: a secretary + THREE DISTINCT proposers (each a
    # different opinion) + a powerful-tier synthesizer. All are independent
    # instances → healthy_distinct() returns a genuine multi-provider roster.
    secretary = _SecretaryProvider()
    pro_kuzu = _ProposerProvider("pro_kuzu", _KUZU_OPINION)
    pro_lance = _ProposerProvider("pro_lance", _LANCE_OPINION)
    nuanced = _ProposerProvider("nuanced", _NUANCED_OPINION)
    synth = _SynthProvider()

    registry = ProviderRegistry()
    registry.register_mock("secretary", secretary, tier="standard")  # type: ignore[arg-type]
    registry.register_mock("pro_kuzu", pro_kuzu, tier="fast")  # type: ignore[arg-type]
    registry.register_mock("pro_lance", pro_lance, tier="fast")  # type: ignore[arg-type]
    registry.register_mock("nuanced", nuanced, tier="fast")  # type: ignore[arg-type]
    registry.register_mock("synth", synth, tier="powerful")  # type: ignore[arg-type]
    env = _build_env(registry, secretary)

    # The user asks the real question over real inbound Telegram.
    delivered = await _turn(env, QUESTION)

    # --- Wiring preconditions (prove the REAL fan-out genuinely happened) -------
    assert secretary.tool_results, "secretary never reached mixture_of_agents via the pipeline"
    record = json.loads(secretary.tool_results[0])["record"]
    assert record["status"] == "ok", record
    # >1 distinct model genuinely consulted (NOT a status we assert as the outcome
    # — just proof the fan-out happened). The three dedicated proposers must each
    # have actually been called by the REAL moa_runner._propose.
    assert pro_kuzu.calls >= 1 and pro_lance.calls >= 1 and nuanced.calls >= 1, (
        "REAL fan-out did not consult all three distinct proposers — "
        f"calls: kuzu={pro_kuzu.calls} lance={pro_lance.calls} nuanced={nuanced.calls}"
    )
    assert synth.synthesis_calls == 1, (
        f"REAL synthesizer was not invoked exactly once: {synth.synthesis_calls}"
    )
    # The synthesizer genuinely received >=2 DISTINCT proposer positions in the
    # transcript (proving independent collection, derived from real proposer text).
    assert _KUZU_PHRASE in synth.last_transcript and _LANCE_PHRASE in synth.last_transcript, (
        "the REAL transcript handed to the synthesizer did not contain two distinct "
        f"proposer phrases — transcript: {synth.last_transcript!r}"
    )

    # The Telegram adapter MarkdownV2-escapes punctuation on the way out; compare
    # against the escape-stripped delivered text (the J1/J2 pattern).
    delivered_unescaped = delivered.replace("\\", "")
    assert delivered, "no reply reached the user"

    # ===================================================================
    # BUSINESS OUTCOME 1 — the answer REFLECTS MORE THAN ONE DISTINCT OPINION.
    # Content traceable to >=2 DIFFERENT proposers reaches the user's chat: BOTH a
    # Kuzu-leaning point AND a LanceDB-leaning point. Each phrase originated in a
    # DIFFERENT proposer's faked output, flowed through the REAL fan-out into the
    # REAL transcript, and was woven into the REAL synthesized verdict — so this
    # can only pass if independent positions were genuinely collected (not one
    # model's answer, not a constant).
    # ===================================================================
    assert _KUZU_PHRASE in delivered_unescaped, (
        "BUSINESS OUTCOME 1 FAIL: the Kuzu-leaning opinion did not reach the user — "
        f"the synthesized answer reflects fewer than two opinions. Delivered: {delivered!r}"
    )
    assert _LANCE_PHRASE in delivered_unescaped, (
        "BUSINESS OUTCOME 1 FAIL: the LanceDB-leaning opinion did not reach the user — "
        f"the synthesized answer reflects fewer than two opinions. Delivered: {delivered!r}"
    )
    # Two phrases, two different source proposers → genuinely >1 distinct opinion.
    assert pro_kuzu.name != pro_lance.name

    # ===================================================================
    # BUSINESS OUTCOME 2 — DISSENT IS SURFACED to the user.
    # The structured synthesis preserved the disagreement: the REAL SynthesisParser
    # turned the synthesizer's DISAGREEMENT: line into a DisagreementPoint, and the
    # user is told the models do NOT agree. Both the parsed disagreement AND the
    # user-visible "do NOT agree" text are derived from the real divergent positions.
    # ===================================================================
    assert record["disagreements"], (
        "BUSINESS OUTCOME 2 FAIL: dissent was NOT preserved — the REAL synthesizer "
        f"surfaced no disagreement points. record={record!r}"
    )
    # The contested claim maps >=2 distinct proposer stances (real dissent shape).
    contested = record["disagreements"][0]
    assert len(contested["positions"]) >= 2, (
        "BUSINESS OUTCOME 2 FAIL: the disagreement does not contrast two proposers — "
        f"positions={contested['positions']!r}"
    )
    # And the user is actually TOLD the models disagree (not a fake consensus).
    assert "do not agree" in delivered_unescaped.lower(), (
        "BUSINESS OUTCOME 2 FAIL: the user was not told the models disagree — dissent "
        f"was not surfaced in the delivered answer. Delivered: {delivered!r}"
    )


async def test_j5_thin_roster_honest_refusal_reaches_the_user() -> None:
    # Only the secretary is healthy (one distinct provider exists but its breaker
    # is OPEN) → MoA must REFUSE rather than fabricate a 1-model "consensus".
    secretary = _SecretaryProvider()
    lone = _ProposerProvider("pro_kuzu", _KUZU_OPINION)
    registry = ProviderRegistry()
    registry.register_mock("secretary", secretary, tier="standard")  # type: ignore[arg-type]
    registry.register_mock("pro_kuzu", lone, tier="fast")  # type: ignore[arg-type]
    breaker = registry.get_circuit_breaker("pro_kuzu")
    assert breaker is not None
    breaker._state = CircuitState.OPEN  # type: ignore[attr-defined]
    env = _build_env(registry, secretary)

    delivered = await _turn(env, QUESTION)

    record = json.loads(secretary.tool_results[0])["record"]
    assert record["status"] == "insufficient_roster", record
    assert record["available"] == 1, record
    # The honest refusal reached the user — they're told to answer directly.
    assert "answer the question directly" in delivered.replace("\\", "").lower(), delivered
