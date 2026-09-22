"""SQLite system of record: versioned artifacts plus an append-only audit log.

A personal copilot needs zero-ops storage, so this is a single SQLite file. Every
write creates a new version (unless the content is unchanged) and an audit row
recording the actor, rationale and sources.
"""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pm_agent.governance import GovernanceError, check_write, validate_actor
from pm_agent.schemas import Artifact, artifact_type

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_versions (
    project_id   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    id           TEXT NOT NULL,
    version      INTEGER NOT NULL,
    data         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    actor        TEXT NOT NULL,
    rationale    TEXT,
    sources      TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    PRIMARY KEY (project_id, kind, id, version)
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    project_id  TEXT,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    kind        TEXT,
    artifact_id TEXT,
    version     INTEGER,
    rationale   TEXT,
    sources     TEXT NOT NULL DEFAULT '[]',
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _content_hash(artifact: Artifact) -> str:
    canonical = json.dumps(artifact.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class StoredArtifact:
    artifact: Artifact
    version: int
    actor: str
    created_at: datetime
    rationale: str | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.artifact.kind,
            "version": self.version,
            "actor": self.actor,
            "created_at": self.created_at.isoformat(),
            "rationale": self.rationale,
            "sources": self.sources,
            "data": self.artifact.model_dump(mode="json"),
        }


@dataclass(frozen=True)
class PutResult:
    version: int
    changed: bool


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path = str(Path(path).expanduser())
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ----- artifacts -------------------------------------------------------

    def put(
        self,
        artifact: Artifact,
        *,
        actor: str,
        rationale: str | None = None,
        sources: list[str] | tuple[str, ...] = (),
    ) -> PutResult:
        """Validate, check governance rules, and store a new version if content changed."""
        validate_actor(actor)
        artifact = type(artifact).model_validate(artifact.model_dump())  # re-run validators on mutated copies
        latest = self.get(artifact.project_id, artifact.kind, artifact.id)
        violations = check_write(artifact, latest.artifact if latest else None, actor)
        if violations:
            self._audit(artifact.project_id, actor, "write_denied", artifact.kind, artifact.id, None,
                        rationale, list(sources), {"violations": violations})
            raise GovernanceError(violations)

        digest = _content_hash(artifact)
        if latest and self._hash_of(artifact.project_id, artifact.kind, artifact.id, latest.version) == digest:
            return PutResult(version=latest.version, changed=False)

        version = latest.version + 1 if latest else 1
        with self._conn:
            self._conn.execute(
                "INSERT INTO artifact_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (artifact.project_id, artifact.kind, artifact.id, version,
                 artifact.model_dump_json(), digest, actor, rationale,
                 json.dumps(list(sources)), _now().isoformat()),
            )
        self._audit(artifact.project_id, actor, "create" if version == 1 else "update",
                    artifact.kind, artifact.id, version, rationale, list(sources))
        return PutResult(version=version, changed=True)

    def get(self, project_id: str, kind: str, artifact_id: str, version: int | None = None) -> StoredArtifact | None:
        query = "SELECT * FROM artifact_versions WHERE project_id=? AND kind=? AND id=?"
        params: list[Any] = [project_id, kind, artifact_id]
        if version is not None:
            query += " AND version=?"
            params.append(version)
        row = self._conn.execute(query + " ORDER BY version DESC LIMIT 1", params).fetchone()
        return self._row_to_stored(row) if row else None

    def list_latest(self, project_id: str, kind: str) -> list[StoredArtifact]:
        rows = self._conn.execute(
            """SELECT v.* FROM artifact_versions v
               JOIN (SELECT id, MAX(version) AS version FROM artifact_versions
                     WHERE project_id=? AND kind=? GROUP BY id) latest
                 ON v.id = latest.id AND v.version = latest.version
               WHERE v.project_id=? AND v.kind=? ORDER BY v.id""",
            (project_id, kind, project_id, kind),
        ).fetchall()
        return [self._row_to_stored(r) for r in rows]

    def history(self, project_id: str, kind: str, artifact_id: str) -> list[StoredArtifact]:
        rows = self._conn.execute(
            "SELECT * FROM artifact_versions WHERE project_id=? AND kind=? AND id=? ORDER BY version",
            (project_id, kind, artifact_id),
        ).fetchall()
        return [self._row_to_stored(r) for r in rows]

    def projects(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT project_id FROM artifact_versions WHERE kind='project_profile' ORDER BY project_id"
        ).fetchall()
        return [r["project_id"] for r in rows]

    def new_id(self, project_id: str, kind: str) -> str:
        """Next sequential id for a kind, e.g. 'R-7' for risks."""
        prefix = artifact_type(kind).id_prefix
        if not prefix:
            raise ValueError(f"{kind} artifacts need an explicit id")
        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        rows = self._conn.execute(
            "SELECT DISTINCT id FROM artifact_versions WHERE project_id=? AND kind=?", (project_id, kind)
        ).fetchall()
        numbers = [int(m.group(1)) for r in rows if (m := pattern.match(r["id"]))]
        return f"{prefix}-{max(numbers, default=0) + 1}"

    # ----- audit and meta --------------------------------------------------

    def log(self, project_id: str | None, actor: str, action: str, detail: dict[str, Any] | None = None,
            rationale: str | None = None, sources: list[str] | None = None) -> None:
        """Record a non-artifact action (a sync run, a computation, an approval note)."""
        validate_actor(actor)
        self._audit(project_id, actor, action, None, None, None, rationale, sources or [], detail)

    def audit(self, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM audit_log", []
        if project_id is not None:
            query += " WHERE project_id=?"
            params.append(project_id)
        rows = self._conn.execute(query + " ORDER BY seq DESC LIMIT ?", [*params, limit]).fetchall()
        return [
            {**dict(r), "sources": json.loads(r["sources"]), "detail": json.loads(r["detail"]) if r["detail"] else None}
            for r in rows
        ]

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (key, value))

    # ----- internals -------------------------------------------------------

    def _hash_of(self, project_id: str, kind: str, artifact_id: str, version: int) -> str:
        row = self._conn.execute(
            "SELECT content_hash FROM artifact_versions WHERE project_id=? AND kind=? AND id=? AND version=?",
            (project_id, kind, artifact_id, version),
        ).fetchone()
        return row["content_hash"]

    def _audit(self, project_id: str | None, actor: str, action: str, kind: str | None, artifact_id: str | None,
               version: int | None, rationale: str | None, sources: list[str],
               detail: dict[str, Any] | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO audit_log (ts, project_id, actor, action, kind, artifact_id, version, rationale, sources,"
                " detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_now().isoformat(), project_id, actor, action, kind, artifact_id, version, rationale,
                 json.dumps(sources), json.dumps(detail) if detail is not None else None),
            )

    @staticmethod
    def _row_to_stored(row: sqlite3.Row) -> StoredArtifact:
        cls = artifact_type(row["kind"])
        return StoredArtifact(
            artifact=cls.model_validate_json(row["data"]),
            version=row["version"],
            actor=row["actor"],
            created_at=datetime.fromisoformat(row["created_at"]),
            rationale=row["rationale"],
            sources=json.loads(row["sources"]),
        )
