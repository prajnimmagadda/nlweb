"""Read-only sync of GitHub issues into work items.

Uses the REST issues endpoint, which works with or without a token (a token
raises the rate limit and allows private repos), plus GraphQL for the Status field
of a GitHub project (v2) when the profile names one. Set GITHUB_TOKEN to a token
with read access to Issues, and to Projects if you use a project board.
"""

import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from pm_agent.schemas import ProjectProfile, StatusMapping, WorkItem
from pm_agent.schemas.work import ACTIVE_STATES
from pm_agent.store import Store

SYNC_ACTOR = "system:github-sync"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com",
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pm-copilot",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(base_url=base_url, headers=headers, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def iter_issues(self, repo: str, since: datetime | None = None) -> Iterator[dict[str, Any]]:
        """All issues (not pull requests) in a repo, optionally only those updated since a time."""
        params: dict[str, Any] | None = {"state": "all", "per_page": 100, "sort": "updated", "direction": "asc"}
        if since is not None:
            params["since"] = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url: str | None = f"/repos/{repo}/issues"
        while url:
            response = self._client.get(url, params=params)
            if response.status_code >= 400:
                try:
                    message = response.json().get("message", response.text)
                except ValueError:
                    message = response.text
                raise GitHubError(f"GitHub returned {response.status_code} for {repo}: {message}")
            for issue in response.json():
                if "pull_request" not in issue:
                    yield issue
            url, params = response.links.get("next", {}).get("url"), None

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("/graphql", json={"query": query, "variables": variables})
        if response.status_code >= 400:
            raise GitHubError(f"GitHub GraphQL returned {response.status_code}: {response.text[:300]}")
        body = response.json()
        if body.get("errors"):
            raise GitHubError("GitHub GraphQL error: " + "; ".join(e.get("message", "?") for e in body["errors"]))
        return body["data"]


_PROJECT_ITEMS_QUERY = """
query($owner: String!, $number: Int!, $field: String!, $cursor: String) {
  repositoryOwner(login: $owner) {
    ... on User { projectV2(number: $number) { ...Items } }
    ... on Organization { projectV2(number: $number) { ...Items } }
  }
}
fragment Items on ProjectV2 {
  items(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      status: fieldValueByName(name: $field) {
        ... on ProjectV2ItemFieldSingleSelectValue { name updatedAt }
      }
      content { ... on Issue { number repository { nameWithOwner } } }
    }
  }
}
"""


@dataclass(frozen=True)
class ProjectStatus:
    item_id: str  # work item id, 'owner/repo#number'
    status: str
    updated_at: datetime | None


def fetch_project_statuses(client: GitHubClient, project: str, field: str = "Status") -> dict[str, ProjectStatus]:
    """Status of every issue in a GitHub project (v2), keyed by work item id. Needs read access to Projects."""
    owner, number = project.rsplit("/", 1)
    statuses: dict[str, ProjectStatus] = {}
    cursor = None
    while True:
        data = client.graphql(_PROJECT_ITEMS_QUERY,
                              {"owner": owner, "number": int(number), "field": field, "cursor": cursor})
        owner_node = data.get("repositoryOwner")
        if owner_node is None:
            raise GitHubError(f"no GitHub user or organization named {owner!r}")
        board = owner_node.get("projectV2")
        if board is None:
            raise GitHubError(f"project {project} not found, or the token can't read Projects")
        for node in board["items"]["nodes"]:
            content, status = node.get("content") or {}, node.get("status")
            if "number" not in content or not status:
                continue  # draft items, pull requests, or no status set
            item_id = f"{content['repository']['nameWithOwner']}#{content['number']}"
            statuses[item_id] = ProjectStatus(item_id, status["name"], _parse_time(status.get("updatedAt")))
        page = board["items"]["pageInfo"]
        if not page["hasNextPage"]:
            return statuses
        cursor = page["endCursor"]


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _label_names(issue: dict[str, Any]) -> list[str]:
    return [label["name"] if isinstance(label, dict) else str(label) for label in issue.get("labels", [])]


def _estimate(labels: list[str], prefixes: list[str]) -> float | None:
    for label in labels:
        for prefix in prefixes:
            if label.lower().startswith(prefix.lower()):
                try:
                    return float(label[len(prefix):].strip())
                except ValueError:
                    continue
    return None


def _project_state(status: ProjectStatus | None, mapping: StatusMapping) -> str | None:
    if status is None:
        return None
    states = {name.lower(): state for name, state in mapping.project_status_states.items()}
    return states.get(status.status.lower())


