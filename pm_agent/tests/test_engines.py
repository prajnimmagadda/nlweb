from datetime import timedelta

import pytest

from pm_agent.engines import rag, risk
from pm_agent.engines.evm import agile_evm, evm
from pm_agent.engines.flow import compute_flow, daily_throughput, percentile
from pm_agent.engines.forecast import ForecastError, forecast_how_many, forecast_when
from pm_agent.schemas import RagThresholds, Risk

from .conftest import NOW, work_item

TODAY = NOW.date()


# ----- flow ---------------------------------------------------------------

def test_percentile_is_nearest_rank():
    values = list(range(1, 11))
    assert percentile(values, 50) == 5
    assert percentile(values, 85) == 9
    assert percentile([], 85) is None


def test_daily_throughput_counts_completed_items_per_day():
    items = [work_item(1, "done", closed=0.1), work_item(2, "done", closed=0.2), work_item(3, "done", closed=2),
             work_item(4, "cancelled", closed=1), work_item(5, "in_progress", started=3)]
    assert daily_throughput(items, TODAY - timedelta(days=2), TODAY) == [1, 0, 2]


def test_compute_flow_metrics():
    items = [
        work_item(1, "done", started=10, closed=8),     # cycle time 2 days
        work_item(2, "done", started=10, closed=6),     # 4 days
        work_item(3, "done", started=10, closed=4),     # 6 days
        work_item(4, "done", closed=3),                 # no start known: lead time only
        work_item(5, "in_progress", started=20),        # aging well past P85
        work_item(6, "in_progress", started=1),
        work_item(7, "blocked", started=2),
        work_item(8, "done", item_type="epic", started=30, closed=1),  # epics excluded
        work_item(9, "done", started=200, closed=120),  # outside the window
    ]
    metrics = compute_flow(items, NOW, window_days=30)
    assert metrics.throughput_total == 4
    assert metrics.throughput_per_week == round(4 / 30 * 7, 2)
    assert metrics.cycle_time_samples == 3
    assert (metrics.cycle_time_p50, metrics.cycle_time_p85) == (4.0, 6.0)
    assert (metrics.wip, metrics.blocked) == (3, 1)
    assert [a.id for a in metrics.aging_over_p85] == ["o/r#5"]
    assert metrics.aging[0].age_basis == "started_at"


def test_aging_falls_back_to_created_at_when_start_unknown():
    items = [work_item(1, "done", created=10, closed=5), work_item(2, "in_progress", created=40)]
    item = compute_flow(items, NOW, window_days=30).aging[0]
    assert (item.age_basis, item.over_p85) == ("created_at", True)


# ----- forecast -----------------------------------------------------------

def test_forecast_when_with_constant_throughput_is_exact():
    result = forecast_when(10, [2] * 30, TODAY, trials=500, seed=1)
    assert set(result.percentiles.values()) == {TODAY + timedelta(days=5)}
    assert result.beyond_horizon_share == 0


def test_forecast_when_is_seeded_and_monotonic():
    history = [0, 0, 1, 3, 0, 2, 1, 0, 0, 4, 1, 0, 2]
    a = forecast_when(25, history, TODAY, trials=3000, seed=7)
    b = forecast_when(25, history, TODAY, trials=3000, seed=7)
    assert a.percentiles == b.percentiles
    dates = [a.percentiles[p] for p in (50, 70, 85, 95)]
    assert dates == sorted(dates) and dates[0] > TODAY


def test_forecast_when_edge_cases():
    assert forecast_when(0, [], TODAY).percentiles[85] == TODAY
    with pytest.raises(ForecastError, match="no completed items"):
        forecast_when(5, [0, 0, 0], TODAY)
    capped = forecast_when(100, [1, 0, 0, 0, 0, 0, 0, 0, 0, 0], TODAY, trials=200, horizon_days=30, seed=3)
    assert capped.beyond_horizon_share == 1.0


