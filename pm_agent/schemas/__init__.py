"""Artifact schemas and the kind -> class registry."""

from pm_agent.schemas.change import ChangeImpact, ChangeRequest
from pm_agent.schemas.common import Artifact, Link, Rag
from pm_agent.schemas.communication import ActionItem, DimensionStatus, LessonLearned, StatusReport
from pm_agent.schemas.project import (
    Charter,
    Milestone,
    Objective,
    ProjectProfile,
    RagThresholds,
    ScopeElement,
    ScopeStructure,
    Stakeholder,
    StatusMapping,
)
from pm_agent.schemas.raid import Assumption, Decision, Issue, Risk
from pm_agent.schemas.work import WorkItem

ARTIFACT_TYPES: dict[str, type[Artifact]] = {
    cls.kind: cls
    for cls in (
        ProjectProfile,
        Charter,
        ScopeStructure,
        Stakeholder,
        WorkItem,
        Risk,
        Issue,
        Assumption,
        Decision,
        ChangeRequest,
        StatusReport,
        ActionItem,
        LessonLearned,
    )
}


def artifact_type(kind: str) -> type[Artifact]:
    try:
        return ARTIFACT_TYPES[kind]
    except KeyError:
        raise ValueError(f"unknown artifact kind {kind!r}; known kinds: {sorted(ARTIFACT_TYPES)}") from None


__all__ = [
    "ARTIFACT_TYPES",
    "ActionItem",
    "Artifact",
    "Assumption",
    "ChangeImpact",
    "ChangeRequest",
    "Charter",
    "Decision",
    "DimensionStatus",
    "Issue",
    "LessonLearned",
    "Link",
    "Milestone",
    "Objective",
    "ProjectProfile",
    "Rag",
    "RagThresholds",
    "Risk",
    "ScopeElement",
    "ScopeStructure",
    "Stakeholder",
    "StatusMapping",
    "StatusReport",
    "WorkItem",
    "artifact_type",
]
