"""Command line for the human side of PM Copilot.

Everything run here acts as 'human:<you>': PM_COPILOT_USER if set, otherwise your
git user.name, otherwise your login name. That is what lets you approve charters,
decisions and change requests, promote risks, and change RAG thresholds, which
the MCP server (the agent) cannot do.
"""

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from pm_agent.governance import GovernanceError
from pm_agent.integrations.github import GitHubError
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

    sub.add_parser("serve", help="run the MCP server on stdio")
    return parser


def run(args: argparse.Namespace, copilot: Copilot) -> Any:
    user = current_user()
    cmd = args.command
    if cmd == "init":
        return copilot.setup_project(args.project, actor=user, name=args.name or args.project, repos=args.repos,
                                     iteration_days=args.iteration_days, start_date=args.start,
                                     target_date=args.target, release_milestone=args.milestone)
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
    raise ValueError(f"unknown command {cmd}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from pm_agent.mcp_server import main as serve

        serve(args.db)
        return 0
    store = Store(args.db)
    try:
        result = run(args, Copilot(store))
    except (GovernanceError, GitHubError, LookupError, ValueError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    if result is not None:
        _print(result)
    return 0
