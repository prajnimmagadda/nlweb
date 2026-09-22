"""Action requests: writes to outside systems that wait for a human (autonomy L2)."""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from pm_agent.schemas.common import Artifact, Model

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class IssueDraft(Model):
    title: str = Field(min_length=3, max_length=256)
    body: str = Field("", max_length=20000)
    labels: list[str] = Field(default_factory=list, max_length=10)
    milestone: str | None = None


class CreateIssuesPayload(Model):
    repo: str
    issues: list[IssueDraft] = Field(min_length=1, max_length=20)

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        if not _REPO_RE.match(value):
            raise ValueError("repo must look like 'owner/name'")
        return value


class SetMilestonePayload(Model):
    repo: str
    issue_numbers: list[int] = Field(min_length=1, max_length=50)
    milestone: str | None = Field(None, description="Milestone title; None clears the milestone.")

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        if not _REPO_RE.match(value):
            raise ValueError("repo must look like 'owner/name'")
        return value


PAYLOAD_TYPES: dict[str, type[Model]] = {
    "github.create_issues": CreateIssuesPayload,
    "github.set_milestone": SetMilestonePayload,
}
ActionType = Literal["github.create_issues", "github.set_milestone"]


class ActionRequest(Artifact):
    """Agents create these as pending; a human approves; the executor carries them out."""

    kind = "action_request"
    id_prefix = "AR"

    action: ActionType
    title: str
    payload: dict[str, Any]
    rationale: str
    requested_by: str | None = None
    change_request_id: str | None = Field(None, description="The approved change request this carries out.")
    replaces: str | None = Field(None, description="A failed or rejected request this one files again.")
    status: Literal["pending", "approved", "rejected", "executed", "failed"] = "pending"
    decided_by: str | None = None
    decision_note: str | None = None
    decided_at: datetime | None = None
    result: dict[str, Any] | None = None
    executed_at: datetime | None = None

    @model_validator(mode="after")
    def _payload_matches_action(self) -> "ActionRequest":
        PAYLOAD_TYPES[self.action].model_validate(self.payload)
        return self

    def typed_payload(self) -> Model:
        return PAYLOAD_TYPES[self.action].model_validate(self.payload)
