"""LearnedShellTool — a Tool object materialized from a LearnedToolSpec.

A learned tool is NOT model-authored code: it is a declarative spec whose
``argv_template`` runs through the SAME allowlisted shell ``create_subprocess_exec``
seam every other command uses (:func:`stackowl.tools.system.shell.run_argv`). At
call time the declared params are substituted as WHOLE argv elements
(:func:`build_argv`), so a value can never become code. ``execute`` never raises
past ``Tool.__call__`` — a bad call becomes a structured failed ToolResult.

``toolset_group`` is pinned to ``"learned"`` here (NOT author-controlled): a
learned tool joins an owl's presented set only when that owl's capability_profile
opts into the ``learned`` group.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.meta.tool_spec import (
    LearnedToolSpec,
    ToolSpecError,
    build_argv,
)
from stackowl.tools.system.shell import _TIMEOUT_CEILING_SEC, _TIMEOUT_SEC, run_argv

_LEARNED_GROUP = "learned"


class LearnedShellTool(Tool):
    """A registered Tool that runs a learned spec's argv through the shell seam."""

    def __init__(self, spec: LearnedToolSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> LearnedToolSpec:
        return self._spec

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def description(self) -> str:
        return self._spec.description

    @property
    def parameters(self) -> dict[str, object]:
        """A JSON Schema derived from the spec's declared params."""
        properties: dict[str, object] = {}
        required: list[str] = []
        for p in self._spec.params:
            properties[p.name] = {"type": p.type, "description": p.description}
            if p.required:
                required.append(p.name)
        schema: dict[str, object] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity=self._spec.action_severity,
            toolset_group=_LEARNED_GROUP,
        )

    def _timeout(self) -> float:
        """Effective timeout: the spec's request bounded by the hard ceiling."""
        requested = self._spec.timeout_sec
        if requested is None or requested <= 0:
            return _TIMEOUT_SEC
        return min(requested, _TIMEOUT_CEILING_SEC)

    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY
        log.tool.debug(
            "learned_tool.execute: entry",
            extra={"_fields": {"tool": self.name, "args": sorted(kwargs.keys())}},
        )
        # 2. DECISION — render the concrete argv from the spec + supplied args. A
        # missing required arg surfaces as a structured failed ToolResult (never a
        # raise): build_argv is the only place that can reject the call.
        try:
            argv = build_argv(self._spec, kwargs)
        except ToolSpecError as exc:
            log.tool.info(
                "learned_tool.execute: bad call — structured failure",
                extra={"_fields": {"tool": self.name, "error": str(exc)}},
            )
            return ToolResult(success=False, output="", error=str(exc), duration_ms=0)
        # 3. STEP — run via the SAME shell seam (catastrophic check + consent +
        # create_subprocess_exec + timeout). shell=False keeps values inert data.
        result = await run_argv(argv, tool_name=self.name, timeout_sec=self._timeout())
        # 4. EXIT
        log.tool.debug(
            "learned_tool.execute: exit",
            extra={"_fields": {"tool": self.name, "success": result.success}},
        )
        return result
