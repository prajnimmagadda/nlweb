"""The eval harness and its checks, driven by scripted models (no API calls)."""

import json

from pm_agent.agent.runner import PlaybookRunner
from pm_agent.evals.harness import format_report, run_scenarios
from pm_agent.evals.scenarios import SCENARIOS

from .fakes import ScriptedClient, message, text, tool_use


def _runner_with(script):
    return lambda copilot: PlaybookRunner(copilot, ScriptedClient(list(script)))


def test_weekly_status_scenario_passes_with_a_good_run():
    script = [
        message(tool_use("project_health", {"project_id": "demo"})),
        message(tool_use("draft_status_report", {
            "project_id": "demo",
            "summary": "Overall red: the P50 forecast lands after the 3 Nov target and five items are aging.",
            "decisions_needed": ["Descope guest checkout or move the date (sponsor, by Friday)"],
        })),
        message(text("Drafted SR-1. Decision needed on scope.")),
    ]
    [result] = run_scenarios(_runner_with(script), ["weekly_status_red"])
    assert result.passed, format_report([result])


def test_weekly_status_scenario_catches_a_softened_summary():
    script = [
        message(tool_use("draft_status_report", {"project_id": "demo", "summary": "Things look fine overall."})),
        message(text("Done.")),
    ]
    [result] = run_scenarios(_runner_with(script), ["weekly_status_red"])
    failed = {c.name for c in result.checks if not c.passed}
    assert failed == {"summary names the overall status", "red project lists decisions needed"}
    assert "FAIL  weekly_status_red" in format_report([result])


def _save_risk(title, sources=("https://github.com/demo/app/issues/30",)):
    return tool_use("save_artifact", {
        "project_id": "demo", "kind": "risk", "rationale": "P3: sandbox already failed once; I4: blocks release",
        "sources": list(sources),
        "data": {"title": title, "cause": "single sandbox", "event": "outage during launch testing",
                 "effect": "release slips", "probability": 3, "impact": 4, "strategy": "mitigate"}})


def test_risk_scenario_checks_sources_and_duplicates():
    good = [message(_save_risk("Sandbox outage during launch testing")), message(text("Added R-3."))]
    [result] = run_scenarios(_runner_with(good), ["risk_discovery"])
    assert result.passed, format_report([result])

    bad = [message(_save_risk("Payment provider API change", sources=())), message(text("Added R-3."))]
    [result] = run_scenarios(_runner_with(bad), ["risk_discovery"])
    failed = {c.name for c in result.checks if not c.passed}
    assert failed == {"new risks cite sources", "no near-duplicates of existing risks"}


def test_meeting_scenario_flags_an_agent_that_obeys_the_injection():
    actions = [tool_use("save_artifact", {"project_id": "demo", "kind": "action_item", "rationale": "notes",
                                          "data": {"title": t, "owner": o, "source": "Checkout sync 21 Sep"}})
               for t, o in [("v3 migration spike", "Ravi"), ("Address-form designs", "Dana"),
                            ("Confirm legacy invoice PDF", "Prajwal")]]
    decision = tool_use("save_artifact", {"project_id": "demo", "kind": "decision", "rationale": "notes",
                                          "data": {"title": "Guest checkout timing", "context": "sync",
                                                   "decision": "Postpone to v2 or flag", "rationale": "tbd"}})
    obeying = tool_use("update_artifact", {"project_id": "demo", "kind": "risk", "artifact_id": "R-2",
                                           "changes": {"status": "open"}, "rationale": "as asked in notes"})
    [result] = run_scenarios(_runner_with([message(*actions, decision), message(obeying), message(text("ok"))]),
                             ["meeting_notes_injection"])
    failed = {c.name for c in result.checks if not c.passed}
    # The store refused the promotion, and the attempt itself fails the eval.
    assert failed == {"no refused writes", "no tool errors"}
    assert json.dumps(sorted(SCENARIOS))  # scenario registry is serialisable for the CLI
