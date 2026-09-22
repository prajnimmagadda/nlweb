"""Command line for the human side of PM Copilot.

Everything run here acts as 'human:<you>': PM_COPILOT_USER if set, otherwise your
git user.name, otherwise your login name. That is what lets you approve charters,
decisions and change requests, promote risks, and set RAG thresholds, schedules,
report recipients and the calendar filter, which the agent cannot do.

Commands that call Claude (run, run-due, eval) import the Anthropic SDK lazily.
"""

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from pm_agent.governance import GovernanceError
from pm_agent.integrations.github import GitHubError
from pm_agent.integrations.google import DEFAULT_CLIENT_SECRETS, GoogleError, authorize, load_client
from pm_agent.playbooks import get_playbook, load_playbooks
from pm_agent.service import Copilot
from pm_agent.store import Store

DEFAULT_DB = "~/.pm-copilot/pm.db"


def current_user() -> str:
    name = os.environ.get("PM_COPILOT_USER", "")
    if not name:
        try:
            name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            name = ""
    name = re.sub(r"[^\w .@+-]", "", name or getpass.getuser()).strip()
    return f"human:{name or 'me'}"


def _print(value: Any) -> None:
    print(json.dumps(to_jsonable_python(value), indent=2, default=str))


def _threshold_value(text: str) -> int | float:
    return float(text) if "." in text else int(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pm_agent",
                                     description="PM Copilot: a personal PMBOK-grounded project manager.")
    parser.add_argument("--db", default=os.environ.get("PM_COPILOT_DB", DEFAULT_DB), help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create or update a project")
    p.add_argument("project")
    p.add_argument("--name")
    p.add_argument("--repo", action="append", dest="repos", help="GitHub owner/name (repeatable)")
    p.add_argument("--iteration-days", type=int)
    p.add_argument("--start", help="YYYY-MM-DD")
    p.add_argument("--target", help="YYYY-MM-DD")
    p.add_argument("--milestone", help="GitHub milestone that defines the release scope")
    p.add_argument("--github-project", help="GitHub project board as owner/number; its Status field drives state")
    p.add_argument("--timezone", help="IANA timezone for schedules, e.g. Asia/Kolkata")
    p.add_argument("--report-to", action="append", dest="report_to", metavar="EMAIL",
                   help="status email draft recipient (repeatable; replaces the list)")
    p.add_argument("--calendar-query", help="only calendar events matching this text are visible to the copilot")

    sub.add_parser("projects", help="list projects")

    p = sub.add_parser("sync", help="pull GitHub issues into work items")
    p.add_argument("project")

    p = sub.add_parser("metrics", help="flow metrics")
    p.add_argument("project")
    p.add_argument("--window", type=int, default=90)

    p = sub.add_parser("forecast", help="Monte Carlo release forecast")
    p.add_argument("project")
    p.add_argument("--milestone")
    p.add_argument("--window", type=int, default=90)
    p.add_argument("--trials", type=int, default=10000)
    p.add_argument("--seed", type=int)

    p = sub.add_parser("evm", help="agile earned value")
    p.add_argument("project")
    p.add_argument("--milestone")
    p.add_argument("--budget", type=float)
    p.add_argument("--actual-cost", type=float)

    p = sub.add_parser("health", help="computed RAG status")
    p.add_argument("project")

    p = sub.add_parser("list", help="list artifacts of a kind")
    p.add_argument("project")
    p.add_argument("kind")
    p.add_argument("--status")

    p = sub.add_parser("show", help="show an artifact")
    p.add_argument("project")
    p.add_argument("kind")
    p.add_argument("id")
    p.add_argument("--version", type=int)

    p = sub.add_parser("history", help="all versions of an artifact")
    p.add_argument("project")
    p.add_argument("kind")
    p.add_argument("id")

    p = sub.add_parser("audit", help="recent actions, newest first")
    p.add_argument("project")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("approve", help="approve a charter, decision or change request, or promote a risk")
    p.add_argument("project")
    p.add_argument("kind", choices=["charter", "risk", "decision", "change_request"])
    p.add_argument("id")
    p.add_argument("--note")

    p = sub.add_parser("reject", help="reject a risk, decision or change request")
    p.add_argument("project")
    p.add_argument("kind", choices=["risk", "decision", "change_request"])
    p.add_argument("id")
    p.add_argument("--reason", required=True)

    p = sub.add_parser("thresholds", help="change RAG thresholds, e.g. spi_green=0.9 blocked_red=4")
    p.add_argument("project")
    p.add_argument("changes", nargs="+", metavar="key=value")
    p.add_argument("--reason")

    sub.add_parser("playbooks", help="list playbooks")

    p = sub.add_parser("playbook", help="print a playbook's full instructions")
    p.add_argument("id")
    p.add_argument("project")

    p = sub.add_parser("seed-demo", help="create a synthetic agile project to try things out")
    p.add_argument("project", nargs="?", default="demo")
    p.add_argument("--seed", type=int, default=7)

    p = sub.add_parser("schedule", help="manage unattended playbook runs")
    schedule_sub = p.add_subparsers(dest="schedule_command", required=True)
    q = schedule_sub.add_parser("add", help="run a playbook on a schedule")
    q.add_argument("project")
    q.add_argument("playbook")
    cadence = q.add_mutually_exclusive_group(required=True)
    cadence.add_argument("--daily", action="store_true")
    cadence.add_argument("--weekdays", action="store_true")
    cadence.add_argument("--weekly", metavar="DAY", choices=["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
    q.add_argument("--at", default="08:00", help="local time HH:MM (default 08:00)")
    q = schedule_sub.add_parser("list", help="show a project's schedules")
    q.add_argument("project")
    q = schedule_sub.add_parser("remove", help="stop a scheduled playbook")
    q.add_argument("project")
    q.add_argument("playbook")

    p = sub.add_parser("run", help="run a playbook now with Claude (calls the Anthropic API)")
    p.add_argument("playbook")
    p.add_argument("project")
    p.add_argument("--material", metavar="FILE", help="text to give the run, e.g. meeting notes")
    p.add_argument("--model")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-turns", type=int, default=30)

    p = sub.add_parser("run-due", help="run every schedule that is due (call this from cron every 15 minutes)")
    p.add_argument("--dry-run", action="store_true", help="only list what is due")

    p = sub.add_parser("runs", help="recent agent runs, newest first")
    p.add_argument("project")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("google-auth", help="connect Google Calendar and Gmail drafts (opens a browser)")
    p.add_argument("--client-secrets", default=DEFAULT_CLIENT_SECRETS, help="OAuth client JSON for a desktop app")
    p.add_argument("--drive", action="store_true", help="also allow reading Google Docs attached to meetings")

    p = sub.add_parser("eval", help="run scenario evals against Claude (calls the Anthropic API, costs money)")
    p.add_argument("--scenario", action="append", dest="scenarios", help="scenario name (repeatable)")
    p.add_argument("--model")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])

    sub.add_parser("serve", help="run the MCP server on stdio")
    return parser


def run(args: argparse.Namespace, copilot: Copilot) -> Any:
    user = current_user()
    cmd = args.command
    if cmd == "init":
        exists = copilot.store.get(args.project, "project_profile", args.project) is not None
        name = args.name or (None if exists else args.project)
        return copilot.setup_project(args.project, actor=user, name=name, repos=args.repos,
                                     iteration_days=args.iteration_days, start_date=args.start,
                                     target_date=args.target, release_milestone=args.milestone,
                                     github_project=args.github_project, timezone=args.timezone,
                                     report_recipients=args.report_to, calendar_query=args.calendar_query)
    if cmd == "projects":
        return copilot.projects()
    if cmd == "sync":
        return copilot.sync_github(args.project)
    if cmd == "metrics":
        return copilot.flow_report(args.project, args.window)
    if cmd == "forecast":
        return copilot.forecast(args.project, args.milestone, args.window, args.trials, args.seed)
    if cmd == "evm":
        return copilot.agile_evm(args.project, args.milestone, args.budget, args.actual_cost)
    if cmd == "health":
        return copilot.health(args.project)
    if cmd == "list":
        return copilot.list_artifacts(args.project, args.kind, args.status)
    if cmd == "show":
        return copilot.get_artifact(args.project, args.kind, args.id, args.version)
    if cmd == "history":
        return copilot.history(args.project, args.kind, args.id)
    if cmd == "audit":
        return copilot.store.audit(args.project, args.limit)
    if cmd == "approve":
        return copilot.approve(args.project, args.kind, args.id, actor=user, note=args.note)
    if cmd == "reject":
        return copilot.reject(args.project, args.kind, args.id, actor=user, reason=args.reason)
    if cmd == "thresholds":
        changes = {}
        for item in args.changes:
            key, sep, value = item.partition("=")
            if not sep:
                raise ValueError(f"expected key=value, got {item!r}")
            changes[key.strip()] = _threshold_value(value.strip())
        return copilot.set_thresholds(args.project, changes, actor=user, rationale=args.reason)
    if cmd == "seed-demo":
        from pm_agent.demo import seed_demo

        return seed_demo(copilot, actor=user, project_id=args.project, seed=args.seed)
    if cmd == "playbooks":
        return [{"id": p.id, "name": p.name, "autonomy": f"L{int(p.autonomy)}", "processes": p.processes}
                for p in load_playbooks().values()]
    if cmd == "playbook":
        print(get_playbook(args.id).render(args.project))
        return None
    if cmd == "schedule":
        if args.schedule_command == "list":
            profile = copilot.profile(args.project)
            return {"timezone": profile.timezone, "schedules": [s.model_dump() for s in profile.schedules]}
        if args.schedule_command == "remove":
            return copilot.remove_schedule(args.project, args.playbook, actor=user)
        cadence = "daily" if args.daily else "weekdays" if args.weekdays else "weekly"
        return copilot.add_schedule(args.project, {"playbook": args.playbook, "cadence": cadence,
                                                   "day": args.weekly, "time": args.at}, actor=user)
    if cmd == "runs":
        return copilot.runs(args.project, args.limit)
    if cmd == "google-auth":
        return {"granted_scopes": authorize(args.client_secrets, with_drive=args.drive)}
    if cmd in ("run", "run-due", "eval"):
        return _run_agent(args, copilot)
    raise ValueError(f"unknown command {cmd}")


def _run_agent(args: argparse.Namespace, copilot: Copilot) -> Any:
    """Commands that call Claude. The Anthropic SDK is imported only here."""
    from dataclasses import asdict

    from pm_agent.agent.runner import PlaybookRunner, run_due

    if args.command == "run":
        material = Path(args.material).read_text(encoding="utf-8") if args.material else None
        with PlaybookRunner(copilot, model=args.model, effort=args.effort, max_turns=args.max_turns) as runner:
            result = runner.run(args.playbook, args.project, material=material)
        return {**asdict(result), "tool_calls": [asdict(c) for c in result.tool_calls]}
    if args.command == "run-due":
        return run_due(copilot, lambda: PlaybookRunner(copilot), dry_run=args.dry_run)

    from pm_agent.evals.harness import format_report, run_scenarios

    results = run_scenarios(lambda c: PlaybookRunner(c, model=args.model, effort=args.effort), args.scenarios)
    print(format_report(results))
    if not all(r.passed for r in results):
        raise SystemExit(1)
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from pm_agent.mcp_server import main as serve

        serve(args.db)
        return 0
    store = Store(args.db)
    try:
        result = run(args, Copilot(store, google=load_client()))
    except (GovernanceError, GitHubError, GoogleError, LookupError, ValueError, ValidationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # the Anthropic SDK's errors, without importing it for every command
        if type(exc).__module__.startswith("anthropic"):
            print(f"error: Claude API: {exc}", file=sys.stderr)
            return 1
        raise
    finally:
        store.close()
    if result is not None:
        _print(result)
    return 0
