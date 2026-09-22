"""Earned value management, classic and agile (release-level AgileEVM).

Formulas follow standard EVM definitions:
    SV = EV - PV          SPI = EV / PV
    CV = EV - AC          CPI = EV / AC
    EAC = BAC / CPI       ETC = EAC - AC       VAC = BAC - EAC
    TCPI = (BAC - EV) / (BAC - AC)

AgileEVM derives PV and EV from the release plan: planned % complete is
iterations completed / iterations planned, and actual % complete is points
completed / points planned. When no budget is given, the results are expressed
in story points and only the schedule indices are meaningful.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EvmResult:
    bac: float
    pv: float
    ev: float
    ac: float | None
    sv: float
    spi: float | None
    cv: float | None
    cpi: float | None
    eac: float | None
    etc: float | None
    vac: float | None
    tcpi: float | None

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def evm(bac: float, pv: float, ev: float, ac: float | None = None) -> EvmResult:
    if bac <= 0:
        raise ValueError("bac must be positive")
    if pv < 0 or ev < 0 or (ac is not None and ac < 0):
        raise ValueError("pv, ev and ac must be non-negative")
    spi = _ratio(ev, pv)
    cv = cpi = eac = etc = vac = tcpi = None
    if ac is not None:
        cv = ev - ac
        cpi = _ratio(ev, ac)
        if cpi:
            eac = bac / cpi
            etc = eac - ac
            vac = bac - eac
        tcpi = _ratio(bac - ev, bac - ac)
    return EvmResult(bac, pv, ev, ac, ev - pv, spi, cv, cpi, eac, etc, vac, tcpi)


@dataclass(frozen=True)
class AgileEvmResult:
    unit: str  # "currency" or "points"
    planned_pct_complete: float
    actual_pct_complete: float
    projected_iterations: float | None  # iterations needed at the current rate
    evm: EvmResult

    def to_dict(self) -> dict:
        return {
            "unit": self.unit,
            "planned_pct_complete": round(self.planned_pct_complete, 4),
            "actual_pct_complete": round(self.actual_pct_complete, 4),
            "projected_iterations": None if self.projected_iterations is None else round(self.projected_iterations, 2),
            **self.evm.to_dict(),
        }


def agile_evm(total_points: float, completed_points: float, iterations_planned: int, iterations_completed: int,
              budget: float | None = None, actual_cost: float | None = None) -> AgileEvmResult:
    if total_points <= 0:
        raise ValueError("total_points must be positive")
    if iterations_planned <= 0:
        raise ValueError("iterations_planned must be positive")
    if not 0 <= completed_points <= total_points:
        raise ValueError("completed_points must be between 0 and total_points")
    if iterations_completed < 0:
        raise ValueError("iterations_completed must be non-negative")
    if actual_cost is not None and budget is None:
        raise ValueError("actual_cost needs a budget")

    ppc = min(iterations_completed / iterations_planned, 1.0)
    apc = completed_points / total_points
    projected = iterations_completed / apc if apc else None
    if budget is not None:
        result = evm(budget, ppc * budget, apc * budget, actual_cost)
        return AgileEvmResult("currency", ppc, apc, projected, result)
    result = evm(total_points, ppc * total_points, completed_points)
    return AgileEvmResult("points", ppc, apc, projected, result)
