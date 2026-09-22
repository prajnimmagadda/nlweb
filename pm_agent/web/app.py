"""Local web UI for the human side of PM Copilot.

`python -m pm_agent ui` serves a small single-page app on 127.0.0.1. Everything done
through it acts as you ('human:<you>', as in the CLI): approving, rejecting,
scheduling, changing settings and thresholds. The agent never talks to this server.

Because it can approve things, the server only answers requests that:
* arrive with a Host header naming the loopback address it listens on (blocks DNS rebinding);
* carry the per-launch token in the X-PM-Token header (the browser gets it from the URL
  fragment printed at launch; fragments are never sent to servers, and the page removes it
  from the address bar straight away);
* send JSON when they change something.
A custom header and JSON body can't be sent cross-site without a CORS preflight, which
this server never grants, so other sites can't drive it from your browser.
"""

import json
import secrets
import threading
import uuid
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pm_agent.engines import impact
from pm_agent.engines.forecast import ForecastError
from pm_agent.governance import AutonomyLevel, GovernanceError
from pm_agent.integrations.github import GitHubError
from pm_agent.integrations.google import GoogleError
from pm_agent.playbooks import load_playbooks
from pm_agent.schemas.project import DEFAULT_PLAYBOOKS
from pm_agent.service import Copilot

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8765
TOKEN_HEADER = "x-pm-token"
_MAX_BODY = 256 * 1024
_MAX_MATERIAL = 100_000

_SECURITY_HEADERS = [
    (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                 b"connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]

AUTONOMY_LABELS = {
    AutonomyLevel.INFORM: "informs",
    AutonomyLevel.DRAFT: "drafts",
    AutonomyLevel.ACT_WITH_APPROVAL: "asks first",
    AutonomyLevel.AUTONOMOUS: "proposes",
}

RunnerFactory = Callable[..., Any]


@dataclass(frozen=True)
class Req:
    body: dict[str, Any]
    query: dict[str, str]


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Guard:
    """ASGI middleware: Host allowlist and token check, plus security headers on every response."""

    def __init__(self, app: ASGIApp, *, token: str, allowed_hosts: set[str]):
        self.app = app
        self.token = token.encode()
        self.allowed_hosts = {h.lower() for h in allowed_hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin-1").lower()
        if host not in self.allowed_hosts:
            await _plain(send, 421, "This server only answers on its loopback address.")
            return
        path, method = scope["path"], scope["method"]
        if path.startswith("/api/"):
            if not secrets.compare_digest(headers.get(TOKEN_HEADER.encode(), b""), self.token):
                await _plain(send, 401, "Missing or wrong token. Open the link printed when the UI started.")
                return
            if method in ("POST", "PUT", "PATCH"):
                content_type = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
                if content_type != b"application/json":
                    await _plain(send, 415, "Send JSON.")
                    return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *_SECURITY_HEADERS]
                if path.startswith("/api/"):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def _plain(send: Send, status: int, text: str) -> None:
    body = text.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()), *_SECURITY_HEADERS]})
    await send({"type": "http.response.body", "body": body})


def _error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors():
            where = ".".join(str(p) for p in error["loc"])
            parts.append(f"{where}: {error['msg']}" if where else error["msg"])
        return "; ".join(parts)
    return str(exc)


def _status_for(exc: Exception) -> int:
    if isinstance(exc, HttpError):
        return exc.status
    if isinstance(exc, GovernanceError):
        return 403
    if isinstance(exc, LookupError):
        return 404
    if isinstance(exc, (GitHubError, GoogleError)):
        return 502
    return 400


