"""Parliament data models — ParliamentRound, ParliamentSession, CriticPersona."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stackowl.owls.manifest import OwlAgentManifest


class ParliamentRound(BaseModel):
    """A single round of Parliament debate.

    Captures every participating owl's response, whether that response was
    truncated (timeout / token budget), and the wall-clock duration of the
    round.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    round_number: int
    responses: dict[str, str]
    truncated: dict[str, bool]
    duration_ms: float = 0.0

    def genuine_responses(self) -> dict[str, str]:
        """Return only the non-truncated, non-error owl responses (PARL-1 / F078).

        Error/timeout sentinels (``[error: …]``, ``[timed out …]``,
        ``[backend error: …]``) are recorded with ``truncated[name] = True`` by
        the RoundRunner, as are token-budget-clipped responses. None of these is a
        genuine owl position, so they must NOT be embedded for convergence nor fed
        to synthesis. A name missing from ``truncated`` defaults to genuine
        (backward-compatible with any round built without the flag).
        """
        return {
            name: text
            for name, text in self.responses.items()
            if not self.truncated.get(name, False)
        }


class ParliamentSession(BaseModel):
    """Full Parliament session lifecycle record — persisted to SQLite.

    Instances are immutable; lifecycle transitions (``add_round``,
    ``complete``, ``fail``, ``add_interjection``) return new instances.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    topic: str
    owl_names: list[str]
    rounds: list[ParliamentRound] = Field(default_factory=list)
    synthesis: str | None = None
    # PARL-3 (F081): ``completed_no_synthesis`` is a DISTINCT degraded terminal —
    # the debate ran to completion but synthesis raised, so there is no verdict.
    # It must never be reported as a clean ``completed``.
    status: Literal[
        "running", "completed", "completed_no_synthesis", "failed"
    ] = "running"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    interjections: list[str] = Field(default_factory=list)

    def add_round(self, round_: ParliamentRound) -> ParliamentSession:
        """Return a new session with ``round_`` appended to ``rounds``."""
        return self.model_copy(update={"rounds": [*self.rounds, round_]})

    def add_interjection(self, msg: str) -> ParliamentSession:
        """Return a new session with ``msg`` appended to ``interjections``."""
        return self.model_copy(update={"interjections": [*self.interjections, msg]})

    def complete(self, synthesis: str | None = None) -> ParliamentSession:
        """Mark the session as completed, optionally storing the synthesis."""
        return self.model_copy(
            update={
                "status": "completed",
                "synthesis": synthesis,
                "completed_at": datetime.now(UTC),
            }
        )

    def complete_no_synthesis(self) -> ParliamentSession:
        """Mark a DEGRADED terminal: the debate ran but synthesis failed (PARL-3).

        Distinct from ``complete()`` — there is no verdict, so the channel must
        tell the user the debate finished without a conclusion rather than show a
        silent synthesis=None dressed up as a clean completion. Distinct from
        ``fail()`` — the rounds DID run and are preserved.
        """
        return self.model_copy(
            update={
                "status": "completed_no_synthesis",
                "synthesis": None,
                "completed_at": datetime.now(UTC),
            }
        )

    def fail(self) -> ParliamentSession:
        """Mark the session as failed (timeout, hard error)."""
        return self.model_copy(
            update={
                "status": "failed",
                "completed_at": datetime.now(UTC),
            }
        )

    def cumulative_token_estimate(self) -> int:
        """Estimate total tokens across all rounds (PARL-1 / F078).

        Uses the script-aware multilingual estimator instead of the legacy
        ``chars // 4`` heuristic, which grossly undercounts space-free scripts.
        Counts GENUINE responses only — error/timeout sentinels are not real
        token spend the budget should reserve against.
        """
        from stackowl.parliament.token_estimate import estimate_tokens

        return sum(
            estimate_tokens(response)
            for rnd in self.rounds
            for response in rnd.genuine_responses().values()
        )


def make_critic_persona() -> OwlAgentManifest:
    """Built-in virtual owl for mini-parliament when < 2 registered owls.

    The critic argues the counter-position, surfaces hidden assumptions,
    and stress-tests the other participants' reasoning. The persona is
    intentionally generic and language-neutral.
    """
    return OwlAgentManifest(
        name="critic",
        role="devil-advocate",
        system_prompt=(
            "You are a rigorous critical thinker. Your role in this "
            "Parliament session is to challenge assumptions, surface flaws "
            "in reasoning, and argue the counter-position to whatever the "
            "other participants propose. Be analytical and constructive, "
            "not contrarian for its own sake."
        ),
        model_tier="standard",
    )
