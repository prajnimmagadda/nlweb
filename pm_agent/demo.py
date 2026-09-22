"""A synthetic agile project, so PM Copilot can be tried without real tracker data."""

import random
from datetime import timedelta
from typing import Any

from pm_agent.schemas import WorkItem
from pm_agent.service import Copilot

DEMO_ACTOR = "system:demo-seed"
_POINTS = [1, 2, 3, 3, 5, 5, 8]


def seed_demo(copilot: Copilot, *, actor: str, project_id: str = "demo", seed: int = 7) -> dict[str, Any]:
    """Create a project with 12 weeks of history, a release milestone and a few risks.

    `actor` must be human: the demo approves one risk to show the governance flow.
    """
    rng = random.Random(seed)
    now = copilot.now()
    today = now.date()
    copilot.setup_project(project_id, actor=actor, name="Demo: checkout redesign", repos=["demo/app"],
                          iteration_days=14, start_date=today - timedelta(days=84),
                          target_date=today + timedelta(days=42), release_milestone="v1",
                          rationale="demo project")

    def put(number: int, state: str, *, created: float, started: float | None = None,
            closed: float | None = None, milestone: str = "v1", item_type: str = "story") -> None:
        copilot.store.put(WorkItem(
            id=f"demo/app#{number}", project_id=project_id, title=f"Demo story {number}", state=state,
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
    return {"project_id": project_id, "work_items": number, "risks": ["R-1 (open)", "R-2 (proposed)"]}
