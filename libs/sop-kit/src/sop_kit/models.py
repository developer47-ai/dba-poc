"""Data model of an SOP (standard operating procedure) or reference document."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DocKind(StrEnum):
    PROCEDURE = "procedure"  # step-by-step troubleshooting flow
    REFERENCE = "reference"  # knowledge only (tables, explanations)


class DocStatus(StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    RETIRED = "retired"


class Actor(StrEnum):
    AGENT = "agent"        # the AI agent does it (usually with a tool)
    EXPERT = "expert"      # the support expert does or says it
    CUSTOMER = "customer"  # the expert asks the customer to do it


class StepEnd(StrEnum):
    RESOLVED = "resolved"    # problem solved or nothing more to do
    ESCALATE = "escalate"    # hand to a team or another escalation SOP
    HANDOVER = "handover"    # another agent takes over
    CONTINUE = "continue"    # continue with another SOP of the same agent


class Section(BaseModel):
    title: str
    text: str


class Step(BaseModel):
    step_id: str
    title: str
    actor: Actor
    instruction: str
    tool: str | None = None
    requires_consent: bool = False
    next: dict[str, str] = Field(default_factory=dict)  # outcome -> step_id
    end: StepEnd | None = None
    escalate_to: str | None = None
    handover_to: str | None = None
    continue_with: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.end is not None


class SopDocument(BaseModel):
    doc_id: str
    title: str
    version: str
    status: DocStatus = DocStatus.ACTIVE
    kind: DocKind = DocKind.PROCEDURE
    owner: str = ""
    language: str = "en"
    effective_from: str | None = None
    agent_scope: list[str] = Field(default_factory=list)
    carrier_types: list[str] = Field(default_factory=list)
    carrier_tech_types: list[str] = Field(default_factory=list)
    device_models: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    user_groups: list[str] = Field(default_factory=list)
    entry_step: str | None = None
    sections: list[Section] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    source: str = ""
    checksum: str = ""

    def step(self, step_id: str) -> Step:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"{self.doc_id} has no step {step_id}")

    def metadata(self) -> dict[str, Any]:
        """Filterable metadata copied onto every chunk."""
        return {
            "doc_id": self.doc_id,
            "version": self.version,
            "kind": self.kind.value,
            "title": self.title,
            "agent_scope": self.agent_scope,
            "carrier_types": self.carrier_types,
            "carrier_tech_types": self.carrier_tech_types,
            "device_models": self.device_models,
            "symptoms": self.symptoms,
            "user_groups": self.user_groups,
        }


class Chunk(BaseModel):
    """A piece of a document that is embedded and searched on its own."""

    chunk_id: str
    doc_id: str
    version: str
    chunk_type: str  # summary | section | step
    section_path: list[str]
    step_id: str | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
