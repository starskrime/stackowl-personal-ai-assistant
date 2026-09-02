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

    async def load_all(self, registry: ToolRegistry, db: object | None = None) -> int:
        """Register every valid learned-tool spec on disk. Returns the count.

        Self-healing: a file that fails to parse / validate / register is logged
        and skipped; this method never raises.

        AND SAYS WHICH ONES HAVE NEVER RUN, when a pool is supplied. MEASURED
        2026-09-01: learned tools have NO lifecycle at all. A skill carries
        ``lifecycle_state``, ``last_used_at``, ``n_executions``, ``enabled`` and
        ``pinned``, and can be archived, pruned and revived. A learned-tool spec
        carries ``action_severity, argv_template, description, name, params,
        spec_version, timeout_sec`` — nothing that records whether it has ever
        been invoked. The platform can CREATE tools and can never retire them.

        The consequence is already on disk: ``run_claude_code_demo`` has been
        invoked ZERO times in the whole retained history, shells out to flags
        that do not match the CLI it names, occupies a slot in the presented tool
        set, and trips a "thin tool description" WARNING on every single boot.
        It will do that forever.

        This matters NOW rather than eventually: the tool_build consent path was
        verified working earlier today, so the RCA loop — which attempted twenty
        builds and had every one refused — is about to start creating tools into
        an append-only registry.

        NOTHING IS DELETED HERE. Retiring a learned capability is the operator's
        call, not a loader's. What was missing is that the accumulation was
        INVISIBLE; usage is derived from ``task_outcomes.tool_sequence``, which
        the platform already records, rather than by adding a second store.
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
        registered_names: list[str] = []
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
                registered_names.append(spec.name)
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

        await self._report_unused(registered_names, db)
        # 4. EXIT
        log.tool.info(
            "[tools] learned_loader.load_all: exit",
            extra={"_fields": {"registered": registered}},
        )
        return registered

    async def _report_unused(self, names: list[str], db: object | None) -> None:
        """Name the learned tools that have never been invoked. Never raises.

        ONE SOURCE: this asks :func:`report_never_invoked`, which answers the same
        question for the whole registry at boot. The rule lived here first and
        covered learned tools only — the example, not the architecture — and two
        copies of it would have drifted exactly where it matters.
        """
        from stackowl.tools._infra.usage_report import report_never_invoked

        await report_never_invoked(names, db, scope="learned")

