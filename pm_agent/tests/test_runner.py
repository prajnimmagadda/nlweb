import json
from datetime import timedelta

import anthropic
import httpx2
import pytest

from pm_agent.agent.runner import FALLBACK_BETA, RUNNER_ACTOR, PlaybookRunner, echo_content, run_due
from pm_agent.demo import seed_demo
from pm_agent.integrations.google import GoogleClient

from .conftest import HUMAN, NOW
from .fakes import ScriptedClient, message, text, tool_use


@pytest.fixture
def demo(copilot):
    seed_demo(copilot, actor=HUMAN)
    return "demo"


def _runner(copilot, script, **kwargs):
    client = ScriptedClient(script)
    return PlaybookRunner(copilot, client, **kwargs), client


def test_request_shape_and_tool_allowlist(copilot, demo):
    runner, client = _runner(copilot, [message(text("Nothing to do."))])
    result = runner.run("develop_schedule", demo)
    request = client.requests[0]
    assert request["model"] == "claude-opus-5"
    assert request["fallbacks"] == "default" and request["betas"] == [FALLBACK_BETA]
    assert request["thinking"] == {"type": "adaptive"} and request["output_config"] == {"effort": "high"}
    assert request["cache_control"] == {"type": "ephemeral"}
    names = [t["name"] for t in request["tools"]]
    assert names == sorted(names)  # stable order keeps the prompt cache warm
    assert set(names) == {"sync_github", "flow_metrics", "forecast_release", "agile_evm", "project_health",
                          "artifact_schema", "save_artifact"}
    assert "Today is 2026-09-22" in request["messages"][0]["content"]
    assert result.status == "completed" and result.summary == "Nothing to do."


def test_tool_calls_run_against_the_real_tools(copilot, demo):
    runner, client = _runner(copilot, [
        message(text("Checking health."), tool_use("project_health", {"project_id": demo})),
        message(text("Overall is red.")),
    ])
    result = runner.run("develop_schedule", demo)
    [health] = client.tool_results(1)
    assert "is_error" not in health
    assert json.loads(health["content"])["overall"] == copilot.health(demo)["overall"]
    assert result.turns == 2 and result.tool_calls[0].ok
    assert result.usage == {"input_tokens": 200, "output_tokens": 40, "cache_read_input_tokens": 160,
                            "cache_creation_input_tokens": 0}


def test_parallel_calls_return_in_one_message_and_errors_reach_the_model(copilot, demo):
    risk = {"title": "t", "cause": "c", "event": "e", "effect": "f", "probability": 2, "impact": 2, "status": "open"}
    runner, client = _runner(copilot, [
        message(tool_use("save_artifact", {"project_id": demo, "kind": "risk", "data": risk, "rationale": "x"}),
                tool_use("draft_status_email", {"project_id": demo}),
                tool_use("flow_metrics", {"project_id": demo})),
        message(text("Done.")),
    ])
    result = runner.run("develop_schedule", demo)
    refused, not_allowed, ok = client.tool_results(1)
    assert refused["is_error"] and "proposed" in refused["content"]
    assert not_allowed["is_error"] and "not available" in not_allowed["content"]
    assert "is_error" not in ok
    assert [c.ok for c in result.tool_calls] == [False, False, True]
    assert copilot.store.audit(demo, limit=50)[1]["action"] == "write_denied"


def test_runs_are_audited_under_their_own_actor(copilot, demo):
    runner, _ = _runner(copilot, [message(text("Summary for you."))])
    runner.run("identify_and_analyze_risks", demo, trigger="schedule")
    [run] = copilot.runs(demo)
    assert run["actor"] == RUNNER_ACTOR and run["trigger"] == "schedule"
    assert run["status"] == "completed" and run["summary"] == "Summary for you."
    assert run["playbook"] == "pmbok8.risk.identify_and_analyze_risks"


def test_stop_conditions(copilot, demo):
    looping = [message(tool_use("flow_metrics", {"project_id": demo})) for _ in range(3)]
    runner, _ = _runner(copilot, looping, max_turns=3)
    assert runner.run("develop_schedule", demo).status == "max_turns"

    refusal = message(stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber",
                                                           "explanation": None})
    runner, _ = _runner(copilot, [refusal])
    result = runner.run("develop_schedule", demo)
    assert (result.status, result.error) == ("refusal", "declined (cyber)")

    runner, _ = _runner(copilot, [message(text("partial"), stop_reason="max_tokens")])
    assert runner.run("develop_schedule", demo).status == "max_tokens"


