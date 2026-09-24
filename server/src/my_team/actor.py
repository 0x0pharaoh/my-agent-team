from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Actor:
    kind: Literal["human", "agent", "system"]
    id: str
    session_id: str | None = None
    agent_id: str | None = None

    @property
    def is_human(self) -> bool:
        return self.kind == "human"

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"


HUMAN = Actor("human", "human")
SYSTEM = Actor("system", "system")


def agent_actor(session_row) -> Actor:
    return Actor("agent", session_row["agent_id"], session_row["id"], session_row["agent_id"])
