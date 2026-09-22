from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pm_agent.integrations.github import GitHubClient, GitHubError, sync_project
from pm_agent.schemas import ProjectProfile

from .conftest import NOW


def gh_issue(number, *, state="open", labels=(), created="2026-08-01T10:00:00Z", updated="2026-09-01T09:00:00Z",
             closed=None, state_reason=None, milestone="v1", pull_request=False, issue_type=None):
    issue = {
        "number": number, "title": f"Issue {number}", "state": state, "state_reason": state_reason,
        "labels": [{"name": name} for name in labels], "assignees": [{"login": "dana"}],
        "milestone": {"title": milestone} if milestone else None,
        "created_at": created, "updated_at": updated, "closed_at": closed,
        "html_url": f"https://github.com/o/r/issues/{number}",
    }
    if pull_request:
        issue["pull_request"] = {"url": "..."}
    if issue_type:
        issue["type"] = {"name": issue_type}
    return issue


class FakeGitHub:
    """Serves a list of issues in pages of two, recording each request."""

    def __init__(self, issues):
        self.issues = issues
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = int(request.url.params.get("page", "1"))
        chunk = self.issues[(page - 1) * 2: page * 2]
        headers = {}
        if page * 2 < len(self.issues):
            headers["Link"] = f'<https://api.github.com/repos/o/r/issues?page={page + 1}>; rel="next"'
        return httpx.Response(200, json=chunk, headers=headers)

    def client(self) -> GitHubClient:
        return GitHubClient(token="t", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def profile(store):
    p = ProjectProfile(id="demo", project_id="demo", name="Demo", repos=["o/r"])
    store.put(p, actor="human:prajwal")
    return p


def test_first_sync_maps_issues(store, profile):
    fake = FakeGitHub([
        gh_issue(1, labels=["In Progress"]),
        gh_issue(2, state="closed", closed="2026-09-10T00:00:00Z", state_reason="completed"),
        gh_issue(3, state="closed", closed="2026-09-11T00:00:00Z", state_reason="not_planned"),
        gh_issue(4, labels=["bug", "points:5"]),
        gh_issue(5, pull_request=True),
        gh_issue(6, labels=["blocked"], issue_type="Task"),
        gh_issue(7, labels=["epic"]),
    ])
    [result] = sync_project(store, profile, fake.client(), now=NOW)
    assert (result.fetched, result.created, result.updated) == (6, 6, 0)
    assert len(fake.requests) == 4  # followed the Link header across pages
    assert fake.requests[0].headers["Authorization"] == "Bearer t"
    assert "since" not in fake.requests[0].url.params

    items = {s.artifact.id: s.artifact for s in store.list_latest("demo", "work_item")}
    assert "o/r#5" not in items
    assert items["o/r#1"].state == "in_progress"
    assert items["o/r#1"].started_at == datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
    assert items["o/r#2"].state == "done" and items["o/r#2"].started_at is None
    assert items["o/r#3"].state == "cancelled"
    assert (items["o/r#4"].item_type, items["o/r#4"].estimate) == ("bug", 5.0)
    assert (items["o/r#6"].state, items["o/r#6"].item_type) == ("blocked", "task")
    assert items["o/r#7"].item_type == "epic"
    assert items["o/r#1"].milestone == "v1" and items["o/r#1"].assignees == ["dana"]
    assert store.get("demo", "work_item", "o/r#1").sources == ["https://github.com/o/r/issues/1"]
    assert store.get("demo", "work_item", "o/r#1").actor == "system:github-sync"


def test_incremental_sync_keeps_start_and_skips_unchanged(store, profile):
    sync_project(store, profile, FakeGitHub([gh_issue(1, labels=["in progress"]), gh_issue(2)]).client(), now=NOW)

    later = NOW + timedelta(days=3)
    fake = FakeGitHub([
        gh_issue(1, state="closed", closed="2026-09-24T00:00:00Z", updated="2026-09-24T00:00:00Z"),
        gh_issue(2),
    ])
    [result] = sync_project(store, profile, fake.client(), now=later)
    assert (result.updated, result.unchanged) == (1, 1)
    assert fake.requests[0].url.params["since"] == (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    item = store.get("demo", "work_item", "o/r#1").artifact
    assert item.state == "done"
    assert item.started_at == datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
    assert item.closed_at == datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert store.audit("demo")[0]["action"] == "github_sync"


def test_github_errors_are_reported(store, profile):
    client = GitHubClient(token="", transport=httpx.MockTransport(
        lambda request: httpx.Response(404, json={"message": "Not Found"})))
    with pytest.raises(GitHubError, match="404 for o/r: Not Found"):
        sync_project(store, profile, client, now=NOW)