def test_api_errors_end_the_run_cleanly(copilot, demo):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = anthropic.InternalServerError("overloaded", response=httpx2.Response(529, request=request), body=None)
    runner, _ = _runner(copilot, [overloaded])
    result = runner.run("develop_schedule", demo)
    assert result.status == "api_error" and "529" in result.error
    assert copilot.runs(demo)[0]["status"] == "api_error"


def test_missing_credentials_end_the_run_cleanly(copilot, demo):
    no_auth = TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token..."')
    runner, _ = _runner(copilot, [no_auth])
    result = runner.run("develop_schedule", demo)
    assert result.status == "api_error" and "ANTHROPIC_API_KEY" in result.error
    runner, _ = _runner(copilot, [TypeError("something else")])
    with pytest.raises(TypeError):
        runner.run("develop_schedule", demo)


def test_fallback_turns_are_echoed_safely(copilot, demo):
    fallback = {"type": "fallback", "from": {"model": "claude-opus-5"}, "to": {"model": "claude-opus-4-8"},
                "trigger": {"type": "refusal", "category": "cyber"}}
    served = message(
        {"type": "thinking", "thinking": "", "signature": "s"}, text("partial"),
        tool_use("flow_metrics", {"project_id": demo}, "toolu_declined"), fallback,
        tool_use("project_health", {"project_id": demo}, "toolu_kept"),
        model="claude-opus-4-8",
        usage={"input_tokens": 10, "output_tokens": 5, "iterations": [
            {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 10, "output_tokens": 5,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}]})
    assert [b.type for b in echo_content(list(served.content))] == ["text", "fallback", "tool_use"]

    runner, client = _runner(copilot, [served, message(text("ok"))])
    result = runner.run("develop_schedule", demo)
    assert [r["tool_use_id"] for r in client.tool_results(1)] == ["toolu_kept"]
    assert result.served_by == ["claude-opus-4-8"]


def test_google_tools_only_when_connected(copilot, demo):
    runner, client = _runner(copilot, [message(text("ok"))])
    runner.run("manage_communications", demo)
    assert not {"list_meetings", "read_meeting_notes"} & {t["name"] for t in client.requests[0]["tools"]}

    copilot.google = GoogleClient(lambda: "token", set())
    runner, client = _runner(copilot, [message(text("ok"))])
    runner.run("manage_communications", demo, material="notes")
    names = {t["name"] for t in client.requests[0]["tools"]}
    assert {"list_meetings", "read_meeting_notes"} <= names and "draft_status_email" not in names
    assert "<material>\nnotes\n</material>" in client.requests[0]["messages"][0]["content"]


def test_run_due_runs_each_due_schedule_once(copilot, demo):
    copilot.add_schedule(demo, {"playbook": "monitor_and_control_performance", "cadence": "daily",
                                "time": "08:00"}, actor=HUMAN)
    made = []

    def make_runner():
        runner, _ = _runner(copilot, [message(text("Weekly status drafted."))])
        made.append(runner)
        return runner

    assert run_due(copilot, make_runner, dry_run=True)[0]["status"] == "due"
    assert made == []
    [outcome] = run_due(copilot, make_runner)
    assert outcome["status"] == "completed" and outcome["playbook"] == "monitor_and_control_performance"
    assert run_due(copilot, make_runner) == []  # already ran for today's 08:00
    later = NOW + timedelta(days=1)
    assert len(run_due(copilot, make_runner, now=later, dry_run=True)) == 1


def test_requests_serialize_through_the_real_sdk(copilot, demo):
    """The real Anthropic client over a mock transport: checks the wire request and response parsing."""
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append((request.headers, body))
        if len(seen) == 1:
            content = [{"type": "tool_use", "id": "toolu_1", "name": "project_health",
                        "input": {"project_id": demo}}]
            stop = "tool_use"
        else:
            content, stop = [{"type": "text", "text": "Overall red."}], "end_turn"
        return httpx2.Response(200, json={
            "id": f"msg_{len(seen)}", "type": "message", "role": "assistant", "model": body["model"],
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 50, "cache_read_input_tokens": 1000,
                      "cache_creation_input_tokens": 0}})

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    result = PlaybookRunner(copilot, client).run("develop_schedule", demo)
    headers, first = seen[0]
    assert headers["anthropic-beta"] == FALLBACK_BETA
    assert first["fallbacks"] == "default" and first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"} and first["cache_control"] == {"type": "ephemeral"}
    assert first["tools"][0]["input_schema"]["type"] == "object"
    _, second = seen[1]
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_1"
    assert json.loads(tool_result["content"])["overall"] == "red"
    assert result.status == "completed" and result.summary == "Overall red."
    assert result.usage["cache_read_input_tokens"] == 2000
