"""Shared building blocks for artifact schemas."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

Rag = Literal["green", "amber", "red", "unknown"]
Engagement = Literal["unaware", "resistant", "neutral", "supportive", "leading"]


class Model(BaseModel):
    """Strict base model: unknown fields are rejected, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


class Link(Model):
    """A typed reference from one artifact to another (e.g. risk -> objective)."""

    rel: str = Field(description="Relationship, e.g. 'objective', 'work_item', 'risk', 'decision'.")
    target: str = Field(description="Id of the linked artifact.")


class Artifact(Model):
    """Base class for everything kept in the system of record.

    Provenance (who wrote it, why, from which sources) is recorded by the store
    per version, so it is deliberately not part of the artifact content.
    """

    kind: ClassVar[str]
    id_prefix: ClassVar[str] = ""

    id: str
    project_id: str
    links: list[Link] = Field(default_factory=list)
