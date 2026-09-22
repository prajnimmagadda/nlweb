"""Change control (PMBOK 8: Assess and Implement Changes) and baselines."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from pm_agent.schemas.common import Artifact, Model, Rag


class ChangeImpact(Model):
    """Cross-domain impact in words, as required by the 'Adopt a Holistic View' principle."""

    scope: str
    schedule_days: float | None = Field(None, description="Positive means later.")
    cost: float | None = None
    risk: str
    stakeholders: str | None = None
    benefits: str | None = None


class ChangeProposal(Model):
    """What the change does, in terms the impact engine can compute."""

    move_out_item_ids: list[str] = Field(
        default_factory=list, description="Work items leaving the release scope, e.g. 'owner/repo#12'.")
    move_to_milestone: str | None = Field(None, description="Where moved items go on GitHub, e.g. 'v2'.")
    add_items: int = Field(0, ge=0, description="New items the change adds to the release scope.")
    add_points: float | None = Field(None, ge=0, description="Estimated points of the added items.")
    new_target_date: date | None = None

    @model_validator(mode="after")
    def _does_something(self) -> "ChangeProposal":
        if not (self.move_out_item_ids or self.add_items or self.new_target_date):
            raise ValueError("a proposal must move items out, add items, or change the target date")
        return self


class ImpactSnapshot(Model):
    remaining_items: int
    scope_items: int
    scope_points: float | None
    target_date: date | None
    forecast_p50: date | None
    forecast_p85: date | None
    schedule_rag: Rag
    done_by_target_p85: int | None = Field(None, description="Items done by the target in 85% of simulations.")


class ImpactAnalysis(Model):
    """Computed by the impact engine. Only system components may write it."""

    computed_at: datetime
    proposal_digest: str = Field(
        description="Fingerprint of the proposal analysed; a changed proposal needs a new analysis.")
    before: ImpactSnapshot
    after: ImpactSnapshot
    objectives_affected: list[str] = Field(default_factory=list)
    risks_linked: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ChangeRequest(Artifact):
    kind = "change_request"
    id_prefix = "CR"

    title: str
    description: str
    reason: str
    requested_by: str | None = None
    impact: ChangeImpact
    proposal: ChangeProposal | None = None
    analysis: ImpactAnalysis | None = None
    options: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    status: Literal["draft", "submitted", "approved", "rejected", "implemented"] = "draft"
    decided_by: str | None = None
    decision_rationale: str | None = None
    decided_at: datetime | None = None


class Baseline(Artifact):
    """The approved release scope, target and forecast that variance is measured against."""

    kind = "baseline"
    id_prefix = "B"

    name: str
    reason: str
    scope_item_ids: list[str] = Field(default_factory=list)
    planned_additions: int = Field(0, ge=0, description="Approved new items that aren't on the tracker yet.")
    scope_points: float | None = None
    target_date: date | None = None
    forecast_p50: date | None = None
    forecast_p85: date | None = None
    budget: float | None = Field(None, ge=0)
    change_request_id: str | None = None
    status: Literal["proposed", "approved", "rejected", "superseded"] = "proposed"
    approved_by: str | None = None
    approved_at: datetime | None = None
