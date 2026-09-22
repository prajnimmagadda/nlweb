"""RAID logs: risks, assumptions, issues and decisions."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from pm_agent.schemas.common import Artifact

THREAT_STRATEGIES = ("escalate", "avoid", "transfer", "mitigate", "accept")
OPPORTUNITY_STRATEGIES = ("escalate", "exploit", "share", "enhance", "accept")


class Risk(Artifact):
    """A risk written as cause -> event -> effect.

    Agents may only add risks as 'proposed'; a human promotes them to 'open'.
    """

    kind = "risk"
    id_prefix = "R"

    title: str
    risk_type: Literal["threat", "opportunity"] = "threat"
    cause: str
    event: str
    effect: str
    category: str | None = None
    probability: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    owner: str | None = None
    status: Literal["proposed", "open", "closed", "realized", "rejected"] = "proposed"
    strategy: str | None = Field(None, description="Response strategy; allowed values depend on risk_type.")
    triggers: list[str] = Field(default_factory=list)
    responses: list[str] = Field(default_factory=list)

    @property
    def score(self) -> int:
        return self.probability * self.impact

    @model_validator(mode="after")
    def _strategy_matches_type(self) -> "Risk":
        allowed = THREAT_STRATEGIES if self.risk_type == "threat" else OPPORTUNITY_STRATEGIES
        if self.strategy is not None and self.strategy not in allowed:
            raise ValueError(f"strategy for a {self.risk_type} must be one of {allowed}")
        return self


class Issue(Artifact):
    kind = "issue"
    id_prefix = "I"

    title: str
    description: str
    owner: str | None = None
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    status: Literal["open", "resolved", "closed"] = "open"
    raised_on: date | None = None
    due: date | None = None
    resolution: str | None = None


class Assumption(Artifact):
    kind = "assumption"
    id_prefix = "A"

    statement: str
    owner: str | None = None
    status: Literal["unvalidated", "validated", "invalidated"] = "unvalidated"
    validate_by: date | None = None


class Decision(Artifact):
    """A decision record. Agents may propose; only a human accepts or rejects."""

    kind = "decision"
    id_prefix = "D"

    title: str
    context: str
    decision: str
    rationale: str
    alternatives: list[str] = Field(default_factory=list)
    status: Literal["proposed", "accepted", "rejected", "superseded"] = "proposed"
    decided_by: str | None = None
    decided_at: datetime | None = None
