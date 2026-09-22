"""When scheduled playbooks are due.

`python -m pm_agent run-due` is meant to be called often (every 15 minutes from
cron or launchd). Each call runs the schedules whose most recent due time has
passed since their last run. A run missed by more than the catch-up window (for
example a laptop asleep all Monday) is skipped rather than delivered late.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pm_agent.schemas import ProjectProfile, Schedule
from pm_agent.schemas.project import WEEKDAYS

CATCH_UP = timedelta(hours=24)


def _matches(schedule: Schedule, day: datetime) -> bool:
    if schedule.cadence == "daily":
        return True
    if schedule.cadence == "weekdays":
        return day.weekday() < 5
    return WEEKDAYS[day.weekday()] == schedule.day


def last_due(schedule: Schedule, timezone: str, now: datetime) -> datetime:
    """The most recent due time at or before `now` (a timezone-aware datetime), in UTC."""
    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz)
    hour, minute = map(int, schedule.time.split(":"))
    for back in range(8):
        day = local_now - timedelta(days=back)
        candidate = datetime.combine(day.date(), time(hour, minute), tzinfo=tz)
        if candidate <= local_now and _matches(schedule, candidate):
            return candidate.astimezone(ZoneInfo("UTC"))
    raise AssertionError("every cadence recurs within a week")


def schedule_key(project_id: str, schedule: Schedule) -> str:
    return f"schedule_last_run:{project_id}:{schedule.playbook}:{schedule.cadence}:{schedule.day}:{schedule.time}"


@dataclass(frozen=True)
class DueRun:
    project_id: str
    schedule: Schedule
    due_at: datetime
    key: str


def due_runs(profile: ProjectProfile, last_runs: dict[str, datetime | None], now: datetime,
             catch_up: timedelta = CATCH_UP) -> list[DueRun]:
    """Schedules of this project that should run now. `last_runs` maps schedule keys to last run times."""
    due = []
    for schedule in profile.schedules:
        if not schedule.enabled:
            continue
        due_at = last_due(schedule, profile.timezone, now)
        key = schedule_key(profile.project_id, schedule)
        last = last_runs.get(key)
        if (last is None or last < due_at) and now - due_at <= catch_up:
            due.append(DueRun(profile.project_id, schedule, due_at, key))
    return due
