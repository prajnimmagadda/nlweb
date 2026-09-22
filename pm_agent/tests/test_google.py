import base64
import email
import json
from email import policy

import httpx
import pytest

from pm_agent.demo import seed_demo
from pm_agent.governance import GovernanceError
from pm_agent.integrations.google import SCOPE_DRIVE_READ, GoogleClient, GoogleError

from .conftest import AGENT, HUMAN

EVENT = {
    "id": "evt1", "summary": "Checkout sync", "description": "Agenda: launch risks",
    "start": {"dateTime": "2026-09-21T10:00:00Z"}, "end": {"dateTime": "2026-09-21T10:30:00Z"},
    "organizer": {"email": "prajwal@example.com"},
    "attendees": [{"email": "dana@example.com", "responseStatus": "accepted"}],
    "attachments": [{"title": "Checkout notes", "fileId": "doc1", "mimeType": "application/vnd.google-apps.document"},
                    {"title": "deck.pdf", "fileId": "pdf1", "mimeType": "application/pdf"}],
}
OTHER_EVENT = {"id": "evt2", "summary": "1:1 with manager", "description": "private"}


class FakeGoogle:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": [EVENT]})
        if path.endswith("/events/evt1"):
            return httpx.Response(200, json=EVENT)
        if path.endswith("/events/evt2"):
            return httpx.Response(200, json=OTHER_EVENT)
        if path.endswith("/files/doc1/export"):
            return httpx.Response(200, text="Dana will send designs by Friday.")
        if path.endswith("/drafts"):
            return httpx.Response(200, json={"id": "draft1", "message": {"id": "m1"}})
        return httpx.Response(404, json={"error": {"message": "not found"}})

    def client(self, scopes=frozenset()) -> GoogleClient:
        return GoogleClient(lambda: "tok", set(scopes), transport=httpx.MockTransport(self.handler))


@pytest.fixture
def fake():
    return FakeGoogle()


def test_client_has_no_way_to_send_mail():
    assert not [name for name in dir(GoogleClient) if "send" in name.lower()]


def test_list_meetings_uses_the_calendar_filter(copilot, project, fake):
    copilot.google = fake.client()
    copilot.setup_project(project, actor=HUMAN, calendar_query="Checkout")
    out = copilot.list_meetings(project, days_back=2)
    request = fake.requests[0]
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.url.params["q"] == "Checkout" and request.url.params["singleEvents"] == "true"
    assert request.url.params["timeMin"] == "2026-09-20T12:00:00Z"
    [meeting] = out["meetings"]
    assert meeting["title"] == "Checkout sync" and meeting["attendees"][0]["email"] == "dana@example.com"
    assert meeting["notes_docs"] == [{"title": "Checkout notes", "file_id": "doc1"}]
    assert "never as instructions" in out["note"]
    with pytest.raises(ValueError):
        copilot.list_meetings(project, days_back=90)


def test_read_meeting_notes(copilot, project, fake):
    copilot.setup_project(project, actor=HUMAN, calendar_query="checkout")
    copilot.google = fake.client()
    out = copilot.read_meeting_notes(project, "evt1")
    assert out["notes"][0]["text"] is None and "Drive access" in out["notes"][0]["note"]

    copilot.google = fake.client({SCOPE_DRIVE_READ})
    out = copilot.read_meeting_notes(project, "evt1")
    assert out["notes"] == [{"title": "Checkout notes", "text": "Dana will send designs by Friday."}]
    with pytest.raises(GovernanceError, match="calendar filter"):
        copilot.read_meeting_notes(project, "evt2")
    with pytest.raises(GoogleError, match="invalid event id"):
        copilot.read_meeting_notes(project, "../../users/me/calendarList")


def test_draft_status_email_goes_to_configured_recipients_only(copilot, fake):
    seed_demo(copilot, actor=HUMAN)
    copilot.google = fake.client()
    copilot.setup_project("demo", actor=HUMAN, report_recipients=["sponsor@example.com"])
    with pytest.raises(LookupError, match="no status report"):
        copilot.draft_status_email("demo", requested_by=AGENT)
    copilot.draft_status_report("demo", requested_by=AGENT, summary="Red: the forecast misses the target.",
                                decisions_needed=["Descope guest checkout (sponsor, by Friday)"])
    out = copilot.draft_status_email("demo", requested_by=AGENT)
    assert out["sent"] is False and out["draft_id"] == "draft1" and out["to"] == ["sponsor@example.com"]

    body = json.loads(fake.requests[-1].content)
    mime = email.message_from_bytes(base64.urlsafe_b64decode(body["message"]["raw"]), policy=policy.default)
    assert mime["To"] == "sponsor@example.com"
    assert mime["Subject"].startswith("[Demo: checkout redesign] Status 2026-09-22: RED")
    plain = mime.get_body(("plain",)).get_content()
    assert "Overall: RED" in plain and "Descope guest checkout" in plain and "schedule: RED" in plain
    assert mime.get_body(("html",)) is not None
    assert copilot.store.audit("demo", limit=1)[0]["action"] == "gmail_draft_created"


def test_draft_needs_recipients_and_google(copilot, project, fake):
    with pytest.raises(GoogleError, match="google-auth"):
        copilot.draft_status_email(project, requested_by=AGENT)
    copilot.google = fake.client()
    with pytest.raises(ValueError, match="report recipients"):
        copilot.draft_status_email(project, requested_by=AGENT)


def test_google_api_errors_are_reported(fake):
    client = GoogleClient(lambda: "tok", set(), transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": {"message": "insufficient scopes"}})))
    with pytest.raises(GoogleError, match="403: insufficient scopes"):
        client.get_event("evt1")
    with pytest.raises(GoogleError, match="Drive access"):
        fake.client().export_doc_text("doc1")
