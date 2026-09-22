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

from pm_agent.schemas import Artifact, ChangeRequest, Charter, Decision, ProjectProfile, RagThresholds, Risk

_ACTOR_RE = re.compile(r"^(human|agent|system):[\w .@+-]+$")


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


def _thresholds_unchanged(new: ProjectProfile, previous: ProjectProfile | None, actor: str) -> str | None:
    expected = previous.thresholds if previous else RagThresholds()
    if new.thresholds != expected:
        return "only a human may change RAG thresholds"
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


_RULES: dict[str, list[Rule]] = {
    "work_item": [_system_only],
    "status_report": [_system_only],
    "project_profile": [_thresholds_unchanged],
    "charter": [_charter_approval_unchanged],
    "risk": [_risk_status],
    "decision": [_decision_status],
    "change_request": [_change_request_status],
}


def check_write(new: Artifact, previous: Artifact | None, actor: str) -> list[str]:
    """Return the list of rule violations for this write (empty means allowed)."""
    validate_actor(actor)
    if is_human(actor):
        return []
    return [v for rule in _RULES.get(new.kind, []) if (v := rule(new, previous, actor))]
