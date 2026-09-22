"""Communications and knowledge: status reports, action items, lessons learned."""

from datetime import date
from typing import Literal

from pydantic import Field

from pm_agent.schemas.common import Artifact, Model, Rag


class DimensionStatus(Model):
    name: str
    rag: Rag
    value: str | None = None
    explanation: str


class StatusReport(Artifact):
    """Weekly status. RAG fields are filled from the engines, never by the model."""

    kind = "status_report"
    id_prefix = "SR"

    period_start: date
    period_end: date
    overall: Rag
    dimensions: list[DimensionStatus]
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)
    summary: str
    accomplishments: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    decisions_needed: list[str] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list, description="Risk ids.")


class ActionItem(Artifact):
    kind = "action_item"
    id_prefix = "AI"

    title: str
    owner: str | None = None
    due: date | None = None
    status: Literal["open", "done", "dropped"] = "open"
    source: str | None = Field(None, description="Where it came from, e.g. a meeting title and date.")


class LessonLearned(Artifact):
    kind = "lesson"
    id_prefix = "LL"

    title: str
    what_happened: str
    recommendation: str
    category: str | None = None
