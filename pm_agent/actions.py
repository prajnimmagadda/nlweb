"""Carry out approved action requests. Runs only after a human approval, as 'system:executor'."""

from typing import Any

from pm_agent.integrations.github import GitHubClient, GitHubError
from pm_agent.schemas import ActionRequest, CreateIssuesPayload, SetMilestonePayload


class ActionError(RuntimeError):
    """An action failed part-way; `partial` holds what was done before the failure."""

    def __init__(self, message: str, partial: dict[str, Any]):
        super().__init__(message)
        self.partial = partial


def _milestone_number(github: GitHubClient, repo: str, title: str | None,
                      cache: dict[str, dict[str, int]]) -> int | None:
    if title is None:
        return None
    if repo not in cache:
        cache[repo] = github.milestones(repo)
    if title not in cache[repo]:
        raise GitHubError(f"milestone {title!r} doesn't exist in {repo}")
    return cache[repo][title]


def simulate(request: ActionRequest) -> dict[str, Any]:
    """What execute() would report, for sandbox (demo) projects. Nothing is sent anywhere."""
    payload = request.typed_payload()
    note = "Sandbox project: nothing was sent to GitHub."
    if isinstance(payload, CreateIssuesPayload):
        return {"simulated": True, "note": note,
                "created": [{"number": None, "url": None, "title": issue.title} for issue in payload.issues]}
    if isinstance(payload, SetMilestonePayload):
        return {"simulated": True, "note": note, "updated": list(payload.issue_numbers),
                "milestone": payload.milestone}
    raise ActionError(f"no executor for {request.action}", {})


def execute(request: ActionRequest, github: GitHubClient, approved_by: str) -> dict[str, Any]:
    payload = request.typed_payload()
    footer = f"\n\n---\nCreated by PM Copilot ({request.id}) after approval by {approved_by}."
    milestones: dict[str, dict[str, int]] = {}
    if isinstance(payload, CreateIssuesPayload):
        created: list[dict[str, Any]] = []
        try:
            for issue in payload.issues:
                body: dict[str, Any] = {"title": issue.title, "body": issue.body + footer, "labels": issue.labels}
                number = _milestone_number(github, payload.repo, issue.milestone, milestones)
                if number is not None:
                    body["milestone"] = number
                response = github.create_issue(payload.repo, body)
                created.append({"number": response["number"], "url": response.get("html_url"), "title": issue.title})
        except GitHubError as exc:
            raise ActionError(str(exc), {"created": created}) from exc
        return {"created": created}
    if isinstance(payload, SetMilestonePayload):
        updated: list[int] = []
        try:
            number = _milestone_number(github, payload.repo, payload.milestone, milestones)
            for issue_number in payload.issue_numbers:
                github.update_issue(payload.repo, issue_number, {"milestone": number})
                updated.append(issue_number)
        except GitHubError as exc:
            raise ActionError(str(exc), {"updated": updated}) from exc
        return {"updated": updated, "milestone": payload.milestone}
    raise ActionError(f"no executor for {request.action}", {})
