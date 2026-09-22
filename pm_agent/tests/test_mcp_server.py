import asyncio
import json

import pytest

from pm_agent.mcp_server import ToolError, build_server, prompt_name
from pm_agent.playbooks import load_playbooks


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def server(copilot):
    return build_server(copilot)


def _text(result) -> str:
    content = result.content if hasattr(result, "content") else result[0]
    return content[0].text


def test_tools_cover_every_playbook(server):
    tools = {t.name for t in run(server.list_tools())}
    for playbook in load_playbooks().values():
        missing = set(playbook.tools) - tools
        assert not missing, f"{playbook.id} references unknown tools {missing}"


def test_human_only_actions_are_not_exposed(server):
    tools = {t.name for t in run(server.list_tools())}
    assert not tools & {"approve", "reject", "set_thresholds"}


def test_prompts_render_each_playbook(server):
    prompts = {p.name for p in run(server.list_prompts())}
    assert prompts == {prompt_name(p) for p in load_playbooks().values()}
    result = run(server.get_prompt("develop_schedule", {"project_id": "demo"}))
    text = result.messages[0].content.text
    assert "Project: demo" in text and "## Guardrails" in text and "forecast_release" in text


def test_writes_are_attributed_to_the_agent(server, copilot, project):
    risk = {"title": "t", "cause": "c", "event": "e", "effect": "f", "probability": 2, "impact": 3}
    result = run(server.call_tool("save_artifact", {"project_id": project, "kind": "risk", "data": risk,
                                                    "rationale": "seen in standup"}))
    assert json.loads(_text(result))["id"] == "R-1"
    stored = copilot.store.get(project, "risk", "R-1")
    assert (stored.actor, stored.rationale) == ("agent:copilot", "seen in standup")


def test_refusals_reach_the_model_with_their_reason(server, project):
    risk = {"title": "t", "cause": "c", "event": "e", "effect": "f", "probability": 2, "impact": 3,
            "status": "open"}
    with pytest.raises(ToolError, match="proposed"):
        run(server.call_tool("save_artifact", {"project_id": project, "kind": "risk", "data": risk,
                                               "rationale": "x"}))
    with pytest.raises(ToolError, match="no project"):
        run(server.call_tool("project_health", {"project_id": "missing"}))


def test_change_control_tools_file_requests_as_the_agent(server, copilot, project):
    tools = {t.name for t in run(server.list_tools())}
    assert {"propose_baseline", "baseline_variance", "assess_change_request", "propose_github_issues",
            "propose_milestone_move", "list_inbox"} <= tools
    result = run(server.call_tool("propose_github_issues", {
        "project_id": project, "repo": "o/r", "issues": [{"title": "New story", "milestone": "v1"}],
        "rationale": "gap in the story map"}))
    assert json.loads(_text(result))["status"] == "pending"
    stored = copilot.store.get(project, "action_request", "AR-1")
    assert (stored.actor, stored.artifact.title) == ("agent:copilot", "Create 1 issue in o/r")
    inbox = json.loads(_text(run(server.call_tool("list_inbox", {"project_id": project}))))
    assert [i["id"] for i in inbox["items"]] == ["AR-1"]
    with pytest.raises(ToolError, match="isn't one of this project's repos"):
        run(server.call_tool("propose_milestone_move", {"project_id": project, "repo": "x/y", "issue_numbers": [1],
                                                        "milestone": "v2", "rationale": "r"}))
