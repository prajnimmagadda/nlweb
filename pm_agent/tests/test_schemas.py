import pytest
from pydantic import ValidationError

from pm_agent.schemas import ARTIFACT_TYPES, Charter, ProjectProfile, RagThresholds, Risk, ScopeStructure, WorkItem

from .conftest import NOW


def test_every_kind_is_registered_once():
    assert set(ARTIFACT_TYPES) == {
        "project_profile", "charter", "scope_structure", "stakeholder", "work_item", "risk", "issue",
        "assumption", "decision", "change_request", "baseline", "action_request", "status_report", "action_item",
        "lesson",
    }


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Risk(id="R-1", project_id="p", title="t", cause="c", event="e", effect="f", probability=3, impact=3,
             severity="high")


def test_risk_strategy_must_match_type():
    Risk(id="R-1", project_id="p", title="t", cause="c", event="e", effect="f", probability=3, impact=3,
         strategy="mitigate")
    with pytest.raises(ValidationError, match="opportunity"):
        Risk(id="R-2", project_id="p", title="t", risk_type="opportunity", cause="c", event="e", effect="f",
             probability=3, impact=3, strategy="mitigate")


def test_risk_scale_is_one_to_five():
    with pytest.raises(ValidationError):
        Risk(id="R-1", project_id="p", title="t", cause="c", event="e", effect="f", probability=6, impact=3)


def test_charter_needs_measurable_objectives():
    with pytest.raises(ValidationError):
        Charter(id="charter", project_id="p", title="t", purpose="p", in_scope=["x"],
                objectives=[{"id": "O1", "statement": "s", "success_criteria": []}])
    with pytest.raises(ValidationError, match="unique"):
        Charter(id="charter", project_id="p", title="t", purpose="p", in_scope=["x"],
                objectives=[{"id": "O1", "statement": "s", "success_criteria": ["c"]},
                            {"id": "O1", "statement": "s2", "success_criteria": ["c"]}])


def test_profile_validation():
    with pytest.raises(ValidationError, match="owner/name"):
        ProjectProfile(id="p", project_id="p", name="n", repos=["not-a-repo"])
    with pytest.raises(ValidationError, match="project_id"):
        ProjectProfile(id="x", project_id="p", name="n")
    with pytest.raises(ValidationError, match="target_date"):
        ProjectProfile(id="p", project_id="p", name="n", start_date="2026-09-01", target_date="2026-08-01")


def test_thresholds_must_be_ordered():
    with pytest.raises(ValidationError):
        RagThresholds(spi_green=0.8, spi_amber=0.9)
    with pytest.raises(ValidationError):
        RagThresholds(blocked_amber=5, blocked_red=2)


def test_scope_structure_parents_must_exist():
    with pytest.raises(ValidationError, match="unknown parent"):
        ScopeStructure(id="scope", project_id="p", elements=[{"id": "S1", "title": "s", "parent": "E9"}])


def test_done_work_item_needs_closed_at():
    with pytest.raises(ValidationError, match="closed_at"):
        WorkItem(id="o/r#1", project_id="p", title="t", state="done", created_at=NOW)
