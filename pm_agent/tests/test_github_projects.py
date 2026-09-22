import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pm_agent.integrations.github import GitHubClient, GitHubError, fetch_project_statuses, sync_project
from pm_agent.schemas import ProjectProfile

from .conftest import NOW
from .test_github_sync import gh_issue


def _node(number, status, updated="2026-09-20T08:00:00Z", repo="o/r"):
    return {"status": {"name": status, "updatedAt": updated} if status else None,
            "content": {"number": number, "repository": {"nameWithOwner": repo}}}


class FakeGitHubWithBoard:
    """REST issues plus a GraphQL project board, served two board items per page."""

    def __init__(self, issues, board_nodes, owner_type="User"):
        self.issues, self.nodes, self.owner_type = issues, board_nodes, owner_type
        self.graphql_calls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            body = json.loads(request.content)
            self.graphql_calls.append(body["variables"])
            start = int(body["variables"]["cursor"] or 0)
            chunk = self.nodes[start:start + 2]
            more = start + 2 < len(self.nodes)
            board = {"items": {"nodes": chunk, "pageInfo": {"hasNextPage": more, "endCursor": str(start + 2)}}}
            return httpx.Response(200, json={"data": {"repositoryOwner": {"projectV2": board}}})
        return httpx.Response(200, json=self.issues)

    def client(self):
        return GitHubClient(token="t", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def profile(store):
    p = ProjectProfile(id="demo", project_id="demo", name="Demo", repos=["o/r"], github_project="acme/7")
    store.put(p, actor="human:prajwal")
    return p


def test_fetch_statuses_paginates_and_skips_non_issues():
    nodes = [_node(1, "In Progress"), _node(2, "Todo"), _node(3, None),
             {"status": {"name": "Done"}, "content": {}},  # a draft item
             _node(4, "Blocked", repo="other/repo")]
    fake = FakeGitHubWithBoard([], nodes)
    statuses = fetch_project_statuses(fake.client(), "acme/7")
    assert set(statuses) == {"o/r#1", "o/r#2", "other/repo#4"}
    assert statuses["o/r#1"].updated_at == datetime(2026, 9, 20, 8, tzinfo=timezone.utc)
    assert [c["cursor"] for c in fake.graphql_calls] == [None, "2", "4"]
    assert fake.graphql_calls[0] == {"owner": "acme", "number": 7, "field": "Status", "cursor": None}


def test_board_status_drives_state_and_start_time(store, profile):
    issues = [gh_issue(1), gh_issue(2, labels=["blocked"]), gh_issue(3), gh_issue(4, state="closed",
                                                                            closed="2026-09-19T00:00:00Z")]
    nodes = [_node(1, "In Progress"), _node(2, "In Progress"), _node(3, "Done"), _node(4, "In Progress")]
    sync_project(store, profile, FakeGitHubWithBoard(issues, nodes).client(), now=NOW)
    items = {s.artifact.id: s.artifact for s in store.list_latest("demo", "work_item")}
    assert items["o/r#1"].state == "in_progress"
    assert items["o/r#1"].started_at == datetime(2026, 9, 20, 8, tzinfo=timezone.utc)
    assert items["o/r#2"].state == "blocked"   # a blocked label wins over the board
    assert items["o/r#3"].state == "todo"      # Done on the board, but the issue is still open
    assert items["o/r#4"].state == "done"      # closed issues follow GitHub, not the board


def test_board_moves_are_picked_up_without_issue_updates(store, profile):
    sync_project(store, profile, FakeGitHubWithBoard([gh_issue(1)], [_node(1, "Todo")]).client(), now=NOW)
    assert store.get("demo", "work_item", "o/r#1").artifact.state == "todo"

    later = NOW + timedelta(days=1)
    moved = FakeGitHubWithBoard([], [_node(1, "In Review", updated="2026-09-23T09:00:00Z")])
    [result] = sync_project(store, profile, moved.client(), now=later)
    item = store.get("demo", "work_item", "o/r#1")
    assert result.board_updates == 1 and result.fetched == 0
    assert item.artifact.state == "in_progress"
    assert item.artifact.started_at == datetime(2026, 9, 23, 9, tzinfo=timezone.utc)
    assert item.rationale == "project board status: In Review"


def test_missing_project_is_reported(store, profile):
    client = GitHubClient(token="t", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"repositoryOwner": {"projectV2": None}}})))
    with pytest.raises(GitHubError, match="acme/7 not found"):
        sync_project(store, profile, client, now=NOW)
    errors = GitHubClient(token="t", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"errors": [{"message": "Resource not accessible"}]})))
    with pytest.raises(GitHubError, match="Resource not accessible"):
        fetch_project_statuses(errors, "acme/7")
