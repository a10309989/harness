"""Optional entrypoint: expose the local Master as an A2A service.

Specialized agents are deployed independently and imported through remote A2A.
"""

from __future__ import annotations

import asyncio

import uvicorn

from harness.a2a.bridge import create_real_a2a_app
from harness.runtime.settings import RuntimeSettings


def create_app():
    settings = RuntimeSettings()
    return asyncio.run(
        create_real_a2a_app(
            db_path=settings.database_url,
            service_name="Harness Master A2A Service",
            auth_token=settings.a2a_remote_agent_token,
        )
    )


def main() -> None:
    settings = RuntimeSettings()
    uvicorn.run(
        "harness.a2a.local_app:create_app",
        factory=True,
        host=settings.a2a_remote_agent_host,
        port=settings.a2a_remote_agent_port + 1,
        reload=False,
    )


if __name__ == "__main__":
    main()
