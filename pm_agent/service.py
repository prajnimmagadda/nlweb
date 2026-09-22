"""Copilot service: the operations shared by the CLI and the MCP server.

Callers pass an actor string. The MCP server acts as 'agent:copilot'; the CLI
acts as 'human:<you>'. Human-only operations (approve, reject, thresholds)
refuse non-human actors, and the store enforces the governance rules on every
write regardless of which path the write came from.
"""

import math
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pm_agent.engines import evm as evm_engine
from pm_agent.engines import rag, risk
from pm_agent.engines.flow import FlowMetrics, compute_flow
from pm_agent.engines.forecast import ForecastError, forecast_how_many, forecast_when
from pm_agent.governance import GovernanceError, is_human, is_system, validate_actor
from pm_agent.integrations.github import GitHubClient, sync_project
from pm_agent.schemas import (
    Artifact,
    Charter,
    ProjectProfile,
    RagThresholds,
    Risk,
    ScopeStructure,
    StatusReport,
    WorkItem,
    artifact_type,
)
from pm_agent.store import Store

REPORTING_ACTOR = "system:reporting"
OPEN_STATES = ("todo", "in_progress", "blocked")

# Artifacts that have exactly one instance per project use a fixed id.
_SINGLETON_IDS = {"charter": "charter", "scope_structure": "scope"}