def test_forecast_how_many():
    result = forecast_how_many([2] * 10, TODAY, TODAY + timedelta(days=10), trials=200, seed=1)
    assert set(result.at_least.values()) == {20}
    varied = forecast_how_many([0, 1, 2, 3], TODAY, TODAY + timedelta(days=20), trials=2000, seed=1)
    assert varied.at_least[95] <= varied.at_least[85] <= varied.at_least[50]
    assert forecast_how_many([1], TODAY, TODAY).at_least[85] == 0


# ----- earned value -------------------------------------------------------

def test_classic_evm():
    result = evm(bac=100, pv=50, ev=40, ac=45)
    assert (result.sv, result.cv) == (-10, -5)
    assert result.spi == pytest.approx(0.8)
    assert result.cpi == pytest.approx(40 / 45)
    assert result.eac == pytest.approx(112.5)
    assert result.etc == pytest.approx(67.5)
    assert result.vac == pytest.approx(-12.5)
    assert result.tcpi == pytest.approx(60 / 55)


def test_agile_evm_in_points_and_currency():
    points = agile_evm(total_points=100, completed_points=40, iterations_planned=10, iterations_completed=5)
    assert points.unit == "points"
    assert (points.evm.pv, points.evm.ev) == (50, 40)
    assert points.evm.spi == pytest.approx(0.8)
    assert points.projected_iterations == pytest.approx(12.5)
    assert points.evm.cpi is None

    money = agile_evm(100, 40, 10, 5, budget=200_000, actual_cost=110_000)
    assert money.evm.cpi == pytest.approx(80_000 / 110_000)
    assert money.evm.eac == pytest.approx(275_000)
    with pytest.raises(ValueError):
        agile_evm(100, 120, 10, 5)
    with pytest.raises(ValueError):
        agile_evm(100, 40, 10, 5, actual_cost=5)


# ----- RAG and risk -------------------------------------------------------

def _forecast(p50_days: int, p85_days: int):
    class F:
        percentiles = {50: TODAY + timedelta(days=p50_days), 85: TODAY + timedelta(days=p85_days)}
    return F()


def test_schedule_status():
    target = TODAY + timedelta(days=30)
    assert rag.schedule_status(_forecast(10, 20), target).rag == "green"
    assert rag.schedule_status(_forecast(20, 40), target).rag == "amber"
    assert rag.schedule_status(_forecast(40, 60), target).rag == "red"
    assert rag.schedule_status(None, target).rag == "unknown"
    assert rag.schedule_status(_forecast(1, 2), None).rag == "unknown"


def test_spi_and_count_statuses():
    t = RagThresholds()
    assert [rag.spi_status(v, t).rag for v in (1.0, 0.9, 0.5, None)] == ["green", "amber", "red", "unknown"]


def _r(rid: str, p: int, i: int, status: str = "open") -> Risk:
    return Risk(id=rid, project_id="demo", title=rid, cause="c", event="e", effect="f", probability=p, impact=i,
                status=status)


def test_risk_status_uses_open_risks_only():
    t = RagThresholds()
    assert rag.risk_status([_r("R-1", 5, 5, "proposed")], t).rag == "green"
    assert "1 proposed" in rag.risk_status([_r("R-1", 5, 5, "proposed")], t).explanation
    assert rag.risk_status([_r("R-1", 2, 5)], t).rag == "amber"
    assert rag.risk_status([_r("R-1", 3, 5), _r("R-2", 1, 1)], t).rag == "red"


def test_overall_is_worst_known():
    dims = [rag.spi_status(None, RagThresholds()), rag.spi_status(0.9, RagThresholds())]
    assert rag.overall(dims) == "amber"
    assert rag.overall([rag.spi_status(None, RagThresholds())]) == "unknown"


def test_risk_rank_and_heat_map():
    risks = [_r("R-1", 2, 2), _r("R-2", 4, 5), _r("R-3", 5, 4), _r("R-4", 5, 5, "closed")]
    assert [r.id for r in risk.rank(risks)] == ["R-2", "R-3", "R-1"]
    grid = risk.heat_map(risks)
    assert grid["P4"]["I5"] == ["R-2"] and grid["P5"]["I5"] == []
    assert list(grid) == ["P5", "P4", "P3", "P2", "P1"]
