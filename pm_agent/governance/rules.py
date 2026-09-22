"""Write rules that keep a human accountable (PMBOK 8: Be an Accountable Leader).

The store calls check_write() before every write by a non-human actor. The rules
are deliberately enforced in code rather than in prompts, so no prompt injection
or model mistake can approve a charter, accept a decision, or soften RAG
thresholds.

Actors are strings of the form 'human:<name>', 'agent:<role>' or 'system:<component>'.
"""

import re
from enum import IntEnum
from typing import Callable

from pm_agent.schemas import ActionRequest, Artifact, Baseline, ChangeRequest, Charter, Decision, ProjectProfile, Risk

_ACTOR_RE = re.compile(r"^(human|agent|system):[\w .@+-]+$")

IMPACT_ACTOR = "system:impact-analysis"
EXECUTOR_ACTOR = "system:executor"


class AutonomyLevel(IntEnum):
    INFORM = 0
    DRAFT = 1
    ACT_WITH_APPROVAL = 2
    AUTONOMOUS = 3

    @classmethod
    def parse(cls, value: "str | int | AutonomyLevel") -> "AutonomyLevel":
        if isinstance(value, str):
            text = value.strip().upper()
            if text.startswith("L") and text[1:].isdigit():
                return cls(int(text[1:]))
            return cls[text]
        return cls(value)


class GovernanceError(PermissionError):
    """Raised when a non-human actor attempts a human-only change."""

    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


def validate_actor(actor: str) -> str:
    if not _ACTOR_RE.match(actor):
        raise ValueError(f"actor must look like 'human:<name>', 'agent:<role>' or 'system:<component>', got {actor!r}")
    return actor


def is_human(actor: str) -> bool:
    return actor.startswith("human:")


def is_system(actor: str) -> bool:
    return actor.startswith("system:")


Rule = Callable[[Artifact, Artifact | None, str], str | None]


def _system_only(new: Artifact, previous: Artifact | None, actor: str) -> str | None:
    if not is_system(actor):
        return f"{new.kind} artifacts are written by system components only (got {actor})"
    return None


# Profile fields that decide how status is judged, when the agent runs unattended, who hears
# from it, and how much of your calendar it can see. An agent may not set or change them.
_HUMAN_ONLY_PROFILE_FIELDS = {
    "thresholds": "RAG thresholds",
    "schedules": "schedules",
    "timezone": "the schedule timezone",
    "report_recipients": "report recipients",
    "calendar_query": "the calendar filter",
}


def _human_only_profile_fields(new: ProjectProfile, previous: ProjectProfile | None, actor: str) -> str | None:
    baseline = previous or ProjectProfile(id=new.id, project_id=new.project_id, name=new.name)
    changed = [label for field, label in _HUMAN_ONLY_PROFILE_FIELDS.items()
               if getattr(new, field) != getattr(baseline, field)]
    if changed:
        return "only a human may change " + ", ".join(changed)
    return None


def _charter_approval_unchanged(new: Charter, previous: Charter | None, actor: str) -> str | None:
    before = (previous.approved_by, previous.approved_at) if previous else (None, None)
    if (new.approved_by, new.approved_at) != before:
        return "only a human may approve a charter"
    return None


def _risk_status(new: Risk, previous: Risk | None, actor: str) -> str | None:
    if previous is None and new.status != "proposed":
        return "agents may only add risks with status 'proposed'"
    if previous is not None and new.status != previous.status:
        return f"only a human may change a risk's status ({previous.status} -> {new.status})"
    return None


def _decision_status(new: Decision, previous: Decision | None, actor: str) -> str | None:
    before = (previous.status, previous.decided_by, previous.decided_at) if previous else ("proposed", None, None)
    if (new.status, new.decided_by, new.decided_at) != before:
        return "only a human may accept, reject or supersede a decision"
    return None


def _change_request_status(new: ChangeRequest, previous: ChangeRequest | None, actor: str) -> str | None:
    before_status = previous.status if previous else None
    decision_before = (
        (previous.decided_by, previous.decision_rationale, previous.decided_at) if previous else (None, None, None)
    )
    if (new.decided_by, new.decision_rationale, new.decided_at) != decision_before:
        return "only a human may record a change request decision"
    allowed = (
        new.status == before_status
        or (new.status in ("draft", "submitted") and before_status in (None, "draft", "submitted"))
        or (new.status == "implemented" and before_status == "approved")
    )
    if not allowed:
        return f"agents may not move a change request from {before_status or 'new'} to {new.status}"
    return None


def _analysis_from_engine(new: ChangeRequest, previous: ChangeRequest | None, actor: str) -> str | None:
    before = previous.analysis if previous else None
    if new.analysis != before and actor != IMPACT_ACTOR:
        return "impact analysis is computed by PM Copilot; request it with assess_change_request"
    return None


def _baseline_status(new: Baseline, previous: Baseline | None, actor: str) -> str | None:
    before = (previous.status, previous.approved_by, previous.approved_at) if previous else ("proposed", None, None)
    if (new.status, new.approved_by, new.approved_at) != before:
        return "only a human may approve, reject or supersede a baseline"
    return None


def _action_request(new: ActionRequest, previous: ActionRequest | None, actor: str) -> str | None:
    outcome = (new.status, new.result, new.executed_at)
    decision = (new.decided_by, new.decision_note, new.decided_at)
    if previous is None:
        if outcome != ("pending", None, None) or decision != (None, None, None):
            return "agents may only file action requests as 'pending'"
        return None
    if decision != (previous.decided_by, previous.decision_note, previous.decided_at):
        return "only a human may approve or reject an action request"
    if new.payload != previous.payload and previous.status != "pending":
        return "an action request can't change after it has been decided"
    if outcome != (previous.status, previous.result, previous.executed_at):
        executing = actor == EXECUTOR_ACTOR and previous.status == "approved" and new.status in ("executed", "failed")
        if not executing:
            return "only the executor may record the outcome of an approved action request"
    return None


_RULES: dict[str, list[Rule]] = {
    "work_item": [_system_only],
    "status_report": [_system_only],
    "project_profile": [_human_only_profile_fields],
    "charter": [_charter_approval_unchanged],
    "risk": [_risk_status],
    "decision": [_decision_status],
    "change_request": [_change_request_status, _analysis_from_engine],
    "baseline": [_baseline_status],
    "action_request": [_action_request],
}


def check_write(new: Artifact, previous: Artifact | None, actor: str) -> list[str]:
    """Return the list of rule violations for this write (empty means allowed)."""
    validate_actor(actor)
    if is_human(actor):
        return []
    return [v for rule in _RULES.get(new.kind, []) if (v := rule(new, previous, actor))]
