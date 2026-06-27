"""AcceptanceAuthority — the ONE authority that answers "did the effect happen?"

ADR-1 (keystone). "Success" in StackOwl was asserted by the actor (``returncode==0``,
"backend returned non-str", "no exception") and re-decided by ≥6 disjoint proxies. This
module introduces the single decider those proxies delegate to:

* :class:`PostCondition` — a typed, OBSERVABLE assertion about reality after an action
  (:class:`NonEmptyText`, :class:`ArtifactFresh`, :class:`HttpOk`, :class:`DeliveryAck`,
  :class:`Custom`). An action declares one the way it declares ``capability_tag`` today.
* :class:`AcceptanceAuthority` — ``observe(post_condition, …) -> Verdict`` runs the probe
  against reality, distinct from the actor. The actor never sets its own success.
* :class:`Verdict` — tri-state (``accepted: True | False | None`` + reason + evidence),
  mirroring ``ToolResult.verified`` and :class:`~stackowl.pipeline.acceptance.AcceptanceVerdict`.

The unification (nothing removed): the authority's verdict is written into
``ToolResult.verified``; every proxy already READS ``verified`` (via
:func:`~stackowl.tools.verification.is_trustworthy_success` and the ledger's
``is_effectful_failure``), so routing the writer through one authority makes all of them
delegate without changing what they read. The per-tool ``verify()`` and the goal-level
:class:`~stackowl.pipeline.acceptance.AcceptanceChecker` become two callers of this one
authority rather than two independent authorities.

**Invariant.** ``success ⟺ measured`` — a declared effect is a trustworthy success only
with a ``Verdict(accepted=True)`` from an observation distinct from the actor. A tool that
self-stamps ``verified=True`` against a failing post-condition is ignored.

General and vendor-neutral: every probe is an objective observation of reality (filesystem
freshness, HTTP status class, non-empty text, a delivery ack, or an injected callable). No
keyword/language lists, no per-vendor logic. Never raises — an inability to observe yields
``None`` (no opinion), never a fabricated failure that would flip a real success.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from stackowl.infra.observability import log
from stackowl.pipeline import acceptance as _acceptance
from stackowl.pipeline.acceptance import HttpProber, _default_http_prober
from stackowl.tools.verification import verify_artifact

# --- PostCondition family ------------------------------------------------------
# Each is a standalone frozen value (no inheritance ordering pitfalls); the authority
# dispatches on type. ``kind`` is a stable semantic label for tracing/evidence — not a
# user-facing or language-specific string.


@dataclass(frozen=True)
class NoPostCondition:
    """No declared effect to observe ⇒ the authority has no opinion (byte-identical:
    the caller falls back to its prior ``success``/``verified`` signal). The explicit
    default so "nothing declared" is a value, not a ``None`` special-case at every site."""

    kind: str = "none"


@dataclass(frozen=True)
class NonEmptyText:
    """The result's output text must be non-empty (non-whitespace). Measures the
    empty-as-success class: a provider that "returned non-str" (F-20/F-23) or an MCP
    call that returned an empty body (F-82/F-83) reported success with nothing real."""

    kind: str = "non_empty_text"


@dataclass(frozen=True)
class ArtifactFresh:
    """A fresh, non-empty artifact must exist. Two modes (the file kind,
    F-31/F-33/F-34, and the goal-level declared-output kind the AcceptanceChecker owns):

    * ``path`` set — a specific file, observed via
      :func:`~stackowl.tools.verification.verify_artifact` (existence + freshness +
      optional magic-byte ``expect_kind``).
    * ``artifact_dir`` set (``path`` None) — ANY fresh non-empty file under a declared
      directory, observed via the recursive bounded scan the goal-level checker uses.
      ``artifact_dir`` is resolved under the workspace when relative."""

    path: str | None = None
    expect_kind: str | None = None
    artifact_dir: str | None = None
    kind: str = "artifact_fresh"


@dataclass(frozen=True)
class HttpOk:
    """A declared endpoint must answer 2xx/3xx — observed via an injectable prober
    (the network re-probe kind, F-32 / deploy / upload-URL effects)."""

    url: str = ""
    kind: str = "http_ok"


@dataclass(frozen=True)
class DeliveryAck:
    """A message/file delivery must be ACKed by the transport (F-29/F-30). ``acked`` is
    tri-state: ``True`` (transport confirmed), ``False`` (transport refused/failed),
    ``None`` (lossy boundary — no confirmation possible, so no opinion). The actor
    passes the transport's own ack, never its intention to send."""

    acked: bool | None = None
    channel: str | None = None
    kind: str = "delivery_ack"


