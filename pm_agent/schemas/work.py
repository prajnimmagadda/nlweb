"""Work items mirrored from the tracker (GitHub issues)."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from pm_agent.schemas.common import Artifact

WorkState = Literal["todo", "in_progress", "blocked", "done", "cancelled"]
ACTIVE_STATES = ("in_progress", "blocked")


class WorkItem(Artifact):
    """A tracker item. Written by the GitHub sync, never directly by an agent."""

    kind = "work_item"

    title: str
    url: str | None = None
    item_type: Literal["epic", "story", "bug", "task"] = "story"
    state: WorkState
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    milestone: str | None = None
    estimate: float | None = Field(None, ge=0)
    created_at: datetime
    started_at: datetime | None = Field(
        None, description="First time the item was seen in progress; unknown if it skipped straight to done."
    )
    closed_at: datetime | None = None

    @model_validator(mode="after")
    def _closed_consistency(self) -> "WorkItem":
        if self.state in ("done", "cancelled") and self.closed_at is None:
            raise ValueError("done or cancelled items need closed_at")
        return self