# Kinds only system components write, and where callers should go instead.
_SYSTEM_KINDS = {
    "status_report": "status reports are created with draft_status_report, which attaches the computed RAG",
    "work_item": "work items mirror GitHub and are written by sync_github",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _human_name(actor: str) -> str:
    return actor.split(":", 1)[1]


class Copilot:
    def __init__(self, store: Store, github: GitHubClient | None = None,
                 clock: Callable[[], datetime] = _utc_now):
        self.store = store
        self._github = github
        self._clock = clock

    def now(self) -> datetime:
        return self._clock()

    # ----- projects ------------------------------------------------------

    def projects(self) -> list[dict[str, Any]]:
        return [
            {"project_id": p.id, "name": p.name, "repos": p.repos,
             "target_date": p.target_date.isoformat() if p.target_date else None}
            for p in (self.profile(pid) for pid in self.store.projects())
        ]

    def profile(self, project_id: str) -> ProjectProfile:
        stored = self.store.get(project_id, "project_profile", project_id)
        if stored is None:
            raise LookupError(f"no project {project_id!r}; create it with setup_project first")
        return stored.artifact

    def setup_project(self, project_id: str, *, actor: str, name: str | None = None, repos: list[str] | None = None,
                      iteration_days: int | None = None, start_date: date | str | None = None,
                      target_date: date | str | None = None, release_milestone: str | None = None,
                      rationale: str | None = None) -> dict[str, Any]:
        """Create or update a project profile. Only the fields given are changed."""
        previous = self.store.get(project_id, "project_profile", project_id)
        base = previous.artifact.model_dump() if previous else {"id": project_id, "project_id": project_id}
        updates = {
            "name": name, "repos": repos, "iteration_days": iteration_days, "start_date": start_date,
            "target_date": target_date, "release_milestone": release_milestone,
        }
        profile = ProjectProfile.model_validate({**base, **{k: v for k, v in updates.items() if v is not None}})
        result = self.store.put(profile, actor=actor, rationale=rationale)
        return {"version": result.version, "changed": result.changed, "profile": profile.model_dump(mode="json")}

    def set_thresholds(self, project_id: str, changes: dict[str, Any], *, actor: str,
                       rationale: str | None = None) -> dict[str, Any]:
        self._require_human(actor, "change RAG thresholds")
        profile = self.profile(project_id)
        thresholds = RagThresholds.model_validate({**profile.thresholds.model_dump(), **changes})
        result = self.store.put(profile.model_copy(update={"thresholds": thresholds}), actor=actor,
                                rationale=rationale)
        return {"version": result.version, "thresholds": thresholds.model_dump()}

    # ----- tracker sync --------------------------------------------------

    def sync_github(self, project_id: str) -> dict[str, Any]:
        profile = self.profile(project_id)
        if not profile.repos:
            raise ValueError("the project has no repos configured")
        if self._github is None:
            self._github = GitHubClient()
        results = sync_project(self.store, profile, self._github, now=self._clock())
        return {"repos": [asdict(r) for r in results]}

    def work_items(self, project_id: str) -> list[WorkItem]:
        return [s.artifact for s in self.store.list_latest(project_id, "work_item")]

    # ----- metrics and forecasts ----------------------------------------

    def flow(self, project_id: str, window_days: int = 90) -> FlowMetrics:
        return compute_flow(self.work_items(project_id), self._clock(), window_days)

    def flow_report(self, project_id: str, window_days: int = 90) -> dict[str, Any]:
        metrics = self.flow(project_id, window_days)
        data = asdict(metrics)
        data.pop("daily_throughput")
        data["aging"] = data["aging"][:15]
        data["cycle_time_note"] = (
            "Cycle time needs a known start of work; items that went straight from todo to done "
            "between syncs only have lead time."
        )
        return data

    def _scope(self, project_id: str, milestone: str | None) -> tuple[list[WorkItem], str]:
        profile = self.profile(project_id)
        milestone = milestone or profile.release_milestone
        items = [i for i in self.work_items(project_id) if i.item_type != "epic" and i.state != "cancelled"]
        if milestone:
            items = [i for i in items if i.milestone == milestone]
        return items, milestone or "all open items"

    def forecast(self, project_id: str, milestone: str | None = None, window_days: int = 90,
                 trials: int = 10000, seed: int | None = None) -> dict[str, Any]:
        profile = self.profile(project_id)
        scope, scope_name = self._scope(project_id, milestone)
        remaining = sum(1 for i in scope if i.state in OPEN_STATES)
        metrics = self.flow(project_id, window_days)
        today = self._clock().date()
        result: dict[str, Any] = {"scope": scope_name, "remaining_items": remaining,
                                  "history_window_days": window_days,
                                  "throughput_per_week": metrics.throughput_per_week}
        if not scope:
            result["error"] = f"no work items in scope ({scope_name}); run sync_github or check the milestone name"
            return result
        try:
            result["when"] = forecast_when(remaining, metrics.daily_throughput, today, trials=trials,
                                           seed=seed).to_dict()
            if profile.target_date:
                result["how_many_by_target"] = forecast_how_many(metrics.daily_throughput, today,
                                                                 profile.target_date, trials=trials,
                                                                 seed=seed).to_dict()
        except ForecastError as exc:
            result["error"] = str(exc)
        return result

    def agile_evm(self, project_id: str, milestone: str | None = None, budget: float | None = None,
                  actual_cost: float | None = None) -> dict[str, Any]:
        profile = self.profile(project_id)
        if not (profile.start_date and profile.target_date):
            return {"error": "agile EVM needs start_date and target_date on the project"}
        scope, scope_name = self._scope(project_id, milestone)
        estimated = [i for i in scope if i.estimate is not None]
        total = sum(i.estimate for i in estimated)
        if not total:
            return {"error": f"no estimated items in scope ({scope_name}); add labels like 'points:3'"}
        completed = sum(i.estimate for i in estimated if i.state == "done")
        planned = max(math.ceil((profile.target_date - profile.start_date).days / profile.iteration_days), 1)
        elapsed = (self._clock().date() - profile.start_date).days // profile.iteration_days
        result = evm_engine.agile_evm(total, completed, planned, min(max(elapsed, 0), planned), budget, actual_cost)
        return {
            "scope": scope_name,
            "estimate_coverage": f"{len(estimated)} of {len(scope)} items estimated",
            "iterations_planned": planned,
            "iterations_completed": min(max(elapsed, 0), planned),
            **result.to_dict(),
        }

    def health(self, project_id: str, seed: int | None = None) -> dict[str, Any]:
        """Computed RAG per dimension plus overall. No model involvement."""
        profile = self.profile(project_id)
        metrics = self.flow(project_id)
        scope, _ = self._scope(project_id, None)
        remaining = sum(1 for i in scope if i.state in OPEN_STATES)
        when = None
        if scope:
            try:
                when = forecast_when(remaining, metrics.daily_throughput, self._clock().date(), seed=seed)
            except ForecastError:
                pass
        evm_result = self.agile_evm(project_id)
        dimensions = [
            rag.schedule_status(when, profile.target_date),
            rag.spi_status(evm_result.get("spi"), profile.thresholds),
            *rag.flow_statuses(metrics, profile.thresholds),
            rag.risk_status(self._risks(project_id), profile.thresholds),
        ]
        return {
            "overall": rag.overall(dimensions),
            "unknown_dimensions": [d.name for d in dimensions if d.rag == "unknown"],
            "dimensions": [d.model_dump() for d in dimensions],
            "metrics": {
                "remaining_items": remaining,
                "throughput_per_week": metrics.throughput_per_week,
                "cycle_time_p85_days": metrics.cycle_time_p85,
                "wip": metrics.wip,
                "blocked": metrics.blocked,
                "spi": evm_result.get("spi"),
                "forecast_p50": when.percentiles[50].isoformat() if when else None,
                "forecast_p85": when.percentiles[85].isoformat() if when else None,
            },
        }

    def draft_status_report(self, project_id: str, *, requested_by: str, summary: str,
                            accomplishments: list[str] | None = None, next_steps: list[str] | None = None,
                            decisions_needed: list[str] | None = None, period_days: int = 7) -> dict[str, Any]:
        """Store a status report whose RAG and metrics come from health(); the caller writes only the narrative."""
        validate_actor(requested_by)
        health = self.health(project_id)
        today = self._clock().date()
        report = StatusReport(
            id=self.store.new_id(project_id, "status_report"),
            project_id=project_id,
            period_start=today - timedelta(days=period_days - 1),
            period_end=today,
            overall=health["overall"],
            dimensions=health["dimensions"],
            metrics=health["metrics"],
            summary=summary,
            accomplishments=accomplishments or [],
            next_steps=next_steps or [],
            decisions_needed=decisions_needed or [],
            top_risks=[r.id for r in risk.rank(self._risks(project_id))[:5]],
        )
        result = self.store.put(report, actor=REPORTING_ACTOR, rationale=f"Drafted at the request of {requested_by}")
        return {"version": result.version, "report": report.model_dump(mode="json")}

    # ----- generic artifacts ---------------------------------------------

    def artifact_schema(self, kind: str) -> dict[str, Any]:
        return artifact_type(kind).model_json_schema()

    def save_artifact(self, project_id: str, kind: str, data: dict[str, Any], *, actor: str,
                      rationale: str | None = None, sources: list[str] | None = None) -> dict[str, Any]:
        """Create or fully replace an artifact. A missing id is generated (e.g. 'R-4')."""
        cls = artifact_type(kind)
        self._check_writable_kind(kind, actor)
        data = dict(data)
        if data.setdefault("project_id", project_id) != project_id:
            raise ValueError("data.project_id does not match project_id")
        if not data.get("id"):
            data["id"] = project_id if kind == "project_profile" else (
                _SINGLETON_IDS.get(kind) or self.store.new_id(project_id, kind))
        artifact = cls.model_validate(data)
        result = self.store.put(artifact, actor=actor, rationale=rationale, sources=sources or [])
        return {"id": artifact.id, "version": result.version, "changed": result.changed}

    def update_artifact(self, project_id: str, kind: str, artifact_id: str, changes: dict[str, Any], *,
                        actor: str, rationale: str | None = None,
                        sources: list[str] | None = None) -> dict[str, Any]:
        """Apply top-level field changes to the latest version of an artifact."""
        self._check_writable_kind(kind, actor)
        current = self._require(project_id, kind, artifact_id)
        if {"id", "project_id"} & changes.keys():
            raise ValueError("id and project_id cannot be changed")
        updated = type(current).model_validate({**current.model_dump(), **changes})
        result = self.store.put(updated, actor=actor, rationale=rationale, sources=sources or [])
        return {"id": artifact_id, "version": result.version, "changed": result.changed}

    def get_artifact(self, project_id: str, kind: str, artifact_id: str,
                     version: int | None = None) -> dict[str, Any]:
        stored = self.store.get(project_id, kind, artifact_id, version)
        if stored is None:
            raise LookupError(f"no {kind} {artifact_id!r} in project {project_id!r}")
        return stored.to_dict()

    def list_artifacts(self, project_id: str, kind: str, status: str | None = None) -> list[dict[str, Any]]:
        artifact_type(kind)  # validates the kind
        items = [s.artifact for s in self.store.list_latest(project_id, kind)]
        if status is not None:
            items = [a for a in items if getattr(a, "status", getattr(a, "state", None)) == status]
        return [a.model_dump(mode="json") for a in items]

    def history(self, project_id: str, kind: str, artifact_id: str) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.store.history(project_id, kind, artifact_id)]

    # ----- human decisions -----------------------------------------------

    def approve(self, project_id: str, kind: str, artifact_id: str, *, actor: str,
                note: str | None = None) -> dict[str, Any]:
        self._require_human(actor, "approve")
        current = self._require(project_id, kind, artifact_id)
        now, name = self._clock(), _human_name(actor)
        changes: dict[str, Any]
        if kind == "charter":
            changes = {"approved_by": name, "approved_at": now}
        elif kind == "risk":
            changes = {"status": "open"}
        elif kind == "decision":
            changes = {"status": "accepted", "decided_by": name, "decided_at": now}
        elif kind == "change_request":
            changes = {"status": "approved", "decided_by": name, "decided_at": now, "decision_rationale": note}
        else:
            raise ValueError(f"{kind} artifacts have no approval step")
        updated = type(current).model_validate({**current.model_dump(), **changes})
        result = self.store.put(updated, actor=actor, rationale=note or "approved")
        return {"id": artifact_id, "version": result.version, "approved": True}

    def reject(self, project_id: str, kind: str, artifact_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        self._require_human(actor, "reject")
        current = self._require(project_id, kind, artifact_id)
        now, name = self._clock(), _human_name(actor)
        if kind == "risk":
            changes: dict[str, Any] = {"status": "rejected"}
        elif kind == "decision":
            changes = {"status": "rejected", "decided_by": name, "decided_at": now}
        elif kind == "change_request":
            changes = {"status": "rejected", "decided_by": name, "decided_at": now, "decision_rationale": reason}
        else:
            raise ValueError(f"{kind} artifacts cannot be rejected")
        updated = type(current).model_validate({**current.model_dump(), **changes})
        result = self.store.put(updated, actor=actor, rationale=reason)
        return {"id": artifact_id, "version": result.version, "rejected": True}

    # ----- checks ----------------------------------------------------------

    def scope_coverage(self, project_id: str) -> dict[str, Any]:
        """Trace charter objectives to scope elements and scope elements to GitHub issues."""
        charter = self.store.get(project_id, "charter", "charter")
        scope = self.store.get(project_id, "scope_structure", "scope")
        if charter is None or scope is None:
            return {"error": "scope coverage needs both a charter and a scope structure"}
        charter_obj: Charter = charter.artifact
        structure: ScopeStructure = scope.artifact
        objective_ids = {o.id for o in charter_obj.objectives}
        by_id = {e.id: e for e in structure.elements}

        def effective(element_id: str) -> set[str]:
            element = by_id[element_id]
            if element.objective_ids or element.parent is None:
                return set(element.objective_ids)
            return effective(element.parent)

        covered = set().union(*(effective(e.id) for e in structure.elements)) if structure.elements else set()
        mapped = {e.work_item_id for e in structure.elements if e.work_item_id}
        items = {i.id: i for i in self.work_items(project_id)}
        untraced = sorted(i.id for i in items.values()
                          if i.state in OPEN_STATES and i.item_type != "epic" and i.id not in mapped)
        return {
            "objectives_without_scope": sorted(objective_ids - covered),
            "elements_without_objective": sorted(e.id for e in structure.elements if not effective(e.id)),
            "unknown_objective_refs": sorted({o for e in structure.elements for o in e.objective_ids} - objective_ids),
            "mapped_issues_missing": sorted(m for m in mapped if m not in items),
            "open_issues_not_in_scope": untraced if mapped else [],
            "note": None if mapped else "No scope elements are mapped to GitHub issues yet, so creep detection is off.",
        }

    def risk_heatmap(self, project_id: str) -> dict[str, Any]:
        risks = self._risks(project_id)
        return {
            "ranked": [{"id": r.id, "title": r.title, "score": r.score, "status": r.status}
                       for r in risk.rank(risks)],
            "heat_map": risk.heat_map(risks),
        }

    # ----- internals -------------------------------------------------------

    def _risks(self, project_id: str) -> list[Risk]:
        return [s.artifact for s in self.store.list_latest(project_id, "risk")]

    def _require(self, project_id: str, kind: str, artifact_id: str) -> Artifact:
        stored = self.store.get(project_id, kind, artifact_id)
        if stored is None:
            raise LookupError(f"no {kind} {artifact_id!r} in project {project_id!r}")
        return stored.artifact

    @staticmethod
    def _check_writable_kind(kind: str, actor: str) -> None:
        if kind in _SYSTEM_KINDS and not is_system(actor):
            raise GovernanceError([_SYSTEM_KINDS[kind]])

    @staticmethod
    def _require_human(actor: str, what: str) -> None:
        validate_actor(actor)
        if not is_human(actor):
            raise GovernanceError([f"only a human may {what}"])
