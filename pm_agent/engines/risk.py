"""Risk register analytics: ranking and the probability x impact heat map."""

from collections.abc import Iterable

from pm_agent.schemas import Risk

ACTIVE_RISK_STATUSES = ("proposed", "open")


def rank(risks: Iterable[Risk], statuses: tuple[str, ...] = ACTIVE_RISK_STATUSES) -> list[Risk]:
    """Active risks, highest score first (ties broken by impact, then id)."""
    active = [r for r in risks if r.status in statuses]
    return sorted(active, key=lambda r: (-r.score, -r.impact, r.id))


def heat_map(risks: Iterable[Risk], statuses: tuple[str, ...] = ACTIVE_RISK_STATUSES) -> dict:
    """5x5 grid of risk ids, keyed by 'P<probability>' then 'I<impact>'."""
    grid: dict[str, dict[str, list[str]]] = {f"P{p}": {f"I{i}": [] for i in range(1, 6)} for p in range(5, 0, -1)}
    for risk in risks:
        if risk.status in statuses:
            grid[f"P{risk.probability}"][f"I{risk.impact}"].append(risk.id)
    return grid
