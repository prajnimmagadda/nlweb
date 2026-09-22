import json
from datetime import date

import httpx
import pytest

from pm_agent.engines import impact
from pm_agent.governance import GovernanceError
from pm_agent.governance.rules import EXECUTOR_ACTOR, IMPACT_ACTOR
from pm_agent.integrations.github import GitHubClient
from pm_agent.schemas import ActionRequest, Baseline, ChangeProposal, RagThresholds
from pm_agent.service import Copilot

from .conftest import AGENT, HUMAN, NOW, SYSTEM, work_item


def _seed(copilot, done=20, open_items=10):
    """One item done every two days for 40 days, plus open v1 items numbered from 100."""
    for n in range(done):
        copilot.store.put(work_item(n, "done", started=2 * n + 4, closed=2 * n + 1, estimate=2), actor=SYSTEM)
    for n in range(100, 100 + open_items):
        copilot.store.put(work_item(n, "todo", estimate=3), actor=SYSTEM)


def _change_request(copilot, project, proposal):
    return copilot.save_artifact(project, "change_request", {
        "title": "Trim v1", "description": "d", "reason": "r", "status": "submitted",
        "impact": {"scope": "smaller", "risk": "lower"}, "proposal": proposal,
    }, actor=AGENT)["id"]


class FakeGitHub:
    """Records writes; milestone 'v2' is number 7."""

    def __init__(self, fail_on_issue: int | None = None):
        self.requests: list[tuple[str, str, dict]] = []
        self.fail_on_issue = fail_on_issue

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/milestones"):
            return httpx.Response(200, json=[{"title": "v1", "number": 3}, {"title": "v2", "number": 7}])
        if request.method == "POST":
            number = 500 + sum(1 for m, _, _ in self.requests if m == "POST")
            if number == self.fail_on_issue:
                return httpx.Response(403, json={"message": "Resource not accessible by integration"})
            return httpx.Response(201, json={"number": number, "html_url": f"https://github.com/o/r/issues/{number}"})
        return httpx.Response(200, json={})

    def client(self) -> GitHubClient:
        return GitHubClient(token="t", transport=httpx.MockTransport(self.handler))


# ----- impact engine ---------------------------------------------------------


def test_analysis_isolates_the_effect_of_the_change():
    scope = [work_item(n, "todo") for n in range(10)]
    history = [1, 0] * 30
    proposal = ChangeProposal(move_out_item_ids=["o/r#0", "o/r#1", "o/r#99"], add_items=1)
    analysis = impact.analyze_change(scope, proposal, date(2026, 10, 15), history, NOW.date(), NOW,
                                     risks_by_item={"o/r#1": ["R-3"]}, objectives_by_item={"o/r#0": ["O1"]})
    assert (analysis.before.remaining_items, analysis.after.remaining_items) == (10, 9)
    assert analysis.after.forecast_p85 < analysis.before.forecast_p85
    assert analysis.risks_linked == ["R-3"] and analysis.objectives_affected == ["O1"]
    assert "o/r#99" in analysis.notes[0]
    assert analysis.proposal_digest == impact.proposal_digest(proposal)
    assert impact.proposal_digest(proposal.model_copy(update={"add_items": 2})) != analysis.proposal_digest


def test_a_proposal_must_do_something():
    with pytest.raises(ValueError, match="must move items out"):
        ChangeProposal(move_to_milestone="v2")


def test_variance_counts_planned_additions():
    baseline = Baseline(id="B-1", project_id="demo", name="b", reason="r",
                        scope_item_ids=[f"o/r#{n}" for n in range(10)], planned_additions=2)
    scope = [work_item(n) for n in range(1, 14)]  # one removed, four new
    variance = impact.baseline_variance(baseline, scope, None, None)
    assert variance.added == ["o/r#10", "o/r#11", "o/r#12", "o/r#13"] and variance.removed == ["o/r#0"]
    assert variance.growth == pytest.approx(1 / 12, abs=1e-4)
    thresholds = RagThresholds()
    assert impact.scope_status(variance, thresholds).rag == "green"
    assert impact.scope_status(impact.Variance("B-1", growth=0.15), thresholds).rag == "amber"
    assert impact.scope_status(impact.Variance("B-1", growth=0.25), thresholds).rag == "red"
    assert impact.scope_status(None, thresholds).rag == "unknown"


# ----- governance ------------------------------------------------------------


