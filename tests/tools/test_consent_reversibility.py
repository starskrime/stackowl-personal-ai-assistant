"""F-27 — reversibility-aware consent.

A consequential action whose effect is locally owned and rollback-able
(``commit_coupling="transactional"`` — atomic with our own ledger) is
low-blast-radius REVERSIBLE: the policy may auto-allow-with-undo instead of
forcing a prompt every time. An irreversible consequential action (an
``unconfirmed`` remote send, or one with no coupling declared) keeps the
ALWAYS_ASK default — when in doubt, ask. An always-ask / dangerous-category
tool is NEVER relaxed by reversibility.
"""

from __future__ import annotations

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import (
    ConsentPolicy,
    ConsentRequest,
    ConsentScope,
)
from stackowl.tools.registry import ConsequentialActionGate


class _RecordingPrompter:
    """Prompter that records whether it was asked, and returns a fixed scope."""

    def __init__(self, scope: ConsentScope = ConsentScope.ONCE) -> None:
        self._scope = scope
        self.asked = False
        self.last_req: ConsentRequest | None = None

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.asked = True
        self.last_req = req
        return self._scope


class _Consequential(Tool):
    def __init__(
        self,
        name: str,
        *,
        commit_coupling: str | None = None,
        consent_category: str | None = None,
    ) -> None:
        self._name = name
        self._coupling = commit_coupling
        self._category = consent_category

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "does a consequential thing"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            consent_category=self._category,
            commit_coupling=self._coupling,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:  # pragma: no cover
        return ToolResult(success=True, output="", duration_ms=1.0)


async def test_reversible_transactional_tool_auto_allows_without_prompt() -> None:
    prompter = _RecordingPrompter(scope=ConsentScope.DENY)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    tool = _Consequential("rev_tool", commit_coupling="transactional")

    allowed = await gate.check(tool, channel="cli", session_id="s1")

    assert allowed is True
    assert prompter.asked is False  # auto-allowed-with-undo, never prompted


async def test_irreversible_unconfirmed_tool_still_prompts() -> None:
    prompter = _RecordingPrompter(scope=ConsentScope.DENY)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    tool = _Consequential("send", commit_coupling="unconfirmed")

    allowed = await gate.check(tool, channel="cli", session_id="s1")

    assert allowed is False
    assert prompter.asked is True  # no reversibility ⇒ ALWAYS_ASK


async def test_undeclared_coupling_tool_still_prompts() -> None:
    prompter = _RecordingPrompter(scope=ConsentScope.DENY)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    tool = _Consequential("mystery")  # commit_coupling=None ⇒ fail-safe to ask

    allowed = await gate.check(tool, channel="cli", session_id="s1")

    assert allowed is False
    assert prompter.asked is True


async def test_dangerous_category_tool_never_relaxed_by_reversibility() -> None:
    # Even if locally transactional, a destructive-category tool is on the
    # always-ask exclusion list and must still prompt.
    prompter = _RecordingPrompter(scope=ConsentScope.DENY)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    tool = _Consequential(
        "wipe", commit_coupling="transactional", consent_category="destructive"
    )

    allowed = await gate.check(tool, channel="cli", session_id="s1")

    assert allowed is False
    assert prompter.asked is True


async def test_always_ask_tool_name_never_relaxed_by_reversibility() -> None:
    # execute_code is on the default always-ask tool list.
    prompter = _RecordingPrompter(scope=ConsentScope.DENY)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    tool = _Consequential("execute_code", commit_coupling="transactional")

    allowed = await gate.check(tool, channel="cli", session_id="s1")

    assert allowed is False
    assert prompter.asked is True


async def test_policy_request_default_reversible_false_is_unchanged() -> None:
    # The reversible signal defaults False on the policy seam → byte-identical
    # to the historical prompt-every-time behavior.
    prompter = _RecordingPrompter(scope=ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter)

    allowed = await policy.request(
        tool_name="x", channel="cli", session_id="s1"
    )

    assert allowed is True
    assert prompter.asked is True  # had to ask — no reversibility passed
