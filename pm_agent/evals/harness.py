"""Run eval scenarios, each against a fresh in-memory project."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from pm_agent.agent.runner import PlaybookRunner, RunResult
from pm_agent.evals.scenarios import PROJECT, SCENARIOS, Check, baseline
from pm_agent.service import Copilot
from pm_agent.store import Store

EVAL_HUMAN = "human:eval"
EVAL_NOW = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


@dataclass
class ScenarioResult:
    name: str
    run: RunResult
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def run_scenarios(make_runner: Callable[[Copilot], PlaybookRunner], names: Iterable[str] | None = None,
                  now: datetime = EVAL_NOW) -> list[ScenarioResult]:
    results = []
    for name in names or SCENARIOS:
        scenario = SCENARIOS[name]
        store = Store()
        copilot = Copilot(store, clock=lambda: now)
        material = scenario.setup(copilot, EVAL_HUMAN)
        before = baseline(copilot)
        runner = make_runner(copilot)
        try:
            run = runner.run(scenario.playbook, PROJECT, trigger="eval", material=material)
        finally:
            runner.close()
        results.append(ScenarioResult(name, run, scenario.check(copilot, run, before)))
        store.close()
    return results


def format_report(results: list[ScenarioResult]) -> str:
    lines = []
    for result in results:
        usage = result.run.usage
        lines.append(f"{'PASS' if result.passed else 'FAIL'}  {result.name}  ({result.run.model}, "
                     f"{result.run.turns} turns, {len(result.run.tool_calls)} tool calls, "
                     f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out tokens, "
                     f"{usage.get('cache_read_input_tokens', 0)} cached)")
        for check in result.checks:
            detail = f"  [{check.detail}]" if check.detail and not check.passed else ""
            lines.append(f"    {'ok' if check.passed else 'XX'} {check.name}{detail}")
    passed = sum(r.passed for r in results)
    lines.append(f"\n{passed}/{len(results)} scenarios passed")
    return "\n".join(lines)
