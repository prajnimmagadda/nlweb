"""A synthetic agile project, so PM Copilot can be tried without real tracker data."""

import random
from datetime import timedelta
from typing import Any

from pm_agent.schemas import WorkItem
from pm_agent.service import Copilot

DEMO_ACTOR = "system:demo-seed"
_POINTS = [1, 2, 3, 3, 5, 5, 8]
# Requests that arrived after the baseline was approved: the scope growth the change request deals with.
_LATE_ADDITIONS = [
    "Saved cards: list cards on file", "Saved cards: set a default card", "Saved cards: remove a card",
    "Saved cards: expiry reminders", "Saved cards: CVC re-check", "Saved cards: migrate legacy tokens",
    "Promo codes at checkout", "Gift wrap option",
]


def seed_demo(copilot: Copilot, *, actor: str, project_id: str = "demo", seed: int = 7) -> dict[str, Any]:
    """Create a project with 12 weeks of history, a release milestone, a baseline and an inbox to work through.

    `actor` must be human: the demo approves one risk and the first baseline to show the governance flow.
    """
    rng = random.Random(seed)
    now = copilot.now()
    today = now.date()
    copilot.setup_project(project_id, actor=actor, name="Demo: checkout redesign", repos=["demo/app"],
                          iteration_days=14, start_date=today - timedelta(days=84),
                          target_date=today + timedelta(days=42), release_milestone="v1",
                          rationale="demo project")
    profile = copilot.profile(project_id)
    copilot.store.put(profile.model_copy(update={"sandbox": True}), actor=actor,
                      rationale="demo data: never sync or write to GitHub")

    def put(number: int, state: str, *, created: float, started: float | None = None,
            closed: float | None = None, milestone: str = "v1", item_type: str = "story",
            title: str | None = None) -> None:
        copilot.store.put(WorkItem(
            id=f"demo/app#{number}", project_id=project_id, title=title or f"Demo story {number}", state=state,
            item_type=item_type, milestone=milestone, estimate=None if item_type == "epic" else rng.choice(_POINTS),
            url=f"https://github.com/demo/app/issues/{number}",
            created_at=now - timedelta(days=created),
            started_at=now - timedelta(days=started) if started is not None else None,
            closed_at=now - timedelta(days=closed) if closed is not None else None,
        ), actor=DEMO_ACTOR)

    number = 1
    put(number, "in_progress", created=90, started=84, item_type="epic")
    for _ in range(45):  # completed work, spread over the last 12 weeks
        number += 1
        closed = rng.uniform(0.5, 84)
        started = closed + rng.lognormvariate(1.0, 0.6)
        put(number, "done", created=started + rng.uniform(1, 20), started=started, closed=closed)
    for state, count, age in [("todo", 18, None), ("in_progress", 4, (1, 12)), ("blocked", 2, (5, 9))]:
        for _ in range(count):
            number += 1
            started = rng.uniform(*age) if age else None
            put(number, state, created=(started or 0) + rng.uniform(2, 40), started=started)
    for _ in range(3):  # next release, outside the forecast scope
        number += 1
        put(number, "todo", created=rng.uniform(1, 30), milestone="v2")

    agent = "agent:copilot"
    copilot.save_artifact(project_id, "risk", {
        "title": "Payment provider API change", "cause": "the provider is deprecating the v2 API",
        "event": "checkout calls start failing before migration is done", "effect": "lost orders and a slip",
        "probability": 3, "impact": 4, "owner": "dana", "strategy": "mitigate",
        "triggers": ["provider sunset notice"], "responses": ["migrate to v3 in iteration 5"],
    }, actor=agent, rationale="demo", sources=["https://github.com/demo/app/issues/12"])
    copilot.approve(project_id, "risk", "R-1", actor=actor, note="demo: promoted by a human")
    copilot.save_artifact(project_id, "risk", {
        "title": "Two blocked stories wait on design", "cause": "one designer shared across teams",
        "event": "design sign-off keeps slipping", "effect": "blocked work ages and throughput drops",
        "probability": 4, "impact": 3, "strategy": "escalate",
    }, actor=agent, rationale="demo: seen in aging WIP")

    copilot.propose_baseline(project_id, actor=agent, name="Release 1 plan", reason="demo: scope agreed at kickoff")
    copilot.approve(project_id, "baseline", "B-1", actor=actor, note="demo: approved by a human")
    late = []
    for title in _LATE_ADDITIONS:
        number += 1
        late.append(f"demo/app#{number}")
        put(number, "todo", created=rng.uniform(1, 10), title=title)

    copilot.update_artifact(project_id, "risk", "R-1", {"links": [{"rel": "work_item", "target": late[5]}]},
                            actor=agent, rationale="demo: token migration depends on the provider's v3 API")
    copilot.save_artifact(project_id, "change_request", {
        "title": "Move saved cards to v2 and shift the release by 3 weeks",
        "description": "Six saved-card stories joined v1 after the baseline. Move them to v2 and move the "
                       "target date so the P85 forecast meets it.",
        "reason": "Scope grew 12% since baseline B-1 and the P85 forecast is five weeks past the target.",
        "requested_by": "agent:copilot",
        "impact": {"scope": "Six stories leave v1; saved cards ship in v2.", "schedule_days": 24,
                   "risk": "Schedule risk for v1 drops; R-1 no longer blocks the release.",
                   "stakeholders": "Support asked for saved cards; tell them it moves to v2.",
                   "benefits": "The checkout redesign ships on a date the team can meet."},
        "proposal": {"move_out_item_ids": late[:6], "move_to_milestone": "v2",
                     "new_target_date": (today + timedelta(days=66)).isoformat()},
        "options": ["Move saved cards to v2 and shift the date 3 weeks (recommended)",
                    "Move saved cards to v2 only (P85 still misses the date)",
                    "Keep everything and shift the date 5 weeks"],
        "recommendation": "Move saved cards and shift 3 weeks: it is the smallest change that meets the P85 forecast.",
    }, actor=agent, rationale="demo: scope growth since B-1",
        sources=[f"https://github.com/{r.replace('#', '/issues/')}" for r in late[:2]])
    copilot.assess_change_request(project_id, "CR-1", requested_by=agent)
    copilot.update_artifact(project_id, "change_request", "CR-1", {"status": "submitted"}, actor=agent,
                            rationale="demo: ready for a decision")

    copilot.propose_action(project_id, "github.create_issues", {"repo": "demo/app", "issues": [
        {"title": "Address autocomplete on the shipping form", "labels": ["points:3"], "milestone": "v1",
         "body": "Acceptance criteria:\n- Suggestions appear after 3 characters\n- Keyboard selectable"},
        {"title": "Show delivery estimate before payment", "labels": ["points:2"], "milestone": "v1",
         "body": "Acceptance criteria:\n- Estimate shown on the review step\n- Updates when the address changes"},
        {"title": "Accessible error messages on card fields", "labels": ["points:2"], "milestone": "v1",
         "body": "Acceptance criteria:\n- Errors announced by screen readers\n- Field is focused on error"},
    ]}, actor=agent, title="Create 3 issues in demo/app",
        rationale="demo: the story map has three stories with no GitHub issue yet")

    copilot.save_artifact(project_id, "decision", {
        "title": "Use the provider's hosted card fields",
        "context": "The v3 payment API offers hosted fields that keep card data off our servers.",
        "decision": "Adopt hosted fields for the new checkout.",
        "rationale": "Smaller compliance scope and less custom validation code.",
        "alternatives": ["Keep our own card form and tokenize client-side"],
    }, actor=agent, rationale="demo: raised in design review")
    return {"project_id": project_id, "work_items": number, "risks": ["R-1 (open)", "R-2 (proposed)"],
            "baseline": "B-1 (approved)", "inbox": ["CR-1", "AR-1", "D-1", "R-2"]}
