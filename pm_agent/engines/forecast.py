"""Monte Carlo forecasting from historical throughput.

Instead of estimating every item, we resample the team's own daily throughput
history (including zero days, which captures weekends and interruptions) to
answer two questions:

* when will N remaining items be done?  -> forecast_when
* how many items will be done by a date? -> forecast_how_many
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

DEFAULT_PERCENTILES = (50, 70, 85, 95)
_CHUNK = 2000


class ForecastError(ValueError):
    pass


def _samples(daily_throughput: Sequence[int]) -> np.ndarray:
    samples = np.asarray(daily_throughput, dtype=np.int32)
    if samples.size == 0 or samples.sum() == 0:
        raise ForecastError("no completed items in the history window, so there is nothing to forecast from")
    if (samples < 0).any():
        raise ForecastError("throughput samples must be non-negative")
    return samples


@dataclass(frozen=True)
class WhenForecast:
    remaining: int
    start: date
    trials: int
    history_days: int
    percentiles: dict[int, date]
    beyond_horizon_share: float  # share of trials that did not finish within the horizon

    def to_dict(self) -> dict:
        return {
            "remaining_items": self.remaining,
            "start": self.start.isoformat(),
            "trials": self.trials,
            "history_days": self.history_days,
            "completion_dates": {f"P{p}": d.isoformat() for p, d in self.percentiles.items()},
            "beyond_horizon_share": self.beyond_horizon_share,
            "reading": "P85 is the date by which 85% of simulated futures had finished.",
        }


def forecast_when(remaining: int, daily_throughput: Sequence[int], start: date, *, trials: int = 10000,
                  percentiles: Sequence[int] = DEFAULT_PERCENTILES, horizon_days: int = 1095,
                  seed: int | None = None) -> WhenForecast:
    """Forecast completion dates for `remaining` items, starting the day after `start`."""
    if remaining < 0:
        raise ForecastError("remaining must be non-negative")
    if remaining == 0:
        return WhenForecast(0, start, trials, len(daily_throughput), {p: start for p in percentiles}, 0.0)
    samples = _samples(daily_throughput)
    rng = np.random.default_rng(seed)

    days_needed = np.empty(trials, dtype=np.int32)
    for offset in range(0, trials, _CHUNK):
        n = min(_CHUNK, trials - offset)
        cumulative = rng.choice(samples, size=(n, horizon_days)).cumsum(axis=1)
        reached = cumulative >= remaining
        finished = reached.any(axis=1)
        days_needed[offset:offset + n] = np.where(finished, reached.argmax(axis=1) + 1, horizon_days + 1)

    beyond = float((days_needed > horizon_days).mean())
    dates = {
        p: start + timedelta(days=int(np.percentile(days_needed, p, method="inverted_cdf")))
        for p in percentiles
    }
    return WhenForecast(remaining, start, trials, len(samples), dates, round(beyond, 4))


@dataclass(frozen=True)
class HowManyForecast:
    start: date
    target: date
    trials: int
    history_days: int
    at_least: dict[int, int]  # percentile -> items completed with that confidence

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "target": self.target.isoformat(),
            "trials": self.trials,
            "history_days": self.history_days,
            "items_at_least": {f"P{p}": n for p, n in self.at_least.items()},
            "reading": "P85 is the number of items completed in at least 85% of simulated futures.",
        }


def forecast_how_many(daily_throughput: Sequence[int], start: date, target: date, *, trials: int = 10000,
                      percentiles: Sequence[int] = DEFAULT_PERCENTILES, seed: int | None = None) -> HowManyForecast:
    """Forecast how many items will be completed between start (exclusive) and target (inclusive)."""
    days = (target - start).days
    if days <= 0:
        return HowManyForecast(start, target, trials, len(daily_throughput), {p: 0 for p in percentiles})
    samples = _samples(daily_throughput)
    rng = np.random.default_rng(seed)
    totals = np.concatenate([
        rng.choice(samples, size=(min(_CHUNK, trials - offset), days)).sum(axis=1)
        for offset in range(0, trials, _CHUNK)
    ])
    # "At least N with p% confidence" is the (100 - p)th percentile of the simulated totals.
    at_least = {p: int(np.percentile(totals, 100 - p, method="inverted_cdf")) for p in percentiles}
    return HowManyForecast(start, target, trials, len(samples), at_least)
