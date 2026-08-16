"""Execution context and trace propagation using context variables."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Iterator
from uuid import uuid4

from harness.security.models import ActorContext, ActorType


def new_id() -> str:
    return uuid4().hex


SYSTEM_ACTOR = ActorContext(
    actor_id="system",
    actor_type=ActorType.SYSTEM,
    display_name="Harness System",
    roles=("system",),
    permissions=frozenset({"*"}),
    auth_method="internal",
)


@dataclass(frozen=True)
class ExecutionContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    session_id: str | None = None
    workflow_id: str | None = None
    actor: ActorContext = SYSTEM_ACTOR

    def child(
        self,
        *,
        session_id: str | None = None,
        workflow_id: str | None = None,
        trace_id: str | None = None,
    ) -> "ExecutionContext":
        return ExecutionContext(
            trace_id=trace_id or self.trace_id,
            span_id=new_id(),
            parent_span_id=self.span_id,
            session_id=session_id if session_id is not None else self.session_id,
            workflow_id=workflow_id if workflow_id is not None else self.workflow_id,
            actor=self.actor,
        )

    def with_session(self, session_id: str) -> "ExecutionContext":
        return replace(self, session_id=session_id)


_execution_context: ContextVar[ExecutionContext | None] = ContextVar(
    "harness_execution_context", default=None
)


def get_execution_context() -> ExecutionContext:
    context = _execution_context.get()
    if context is not None:
        return context
    return ExecutionContext(trace_id=new_id(), span_id=new_id())


def set_execution_context(context: ExecutionContext) -> Token:
    return _execution_context.set(context)


def reset_execution_context(token: Token) -> None:
    _execution_context.reset(token)


@contextmanager
def child_span(
    *,
    session_id: str | None = None,
    workflow_id: str | None = None,
    trace_id: str | None = None,
) -> Iterator[ExecutionContext]:
    child = get_execution_context().child(
        session_id=session_id,
        workflow_id=workflow_id,
        trace_id=trace_id,
    )
    token = set_execution_context(child)
    try:
        yield child
    finally:
        reset_execution_context(token)