@dataclass(frozen=True)
class Custom:
    """Escape hatch / judge-of-last-resort: an injected probe returning tri-state. Used
    only when no objective post-condition fits (the ADR forbids a draft-reading LLM judge
    as the DEFAULT — it is representation, not observation — but allows it here, last)."""

    probe: Callable[[], bool | None] | None = None
    label: str = "custom"
    kind: str = "custom"


#: The declared post-condition for an action: one of the family, or ``None`` (≡ no opinion).
PostCondition = (
    NoPostCondition | NonEmptyText | ArtifactFresh | HttpOk | DeliveryAck | Custom
)


@dataclass(frozen=True)
class Verdict:
    """The authority's decision about a declared post-condition.

    * ``accepted is None`` — no opinion (nothing declared, or reality could not be
      observed). The caller falls back to its prior signal — byte-identical.
    * ``accepted is True`` — the post-condition was OBSERVED in reality.
    * ``accepted is False`` — the post-condition was declared and reality REFUTED it
      (the action claimed an effect it did not produce). Never fabricated.

    ``unobservable`` distinguishes "could not observe a DECLARED effect" (a soft-fail
    signal a recovery layer may treat as retry-worthy) from "nothing declared" — both
    surface as ``accepted is None`` but mean different things (mirrors
    :class:`~stackowl.pipeline.acceptance.AcceptanceVerdict`). ``evidence`` carries the
    observed facts (status, length, path) for the decision ledger (ADR-7 / F-1)."""

    accepted: bool | None
    reason: str
    evidence: Mapping[str, object] = field(default_factory=dict)
    unobservable: bool = False

    @property
    def refuted(self) -> bool:
        """Reality observed and contradicted the claim — the only hard-fail state."""
        return self.accepted is False


def final_verified(
    *, success: bool, verified: bool | None, verdict: Verdict | None
) -> bool | None:
    """Collapse the authority's verdict and the tool's self-report into the ONE
    ``verified`` signal every proxy reads.

    The authority SUPERSEDES the actor: when it has an opinion (``accepted`` is not
    ``None``), that opinion is the truth — a self-stamped ``verified=True`` cannot
    launder a refuted post-condition. When the authority has no opinion, the prior
    ``verified`` stands (byte-identical). ``success`` is accepted for symmetry with
    :func:`~stackowl.tools.verification.is_trustworthy_success`, which the caller then
    applies to the returned value.
    """
    if verdict is not None and verdict.accepted is not None:
        return verdict.accepted
    return verified


