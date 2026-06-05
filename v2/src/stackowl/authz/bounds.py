"""BoundsSpec — the CLOSED enumeration of an owl's capability bounds (Epic 2 S1, FR33).

An owl's *bounds* are a hard, closed set of capability axes. They are distinct
from *consent* (human approval for a consequential action): bounds describe what
the owl is permitted to attempt **at all**, regardless of any human approval.

The enumeration is intentionally CLOSED — exactly these five axes exist, and a
new capability dimension must be added here deliberately (never ad-hoc). Each
axis documents WHERE it is enforced:

    * ``tools``              — enforced HERE (E2-S1) at the dispatch seam.
    * ``fs_read_roots`` /
      ``fs_write_roots``     — enforced by the workspace sandbox (Epic 3).
    * ``network``            — enforced by the host egress proxy (Epic 3).
    * ``data_owner_id`` /
      ``data_namespaces``    — enforced by tenancy ``OwnedRepository`` (already).
    * ``caps``               — enforced by the budget governor (E2-S4) and the
                               stop-policy (E2-S5).

For every axis, ``None`` means "unrestricted" — which is the model-level default
so that every existing (unbounded) owl is byte-for-byte unchanged. The owl-builder
in Epic 5 sets safe-by-construction bounds; this story only carries + enforces the
tools axis.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from stackowl.exceptions import DomainError


class NetworkRule(BaseModel):
    """A single egress allowlist entry on the ``network`` bounds axis.

    A ``None`` field is a wildcard for that dimension (any port / any scheme).
    Enforced by the host egress proxy in Epic 3 — modeled (only) here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str
    port: int | None = None
    scheme: str | None = None


class ResourceCaps(BaseModel):
    """Per-run resource ceilings on the ``caps`` bounds axis.

    All ``None`` by default (no ceiling). Enforced by the budget governor
    (E2-S4) and the stop-policy (E2-S5) — modeled (only) here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_cost_usd: float | None = None
    max_time_s: float | None = None
    max_steps: int | None = None
    max_concurrency: int | None = None


class BoundsSpec(BaseModel):
    """The closed enumeration of an owl's capability bounds (FR33).

    Attach to an owl via ``OwlAgentManifest.bounds``. ``None`` on the manifest
    (the default) means the owl is unbounded — byte-for-byte legacy behavior.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- TOOLS axis (enforced HERE, E2-S1) ---
    # Allowlist of permitted tool names. ``None`` = unrestricted (back-compat).
    #
    # FAIL-CLOSED FOOTGUN: an empty frozenset ``frozenset()`` is a *present but
    # empty* allowlist that denies ALL tools — including the discovery meta-tools
    # ``tool_search`` / ``tool_describe``. This is deliberate: bounds are
    # fail-closed and meta-tools are NOT auto-exempted. An owl-builder (Epic 5)
    # who wants the owl to be able to discover tools MUST include ``tool_search``
    # in a non-trivial allowlist; an empty set is a hard "no tools at all" owl.
    tools: frozenset[str] | None = None

    # --- FILESYSTEM axis (enforced by the workspace sandbox, Epic 3) ---
    # Workspace path roots the owl may read / write. ``None`` = unrestricted.
    fs_read_roots: tuple[str, ...] | None = None
    fs_write_roots: tuple[str, ...] | None = None

    # --- NETWORK axis (enforced by the host egress proxy, Epic 3) ---
    # Egress allowlist. ``None`` = unrestricted; an empty tuple ``()`` is the
    # explicit deny-all / zero-egress posture (a *present but empty* allowlist).
    network: tuple[NetworkRule, ...] | None = None

    # --- DATA axis (enforced by tenancy OwnedRepository, already) ---
    data_owner_id: str | None = None
    data_namespaces: tuple[str, ...] | None = None

    # --- RESOURCE CAPS axis (enforced by budget E2-S4 / stop-policy E2-S5) ---
    # NOTE the intentional asymmetry: ``caps`` defaults to ``ResourceCaps()`` (a
    # frozen all-``None`` ceiling object), NOT to ``None`` like the other axes.
    # Every BoundsSpec therefore always has a concrete (if empty) caps object —
    # the budget governor (E2-S4) never has to None-guard this axis.
    caps: ResourceCaps = ResourceCaps()

    def intersect(self, other: BoundsSpec) -> BoundsSpec:
        """Return the NARROWED bounds ``self ∩ other`` (narrowing-only contract).

        The effective bounds for a call are ``owl_bounds.intersect(task_bounds)``:
        a task envelope (E2-S2) can only TIGHTEN, never widen, an owl's bounds —
        preventing privilege escalation (FR35-adjacent). A task can REMOVE tools
        from the owl's allowlist; it can never ADD one the owl lacks.

        TOOLS axis (the only axis composed for real in S1):

            * ``None ∩ None`` → ``None``           (both unrestricted → unrestricted)
            * ``None ∩ set``  → ``set``            (a restriction narrows unrestricted)
            * ``set  ∩ None`` → ``set``            (symmetric — unrestricted never widens)
            * ``set  ∩ set``  → set intersection   (only tools BOTH permit; disjoint → ``frozenset()``)

        OTHER AXES (fs/network/data/caps) are STUBS for now: they are enforced in
        Epic 3+, so this method conservatively keeps ``self``'s values (the owl's
        own bounds). When those axes gain real composition they will narrow here
        too; until then keeping ``self`` never WIDENS the owl, preserving the
        narrowing-only guarantee. ``intersect`` is NOT yet wired into dispatch in
        S1 (owl-bounds only at the seam); E2-S2 will call it for task envelopes.
        """
        # TOOLS — narrowing intersection (None = unrestricted).
        if self.tools is None:
            tools = other.tools
        elif other.tools is None:
            tools = self.tools
        else:
            tools = self.tools & other.tools
        # OTHER AXES — stubs: keep self (the owl's own bounds) for now. Documented
        # above: this never widens the owl; real narrowing lands with Epic 3+.
        return self.model_copy(update={"tools": tools})

    def permits_tool(self, tool_name: str) -> bool:
        """Return True if ``tool_name`` is permitted by the tools axis.

        ``True`` when the tools allowlist is ``None`` (unrestricted) OR the name
        is in the allowlist. ``False`` only when an allowlist is present and the
        name is absent from it.
        """
        if self.tools is None:
            return True
        return tool_name in self.tools


class BoundsViolation(DomainError):
    """Raised/reported when an action falls outside an owl's bounds (FR33).

    Carries the offending ``axis`` (e.g. ``"tools"``) and the denied ``value``
    (e.g. the tool name) so the block can be logged + reported precisely. At the
    dispatch seam the tools-axis check returns a clean report string rather than
    raising — this exception exists for the axes (and call-sites) that prefer to
    fail loud.
    """

    def __init__(self, axis: str, value: str) -> None:
        self.axis = axis
        self.value = value
        super().__init__(f"action {value!r} is outside this owl's bounds (axis {axis!r})")
