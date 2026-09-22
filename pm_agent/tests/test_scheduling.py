from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pm_agent.governance import GovernanceError
from pm_agent.scheduling import due_runs, last_due, schedule_key
from pm_agent.schemas import ProjectProfile, Schedule

from .conftest import AGENT, HUMAN

# Tuesday 22 Sep 2026, 12:00 UTC = 17:30 in Asia/Kolkata
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_last_due_by_cadence():
    daily = Schedule(playbook="p", cadence="daily", time="08:00")
    assert last_due(daily, "UTC", NOW) == _utc(2026, 9, 22, 8)
    assert last_due(daily.model_copy(update={"time": "13:00"}), "UTC", NOW) == _utc(2026, 9, 21, 13)

    weekly = Schedule(playbook="p", cadence="weekly", day="mon", time="09:00")
    assert last_due(weekly, "UTC", NOW) == _utc(2026, 9, 21, 9)

    weekdays = Schedule(playbook="p", cadence="weekdays", time="18:00")
    monday_noon = _utc(2026, 9, 21, 12)
    assert last_due(weekdays, "UTC", monday_noon) == _utc(2026, 9, 18, 18)  # Friday evening


def test_last_due_uses_the_profile_timezone():
    schedule = Schedule(playbook="p", cadence="daily", time="09:00")
    assert last_due(schedule, "Asia/Kolkata", NOW) == _utc(2026, 9, 22, 3, 30)
    assert last_due(schedule, "America/Los_Angeles", NOW) == _utc(2026, 9, 21, 16)


def test_due_runs_catch_up_window():
    schedule = Schedule(playbook="monitor_and_control_performance", cadence="daily", time="08:00")
    profile = ProjectProfile(id="p", project_id="p", name="P", schedules=[schedule])
    key = schedule_key("p", schedule)
    assert [d.key for d in due_runs(profile, {}, NOW)] == [key]
    assert due_runs(profile, {key: NOW - timedelta(hours=1)}, NOW) == []
    assert len(due_runs(profile, {key: NOW - timedelta(days=1)}, NOW)) == 1

    weekly = Schedule(playbook="x", cadence="weekly", day="fri", time="08:00")
    stale = ProjectProfile(id="p", project_id="p", name="P", schedules=[weekly])
    assert due_runs(stale, {}, NOW) == []  # last Friday is more than a day ago: skip, don't send late
    disabled = profile.model_copy(update={"schedules": [schedule.model_copy(update={"enabled": False})]})
    assert due_runs(disabled, {}, NOW) == []


def test_schedule_validation():
    with pytest.raises(ValidationError, match="HH:MM"):
        Schedule(playbook="p", cadence="daily", time="8am")
    with pytest.raises(ValidationError, match="day"):
        Schedule(playbook="p", cadence="weekly", time="08:00")
    with pytest.raises(ValidationError, match="day"):
        Schedule(playbook="p", cadence="daily", day="mon")
    with pytest.raises(ValidationError, match="timezone"):
        ProjectProfile(id="p", project_id="p", name="P", timezone="Mars/Olympus")
    with pytest.raises(ValidationError, match="email"):
        ProjectProfile(id="p", project_id="p", name="P", report_recipients=["not-an-email"])


def test_schedules_and_reach_are_human_only(copilot, project):
    schedule = {"playbook": "identify_and_analyze_risks", "cadence": "weekly", "day": "mon", "time": "08:30"}
    with pytest.raises(GovernanceError):
        copilot.add_schedule(project, schedule, actor=AGENT)
    out = copilot.add_schedule(project, schedule, actor=HUMAN)
    assert out["schedules"][0]["playbook"] == "identify_and_analyze_risks"
    with pytest.raises(LookupError):
        copilot.add_schedule(project, {**schedule, "playbook": "nope"}, actor=HUMAN)

    for field, value in [("report_recipients", ["boss@example.com"]), ("calendar_query", "Checkout"),
                         ("timezone", "Europe/Berlin")]:
        with pytest.raises(GovernanceError, match="only a human"):
            copilot.setup_project(project, actor=AGENT, **{field: value})
        copilot.setup_project(project, actor=HUMAN, **{field: value})
    copilot.setup_project(project, actor=AGENT, github_project="acme/7")  # reading config is fine
    with pytest.raises(GovernanceError):
        copilot.store.put(copilot.profile(project).model_copy(update={"schedules": []}), actor=AGENT)
    copilot.remove_schedule(project, "identify_and_analyze_risks", actor=HUMAN)
    assert copilot.profile(project).schedules == []
