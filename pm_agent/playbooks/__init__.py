"""Declarative playbooks: one per PM process (or a small group of related processes).

Playbooks are written in our own words and reference PMI process names; they do
not reproduce PMI text. The MCP server exposes each one as a prompt.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator

from pm_agent.governance import AutonomyLevel
from pm_agent.schemas.common import Model

PLAYBOOK_DIR = Path(__file__).parent

PRINCIPLES = {
    "holistic_view": "Adopt a Holistic View: trace effects across scope, schedule, finance, risk, "
                     "stakeholders and resources before recommending anything.",
    "focus_on_value": "Focus on Value: tie work and decisions to the objectives and benefits they serve.",
    "embed_quality": "Embed Quality Into Processes and Deliverables: check your output against the "
                     "quality checks before you finish.",
    "accountable_leader": "Be an Accountable Leader: be open about uncertainty, cite sources, and leave "
                          "decisions to the human.",
    "sustainability": "Integrate Sustainability Within All Project Areas: consider environmental, social "
                      "and economic effects.",
    "empowered_culture": "Build an Empowered Culture: support the team's own decisions; never judge or "
                         "rank individuals.",
}

AUTONOMY_MEANING = {
    AutonomyLevel.INFORM: "inform only: read and analyze, store nothing",
    AutonomyLevel.DRAFT: "draft for review: store drafts and proposals; the human approves",
    AutonomyLevel.ACT_WITH_APPROVAL: "act with approval: external writes need a one-click human approval",
    AutonomyLevel.AUTONOMOUS: "act within guardrails: routine internal writes allowed, logged for review",
}

GUARDRAILS = [
    "Numbers, dates, forecasts and RAG colours come from the tools. Never compute or choose them yourself.",
    "You can propose; only the human approves charters, decisions and change requests, promotes risks, "
    "and changes thresholds. The store enforces this, so don't try to work around a refusal.",
    "Put the evidence (issue URLs, doc links, meeting names) in `sources` and your reasoning in "
    "`rationale` when you save.",
    "If data is missing or stale, say 'unknown' and ask. Don't fill gaps with guesses.",
    "Never send email or post messages yourself; leave drafts for the user.",
]

FocusArea = Literal["initiating", "planning", "executing", "monitoring_controlling", "closing"]
Principle = Literal["holistic_view", "focus_on_value", "embed_quality", "accountable_leader", "sustainability",
                    "empowered_culture"]


class Playbook(Model):
    id: str
    name: str
    standard: Literal["PMBOK8", "SPgM5", "product"]
    processes: list[str] = Field(min_length=1)
    domain: str
    focus_areas: list[FocusArea] = Field(min_length=1)
    agent: Literal["project", "program", "product"]
    summary: str
    triggers: list[str] = Field(min_length=1)
    inputs: list[str]
    outputs: list[str]
    tools: list[str] = Field(min_length=1)
    host_capabilities: list[str] = Field(
        default_factory=list, description="Capabilities expected from the MCP host, e.g. its Google connectors."
    )
    autonomy: AutonomyLevel
    principles: list[Principle] = Field(min_length=1)
    quality_checks: list[str] = Field(min_length=1)
    instructions: str

    @field_validator("autonomy", mode="before")
    @classmethod
    def _parse_autonomy(cls, value: object) -> AutonomyLevel:
        return AutonomyLevel.parse(value)  # type: ignore[arg-type]

    def render(self, project_id: str) -> str:
        """The prompt text an MCP host receives for this playbook."""
        bullets = lambda items: "\n".join(f"- {item}" for item in items)  # noqa: E731
        host = (f"\n\n## Host tools you may use\n{bullets(self.host_capabilities)}\n"
                "(These come from your own connectors, such as Google Workspace; skip them if unavailable.)"
                if self.host_capabilities else "")
        return (
            f"# Playbook: {self.name}\n"
            f"Implements ({self.standard}): {', '.join(self.processes)}\n"
            f"Project: {project_id}\n"
            f"Autonomy: L{int(self.autonomy)} ({AUTONOMY_MEANING[self.autonomy]})\n\n"
            f"## Goal\n{self.summary.strip()}\n\n"
            f"## Steps\n{self.instructions.strip()}\n\n"
            f"## Store these outputs\n{bullets(self.outputs)}\n\n"
            f"## Quality checks before you finish\n{bullets(self.quality_checks)}\n\n"
            f"## Principles to apply\n{bullets(PRINCIPLES[p] for p in self.principles)}\n\n"
            f"## Guardrails\n{bullets(GUARDRAILS)}\n\n"
            f"## PM Copilot tools for this playbook\n{bullets(self.tools)}"
            f"{host}\n"
        )


@lru_cache(maxsize=None)
def load_playbooks(directory: Path = PLAYBOOK_DIR) -> dict[str, Playbook]:
    playbooks: dict[str, Playbook] = {}
    for path in sorted(directory.rglob("*.yaml")):
        playbook = Playbook.model_validate(yaml.safe_load(path.read_text()))
        if playbook.id in playbooks:
            raise ValueError(f"duplicate playbook id {playbook.id} in {path}")
        playbooks[playbook.id] = playbook
    return playbooks


def get_playbook(playbook_id: str) -> Playbook:
    playbooks = load_playbooks()
    if playbook_id not in playbooks:
        raise LookupError(f"unknown playbook {playbook_id!r}; known: {sorted(playbooks)}")
    return playbooks[playbook_id]


def resolve_playbook(name: str) -> Playbook:
    """Find a playbook by full id ('pmbok8.risk.identify_and_analyze_risks') or short name."""
    playbooks = load_playbooks()
    if name in playbooks:
        return playbooks[name]
    matches = [p for p in playbooks.values() if p.id.rsplit(".", 1)[-1] == name]
    if len(matches) != 1:
        known = sorted(p.id.rsplit(".", 1)[-1] for p in playbooks.values())
        raise LookupError(f"unknown playbook {name!r}; known: {known}")
    return matches[0]
