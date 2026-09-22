"""Change impact and baseline variance, computed from work items and throughput history."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from pm_agent.engines import rag
from pm_agent.engines.forecast import ForecastError, forecast_how_many, forecast_when
from pm_agent.schemas import (
    Baseline,
    ChangeProposal,
    DimensionStatus,
    ImpactAnalysis,
    ImpactSnapshot,
    RagThresholds,
    WorkItem,
)

OPEN_STATES = ("todo", "in_progress", "blocked")
# Before and after use the same random stream, so the difference comes from the change alone.
SEED = 0


def proposal_digest(proposal: ChangeProposal) -> str:
    canonical = json.dumps(proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _points(items: Iterable[WorkItem]) -> float | None:
    estimates = [i.estimate for i in items if i.estimate is not None]
    return float(sum(estimates)) if estimates else None


def snapshot(scope: list[WorkItem], daily_throughput: list[int], today: date, target: date | None, *,
             extra_items: int = 0, extra_points: float | None = None) -> ImpactSnapshot:
    remaining = sum(1 for i in scope if i.state in OPEN_STATES) + extra_items
    points = _points(scope)
    if extra_points is not None:
        points = (points or 0.0) + extra_points
    try:
        when = forecast_when(remaining, daily_throughput, today, seed=SEED)
    except ForecastError:
        when = None
    done_by = None
    if when is not None and target is not None:
        done_by = min(forecast_how_many(daily_throughput, today, target, seed=SEED).at_least[85], remaining)
    return ImpactSnapshot(
        remaining_items=remaining,
        scope_items=len(scope) + extra_items,
        scope_points=points,
        target_date=target,
        forecast_p50=when.percentiles[50] if when else None,
        forecast_p85=when.percentiles[85] if when else None,
        schedule_rag=rag.schedule_status(when, target).rag,
        done_by_target_p85=done_by,
    )


def analyze_change(scope: list[WorkItem], proposal: ChangeProposal, target: date | None,
                   daily_throughput: list[int], today: date, now: datetime, *,
                   objectives_by_item: dict[str, list[str]] | None = None,
                   risks_by_item: dict[str, list[str]] | None = None) -> ImpactAnalysis:
    """Before/after snapshot of the release scope for a proposed change."""
    moved = set(proposal.move_out_item_ids)
    in_scope = {i.id for i in scope}
    notes = []
    if moved - in_scope:
        notes.append(f"Not in the release scope, so ignored: {', '.join(sorted(moved - in_scope))}.")
    if proposal.add_items and proposal.add_points is None and _points(scope) is not None:
        notes.append("The added items have no estimate, so scope points after the change leave them out.")
    after_scope = [i for i in scope if i.id not in moved]
    objectives_by_item = objectives_by_item or {}
    risks_by_item = risks_by_item or {}
    return ImpactAnalysis(
        computed_at=now,
        proposal_digest=proposal_digest(proposal),
        before=snapshot(scope, daily_throughput, today, target),
        after=snapshot(after_scope, daily_throughput, today, proposal.new_target_date or target,
                       extra_items=proposal.add_items, extra_points=proposal.add_points),
        objectives_affected=sorted({o for item in moved for o in objectives_by_item.get(item, [])}),
        risks_linked=sorted({r for item in moved for r in risks_by_item.get(item, [])}),
        notes=notes,
    )


@dataclass(frozen=True)
class Variance:
    baseline_id: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    growth: float = 0.0  # net change in item count as a share of the baseline (including planned additions)
    points_change: float | None = None
    target_shift_days: int | None = None
    p85_drift_days: int | None = None


def baseline_variance(baseline: Baseline, scope: list[WorkItem], target: date | None,
                      current_p85: date | None) -> Variance:
    base, now = set(baseline.scope_item_ids), {i.id for i in scope}
    added, removed = sorted(now - base), sorted(base - now)
    current_points = _points(scope)
    planned = len(base) + baseline.planned_additions
    return Variance(
        baseline_id=baseline.id,
        added=added,
        removed=removed,
        growth=round((len(now) - planned) / planned, 4) if planned else 0.0,
        points_change=(None if current_points is None or baseline.scope_points is None
                       else round(current_points - baseline.scope_points, 2)),
        target_shift_days=(target - baseline.target_date).days if target and baseline.target_date else None,
        p85_drift_days=((current_p85 - baseline.forecast_p85).days
                        if current_p85 and baseline.forecast_p85 else None),
    )


def scope_status(variance: Variance | None, thresholds: RagThresholds) -> DimensionStatus:
    if variance is None:
        return DimensionStatus(name="scope", rag="unknown", explanation="No approved baseline yet.")
    value = f"+{len(variance.added)} / -{len(variance.removed)} items ({variance.growth:+.0%})"
    if variance.growth >= thresholds.scope_growth_red:
        level = "red"
    elif variance.growth >= thresholds.scope_growth_amber:
        level = "amber"
    else:
        level = "green"
    return DimensionStatus(
        name="scope", rag=level, value=value,
        explanation=f"Net scope change since baseline {variance.baseline_id} is {variance.growth:+.0%} "
                    f"(amber at {thresholds.scope_growth_amber:.0%}, red at {thresholds.scope_growth_red:.0%}).",
    )