class Jobs:
    """Background playbook runs. Results are kept in memory for this launch only."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, work: Callable[[], dict[str, Any]], info: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {"id": job_id, "status": "running", **info,
                                  "started_at": datetime.now(timezone.utc).isoformat()}

        def target() -> None:
            try:
                update = {"status": "finished", "result": work()}
            except Exception as exc:  # noqa: BLE001 - reported to the user, never swallowed silently
                update = {"status": "error", "error": _error_message(exc) or type(exc).__name__}
            with self._lock:
                self._jobs[job_id].update(update, finished_at=datetime.now(timezone.utc).isoformat())

        threading.Thread(target=target, name=f"pm-run-{job_id}", daemon=True).start()
        return job_id

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise LookupError(f"no run {job_id!r} in this session")
            return dict(self._jobs[job_id])

    def running(self, project_id: str, playbook: str) -> bool:
        with self._lock:
            return any(j["status"] == "running" and j["project_id"] == project_id and j["playbook"] == playbook
                       for j in self._jobs.values())


def _lifecycle_order(playbook: Any) -> tuple[int, str]:
    order = DEFAULT_PLAYBOOKS.index(playbook.id) if playbook.id in DEFAULT_PLAYBOOKS else len(DEFAULT_PLAYBOOKS)
    return order, playbook.id


def _default_runner_factory(copilot: Copilot, **options: Any) -> Any:
    from pm_agent.agent.runner import PlaybookRunner  # imports the Anthropic SDK only when a run starts

    return PlaybookRunner(copilot, **options)


def create_app(copilot: Copilot, *, user: str, token: str, port: int = DEFAULT_PORT,
               runner_factory: RunnerFactory = _default_runner_factory,
               job_copilot: Callable[[], Copilot] | None = None) -> Starlette:
    """Build the app. `job_copilot` gives background runs their own database connection;
    without it they share `copilot` and hold the lock for the whole run (fine for tests)."""
    lock = threading.RLock()
    jobs = Jobs()

    def call(fn: Callable[[], Any]) -> Any:
        with lock:
            return fn()

    def endpoint(handler: Callable[..., Any]) -> Callable[[Request], Any]:
        async def wrapped(request: Request) -> Response:
            body: dict[str, Any] = {}
            if request.method in ("POST", "PUT", "PATCH"):
                raw = await request.body()
                if len(raw) > _MAX_BODY:
                    return JSONResponse({"error": "request too large"}, status_code=413)
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        return JSONResponse({"error": "body is not valid JSON"}, status_code=400)
                    if not isinstance(body, dict):
                        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
            req = Req(body=body, query=dict(request.query_params))
            try:
                result = await run_in_threadpool(call, lambda: handler(req, **request.path_params))
            except (HttpError, GovernanceError, LookupError, ValueError, GitHubError, GoogleError,
                    ForecastError) as exc:
                return JSONResponse({"error": _error_message(exc)}, status_code=_status_for(exc))
            return JSONResponse(to_jsonable_python(result))

        return wrapped

    # ----- handlers ----------------------------------------------------------

    def counts(project_id: str) -> dict[str, int]:
        return {
            "inbox": len(copilot.inbox(project_id)),
            "change_requests": copilot.count_artifacts(project_id, "change_request", "submitted"),
        }

    def session(req: Req) -> dict[str, Any]:
        google = copilot.google
        return {
            "user": user,
            "projects": copilot.projects(),
            "google": {"connected": google is not None,
                       "drive": bool(google is not None and google.can_read_drive)},
        }

    def seed_demo(req: Req) -> dict[str, Any]:
        from pm_agent.demo import seed_demo as seed

        if copilot.store.get("demo", "project_profile", "demo") is not None:
            raise HttpError(409, "a project called 'demo' already exists")
        return seed(copilot, actor=user)

    def dashboard(req: Req, project_id: str) -> dict[str, Any]:
        profile = copilot.profile(project_id)
        baseline = copilot.current_baseline(project_id)
        return {
            "project": {"id": profile.id, "name": profile.name, "release_milestone": profile.release_milestone,
                        "target_date": profile.target_date, "repos": profile.repos, "sandbox": profile.sandbox},
            "last_synced": copilot.last_synced(project_id),
            "health": copilot.health(project_id),
            "inbox": copilot.inbox(project_id),
            "runs": copilot.runs(project_id, 3),
            "baseline": baseline.model_dump(mode="json") if baseline else None,
            "counts": counts(project_id),
        }

    def get_counts(req: Req, project_id: str) -> dict[str, int]:
        copilot.profile(project_id)
        return counts(project_id)

    def sync(req: Req, project_id: str) -> dict[str, Any]:
        return copilot.sync_github(project_id)

    def inbox(req: Req, project_id: str) -> dict[str, Any]:
        return {"items": copilot.inbox(project_id)}

    def item(req: Req, project_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
        stored = copilot.get_artifact(project_id, kind, artifact_id)
        extra: dict[str, Any] = {"sandbox": copilot.profile(project_id).sandbox}
        if kind == "change_request":
            cr = copilot.store.get(project_id, kind, artifact_id).artifact
            extra["analysis_current"] = bool(
                cr.proposal is not None and cr.analysis is not None
                and cr.analysis.proposal_digest == impact.proposal_digest(cr.proposal))
            extra["actions"] = [a for a in copilot.list_artifacts(project_id, "action_request")
                                if a["change_request_id"] == artifact_id]
            extra["release_milestone"] = copilot.profile(project_id).release_milestone
        if kind == "baseline":
            current = copilot.current_baseline(project_id)
            extra["current_baseline"] = current.model_dump(mode="json") if current else None
        return {**stored, **extra}

    def item_history(req: Req, project_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
        return {"items": copilot.history(project_id, kind, artifact_id)}

    def decide(req: Req, project_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
        decision, note = req.body.get("decision"), (req.body.get("note") or "").strip() or None
        version = req.body.get("version")
        if version is not None and not isinstance(version, int):
            raise ValueError("version must be an integer")
        if decision == "approve":
            result = copilot.approve(project_id, kind, artifact_id, actor=user, note=note, expected_version=version)
        elif decision == "reject":
            if not note:
                raise ValueError("say why you're rejecting it; the reason goes in the audit log")
            result = copilot.reject(project_id, kind, artifact_id, actor=user, reason=note, expected_version=version)
        else:
            raise ValueError("decision must be 'approve' or 'reject'")
        return {**result, "counts": counts(project_id)}

    def assess(req: Req, project_id: str, artifact_id: str) -> dict[str, Any]:
        return copilot.assess_change_request(project_id, artifact_id, requested_by=user)

    def change_requests(req: Req, project_id: str) -> dict[str, Any]:
        return {"items": sorted(copilot.list_artifacts(project_id, "change_request"),
                                key=lambda c: int(c["id"].split("-")[1]), reverse=True)}

    def baseline(req: Req, project_id: str) -> dict[str, Any]:
        return copilot.baseline_variance(project_id)

    def propose_baseline(req: Req, project_id: str) -> dict[str, Any]:
        name = (req.body.get("name") or "").strip()
        if not name:
            raise ValueError("give the baseline a name")
        return copilot.propose_baseline(project_id, actor=user, name=name,
                                        reason=(req.body.get("reason") or "").strip() or "set from the web UI")

    def settings(req: Req, project_id: str) -> dict[str, Any]:
        google = copilot.google
        return {"profile": copilot.profile(project_id).model_dump(mode="json"),
                "google": {"connected": google is not None,
                           "drive": bool(google is not None and google.can_read_drive)}}

    def save_settings(req: Req, project_id: str) -> dict[str, Any]:
        profile_changes, thresholds = req.body.get("profile") or {}, req.body.get("thresholds")
        if not isinstance(profile_changes, dict) or not (thresholds is None or isinstance(thresholds, dict)):
            raise ValueError("expected {'profile': {...}, 'thresholds': {...}}")
        result = copilot.update_profile(project_id, profile_changes, actor=user, rationale="changed in the web UI")
        if thresholds:
            current = copilot.profile(project_id).thresholds.model_dump()
            if {**current, **thresholds} != current:
                copilot.set_thresholds(project_id, thresholds, actor=user, rationale="changed in the web UI")
        return {"version": result["version"], "profile": copilot.profile(project_id).model_dump(mode="json")}

    def add_schedule(req: Req, project_id: str) -> dict[str, Any]:
        return copilot.add_schedule(project_id, req.body, actor=user)

    def remove_schedule(req: Req, project_id: str, playbook: str) -> dict[str, Any]:
        return copilot.remove_schedule(project_id, playbook, actor=user)

    def playbooks(req: Req) -> dict[str, Any]:
        return {"items": [
            {"name": p.id.rsplit(".", 1)[-1], "id": p.id, "title": p.name, "processes": p.processes,
             "summary": " ".join(p.summary.split()), "autonomy": int(p.autonomy),
             "autonomy_label": AUTONOMY_LABELS[p.autonomy]}
            for p in sorted(load_playbooks().values(), key=_lifecycle_order)
        ]}

    def runs(req: Req, project_id: str) -> dict[str, Any]:
        playbook, limit = req.query.get("playbook"), req.query.get("limit", "20")
        if not limit.isdigit():
            raise ValueError("limit must be a number")
        items = copilot.runs(project_id, 200)
        if playbook:
            items = [r for r in items if str(r.get("playbook", "")).rsplit(".", 1)[-1] == playbook]
        return {"items": items[:max(1, min(int(limit), 100))]}

    def start_run(req: Req, project_id: str) -> dict[str, Any]:
        playbook = req.body.get("playbook")
        if playbook not in {p.id.rsplit(".", 1)[-1] for p in load_playbooks().values()}:
            raise ValueError(f"unknown playbook {playbook!r}")
        copilot.profile(project_id)
        material = req.body.get("material") or None
        if material is not None and (not isinstance(material, str) or len(material) > _MAX_MATERIAL):
            raise ValueError(f"material must be text under {_MAX_MATERIAL} characters")
        options: dict[str, Any] = {}
        if req.body.get("model"):
            options["model"] = str(req.body["model"]).strip()
        if req.body.get("effort"):
            if req.body["effort"] not in ("low", "medium", "high", "xhigh", "max"):
                raise ValueError("effort must be low, medium, high, xhigh or max")
            options["effort"] = req.body["effort"]
        if req.body.get("max_turns") is not None:
            turns = req.body["max_turns"]
            if not isinstance(turns, int) or not 1 <= turns <= 100:
                raise ValueError("turn limit must be a whole number from 1 to 100")
            options["max_turns"] = turns
        if jobs.running(project_id, playbook):
            raise HttpError(409, f"{playbook} is already running for this project")

        def work() -> dict[str, Any]:
            own = job_copilot() if job_copilot else None

            def execute(target: Copilot) -> dict[str, Any]:
                runner = runner_factory(target, **options)
                try:
                    result = runner.run(playbook, project_id, material=material)
                finally:
                    runner.close()
                return {**asdict(result), "tool_calls": [asdict(c) for c in result.tool_calls]}

            if own is None:
                return call(lambda: execute(copilot))
            try:
                return execute(own)
            finally:
                own.store.close()

        job_id = jobs.start(work, {"project_id": project_id, "playbook": playbook})
        return {"job_id": job_id}

    def job(req: Req, job_id: str) -> dict[str, Any]:
        return jobs.get(job_id)

    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"cache-control": "no-store"})

    p = "/api/projects/{project_id}"
    routes = [
        Route("/", index),
        Route("/api/session", endpoint(session)),
        Route("/api/demo", endpoint(seed_demo), methods=["POST"]),
        Route("/api/playbooks", endpoint(playbooks)),
        Route("/api/jobs/{job_id}", endpoint(job)),
        Route(f"{p}/dashboard", endpoint(dashboard)),
        Route(f"{p}/counts", endpoint(get_counts)),
        Route(f"{p}/sync", endpoint(sync), methods=["POST"]),
        Route(f"{p}/inbox", endpoint(inbox)),
        Route(p + "/items/{kind}/{artifact_id}", endpoint(item)),
        Route(p + "/items/{kind}/{artifact_id}/history", endpoint(item_history)),
        Route(p + "/items/{kind}/{artifact_id}/decision", endpoint(decide), methods=["POST"]),
        Route(f"{p}/change-requests", endpoint(change_requests)),
        Route(p + "/change-requests/{artifact_id}/assess", endpoint(assess), methods=["POST"]),
        Route(f"{p}/baseline", endpoint(baseline)),
        Route(f"{p}/baseline", endpoint(propose_baseline), methods=["POST"]),
        Route(f"{p}/settings", endpoint(settings)),
        Route(f"{p}/settings", endpoint(save_settings), methods=["PUT"]),
        Route(f"{p}/schedules", endpoint(add_schedule), methods=["POST"]),
        Route(p + "/schedules/{playbook}", endpoint(remove_schedule), methods=["DELETE"]),
        Route(f"{p}/runs", endpoint(runs)),
        Route(f"{p}/runs", endpoint(start_run), methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    return Guard(app, token=token, allowed_hosts=hosts)  # type: ignore[return-value]


def serve(db_path: str, *, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    import uvicorn

    from pm_agent.cli import current_user
    from pm_agent.integrations.google import load_client
    from pm_agent.store import Store

    def make_copilot() -> Copilot:
        return Copilot(Store(db_path), google=load_client())

    token = secrets.token_urlsafe(32)
    copilot = make_copilot()
    app = create_app(copilot, user=current_user(), token=token, port=port, job_copilot=make_copilot)
    url = f"http://127.0.0.1:{port}/#token={token}"
    print(f"PM Copilot UI for {current_user()}: {url}\nKeep this link private; it lets whoever has it act as you. "
          "Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", server_header=False)
    finally:
        copilot.store.close()
