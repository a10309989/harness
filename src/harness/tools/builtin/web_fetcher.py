"""Built-in tool: HTTP/Web fetch operations."""

import httpx

from harness.models.tool import ToolResult
from harness.models.policy import RiskLevel
from harness.models.workflow import IdempotencyMode
from harness.tools.base import BaseTool


class WebFetcher(BaseTool):
    """Fetch content from a URL via HTTP."""

    name = "web_fetcher"
    description = "Fetch content from a URL via HTTP GET request."
    parameters_schema = {
        "url": {
            "type": "string",
            "description": "URL to fetch",
            "required": True,
        },
        "headers": {
            "type": "object",
            "description": "Optional HTTP headers",
        },
    }
    risk_level = RiskLevel.MEDIUM
    risk_tags = frozenset({"network", "read"})
    idempotency_mode = IdempotencyMode.SAFE_RETRY

    def __init__(self, user_agent: str = "Harness-WebFetcher/0.1") -> None:
        super().__init__()
        self.user_agent = user_agent

    async def execute(self, url: str, headers: dict | None = None) -> ToolResult:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                req_headers = {"User-Agent": self.user_agent, **(headers or {})}
                response = await client.get(url, headers=req_headers, follow_redirects=True)
                return ToolResult(
                    success=response.is_success,
                    data={
                        "status_code": response.status_code,
                        "content": response.text[:10000],  # Truncate
                        "content_type": response.headers.get("content-type", ""),
                    },
                )
        except httpx.RequestError as e:
            return ToolResult(success=False, error=str(e))
