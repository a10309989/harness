"""Serializable agent execution metadata propagated to tool boundaries."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class AgentExecutionFrame:
    agent_id: str
    message: str
    session_id: str


_agent_frame: ContextVar[AgentExecutionFrame | None] = ContextVar(
    "harness_agent_execution_frame",
    default=None,
)


def get_agent_execution_frame() -> AgentExecutionFrame | None:
    return _agent_frame.get()


@contextmanager
def agent_execution_scope(
    agent_id: str,
    message: str,
    session_id: str,
) -> Iterator[AgentExecutionFrame]:
    frame = AgentExecutionFrame(
        agent_id=agent_id,
        message=message,
        session_id=session_id,
    )
    token = _agent_frame.set(frame)
    try:
        yield frame
    finally:
        _agent_frame.reset(token)