def test_only_humans_approve_baselines(store):
    baseline = Baseline(id="B-1", project_id="demo", name="b", reason="r")
    with pytest.raises(GovernanceError, match="baseline"):
        store.put(baseline.model_copy(update={"status": "approved"}), actor=AGENT)
    store.put(baseline, actor=AGENT)
    with pytest.raises(GovernanceError, match="baseline"):
        store.put(baseline.model_copy(update={"status": "approved", "approved_by": "copilot"}), actor=AGENT)
    store.put(baseline.model_copy(update={"status": "approved", "approved_by": "prajwal"}), actor=HUMAN)


def test_action_request_lifecycle_rules(store):
    request = ActionRequest(id="AR-1", project_id="demo", action="github.create_issues", title="t",
                            rationale="r", payload={"repo": "o/r", "issues": [{"title": "New story"}]})
    with pytest.raises(GovernanceError, match="pending"):
        store.put(request.model_copy(update={"status": "approved"}), actor=AGENT)
    store.put(request, actor=AGENT)
    store.put(request.model_copy(update={"payload": {"repo": "o/r", "issues": [{"title": "Renamed"}]}}),
              actor=AGENT)  # still pending: the agent may revise it
    with pytest.raises(GovernanceError, match="only a human"):
        store.put(request.model_copy(update={"decided_by": "copilot"}), actor=AGENT)
    with pytest.raises(GovernanceError, match="executor"):
        store.put(request.model_copy(update={"status": "executed"}), actor=EXECUTOR_ACTOR)  # not approved yet
    approved = store.get("demo", "action_request", "AR-1").artifact.model_copy(
        update={"status": "approved", "decided_by": "prajwal", "decided_at": NOW})
    store.put(approved, actor=HUMAN)
    with pytest.raises(GovernanceError, match="after it has been decided"):
        store.put(approved.model_copy(update={"payload": {"repo": "o/r", "issues": [{"title": "Sneaky"}]}}),
                  actor=AGENT)
    with pytest.raises(GovernanceError, match="executor"):
        store.put(approved.model_copy(update={"status": "executed"}), actor=AGENT)
    store.put(approved.model_copy(update={"status": "executed", "result": {"created": []}}), actor=EXECUTOR_ACTOR)


def test_bad_payloads_are_rejected():
    with pytest.raises(ValueError):
        ActionRequest(id="AR-1", project_id="demo", action="github.create_issues", title="t", rationale="r",
                      payload={"repo": "not a repo", "issues": [{"title": "x"}]})
    with pytest.raises(ValueError):
        ActionRequest(id="AR-1", project_id="demo", action="github.set_milestone", title="t", rationale="r",
                      payload={"repo": "o/r", "issue_numbers": []})


def test_analysis_is_written_by_the_engine_only(copilot, project):
    _seed(copilot)
    cr = _change_request(copilot, project, {"move_out_item_ids": ["o/r#100"]})
    stored = copilot.store.get(project, "change_request", cr).artifact
    fake = impact.analyze_change([], stored.proposal, None, [1], NOW.date(), NOW)
    with pytest.raises(GovernanceError, match="assess_change_request"):
        copilot.update_artifact(project, "change_request", cr, {"analysis": fake.model_dump()}, actor=AGENT)
    copilot.assess_change_request(project, cr, requested_by=AGENT)
    latest = copilot.store.get(project, "change_request", cr)
    assert latest.actor == IMPACT_ACTOR and AGENT in latest.rationale


# ----- service flows ---------------------------------------------------------


def test_baselines_are_computed_and_superseded(copilot, project):
    _seed(copilot)
    with pytest.raises(GovernanceError, match="propose_baseline"):
        copilot.save_artifact(project, "baseline", {"name": "b", "reason": "r"}, actor=AGENT)
    assert copilot.health(project)["dimensions"][1] == {
        "name": "scope", "rag": "unknown", "value": None, "explanation": "No approved baseline yet."}

    first = copilot.propose_baseline(project, actor=AGENT, name="Plan", reason="kickoff")
    assert first["baseline"]["scope_item_ids"][0] == "o/r#0" and len(first["baseline"]["scope_item_ids"]) == 30
    assert copilot.current_baseline(project) is None
    with pytest.raises(GovernanceError):
        copilot.approve(project, "baseline", "B-1", actor=AGENT)
    copilot.approve(project, "baseline", "B-1", actor=HUMAN)
    for n in range(200, 204):  # 4 new items on 30: +13%
        copilot.store.put(work_item(n), actor=SYSTEM)
    variance = copilot.baseline_variance(project)
    assert variance["status"]["rag"] == "amber" and len(variance["variance"]["added"]) == 4

    copilot.propose_baseline(project, actor=AGENT, name="Plan 2", reason="accept growth")
    assert copilot.approve(project, "baseline", "B-2", actor=HUMAN)["superseded"] == "B-1"
    assert copilot.current_baseline(project).id == "B-2"
    assert copilot.store.get(project, "baseline", "B-1").artifact.status == "superseded"
    with pytest.raises(ValueError, match="can't be decided"):
        copilot.approve(project, "baseline", "B-1", actor=HUMAN)


