import pytest

from pm_agent.governance import AutonomyLevel, GovernanceError
from pm_agent.governance.rules import EXECUTOR_ACTOR
from pm_agent.schemas import ChangeImpact, ChangeRequest, Charter, Decision, ProjectProfile, RagThresholds, Risk
from pm_agent.store import Store

from .conftest import AGENT, HUMAN, NOW, SYSTEM, work_item


def _risk(**overrides) -> Risk:
    fields = dict(id="R-1", project_id="demo", title="Vendor slip", cause="single vendor", event="API late",
                  effect="release slips", probability=3, impact=4)
    return Risk(**{**fields, **overrides})


def _charter(**overrides) -> Charter:
    fields = dict(id="charter", project_id="demo", title="T", purpose="P", in_scope=["x"],
                  objectives=[{"id": "O1", "statement": "s", "success_criteria": ["c"]}])
    return Charter(**{**fields, **overrides})


def test_versions_audit_and_unchanged_writes(store):
    first = store.put(_risk(), actor=AGENT, rationale="from standup", sources=["https://example/1"])
    assert (first.version, first.changed) == (1, True)
    assert store.put(_risk(), actor=AGENT).changed is False
    second = store.put(_risk(impact=5), actor=AGENT)
    assert second.version == 2

    history = store.history("demo", "risk", "R-1")
    assert [h.version for h in history] == [1, 2]
    assert history[0].rationale == "from standup" and history[0].sources == ["https://example/1"]
    assert store.get("demo", "risk", "R-1", version=1).artifact.impact == 4
    assert [row["action"] for row in store.audit("demo")] == ["update", "create"]


def test_list_latest_returns_newest_version_only(store):
    store.put(_risk(), actor=AGENT)
    store.put(_risk(impact=5), actor=AGENT)
    store.put(_risk(id="R-2"), actor=AGENT)
    latest = store.list_latest("demo", "risk")
    assert [(s.artifact.id, s.version, s.artifact.impact) for s in latest] == [("R-1", 2, 5), ("R-2", 1, 4)]


def test_new_id_is_sequential(store):
    assert store.new_id("demo", "risk") == "R-1"
    store.put(_risk(id="R-7"), actor=AGENT)
    assert store.new_id("demo", "risk") == "R-8"
    assert store.new_id("other", "risk") == "R-1"
    with pytest.raises(ValueError):
        store.new_id("demo", "charter")


def test_actor_format_is_enforced(store):
    with pytest.raises(ValueError):
        store.put(_risk(), actor="copilot")


def test_agent_cannot_approve_charter(store):
    store.put(_charter(), actor=AGENT)
    with pytest.raises(GovernanceError, match="approve a charter"):
        store.put(_charter(approved_by="copilot", approved_at=NOW), actor=AGENT)
    assert store.audit("demo")[0]["action"] == "write_denied"
    store.put(_charter(approved_by="prajwal", approved_at=NOW), actor=HUMAN)
    # Once approved, an agent may edit content but must leave the approval untouched.
    store.put(_charter(purpose="P2", approved_by="prajwal", approved_at=NOW), actor=AGENT)
    with pytest.raises(GovernanceError):
        store.put(_charter(purpose="P3"), actor=AGENT)


def test_agent_risks_start_proposed_and_only_humans_change_status(store):
    with pytest.raises(GovernanceError, match="proposed"):
        store.put(_risk(status="open"), actor=AGENT)
    store.put(_risk(), actor=AGENT)
    with pytest.raises(GovernanceError, match="status"):
        store.put(_risk(status="open"), actor=AGENT)
    store.put(_risk(status="open"), actor=HUMAN)
    store.put(_risk(status="open", owner="dana"), actor=AGENT)  # other edits stay allowed


def test_decisions_are_accepted_by_humans_only(store):
    decision = Decision(id="D-1", project_id="demo", title="t", context="c", decision="d", rationale="r")
    store.put(decision, actor=AGENT)
    accepted = decision.model_copy(update={"status": "accepted", "decided_by": "copilot", "decided_at": NOW})
    with pytest.raises(GovernanceError):
        store.put(accepted, actor=AGENT)
    store.put(accepted, actor=HUMAN)


def test_change_request_lifecycle(store):
    cr = ChangeRequest(id="CR-1", project_id="demo", title="t", description="d", reason="r",
                       impact=ChangeImpact(scope="s", schedule_days=5, risk="r"))
    store.put(cr, actor=AGENT)
    store.put(cr.model_copy(update={"status": "submitted"}), actor=AGENT)
    with pytest.raises(GovernanceError):
        store.put(cr.model_copy(update={"status": "approved"}), actor=AGENT)
    approved = cr.model_copy(update={"status": "approved", "decided_by": "prajwal", "decided_at": NOW})
    store.put(approved, actor=HUMAN)
    # Once decided, the content is frozen: the agent can neither edit it nor declare it implemented.
    with pytest.raises(GovernanceError, match="can't be changed"):
        store.put(approved.model_copy(update={"description": "something else"}), actor=AGENT)
    with pytest.raises(GovernanceError, match="can't be changed"):
        store.put(approved.model_copy(update={"status": "implemented"}), actor=AGENT)
    with pytest.raises(GovernanceError):
        store.put(approved.model_copy(update={"status": "implemented", "title": "x"}), actor=EXECUTOR_ACTOR)
    store.put(approved.model_copy(update={"status": "implemented"}), actor=EXECUTOR_ACTOR)


def test_thresholds_are_human_only(store):
    profile = ProjectProfile(id="demo", project_id="demo", name="Demo")
    softened = profile.model_copy(update={"thresholds": RagThresholds(spi_green=0.5, spi_amber=0.4)})
    with pytest.raises(GovernanceError, match="thresholds"):
        store.put(softened, actor=AGENT)
    store.put(profile, actor=AGENT)
    with pytest.raises(GovernanceError, match="thresholds"):
        store.put(softened, actor=AGENT)
    store.put(softened, actor=HUMAN)
    store.put(softened.model_copy(update={"name": "Renamed"}), actor=AGENT)


def test_work_items_come_from_system_only(store):
    with pytest.raises(GovernanceError, match="system"):
        store.put(work_item(1), actor=AGENT)
    store.put(work_item(1), actor=SYSTEM)


def test_store_persists_to_disk(tmp_path):
    path = tmp_path / "nested" / "pm.db"
    first = Store(path)
    first.put(_risk(), actor=AGENT)
    first.close()
    second = Store(path)
    assert second.get("demo", "risk", "R-1").artifact.title == "Vendor slip"
    second.close()


def test_autonomy_level_parsing():
    assert AutonomyLevel.parse("L3") is AutonomyLevel.AUTONOMOUS
    assert AutonomyLevel.parse("draft") is AutonomyLevel.DRAFT
    assert AutonomyLevel.parse(2) is AutonomyLevel.ACT_WITH_APPROVAL
