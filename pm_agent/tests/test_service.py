import pytest

from pm_agent.governance import GovernanceError

from .conftest import AGENT, HUMAN, SYSTEM, work_item


def _seed_items(copilot, count_done=20, open_items=6):
    """A steady team: one item done roughly every two days over the last 40 days."""
    for n in range(count_done):
        closed = 2 * n + 1
        copilot.store.put(work_item(n, "done", started=closed + 3, closed=closed, estimate=3), actor=SYSTEM)
    for n in range(100, 100 + open_items):
        copilot.store.put(work_item(n, "todo", estimate=3), actor=SYSTEM)


def test_setup_project_updates_only_given_fields(copilot, project):
    copilot.setup_project(project, actor=AGENT, target_date="2026-12-15")
    profile = copilot.profile(project)
    assert str(profile.target_date) == "2026-12-15"
    assert profile.repos == ["o/r"] and profile.release_milestone == "v1"
    assert copilot.projects()[0]["target_date"] == "2026-12-15"


def test_forecast_and_health(copilot, project):
    _seed_items(copilot)
    forecast = copilot.forecast(project, seed=1, trials=2000)
    assert forecast["remaining_items"] == 6
    assert set(forecast["when"]["completion_dates"]) == {"P50", "P70", "P85", "P95"}
    assert forecast["how_many_by_target"]["items_at_least"]["P85"] > 6

    health = copilot.health(project, seed=1)
    dims = {d["name"]: d for d in health["dimensions"]}
    assert dims["schedule"]["rag"] == "green"
    assert dims["earned_value"]["rag"] in ("green", "amber", "red")
    assert health["overall"] in ("green", "amber", "red")
    assert health["metrics"]["remaining_items"] == 6


def test_health_is_unknown_without_work_items(copilot, project):
    health = copilot.health(project)
    dims = {d["name"]: d["rag"] for d in health["dimensions"]}
    assert dims["schedule"] == "unknown"
    assert "schedule" in health["unknown_dimensions"]
    assert "error" in copilot.forecast(project)


def test_agile_evm_through_service(copilot, project):
    _seed_items(copilot)
    result = copilot.agile_evm(project)
    assert result["unit"] == "points"
    assert result["estimate_coverage"] == "26 of 26 items estimated"
    assert result["iterations_planned"] == 9  # 2026-08-03 to 2026-11-30 in 14-day iterations
    assert result["iterations_completed"] == 3
    assert result["spi"] == pytest.approx((60 / 78) / (3 / 9), rel=1e-3)


def test_status_report_rag_comes_from_engines(copilot, project):
    _seed_items(copilot)
    out = copilot.draft_status_report(project, requested_by=AGENT, summary="On track.",
                                      accomplishments=["Shipped login"])
    report = out["report"]
    health = copilot.health(project)
    assert report["overall"] == health["overall"]
    assert [d["rag"] for d in report["dimensions"]] == [d["rag"] for d in health["dimensions"]]
    stored = copilot.store.get(project, "status_report", report["id"])
    assert stored.actor == "system:reporting" and AGENT in stored.rationale

    with pytest.raises(GovernanceError, match="draft_status_report"):
        copilot.save_artifact(project, "status_report", report, actor=AGENT)
    with pytest.raises(GovernanceError, match="sync_github"):
        copilot.update_artifact(project, "work_item", "o/r#1", {"title": "x"}, actor=AGENT)


def test_save_update_and_ids(copilot, project):
    risk = {"title": "Vendor slip", "cause": "one vendor", "event": "API late", "effect": "slip",
            "probability": 3, "impact": 4, "strategy": "mitigate"}
    assert copilot.save_artifact(project, "risk", risk, actor=AGENT)["id"] == "R-1"
    assert copilot.save_artifact(project, "risk", risk, actor=AGENT)["id"] == "R-2"
    charter = {"title": "T", "purpose": "P", "in_scope": ["x"],
               "objectives": [{"id": "O1", "statement": "s", "success_criteria": ["c"]}]}
    assert copilot.save_artifact(project, "charter", charter, actor=AGENT)["id"] == "charter"

    copilot.update_artifact(project, "risk", "R-1", {"owner": "dana"}, actor=AGENT, rationale="assigned")
    assert copilot.get_artifact(project, "risk", "R-1")["data"]["owner"] == "dana"
    assert len(copilot.history(project, "risk", "R-1")) == 2
    assert [r["id"] for r in copilot.list_artifacts(project, "risk", status="proposed")] == ["R-1", "R-2"]
    with pytest.raises(ValueError):
        copilot.save_artifact(project, "risk", {**risk, "project_id": "other"}, actor=AGENT)
    with pytest.raises(ValueError):
        copilot.update_artifact(project, "risk", "R-1", {"id": "R-9"}, actor=AGENT)
    with pytest.raises(LookupError):
        copilot.get_artifact(project, "risk", "R-99")


