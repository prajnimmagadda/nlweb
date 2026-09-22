import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pm_agent.schemas import WorkItem  # noqa: E402
from pm_agent.service import Copilot  # noqa: E402
from pm_agent.store import Store  # noqa: E402

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
HUMAN = "human:prajwal"
AGENT = "agent:copilot"
SYSTEM = "system:github-sync"


def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def work_item(number: int, state: str = "todo", *, project_id: str = "demo", created: float = 60,
              started: float | None = None, closed: float | None = None, item_type: str = "story",
              milestone: str | None = "v1", estimate: float | None = None) -> WorkItem:
    """Build a work item with times expressed as days before NOW."""
    return WorkItem(
        id=f"o/r#{number}", project_id=project_id, title=f"Item {number}", state=state, item_type=item_type,
        milestone=milestone, estimate=estimate, created_at=days_ago(created),
        started_at=days_ago(started) if started is not None else None,
        closed_at=days_ago(closed) if closed is not None else None,
    )


@pytest.fixture
def store():
    s = Store()
    yield s
    s.close()


@pytest.fixture
def copilot(store):
    return Copilot(store, clock=lambda: NOW)


@pytest.fixture
def project(copilot):
    copilot.setup_project("demo", actor=HUMAN, name="Demo", repos=["o/r"], iteration_days=14,
                          start_date="2026-08-03", target_date="2026-11-30", release_milestone="v1")
    return "demo"
