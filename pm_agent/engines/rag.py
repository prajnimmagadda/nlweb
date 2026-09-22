"""Threshold-based RAG status.

RAG is computed from metrics and the project's configured thresholds. The model
never picks a colour, so status cannot be softened by wording.
"""

from collections.abc import Iterable
from datetime import date

from pm_agent.engines.flow import FlowMetrics
from pm_agent.engines.forecast import WhenForecast
from pm_agent.schemas import DimensionStatus, RagThresholds, Risk

_SEVERITY = {"green": 0, "amber": 1, "red": 2}


def schedule_status(forecast: WhenForecast | None, target: date | None) -> DimensionStatus:
    if target is None:
        return DimensionStatus(name="schedule", rag="unknown", explanation="No target date is set.")
    if forecast is None:
        return DimensionStatus(name="schedule", rag="unknown",
                               explanation="No forecast is available (no release scope or no throughput history).")
    p50, p85 = forecast.percentiles[50], forecast.percentiles[85]
    value = f"P50 {p50.isoformat()}, P85 {p85.isoformat()}, target {target.isoformat()}"
    if p85 <= target:
        return DimensionStatus(name="schedule", rag="green", value=value,
                               explanation="At least 85% of simulated futures finish by the target date.")
    if p50 <= target:
        return DimensionStatus(name="schedule", rag="amber", value=value,
                               explanation="Between 50% and 85% of simulated futures finish by the target date.")
    return DimensionStatus(name="schedule", rag="red", value=value,
                           explanation="Fewer than half of simulated futures finish by the target date.")


def spi_status(spi: float | None, thresholds: RagThresholds) -> DimensionStatus:
    if spi is None:
        return DimensionStatus(name="earned_value", rag="unknown",
                               explanation="SPI unavailable (needs estimates, start and target dates).")
    value = f"SPI {spi:.2f}"
    if spi >= thresholds.spi_green:
        return DimensionStatus(name="earned_value", rag="green", value=value,
                               explanation=f"SPI is at or above {thresholds.spi_green}.")
    if spi >= thresholds.spi_amber:
        return DimensionStatus(name="earned_value", rag="amber", value=value,
                               explanation=f"SPI is between {thresholds.spi_amber} and {thresholds.spi_green}.")
    return DimensionStatus(name="earned_value", rag="red", value=value,
                           explanation=f"SPI is below {thresholds.spi_amber}.")


def _count_status(name: str, count: int, amber: int, red: int, noun: str) -> DimensionStatus:
    rag = "red" if count >= red else "amber" if count >= amber else "green"
    return DimensionStatus(name=name, rag=rag, value=str(count),
                           explanation=f"{count} {noun} (amber at {amber}, red at {red}).")


def flow_statuses(metrics: FlowMetrics, thresholds: RagThresholds) -> list[DimensionStatus]:
    return [
        _count_status("aging_wip", len(metrics.aging_over_p85), thresholds.aging_wip_amber, thresholds.aging_wip_red,
                      "in-progress items older than the 85th-percentile cycle time"),
        _count_status("blocked", metrics.blocked, thresholds.blocked_amber, thresholds.blocked_red, "blocked items"),
    ]


def risk_status(risks: Iterable[Risk], thresholds: RagThresholds) -> DimensionStatus:
    risks = list(risks)
    open_risks = [r for r in risks if r.status == "open"]
    proposed = sum(1 for r in risks if r.status == "proposed")
    note = f" {proposed} proposed risk(s) await review." if proposed else ""
    if not open_risks:
        return DimensionStatus(name="risk", rag="green", value="0 open",
                               explanation="No open risks." + note)
    top = max(open_risks, key=lambda r: r.score)
    value = f"top score {top.score} ({top.id})"
    if top.score >= thresholds.risk_score_red:
        rag = "red"
    elif top.score >= thresholds.risk_score_amber:
        rag = "amber"
    else:
        rag = "green"
    return DimensionStatus(
        name="risk", rag=rag, value=value,
        explanation=f"Highest open risk scores {top.score} (amber at {thresholds.risk_score_amber}, "
                    f"red at {thresholds.risk_score_red})." + note,
    )


def overall(dimensions: Iterable[DimensionStatus]) -> str:
    """Worst known status; 'unknown' only when nothing is known."""
    known = [d.rag for d in dimensions if d.rag in _SEVERITY]
    if not known:
        return "unknown"
    return max(known, key=_SEVERITY.__getitem__)
