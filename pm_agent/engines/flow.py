"""Flow metrics for agile work: throughput, cycle time, lead time, WIP and aging.

All values are computed deterministically from work items; nothing here involves
a model.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from pm_agent.schemas import WorkItem
from pm_agent.schemas.work import ACTIVE_STATES

_DAY_SECONDS = 86400.0


def percentile(values: Iterable[float], p: float) -> float | None:
    """Nearest-rank percentile: at least p% of values are <= the result."""
    data = list(values)
    if not data:
        return None
    return float(np.percentile(data, p, method="inverted_cdf"))


def _days(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / _DAY_SECONDS, 0.0)


def _completed(items: Iterable[WorkItem], start: date, end: date) -> list[WorkItem]:
    return [i for i in items if i.state == "done" and i.closed_at and start <= i.closed_at.date() <= end]


def daily_throughput(items: Iterable[WorkItem], start: date, end: date) -> list[int]:
    """Items completed on each calendar day from start to end, inclusive."""
    if end < start:
        return []
    counts = [0] * ((end - start).days + 1)
    for item in _completed(items, start, end):
        counts[(item.closed_at.date() - start).days] += 1
    return counts


def cycle_times(items: Iterable[WorkItem], start: date, end: date) -> list[float]:
    """Days from start of work to completion, for completed items whose start is known."""
    return [_days(i.started_at, i.closed_at) for i in _completed(items, start, end) if i.started_at]


def lead_times(items: Iterable[WorkItem], start: date, end: date) -> list[float]:
    """Days from creation to completion."""
    return [_days(i.created_at, i.closed_at) for i in _completed(items, start, end)]


@dataclass(frozen=True)
class AgingItem:
    id: str
    title: str
    state: str
    age_days: float
    age_basis: str  # "started_at" or "created_at" when the start of work is unknown
    over_p85: bool


@dataclass(frozen=True)
class FlowMetrics:
    window_start: date
    window_end: date
    throughput_total: int
    throughput_per_week: float
    cycle_time_p50: float | None
    cycle_time_p85: float | None
    cycle_time_samples: int
    lead_time_p50: float | None
    lead_time_p85: float | None
    wip: int
    blocked: int
    aging: list[AgingItem] = field(default_factory=list)
    daily_throughput: list[int] = field(default_factory=list)

    @property
    def aging_over_p85(self) -> list[AgingItem]:
        return [a for a in self.aging if a.over_p85]


def compute_flow(items: Iterable[WorkItem], as_of: datetime, window_days: int = 90,
                 include_types: tuple[str, ...] = ("story", "bug", "task")) -> FlowMetrics:
    """Flow metrics over the trailing window ending at as_of. Epics are excluded by default."""
    if window_days < 1:
        raise ValueError("window_days must be at least 1")
    items = [i for i in items if i.item_type in include_types]
    end = as_of.date()
    start = end - timedelta(days=window_days - 1)

    throughput = daily_throughput(items, start, end)
    cycles = cycle_times(items, start, end)
    leads = lead_times(items, start, end)
    ct_p85 = percentile(cycles, 85)
    # Fall back to lead time for the aging threshold when cycle times are not yet known.
    aging_threshold = ct_p85 if ct_p85 is not None else percentile(leads, 85)

    aging: list[AgingItem] = []
    for item in items:
        if item.state not in ACTIVE_STATES:
            continue
        basis, since = ("started_at", item.started_at) if item.started_at else ("created_at", item.created_at)
        age = _days(since, as_of)
        aging.append(AgingItem(item.id, item.title, item.state, round(age, 1), basis,
                               aging_threshold is not None and age > aging_threshold))
    aging.sort(key=lambda a: a.age_days, reverse=True)

    total = sum(throughput)
    return FlowMetrics(
        window_start=start,
        window_end=end,
        throughput_total=total,
        throughput_per_week=round(total / window_days * 7, 2),
        cycle_time_p50=_round(percentile(cycles, 50)),
        cycle_time_p85=_round(ct_p85),
        cycle_time_samples=len(cycles),
        lead_time_p50=_round(percentile(leads, 50)),
        lead_time_p85=_round(percentile(leads, 85)),
        wip=len(aging),
        blocked=sum(1 for a in aging if a.state == "blocked"),
        aging=aging,
        daily_throughput=throughput,
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
