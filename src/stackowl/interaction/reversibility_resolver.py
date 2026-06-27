"""ADR-3 — the one ReversibilityResolver authority.

Clarify / consent / cost-pause / router each re-decide "ask the human or act?" with no
shared signal, so each defaults to the passive "ask" — structurally capping proactivity.
This module introduces the single authority every gate delegates to: given a declared
``Reversibility`` signal plus a stakes estimate, ``resolve`` answers ACT_WITH_ASSUMPTION
(act on the most-likely interpretation, state the assumption, keep the undo handle) or ASK
(park on the human). It parks **only** for an irreversible-or-high-stakes-or-genuinely-
ambiguous decision; "ask" becomes the exception gated on declared irreversibility, not the
default.

Nothing is removed: each gate keeps its own park path and still parks the genuinely
irreversible. The resolver generalizes the per-gate ``_resolve_default`` rule
(``ClarifyGateway._resolve_default``, the consent ``reversible`` tier, the cost-pause
block-threshold, the objective driver's ``_park_is_irreversible``) into one policy. The
flag ``settings.reversibility_resolver`` defaults OFF ⇒ every gate runs its pre-ADR inline
rule, byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stackowl.infra.observability import log

# --------------------------------------------------------------- declared signal


@dataclass(frozen=True)
class Reversibility:
    """The declared reversibility of an action — a tri-state value.

    Construct via the classmethods rather than the raw ``kind``:

    * :meth:`reversible` (optionally with ``via`` = an undo-handle name such as
      ``"undo_write"``) — the action can be undone / a wrong assumption can be corrected.
    * :meth:`irreversible` — the action cannot be taken back (a sent message, a delete).
    * :meth:`unknown` — no declared opinion; the resolver treats it exactly as the pre-ADR
      behavior (so an undeclared action is byte-identical).
    """

    kind: str = "unknown"
    reversible_via: str | None = None

    @classmethod
    def reversible(cls, via: str | None = None) -> Reversibility:
        return cls(kind="reversible", reversible_via=via)

    @classmethod
    def irreversible(cls) -> Reversibility:
        return cls(kind="irreversible")

    @classmethod
    def unknown(cls) -> Reversibility:
        return cls(kind="unknown")

    @property
    def is_reversible(self) -> bool:
        return self.kind == "reversible"

    @property
    def is_irreversible(self) -> bool:
        return self.kind == "irreversible"

    @property
    def is_unknown(self) -> bool:
        return self.kind == "unknown"


# ------------------------------------------------------------- decision / verdict


@dataclass(frozen=True)
class Decision:
    """A single "ask the human or act?" decision presented to the resolver.

    ``choices``/``default`` describe the most-likely-interpretation surface (a clarify
    menu, a consent yes/no, a cost continue/stop). ``interactive`` is the fail-closed
    flag every gate already carries: a non-interactive context (cron/parliament/a2a) has
    no human to answer, so the resolver may only *downgrade* park→act there, never the
    reverse.
    """

    reversibility: Reversibility
    high_stakes: bool = False
    choices: tuple[str, ...] = ()
    default: str | None = None
    interactive: bool = True
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    """The resolver's answer: ``act`` (with a stated ``assumption`` + retained
    ``undo_handle``) or park (``act is False``)."""

    act: bool
    assumption: str | None = None
    undo_handle: str | None = None


# ------------------------------------------------------------------- the authority


class ReversibilityResolver:
    """The one authority that answers ACT_WITH_ASSUMPTION vs ASK for every gate."""

    def resolve(self, decision: Decision) -> Verdict:
        """Resolve a decision. Parks (``act=False``) for an irreversible or high-stakes
        action, or when there is no safe most-likely interpretation; otherwise acts on
        that interpretation, stating the assumption and retaining the undo handle.

        Total / never raises — a gate must be able to delegate without risking the turn.
        """
        rev = decision.reversibility
        # Irreversible or high-stakes ⇒ always park (keep the human in the loop).
        if self.must_reach_user(decision):
            verdict = Verdict(act=False)
            log.gateway.debug(
                "reversibility.resolve: park (irreversible/high-stakes)",
                extra={
                    "_fields": {
                        "kind": rev.kind,
                        "high_stakes": decision.high_stakes,
                    }
                },
            )
            return verdict
        # Reversible or unknown ⇒ act IFF a clear most-likely interpretation exists.
        # ``unknown`` is resolved exactly as the pre-ADR ``_resolve_default`` (which had
        # no reversibility signal at all), so flag-on is a strict generalization.
        assumption = self._most_likely(decision)
        if assumption is None:
            log.gateway.debug(
                "reversibility.resolve: park (no safe default)",
                extra={"_fields": {"kind": rev.kind, "choices": len(decision.choices)}},
            )
            return Verdict(act=False)
        verdict = Verdict(
            act=True, assumption=assumption, undo_handle=rev.reversible_via
        )
        log.gateway.debug(
            "reversibility.resolve: act on assumption",
            extra={
                "_fields": {
                    "assumption": assumption,
                    "undo_handle": rev.reversible_via,
                }
            },
        )
        return verdict

    @staticmethod
    def must_reach_user(decision: Decision) -> bool:
        """True iff the decision is irreversible or high-stakes and so MUST reach the
        human — it cannot be resolved by acting on an assumption, regardless of any
        available default. This is the binary reversibility/stakes gate, factored out so a
        gate that only needs the escalate-or-not classification (e.g. the objective
        driver's park) can delegate to the same authority as :meth:`resolve`."""
        return decision.high_stakes or decision.reversibility.is_irreversible

    @staticmethod
    def _most_likely(decision: Decision) -> str | None:
        """The most-likely interpretation, or ``None`` when there is no safe default.

        Mirrors the pre-ADR ``ClarifyGateway._resolve_default`` rule exactly:

        * exactly one offered choice ⇒ that choice (no decision to make);
        * an explicit ``default`` consistent with the menu (no menu, or default in it)
          ⇒ that default;
        * otherwise ``None`` — a real multi-choice decision with no default parks.
        """
        if len(decision.choices) == 1:
            return decision.choices[0]
        if decision.default is not None and (
            not decision.choices or decision.default in decision.choices
        ):
            return decision.default
        return None


# --------------------------------------------------------------------- flag reader


def reversibility_resolver_enabled() -> bool:
    """Read the ADR-3 ``reversibility_resolver`` flag. Fail-safe to ``False`` (the
    byte-identical default) on any config error — a gate delegating to the resolver must
    never break by failing to read a flag."""
    try:
        from stackowl.config.settings import Settings

        return bool(Settings().reversibility_resolver)
    except Exception as exc:  # noqa: BLE001 — flag read must never raise into a turn
        log.gateway.debug(
            "reversibility_resolver_enabled: could not read flag — treating OFF",
            extra={"_fields": {"err": type(exc).__name__}},
        )
        return False


__all__ = [
    "Decision",
    "Reversibility",
    "ReversibilityResolver",
    "Verdict",
    "reversibility_resolver_enabled",
]
