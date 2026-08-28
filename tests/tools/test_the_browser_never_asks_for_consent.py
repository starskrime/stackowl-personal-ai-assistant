"""Using the browser must never stop to ask permission.

Bakir, 2026-08-28: "i do not want platfomr to ask when using browser. No access
required. otherwise ask a lot browser related permission."

MEASURED BEFORE CHANGING ANYTHING. On 2026-08-27 every browser consent prompt in
production was ``browser_eval_js`` — 13 ``[consent] policy.request: exit``, 4
``[ipc] socket consent: requesting decision``, 3 ``[telegram] consent.prompt``.
No other browser tool ever reached the prompter, so the volume Bakir is
describing has exactly one source today.

BUT FIVE BROWSER TOOLS ARE DECLARED ``consequential``, not one: ``browser_eval_js``,
``browser_upload`` and ``browser_download`` in tools.py, plus ``browse`` and
``browser_dialog`` — and those last two subclass ``Tool`` DIRECTLY, in their own
files. ``browse`` is the main browsing entry point and ``browser_dialog`` is
commented "gated on every call (always-ask)". Fixing only the three in tools.py
would have left the most-used tool of the five still prompting, which is exactly
the trap _BrowserTool's own docstring warns about for ``requires_capability``.

WHY THE EXEMPTION IS KEYED ON THE TOOLSET GROUP. Flipping five ``action_severity``
literals across three files would put one rule in five copies, and the sixth
browser tool someone adds would miss it. Every browser tool already declares
``toolset_group="browser"`` (verified across all 25 modules), so the group is the
one place that cannot be forgotten.

AND SEVERITY IS DELIBERATELY LEFT ALONE. ``action_severity`` stays "consequential"
because that is TRUE — eval_js runs arbitrary JS and download writes to disk. It
still drives the ``_audit_consequential`` rows, which keep working unchanged.
What changes is only whether a human is INTERRUPTED. Lowering severity instead
would have made the manifest lie about the action to silence a prompt, which is
the "the system knows something and says something else" defect this tree has
already paid for repeatedly.

BLAST RADIUS, because "no consent" deserves a stated reason and not a shrug:
eval_js is PAGE-scoped, and its network egress still passes the SSRF route guard
registered on ``ctx.route("**/*", make_route_guard())`` — so JS-initiated fetch is
filtered exactly like a navigation. The browser reaching a page it was asked to
reach is the capability, not an escalation of it.
"""

from __future__ import annotations

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import ConsentPolicy
from stackowl.tools.registry import ConsequentialActionGate


class _ExplodingPolicy(ConsentPolicy):
    """Any consent request at all is the failure this test exists to catch."""

    async def request(self, **kwargs: object) -> bool:  # type: ignore[override]
        raise AssertionError(
            f"the browser asked for permission: {kwargs.get('tool_name')!r}"
        )


class _FakeBrowserTool(Tool):
    def __init__(self, name: str, group: str = "browser") -> None:
        self._name = name
        self._group = group

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "a browser tool"

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
            toolset_group=self._group,
            requires_capability="browser",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["browser_eval_js", "browse", "browser_upload", "browser_download"],
)
async def test_a_consequential_browser_tool_is_never_gated(tool_name: str) -> None:
    """THE regression. All five are declared consequential; none may prompt.

    The policy raises on any request, so a prompt cannot pass as a pass.
    """
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())

    assert await gate.check(_FakeBrowserTool(tool_name)) is True


@pytest.mark.asyncio
async def test_a_NON_browser_consequential_tool_is_still_gated() -> None:
    """The control, and the reason this is an exemption rather than a hole.

    Without this, "the gate allowed it" would be indistinguishable from a gate
    that allows everything — which is how a green test hid an uncallable SSRF
    guard on 2026-08-27. ``execute_code`` prompted in the same production window
    as ``browser_eval_js`` and must keep doing so.
    """
    gate = ConsequentialActionGate(policy=_ExplodingPolicy())

    with pytest.raises(AssertionError, match="asked for permission"):
        await gate.check(_FakeBrowserTool("execute_code", group="code"))


def test_every_browser_tool_declares_the_group_the_exemption_keys_on() -> None:
    """The property that keeps this true for tools nobody has written yet.

    The exemption is only as complete as the group declaration. Five browser
    tools are consequential and TWO of them live outside tools.py, so a check
    that only walked _BrowserTool subclasses would have proven nothing about the
    two that mattered most.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    browser_dir = root / "src" / "stackowl" / "tools" / "browser"
    offenders = []
    for path in sorted(browser_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "action_severity" not in text:
            continue  # not a tool module (runtime, sessions, helpers)
        if 'toolset_group="browser"' not in text:
            offenders.append(path.name)

    assert not offenders, (
        "these declare a tool severity but not the browser toolset group, so the "
        f"consent exemption would not cover them: {offenders}"
    )


@pytest.mark.asyncio
async def test_browser_dialog_STILL_asks_because_it_is_not_an_access_decision() -> None:
    """THE LIMIT OF THE EXEMPTION, and it was found by a RED smoke test.

    The first version of this change exempted the whole browser group and turned
    tests/smoke/test_e2_s6_dialog_consent_telegram_smoke.py red. That test pins
    something REAL rather than the superseded decision, so the change was narrowed
    rather than the test rewritten.

    browser_dialog does not grant ACCESS to anything. It answers a JS dialog the
    PAGE is blocked on, so auto-accepting a confirm("Delete your account?") would
    take an irreversible action on a third-party site on the user's behalf. Its
    own docstring: "the safe default is to confirm any dialog interaction with the
    user."

    AND EXEMPTING IT WOULD HAVE BOUGHT NOTHING. Measured on 2026-08-27:
    browser_dialog prompted 0 times; browser_eval_js prompted 20. The tool that
    cost a real protection contributed nothing to the interruptions being removed.

    The discriminator is the existing _DEFAULT_ALWAYS_ASK_TOOLS registry, which
    already names browser_dialog beside execute_code and computer_use — so this
    asks one source of truth instead of keeping a second copy in the gate.
    """
    from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_TOOLS

    assert "browser_dialog" in _DEFAULT_ALWAYS_ASK_TOOLS, (
        "the exemption's discriminator is this registry — if browser_dialog "
        "leaves it, the gate silently starts auto-accepting page dialogs"
    )

    gate = ConsequentialActionGate(policy=_ExplodingPolicy())
    with pytest.raises(AssertionError, match="asked for permission"):
        await gate.check(_FakeBrowserTool("browser_dialog"))
