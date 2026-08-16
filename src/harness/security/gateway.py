"""Ingress controls shared by every HTTP API route."""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

TRACEPARENT_PATTERN = re.compile(r"^[\da-f]{2}-([\da-f]{32})-[\da-f]{16}-[\da-f]{2}$")


def trace_id_from_traceparent(value: str | None) -> str | None:
    """Extract a valid W3C trace ID without accepting arbitrary headers."""
    match = TRACEPARENT_PATTERN.fullmatch((value or "").lower())
    return match.group(1) if match else None


class GatewayMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests and normalize distributed trace propagation."""

    async def dispatch(self, request: Request, call_next):
        settings = request.app.state.settings
        max_bytes = settings.gateway_max_request_bytes
        content_length = request.headers.get("content-length")
        try:
            is_oversized = content_length is not None and int(content_length) > max_bytes
        except ValueError:
            is_oversized = True
        if is_oversized:
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        if settings.gateway_external_required:
            identity = self._gateway_identity(request, settings)
            if identity is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Trusted gateway identity is required"},
                )
            request.state.gateway_identity = identity
        if "x-trace-id" not in request.headers:
            trace_id = trace_id_from_traceparent(request.headers.get("traceparent"))
            if trace_id:
                headers = list(request.scope["headers"])
                headers.append((b"x-trace-id", trace_id.encode()))
                request.scope["headers"] = headers
        return await call_next(request)

    @staticmethod
    def _gateway_identity(request: Request, settings) -> dict | None:
        subject = request.headers.get(settings.gateway_oidc_subject_header)
        mtls_subject = request.headers.get(settings.gateway_mtls_subject_header)
        waf_verdict = request.headers.get(settings.gateway_waf_header, "allow").lower()
        if waf_verdict not in {"allow", "pass"}:
            return None
        if not subject or not mtls_subject:
            return None
        return {
            "subject": subject,
            "email": request.headers.get(settings.gateway_oidc_email_header, ""),
            "mtls_subject": mtls_subject,
            "quota_subject": request.headers.get(settings.gateway_quota_header, subject),
            "waf_verdict": waf_verdict,
        }
