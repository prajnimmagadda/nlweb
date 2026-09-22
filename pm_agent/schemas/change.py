"""Change control (PMBOK 8: Assess and Implement Changes)."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from pm_agent.schemas.common import Artifact, Model


class ChangeImpact(Model):
    """Cross-domain impact, as required by the 'Adopt a Holistic View' principle."""

    scope: str
    schedule_days: float | None = Field(None, description="Positive means later.")
    cost: float | None = None
    risk: str
    stakeholders: str | None = None
    benefits: str | None = None


class ChangeRequest(Artifact):
    kind = "change_request"
    id_prefix = "CR"

    title: str
    description: str
    reason: str
    requested_by: str | None = None
    impact: ChangeImpact
    options: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    status: Literal["draft", "submitted", "approved", "rejected", "implemented"] = "draft"
    decided_by: str | None = None
    decision_rationale: str | None = None
    decided_at: datetime | None = None
