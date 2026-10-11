import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class State(StrEnum):
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    WANTS_TO_SPEAK = "WANTS_TO_SPEAK"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    IDLE = "IDLE"


class Decision(BaseModel):
    desire: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    interrupt: bool = False
    thought: str = Field(max_length=180)
    interest: str = Field(max_length=80)
    memory: str = Field(default="", max_length=120)
    kind: str = Field(default="association", pattern="^(question|association|recall|reaction)$")


@dataclass
class Agent:
    id: str
    name: str
    seed_interest: str
    state: State = State.LISTENING
    memory: list[str] = field(default_factory=list)
    interest: str = ""
    desire: float = 0
    last_spoke: float = -1e9
    pending: Decision | None = None
    observed_revision: int = -1
    reviews: int = 0

    def public(self):
        return {"id": self.id, "name": self.name, "state": self.state,
                "interest": self.interest, "desire": self.desire,
                "memory": self.memory, "pending": self.pending.model_dump() if self.pending else None}


@dataclass
class Message:
    speaker: str
    name: str
    text: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    interrupted: bool = False
    finished: bool = False
    kind: str = "reaction"

    def public(self):
        return asdict(self)


@dataclass
class Request:
    agent: Agent
    decision: Decision
    revision: int
    epoch: int
    created: float = field(default_factory=time.monotonic)