def test_change_approval_needs_a_fresh_analysis(copilot, project):
    _seed(copilot)
    cr = _change_request(copilot, project, {"move_out_item_ids": ["o/r#100"]})
    with pytest.raises(ValueError, match="assess_change_request"):
        copilot.approve(project, "change_request", cr, actor=HUMAN)
    copilot.assess_change_request(project, cr, requested_by=AGENT)
    copilot.update_artifact(project, "change_request", cr,
                            {"proposal": {"move_out_item_ids": ["o/r#100", "o/r#101"]}}, actor=AGENT)
    with pytest.raises(ValueError, match="changed since its impact analysis"):
        copilot.approve(project, "change_request", cr, actor=HUMAN)
    version = copilot.assess_change_request(project, cr, requested_by=AGENT)["version"]
    with pytest.raises(ValueError, match="changed since you opened it"):
        copilot.approve(project, "change_request", cr, actor=HUMAN, expected_version=version - 1)
    copilot.approve(project, "change_request", cr, actor=HUMAN, expected_version=version)


def test_approved_change_updates_target_baseline_and_queues_milestone_moves(copilot, project):
    _seed(copilot)
    copilot.propose_baseline(project, actor=AGENT, name="Plan", reason="kickoff")
    copilot.approve(project, "baseline", "B-1", actor=HUMAN)
    cr = _change_request(copilot, project, {"move_out_item_ids": ["o/r#100", "o/r#101", "x/y#5"],
                                            "move_to_milestone": "v2", "add_items": 1,
                                            "new_target_date": "2026-12-14"})
    copilot.assess_change_request(project, cr, requested_by=AGENT)
    out = copilot.approve(project, "change_request", cr, actor=HUMAN, note="date matters more")

    assert str(copilot.profile(project).target_date) == "2026-12-14"
    baseline = copilot.current_baseline(project)
    assert (baseline.id, out["baseline"], baseline.change_request_id) == ("B-2", "B-2", cr)
    assert "o/r#100" not in baseline.scope_item_ids and baseline.planned_additions == 1
    assert copilot.store.get(project, "baseline", "B-1").artifact.status == "superseded"
    [action_id] = out["actions_waiting_for_approval"]
    action = copilot.store.get(project, "action_request", action_id)
    assert action.actor == "system:change-control"
    assert action.artifact.payload == {"repo": "o/r", "issue_numbers": [100, 101], "milestone": "v2"}
    assert copilot.store.get(project, "change_request", cr).artifact.status == "approved"

    fake = FakeGitHub()
    copilot._github = fake.client()
    result = copilot.approve(project, "action_request", action_id, actor=HUMAN)
    assert result["status"] == "executed" and result["result"] == {"updated": [100, 101], "milestone": "v2"}
    assert [(m, p, b) for m, p, b in fake.requests if m == "PATCH"] == [
        ("PATCH", "/repos/o/r/issues/100", {"milestone": 7}), ("PATCH", "/repos/o/r/issues/101", {"milestone": 7})]
    assert copilot.store.get(project, "change_request", cr).artifact.status == "implemented"
    assert copilot.store.get(project, "action_request", action_id).actor == EXECUTOR_ACTOR


def test_target_only_change_is_implemented_at_once(copilot, project):
    _seed(copilot)
    cr = _change_request(copilot, project, {"new_target_date": "2026-12-21"})
    copilot.assess_change_request(project, cr, requested_by=AGENT)
    out = copilot.approve(project, "change_request", cr, actor=HUMAN)
    assert out["implemented"] is True and "actions_waiting_for_approval" not in out
    assert copilot.store.get(project, "change_request", cr).artifact.status == "implemented"


def test_issue_creation_waits_for_approval_then_runs(copilot, project):
    fake = FakeGitHub()
    copilot._github = fake.client()
    with pytest.raises(GovernanceError, match="isn't one of this project's repos"):
        copilot.propose_action(project, "github.create_issues", {"repo": "else/where", "issues": [{"title": "abc"}]},
                               actor=AGENT, title="t", rationale="r")
    filed = copilot.propose_action(project, "github.create_issues", {"repo": "o/r", "issues": [
        {"title": "Story one", "body": "AC", "labels": ["points:2"], "milestone": "v1"},
        {"title": "Story two"}]}, actor=AGENT, title="Create 2 issues", rationale="story map gaps")
    assert fake.requests == []  # nothing happens before approval
    assert copilot.inbox(project)[0]["id"] == filed["id"]
    with pytest.raises(GovernanceError):
        copilot.approve(project, "action_request", filed["id"], actor=AGENT)

    result = copilot.approve(project, "action_request", filed["id"], actor=HUMAN, note="go")
    assert [c["number"] for c in result["result"]["created"]] == [501, 502]
    posts = [b for m, _, b in fake.requests if m == "POST"]
    assert posts[0]["milestone"] == 3 and posts[0]["labels"] == ["points:2"]
    assert "Created by PM Copilot (AR-1) after approval by prajwal." in posts[0]["body"]
    assert "milestone" not in posts[1]
    assert copilot.inbox(project) == []
    with pytest.raises(ValueError, match="can't be decided"):
        copilot.approve(project, "action_request", filed["id"], actor=HUMAN)


