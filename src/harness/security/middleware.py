"""HTTP authentication, execution context, and request audit middleware."""

from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from harness.observability.context import (
    ExecutionContext,
    SYSTEM_ACTOR,
    new_id,
    reset_execution_context,
    set_execution_context,
)
from harness.security.auth import AuthenticationError
from harness.security.ratelimit import RateLimiter


class SecurityContextMiddleware(BaseHTTPMiddleware):
    PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-ID", "").strip()[:128] or new_id()
        auth_service = request.app.state.auth_service
        audit_service = request.app.state.audit_service
        rate_limiter = getattr(request.app.state, "rate_limiter", None)

        client_key = self._client_key(request)
        if rate_limiter is not None:
            if rate_limiter.is_locked_out(client_key):
                return self._reject(429, "Too many authentication attempts", trace_id)
            if request.url.path not in self.PUBLIC_PATHS and not rate_limiter.allow_request(client_key):
                return self._reject(429, "Rate limit exceeded", trace_id)

        authorization = request.headers.get("Authorization")
        if request.url.path in self.PUBLIC_PATHS and not authorization:
            actor = SYSTEM_ACTOR
        else:
            try:
                actor = await auth_service.authenticate(authorization)
                if rate_limiter is not None:
                    rate_limiter.record_auth_success(client_key)
            except AuthenticationError as exc:
                if rate_limiter is not None:
                    rate_limiter.record_auth_failure(client_key)
                return self._reject(401, str(exc), trace_id)

        request.state.actor = actor
        context = ExecutionContext(trace_id=trace_id, span_id=new_id(), actor=actor)
        token = set_execution_context(context)
        started = time.perf_counter()
        try:
            await audit_service.record(
                "request.started",
                action=f"{request.method} {request.url.path}",
                resource_type="http_request",
                metadata={"method": request.method, "path": request.url.path},
            )
            response = await call_next(request)
            await audit_service.record(
                "request.completed",
                action=f"{request.method} {request.url.path}",
                resource_type="http_request",
                decision="success" if response.status_code < 400 else "failure",
                metadata={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
        except Exception as exc:
            await audit_service.record(
                "request.failed",
                action=f"{request.method} {request.url.path}",
                resource_type="http_request",
                decision="failure",
                reason=exc.__class__.__name__,
                metadata={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        finally:
            reset_execution_context(token)

        response.headers["X-Trace-ID"] = trace_id
        return response

    @staticmethod
    def _client_key(request: Request) -> str:
        """Identify a caller for rate limiting, degrading gracefully behind proxies."""
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
        return ip

    @staticmethod
    def _reject(status_code: int, detail: str, trace_id: str) -> JSONResponse:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        response.headers["X-Trace-ID"] = trace_id
        return response
