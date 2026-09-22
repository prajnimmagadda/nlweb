"""Google Calendar (read), Drive (read attached meeting notes) and Gmail (drafts only).

The client can create Gmail drafts but has no way to send mail: there is no send
method, so a draft always waits for you. Note that Google's `gmail.compose` scope
would technically allow sending; the restriction is in this code.

Authorization uses Google's installed-app OAuth flow (`python -m pm_agent google-auth`).
The google-auth libraries are imported only when needed, so the rest of the copilot
works without them.
"""

import base64
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_DRIVE_READ = "https://www.googleapis.com/auth/drive.readonly"
BASE_SCOPES = [SCOPE_CALENDAR, SCOPE_GMAIL_COMPOSE]

DEFAULT_CLIENT_SECRETS = "~/.pm-copilot/google_client.json"
DEFAULT_TOKEN = "~/.pm-copilot/google_token.json"

_CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_DRIVE_EXPORT = "https://www.googleapis.com/drive/v3/files/{file_id}/export"
_GMAIL_DRAFTS = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
GOOGLE_DOC = "application/vnd.google-apps.document"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class GoogleError(RuntimeError):
    pass


def _import_google_auth():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        raise GoogleError("Google support needs extra packages: pip install google-auth google-auth-oauthlib") \
            from None
    return Credentials, Request


def authorize(client_secrets: str = DEFAULT_CLIENT_SECRETS, token_path: str = DEFAULT_TOKEN,
              with_drive: bool = False) -> list[str]:
    """Run the browser consent flow and store the token (readable only by you). Returns granted scopes."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise GoogleError("Google support needs extra packages: pip install google-auth google-auth-oauthlib") \
            from None
    secrets = Path(client_secrets).expanduser()
    if not secrets.exists():
        raise GoogleError(f"OAuth client file not found at {secrets}; see pm_agent/README.md (Google setup)")
    scopes = BASE_SCOPES + ([SCOPE_DRIVE_READ] if with_drive else [])
    credentials = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes).run_local_server(port=0)
    _save_token(Path(token_path).expanduser(), credentials.to_json())
    return list(credentials.scopes or scopes)


def _save_token(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)


class CredentialsTokenProvider:
    """Supplies a fresh access token from the stored OAuth token, refreshing when needed."""

    def __init__(self, token_path: str = DEFAULT_TOKEN):
        Credentials, Request = _import_google_auth()
        self._path = Path(token_path).expanduser()
        self._request = Request()
        self._credentials = Credentials.from_authorized_user_file(str(self._path))
        self.scopes = set(self._credentials.scopes or [])

    def __call__(self) -> str:
        if not self._credentials.valid:
            self._credentials.refresh(self._request)
            _save_token(self._path, self._credentials.to_json())
        return self._credentials.token


def load_client(token_path: str | None = None) -> "GoogleClient | None":
    """A client from the stored token, or None when Google isn't connected (or can't be loaded)."""
    token_path = token_path or os.environ.get("PM_COPILOT_GOOGLE_TOKEN", DEFAULT_TOKEN)
    if not Path(token_path).expanduser().exists():
        return None
    try:
        provider = CredentialsTokenProvider(token_path)
    except (GoogleError, ValueError, OSError) as exc:
        print(f"pm-copilot: Google token found but not usable ({exc}); continuing without Google", file=sys.stderr)
        return None
    return GoogleClient(provider, provider.scopes)


def _safe_id(value: str, what: str) -> str:
    # Ids come from the model; keep them from reshaping the request path.
    if not _ID_RE.match(value):
        raise GoogleError(f"invalid {what}: {value!r}")
    return value


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleClient:
    def __init__(self, token_provider: Callable[[], str], scopes: set[str],
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self._token = token_provider
        self.scopes = set(scopes)
        self._http = httpx.Client(transport=transport, timeout=timeout)

    @property
    def can_read_drive(self) -> bool:
        return SCOPE_DRIVE_READ in self.scopes

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, url, headers={"Authorization": f"Bearer {self._token()}"}, **kwargs)
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message", response.text)
            except (ValueError, AttributeError):
                message = response.text
            raise GoogleError(f"Google API returned {response.status_code}: {message}")
        return response

    def list_events(self, time_min: datetime, time_max: datetime, query: str | None = None,
                    max_results: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeMin": _rfc3339(time_min), "timeMax": _rfc3339(time_max),
                                  "singleEvents": "true", "orderBy": "startTime", "maxResults": max_results}
        if query:
            params["q"] = query
        return self._request("GET", _CALENDAR, params=params).json().get("items", [])

    def get_event(self, event_id: str) -> dict[str, Any]:
        return self._request("GET", f"{_CALENDAR}/{_safe_id(event_id, 'event id')}").json()

    def export_doc_text(self, file_id: str) -> str:
        if not self.can_read_drive:
            raise GoogleError("Drive access wasn't granted; re-run `python -m pm_agent google-auth --drive`")
        url = _DRIVE_EXPORT.format(file_id=_safe_id(file_id, "file id"))
        return self._request("GET", url, params={"mimeType": "text/plain"}).text

    def create_draft(self, to: list[str], subject: str, text: str, html: str | None = None) -> dict[str, Any]:
        """Create a Gmail draft in your mailbox. Nothing is sent."""
        message = EmailMessage()
        message["To"] = ", ".join(to)
        message["Subject"] = subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return self._request("POST", _GMAIL_DRAFTS, json={"message": {"raw": raw}}).json()