def test_failed_actions_record_what_was_done(copilot, project):
    copilot._github = FakeGitHub(fail_on_issue=502).client()
    filed = copilot.propose_action(project, "github.create_issues", {"repo": "o/r", "issues": [
        {"title": "Story one"}, {"title": "Story two"}]}, actor=AGENT, title="t", rationale="r")
    result = copilot.approve(project, "action_request", filed["id"], actor=HUMAN)
    assert result["status"] == "failed"
    assert result["result"]["created"][0]["number"] == 501 and "403" in result["result"]["error"]


def test_rejected_actions_never_run(copilot, project):
    fake = FakeGitHub()
    copilot._github = fake.client()
    filed = copilot.propose_action(project, "github.set_milestone", {"repo": "o/r", "issue_numbers": [1],
                                                                     "milestone": None},
                                   actor=AGENT, title="t", rationale="r")
    copilot.reject(project, "action_request", filed["id"], actor=HUMAN, reason="not now")
    stored = copilot.store.get(project, "action_request", filed["id"]).artifact
    assert (stored.status, stored.decision_note, fake.requests) == ("rejected", "not now", [])


def test_inbox_lists_pending_decisions(copilot, project):
    risk = {"title": "t", "cause": "c", "event": "e", "effect": "f", "probability": 2, "impact": 3}
    copilot.save_artifact(project, "risk", risk, actor=AGENT)
    copilot.save_artifact(project, "charter", {"title": "T", "purpose": "P", "in_scope": ["x"], "objectives": [
        {"id": "O1", "statement": "s", "success_criteria": ["c"]}]}, actor=AGENT)
    kinds = [i["kind"] for i in copilot.inbox(project)]
    assert sorted(kinds) == ["charter", "risk"]
    copilot.approve(project, "charter", "charter", actor=HUMAN)
    assert [i["kind"] for i in copilot.inbox(project)] == ["risk"]


def test_missing_github_token_fails_cleanly(store, project):
    copilot = Copilot(store, github=GitHubClient(token="", transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"message": "Requires authentication"}))), clock=lambda: NOW)
    filed = copilot.propose_action(project, "github.create_issues", {"repo": "o/r", "issues": [{"title": "abc"}]},
                                   actor=AGENT, title="t", rationale="r")
    result = copilot.approve(project, "action_request", filed["id"], actor=HUMAN)
    assert result["status"] == "failed" and "401" in result["result"]["error"]


def test_sandbox_projects_simulate_github_actions(copilot, project):
    profile = copilot.profile(project)
    copilot.store.put(profile.model_copy(update={"sandbox": True}), actor=HUMAN)
    with pytest.raises(GovernanceError, match="sandbox"):
        copilot.store.put(copilot.profile(project).model_copy(update={"sandbox": False}), actor=AGENT)
    fake = FakeGitHub()
    copilot._github = fake.client()
    filed = copilot.propose_action(project, "github.create_issues", {"repo": "o/r", "issues": [{"title": "abc"}]},
                                   actor=AGENT, title="t", rationale="r")
    result = copilot.approve(project, "action_request", filed["id"], actor=HUMAN)
    assert result["status"] == "executed" and result["result"]["simulated"] is True and fake.requests == []
    assert result["result"]["created"][0]["number"] == 1
    assert copilot.store.get(project, "work_item", "o/r#1").actor == "system:sandbox"
    copilot.store.put(work_item(7), actor=SYSTEM)
    moved = copilot.propose_action(project, "github.set_milestone",
                                   {"repo": "o/r", "issue_numbers": [7], "milestone": "v2"},
                                   actor=AGENT, title="t", rationale="r")
    copilot.approve(project, "action_request", moved["id"], actor=HUMAN)
    assert copilot.store.get(project, "work_item", "o/r#7").artifact.milestone == "v2" and fake.requests == []
    with pytest.raises(ValueError, match="sandbox"):
        copilot.sync_github(project)