class AcceptanceAuthority:
    """Observe a declared :class:`PostCondition` against reality and return a
    :class:`Verdict`. Stateless, pure, and total (never raises). ``http_prober`` is
    injectable so the network kind is unit-testable without touching the wire; it
    defaults to the same short-timeout HEAD prober the goal-level checker uses."""

    def __init__(self, *, http_prober: HttpProber | None = None) -> None:
        self._http_prober: HttpProber = http_prober or _default_http_prober

    def observe(
        self,
        post_condition: PostCondition | None,
        *,
        success: bool,
        verified: bool | None = None,
        output: str = "",
        started_at: float | None = None,
    ) -> Verdict:
        """Run the probe for ``post_condition`` and return its verdict.

        ``output`` is the action's produced text (for :class:`NonEmptyText`);
        ``started_at`` is the call-start epoch time (the freshness clock for
        :class:`ArtifactFresh`). ``success``/``verified`` are the actor's self-report,
        carried for evidence/symmetry — they never override an observation."""
        try:
            if post_condition is None or isinstance(post_condition, NoPostCondition):
                return Verdict(None, "no post-condition declared")
            if isinstance(post_condition, NonEmptyText):
                return self._observe_text(output)
            if isinstance(post_condition, ArtifactFresh):
                return self._observe_artifact(post_condition, started_at)
            if isinstance(post_condition, HttpOk):
                return self._observe_http(post_condition)
            if isinstance(post_condition, DeliveryAck):
                return self._observe_delivery(post_condition)
            if isinstance(post_condition, Custom):
                return self._observe_custom(post_condition)
            return Verdict(None, f"unknown post-condition: {type(post_condition).__name__}")
        except Exception as exc:  # total — an observation must never break a turn
            log.engine.error(
                "[acceptance_authority] observe failed — no opinion",
                exc_info=exc,
            )
            return Verdict(None, "observation error — no opinion")

    # --- per-kind probes (each tri-state, never raises) ------------------------

    def _observe_text(self, output: str) -> Verdict:
        ok = bool(output and output.strip())
        return Verdict(
            ok,
            "non-empty text observed" if ok else "claimed output but text is empty",
            {"text_len": len(output or "")},
        )

    def _observe_artifact(self, pc: ArtifactFresh, started_at: float | None) -> Verdict:
        if pc.artifact_dir is not None and pc.path is None:
            # Directory mode (goal-level declared output): ANY fresh non-empty file.
            # Reference the helpers through the module object so a caller/test that
            # monkeypatches acceptance._dir_has_fresh_file still intercepts (the
            # delegation must not change observable behavior).
            observed = _acceptance._dir_has_fresh_file(
                _acceptance._resolve_dir(pc.artifact_dir),
                started_at if started_at is not None else 0.0,
            )
            ev: dict[str, object] = {"artifact_dir": pc.artifact_dir}
        else:
            observed = verify_artifact(
                pc.path, not_before=started_at, expect_kind=pc.expect_kind
            )
            ev = {"path": pc.path}
        if observed is None:
            return Verdict(None, "artifact could not be observed", ev, unobservable=True)
        if observed:
            return Verdict(True, "fresh artifact observed", ev)
        return Verdict(
            False, "declared an artifact but no fresh file was produced", ev
        )

    def _observe_http(self, pc: HttpOk) -> Verdict:
        observed = self._http_prober(pc.url)
        if observed is None:
            return Verdict(
                None, "endpoint could not be probed", {"url": pc.url}, unobservable=True
            )
        if observed:
            return Verdict(True, "endpoint responded successfully", {"url": pc.url})
        return Verdict(False, "endpoint did not respond successfully", {"url": pc.url})

    def _observe_delivery(self, pc: DeliveryAck) -> Verdict:
        if pc.acked is None:
            return Verdict(
                None,
                "delivery could not be confirmed",
                {"channel": pc.channel},
                unobservable=True,
            )
        if pc.acked:
            return Verdict(True, "delivery acknowledged", {"channel": pc.channel})
        return Verdict(False, "delivery was not acknowledged", {"channel": pc.channel})

    def _observe_custom(self, pc: Custom) -> Verdict:
        if pc.probe is None:
            return Verdict(None, f"custom probe '{pc.label}' is empty")
        try:
            observed = pc.probe()
        except Exception as exc:  # noqa: BLE001 — probe failure ⇒ no opinion
            log.engine.debug(
                "[acceptance_authority] custom probe raised — no opinion",
                extra={"_fields": {"label": pc.label, "err": type(exc).__name__}},
            )
            return Verdict(None, f"custom probe '{pc.label}' could not be evaluated")
        if observed is None:
            return Verdict(
                None, f"custom probe '{pc.label}' had no opinion", unobservable=True
            )
        return Verdict(
            bool(observed),
            f"custom probe '{pc.label}' {'passed' if observed else 'refuted'}",
        )
