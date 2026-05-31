"""LearnedToolLoader — reload agent-authored tool specs into the registry at boot.

Mirrors :class:`stackowl.skills.loader.SkillLoader`'s self-heal contract: scan
``learned_tools_dir()`` for ``*.json`` specs and register each as a
:class:`LearnedShellTool`. A corrupt / schema-invalid / spec-invalid file is
logged and SKIPPED — load_all NEVER raises, so one bad file can't wedge boot.

This is the persistence half of H4 ``tool_build``: a tool the agent minted once is
re-registered on every start, so it is a permanent capability.

IMPORTANT: this loader does NOT exec model-authored Python (unlike the skill
loader's ``tools/*.py`` extension path, which is deliberately avoided here — it
runs arbitrary code at boot). It only reads declarative specs; the resulting tools
run solely through the allowlisted shell argv boundary.
"""

from __future__ import annotations

import json

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.tools.meta.learned_shell_tool import LearnedShellTool
from stackowl.tools.meta.tool_spec import LearnedToolSpec, validate_spec
from stackowl.tools.registry import ToolRegistry

_SOURCE = "learned_tools"


class LearnedToolLoader:
    """Loads ``learned_tools_dir()/*.json`` specs into a ToolRegistry."""

    async def load_all(self, registry: ToolRegistry) -> int:
        """Register every valid learned-tool spec on disk. Returns the count.

        Self-healing: a file that fails to parse / validate / register is logged
        and skipped; this method never raises.
        """
        learned_dir = StackowlHome.learned_tools_dir()
        # 1. ENTRY
        log.tool.info(
            "[tools] learned_loader.load_all: entry",
            extra={"_fields": {"dir": str(learned_dir)}},
        )
        try:
            learned_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # B5 — a missing/unwritable dir is not fatal at boot
            log.tool.error(
                "[tools] learned_loader.load_all: cannot ensure dir — skipping",
                exc_info=exc,
                extra={"_fields": {"dir": str(learned_dir)}},
            )
            return 0

        registered = 0
        for spec_file in sorted(learned_dir.glob("*.json")):
            try:
                raw = json.loads(spec_file.read_text(encoding="utf-8"))
                spec = LearnedToolSpec.model_validate(raw)
                spec_err = validate_spec(spec)
                if spec_err is not None:
                    log.tool.warning(
                        "[tools] learned_loader.load_all: spec invalid — skipping",
                        extra={"_fields": {"file": spec_file.name, "error": spec_err}},
                    )
                    continue
                registry.register(LearnedShellTool(spec), source_name=_SOURCE)
                registered += 1
                log.tool.debug(
                    "[tools] learned_loader.load_all: registered",
                    extra={"_fields": {"tool": spec.name, "file": spec_file.name}},
                )
            except Exception as exc:  # B5 — one bad file never wedges boot
                log.tool.error(
                    "[tools] learned_loader.load_all: failed to load spec — skipping",
                    exc_info=exc,
                    extra={"_fields": {"file": spec_file.name}},
                )
                continue

        # 4. EXIT
        log.tool.info(
            "[tools] learned_loader.load_all: exit",
            extra={"_fields": {"registered": registered}},
        )
        return registered