def test_approvals_are_human_only(copilot, project):
    risk = {"title": "t", "cause": "c", "event": "e", "effect": "f", "probability": 3, "impact": 3}
    copilot.save_artifact(project, "risk", risk, actor=AGENT)
    with pytest.raises(GovernanceError):
        copilot.approve(project, "risk", "R-1", actor=AGENT)
    copilot.approve(project, "risk", "R-1", actor=HUMAN)
    assert copilot.get_artifact(project, "risk", "R-1")["data"]["status"] == "open"

    decision = {"title": "t", "context": "c", "decision": "d", "rationale": "r"}
    copilot.save_artifact(project, "decision", decision, actor=AGENT)
    copilot.reject(project, "decision", "D-1", actor=HUMAN, reason="not now")
    data = copilot.get_artifact(project, "decision", "D-1")["data"]
    assert (data["status"], data["decided_by"]) == ("rejected", "prajwal")

    charter = {"title": "T", "purpose": "P", "in_scope": ["x"],
               "objectives": [{"id": "O1", "statement": "s", "success_criteria": ["c"]}]}
    copilot.save_artifact(project, "charter", charter, actor=AGENT)
    copilot.approve(project, "charter", "charter", actor=HUMAN)
    assert copilot.get_artifact(project, "charter", "charter")["data"]["approved_by"] == "prajwal"

    with pytest.raises(GovernanceError):
        copilot.set_thresholds(project, {"spi_green": 0.9}, actor=AGENT)
    copilot.set_thresholds(project, {"spi_green": 0.9}, actor=HUMAN)
    assert copilot.profile(project).thresholds.spi_green == 0.9


def test_scope_coverage(copilot, project):
    assert "error" in copilot.scope_coverage(project)
    copilot.save_artifact(project, "charter", {
        "title": "T", "purpose": "P", "in_scope": ["x"],
        "objectives": [{"id": "O1", "statement": "a", "success_criteria": ["c"]},
                       {"id": "O2", "statement": "b", "success_criteria": ["c"]}],
    }, actor=AGENT)
    copilot.save_artifact(project, "scope_structure", {"elements": [
        {"id": "E1", "title": "Login", "element_type": "epic", "objective_ids": ["O1"]},
        {"id": "S1", "title": "Password login", "parent": "E1", "work_item_id": "o/r#1"},
        {"id": "S2", "title": "Loose story", "work_item_id": "o/r#404"},
        {"id": "E2", "title": "Typo", "element_type": "epic", "objective_ids": ["O9"]},
    ]}, actor=AGENT)
    copilot.store.put(work_item(1, "in_progress", started=2), actor=SYSTEM)
    copilot.store.put(work_item(2, "todo"), actor=SYSTEM)

    coverage = copilot.scope_coverage(project)
    assert coverage["objectives_without_scope"] == ["O2"]
    assert coverage["elements_without_objective"] == ["S2"]
    assert coverage["unknown_objective_refs"] == ["O9"]
    assert coverage["mapped_issues_missing"] == ["o/r#404"]
    assert coverage["open_issues_not_in_scope"] == ["o/r#2"]


def test_demo_project_exercises_every_engine(copilot):
    from pm_agent.demo import seed_demo

    assert seed_demo(copilot, actor=HUMAN)["work_items"] == 81
    health = copilot.health("demo", seed=1)
    assert health["unknown_dimensions"] == []
    assert health["metrics"]["remaining_items"] == 32
    assert {d["name"]: d["rag"] for d in health["dimensions"]}["scope"] == "amber"
    assert {(i["kind"], i["id"]) for i in copilot.inbox("demo")} == {
        ("change_request", "CR-1"), ("action_request", "AR-1"), ("decision", "D-1"), ("risk", "R-2")}
    assert copilot.flow("demo").cycle_time_samples > 30
    statuses = {r["id"]: r["status"] for r in copilot.list_artifacts("demo", "risk")}
    assert statuses == {"R-1": "open", "R-2": "proposed"}
