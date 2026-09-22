import time

import pytest
from starlette.testclient import TestClient

from pm_agent.agent.runner import RunResult, ToolCall
from pm_agent.demo import seed_demo
from pm_agent.web.app import create_app

from .conftest import HUMAN

TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"


class FakeRunner:
    def __init__(self, copilot, **options):
        self.copilot, self.options, self.closed = copilot, options, False

    def run(self, playbook, project_id, *, material=None):
        return RunResult(playbook=f"pmbok8.x.{playbook}", project_id=project_id, model=self.options.get("model", "m"),
                         trigger="manual", status="completed", summary=f"read {len(material or '')} characters",
                         turns=2, tool_calls=[ToolCall("project_health", True)])

    def close(self):
        self.closed = True


@pytest.fixture
def client(copilot):
    seed_demo(copilot, actor=HUMAN)
    app = create_app(copilot, user=HUMAN, token=TOKEN, runner_factory=FakeRunner)
    with TestClient(app, base_url=BASE, headers={"X-PM-Token": TOKEN}) as c:
        yield c


def test_requests_need_the_token_the_host_and_json(client):
    assert client.get("/", headers={"X-PM-Token": ""}).status_code == 200  # the page itself holds no data
    assert client.get("/api/session", headers={"X-PM-Token": "wrong"}).status_code == 401
    assert client.get("/api/session", headers={"Host": "evil.example:8765"}).status_code == 421
    assert client.get("/api/session", headers={"Host": "localhost:8765"}).status_code == 200
    form = client.post("/api/projects/demo/sync", content="a=1",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert form.status_code == 415
    response = client.get("/api/session")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store" and response.headers["x-frame-options"] == "DENY"
    assert response.json()["user"] == HUMAN


def test_dashboard_reports_computed_health_and_the_inbox(client):
    d = client.get("/api/projects/demo/dashboard").json()
    assert d["health"]["overall"] == "red" and d["project"]["sandbox"] is True
    assert d["counts"] == {"inbox": 4, "change_requests": 1}
    assert {i["id"] for i in d["inbox"]} == {"CR-1", "AR-1", "D-1", "R-2"}
    assert client.get("/api/projects/missing/dashboard").status_code == 404
    assert "sandbox" in client.post("/api/projects/demo/sync", json={}).json()["error"]


def test_change_approval_through_the_ui(client, copilot):
    cr = client.get("/api/projects/demo/items/change_request/CR-1").json()
    assert cr["analysis_current"] is True and cr["data"]["status"] == "submitted"
    stale = client.post("/api/projects/demo/items/change_request/CR-1/decision",
                        json={"decision": "approve", "version": cr["version"] - 1})
    assert stale.status_code == 400 and "changed since you opened it" in stale.json()["error"]
    no_reason = client.post("/api/projects/demo/items/change_request/CR-1/decision", json={"decision": "reject"})
    assert no_reason.status_code == 400

    out = client.post("/api/projects/demo/items/change_request/CR-1/decision",
                      json={"decision": "approve", "note": "date matters", "version": cr["version"]}).json()
    assert out["baseline"] == "B-2" and out["actions_waiting_for_approval"] == ["AR-2"]
    assert copilot.store.get("demo", "change_request", "CR-1").actor == HUMAN

    moved = client.post("/api/projects/demo/items/action_request/AR-2/decision", json={"decision": "approve"}).json()
    assert moved["status"] == "executed" and moved["result"]["simulated"] is True  # demo: never sent to GitHub
    assert client.get("/api/projects/demo/items/change_request/CR-1").json()["data"]["status"] == "implemented"
    history = client.get("/api/projects/demo/items/change_request/CR-1/history").json()["items"]
    assert [v["actor"] for v in history][-1] == "system:executor"


def test_reject_records_the_reason(client, copilot):
    client.post("/api/projects/demo/items/action_request/AR-1/decision",
                json={"decision": "reject", "note": "not yet"}).raise_for_status()
    stored = copilot.store.get("demo", "action_request", "AR-1").artifact
    assert (stored.status, stored.decision_note, stored.decided_by) == ("rejected", "not yet", "prajwal")
    assert client.get("/api/projects/demo/counts").json()["inbox"] == 3


def test_settings_and_thresholds(client, copilot):
    body = {"profile": {"name": "Checkout", "calendar_query": None, "report_recipients": ["sponsor@example.com"],
                        "timezone": "Asia/Kolkata"},
            "thresholds": {"scope_growth_amber": 0.05, "scope_growth_red": 0.15}}
    client.put("/api/projects/demo/settings", json=body).raise_for_status()
    profile = copilot.profile("demo")
    assert (profile.name, profile.timezone, profile.thresholds.scope_growth_amber) == ("Checkout", "Asia/Kolkata", 0.05)
    bad = client.put("/api/projects/demo/settings", json={"profile": {"timezone": "Mars/Base"}})
    assert bad.status_code == 400 and "unknown timezone" in bad.json()["error"]
    sneaky = client.put("/api/projects/demo/settings", json={"profile": {"sandbox": False}})
    assert sneaky.status_code == 400 and copilot.profile("demo").sandbox is True
    order = client.put("/api/projects/demo/settings", json={"thresholds": {"blocked_amber": 5, "blocked_red": 2}})
    assert order.status_code == 400


def test_schedules_and_baselines(client, copilot):
    client.post("/api/projects/demo/schedules",
                json={"playbook": "monitor_and_control_performance", "cadence": "weekly", "day": "mon",
                      "time": "08:00"}).raise_for_status()
    assert [s.playbook for s in copilot.profile("demo").schedules] == ["monitor_and_control_performance"]
    client.delete("/api/projects/demo/schedules/monitor_and_control_performance").raise_for_status()
    assert copilot.profile("demo").schedules == []

    assert client.post("/api/projects/demo/baseline", json={"name": ""}).status_code == 400
    proposed = client.post("/api/projects/demo/baseline", json={"name": "Accept growth", "reason": "sponsor ok"}).json()
    assert proposed["id"] == "B-2"
    assert client.get("/api/projects/demo/baseline").json()["baseline"]["id"] == "B-1"


def test_runs_happen_in_the_background(client):
    names = [p["name"] for p in client.get("/api/playbooks").json()["items"]]
    assert names[0] == "initiate_project" and names[-1] == "assess_and_implement_changes"
    assert client.post("/api/projects/demo/runs", json={"playbook": "nope"}).status_code == 400
    too_long = client.post("/api/projects/demo/runs", json={"playbook": "develop_schedule", "max_turns": 500})
    assert too_long.status_code == 400
    job_id = client.post("/api/projects/demo/runs", json={"playbook": "develop_schedule", "material": "notes",
                                                          "model": "claude-opus-5", "max_turns": 5}).json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "finished" and job["result"]["summary"] == "read 5 characters"
    assert client.get("/api/jobs/unknown").status_code == 404


def test_bad_input_is_a_400_not_a_crash(client, copilot):
    for body in [{"decision": "approve", "note": ["x"]}, {"decision": "approve", "version": True},
                 {"decision": "maybe"}]:
        assert client.post("/api/projects/demo/items/risk/R-2/decision", json=body).status_code == 400
    assert client.post("/api/projects/demo/baseline", json={"name": 5}).status_code == 400
    assert client.post("/api/projects/demo/runs", json={"playbook": ["develop_schedule"]}).status_code == 400
    assert client.post("/api/projects/demo/sync", content=b"[1, 2]",
                       headers={"Content-Type": "application/json"}).status_code == 400
    name = copilot.profile("demo").name
    half = client.put("/api/projects/demo/settings", json={"profile": {"name": "Changed"},
                                                          "thresholds": {"spi_green": 0.5, "spi_amber": 0.9}})
    assert half.status_code == 400 and copilot.profile("demo").name == name  # nothing saved


def test_failed_or_rejected_actions_can_be_filed_again(client, copilot):
    client.post("/api/projects/demo/items/action_request/AR-1/decision",
                json={"decision": "reject", "note": "later"}).raise_for_status()
    out = client.post("/api/projects/demo/items/action_request/AR-1/refile", json={}).json()
    assert out["id"] == "AR-2" and out["counts"]["inbox"] == 4
    assert copilot.store.get("demo", "action_request", "AR-2").artifact.replaces == "AR-1"
    assert client.post("/api/projects/demo/items/action_request/AR-2/refile", json={}).status_code == 400
    inbox = {i["id"]: i for i in client.get("/api/projects/demo/inbox").json()["items"]}
    assert inbox["CR-1"]["requested_by"] == "agent:copilot"  # not the engine that wrote the analysis last