def issue_to_work_item(project_id: str, repo: str, issue: dict[str, Any], mapping: StatusMapping,
                       previous: WorkItem | None, observed_at: datetime,
                       project_status: ProjectStatus | None = None) -> WorkItem:
    labels = _label_names(issue)
    lowered = {label.lower() for label in labels}

    def has_any(names: list[str]) -> bool:
        return any(name.lower() in lowered for name in names)

    board_state = _project_state(project_status, mapping)
    if issue["state"] == "closed":
        state = "cancelled" if issue.get("state_reason") in ("not_planned", "duplicate") else "done"
    elif has_any(mapping.blocked_labels):
        state = "blocked"
    elif board_state in ("todo", "in_progress", "blocked"):
        state = board_state  # an open issue marked Done on the board stays open until it is closed
    elif has_any(mapping.in_progress_labels):
        state = "in_progress"
    else:
        state = "todo"

    native_type = issue.get("type")
    native_type = native_type.get("name", "").lower() if isinstance(native_type, dict) else ""
    if has_any(mapping.epic_labels) or native_type == "epic":
        item_type = "epic"
    elif has_any(mapping.bug_labels) or native_type == "bug":
        item_type = "bug"
    elif native_type == "task":
        item_type = "task"
    else:
        item_type = "story"

    # The REST API has no status history, so the start of work is the first time we
    # see the item in an active state. updated_at approximates when that change happened.
    if previous is not None and previous.started_at is not None:
        started_at = previous.started_at
    elif state in ACTIVE_STATES:
        # Prefer the time the board status changed; fall back to the issue's last update.
        changed = project_status.updated_at if board_state in ACTIVE_STATES and project_status else None
        changed = changed or _parse_time(issue.get("updated_at")) or observed_at
        started_at = min(changed, observed_at)
    else:
        started_at = None

    closed_at = None
    if state in ("done", "cancelled"):
        closed_at = _parse_time(issue.get("closed_at")) or observed_at

    milestone = issue.get("milestone")
    return WorkItem(
        id=f"{repo}#{issue['number']}",
        project_id=project_id,
        title=issue["title"],
        url=issue.get("html_url"),
        item_type=item_type,
        state=state,
        labels=labels,
        assignees=[a["login"] for a in issue.get("assignees") or []],
        milestone=milestone.get("title") if isinstance(milestone, dict) else None,
        estimate=_estimate(labels, mapping.estimate_label_prefixes),
        created_at=_parse_time(issue["created_at"]),
        started_at=started_at,
        closed_at=closed_at,
    )


@dataclass
class SyncResult:
    repo: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    board_updates: int = 0  # state changes that came only from the project board
    incremental_since: str | None = None


def _board_only_update(item: WorkItem, status: ProjectStatus, mapping: StatusMapping,
                       now: datetime) -> WorkItem | None:
    """Apply a board status to an open item that the REST pass didn't return (board moves don't bump updated_at)."""
    board_state = _project_state(status, mapping)
    if item.state in ("done", "cancelled") or board_state not in ("todo", "in_progress", "blocked"):
        return None
    blocked_label = any(label.lower() in {b.lower() for b in mapping.blocked_labels} for label in item.labels)
    state = "blocked" if blocked_label else board_state
    started_at = item.started_at
    if started_at is None and state in ACTIVE_STATES:
        started_at = min(status.updated_at or now, now)
    if (state, started_at) == (item.state, item.started_at):
        return None
    return item.model_copy(update={"state": state, "started_at": started_at})


def sync_project(store: Store, profile: ProjectProfile, client: GitHubClient,
                 now: datetime | None = None) -> list[SyncResult]:
    """Pull issues for every repo in the profile. Subsequent runs only fetch updated issues.

    When the profile names a GitHub project, its Status field is read on every sync and
    takes precedence over labels for open issues.
    """
    now = now or datetime.now(timezone.utc)
    statuses = (fetch_project_statuses(client, profile.github_project, profile.status_mapping.project_status_field)
                if profile.github_project else {})
    results = []
    for repo in profile.repos:
        meta_key = f"github_sync:{profile.project_id}:{repo}"
        last = store.get_meta(meta_key)
        since = datetime.fromisoformat(last) - timedelta(minutes=5) if last else None
        result = SyncResult(repo=repo, incremental_since=since.isoformat() if since else None)
        seen: set[str] = set()
        for issue in client.iter_issues(repo, since):
            result.fetched += 1
            item_id = f"{repo}#{issue['number']}"
            seen.add(item_id)
            previous = store.get(profile.project_id, "work_item", item_id)
            item = issue_to_work_item(profile.project_id, repo, issue, profile.status_mapping,
                                      previous.artifact if previous else None, now, statuses.get(item_id))
            put = store.put(item, actor=SYNC_ACTOR, sources=[issue["html_url"]] if issue.get("html_url") else [])
            if not put.changed:
                result.unchanged += 1
            elif put.version == 1:
                result.created += 1
            else:
                result.updated += 1
        for item_id, status in statuses.items():
            if not item_id.startswith(f"{repo}#") or item_id in seen:
                continue
            stored = store.get(profile.project_id, "work_item", item_id)
            updated = stored and _board_only_update(stored.artifact, status, profile.status_mapping, now)
            if updated:
                store.put(updated, actor=SYNC_ACTOR, rationale=f"project board status: {status.status}",
                          sources=[f"github project {profile.github_project}"])
                result.board_updates += 1
        store.set_meta(meta_key, now.isoformat())
        store.log(profile.project_id, SYNC_ACTOR, "github_sync", asdict(result))
        results.append(result)
    return results
